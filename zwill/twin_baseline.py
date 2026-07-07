"""Cheap conditional baseline for digital-twin validation.

The digital-twin study asks a frontier model to predict a respondent's answer to
a held-out question from that respondent's observed answers. To know whether the
LLM is actually earning its keep, we need a cheap "individual information, no LLM"
baseline that uses the *same* observed answers.

A per-question regression cannot do this: a genuinely held-out target question has
never been seen, so there are no per-question weights to learn. This baseline
instead works entirely in embedding space. Every (question, option) pair and every
option label is embedded once; a respondent is represented by the centroid of the
(question, selected-option) pairs they actually chose. A small logistic regression
is trained across the *non*-held-out questions to map similarity features to a
select/not-select label, then applied to the held-out questions. Because every
feature is a semantic similarity rather than a question identity, the learned model
transfers to target questions it has never seen -- exactly the deployment scenario
a digital twin claims to handle.

Predictions are emitted in the same row schema as real twin predictions, tagged
with a baseline ``model_label``, so they flow through the existing scoring,
diagnostics, and comparison reports unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any, Callable

from .probability import true_probabilities_for
from .twin import one_hot_metrics

# An embedder maps a list of texts to a list of equal-length float vectors.
Embedder = Callable[[list[str]], list[list[float]]]

MODEL_LABEL = "baseline:conditional-embedding"
BASELINE_SERVICE = "baseline"
BASELINE_MODEL = "conditional-embedding"
FEATURE_VERSION = "v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------
def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    na, nb = _norm(a), _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return _dot(a, b) / (na * nb)


def centroid(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    dim = len(vectors[0])
    totals = [0.0] * dim
    for vector in vectors:
        for index in range(dim):
            totals[index] += vector[index]
    return [value / len(vectors) for value in totals]


# ---------------------------------------------------------------------------
# Text conventions for the things we embed
# ---------------------------------------------------------------------------
def pair_text(question_text: str, option_label: str) -> str:
    return f"{question_text}\n[option] {option_label}"


def option_text(option_label: str) -> str:
    return f"[option] {option_label}"


# ---------------------------------------------------------------------------
# OpenAI embedder (lazy-imported so import of this module never requires openai)
# ---------------------------------------------------------------------------
def openai_embedder(
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    api_key: str | None = None,
    batch_size: int = 256,
) -> Embedder:
    def embed(texts: list[str]) -> list[list[float]]:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - exercised only without openai
            raise RuntimeError(
                "The 'openai' package is required for the conditional baseline's "
                "embeddings. Install it, or pass a custom embedder."
            ) from exc
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set; the conditional baseline needs it to "
                "embed question and option text (or pass a custom embedder)."
            )
        client = OpenAI(api_key=key)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            response = client.embeddings.create(model=model, input=chunk)
            vectors.extend(item.embedding for item in response.data)
        return vectors

    return embed


def embedding_index(texts: list[str], embedder: Embedder) -> dict[str, list[float]]:
    """Embed each unique text exactly once and return a text -> vector map."""
    unique = sorted({text for text in texts if text})
    if not unique:
        return {}
    vectors = embedder(unique)
    if len(vectors) != len(unique):
        raise ValueError(
            f"Embedder returned {len(vectors)} vectors for {len(unique)} texts."
        )
    return dict(zip(unique, vectors))


# ---------------------------------------------------------------------------
# Pure-Python logistic regression (small feature vector, so this is plenty)
# ---------------------------------------------------------------------------
class LogisticRegression:
    def __init__(self, *, l2: float = 1.0, learning_rate: float = 0.5, iterations: int = 500) -> None:
        self.l2 = l2
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.weights: list[float] = []
        self.bias: float = 0.0
        self._means: list[float] = []
        self._stds: list[float] = []

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0:
            z = math.exp(-value)
            return 1.0 / (1.0 + z)
        z = math.exp(value)
        return z / (1.0 + z)

    def _standardize(self, rows: list[list[float]]) -> list[list[float]]:
        return [
            [
                (row[index] - self._means[index]) / self._stds[index]
                for index in range(len(self._means))
            ]
            for row in rows
        ]

    def fit(self, features: list[list[float]], labels: list[int]) -> "LogisticRegression":
        if not features:
            raise ValueError("cannot fit logistic regression on empty features")
        dim = len(features[0])
        n = len(features)
        self._means = [sum(row[i] for row in features) / n for i in range(dim)]
        self._stds = []
        for i in range(dim):
            variance = sum((row[i] - self._means[i]) ** 2 for row in features) / n
            self._stds.append(math.sqrt(variance) or 1.0)
        standardized = self._standardize(features)
        self.weights = [0.0] * dim
        self.bias = 0.0
        for _ in range(self.iterations):
            grad_w = [0.0] * dim
            grad_b = 0.0
            for row, label in zip(standardized, labels):
                prediction = self._sigmoid(self._raw(row))
                error = prediction - label
                for i in range(dim):
                    grad_w[i] += error * row[i]
                grad_b += error
            for i in range(dim):
                grad_w[i] = grad_w[i] / n + self.l2 * self.weights[i] / n
                self.weights[i] -= self.learning_rate * grad_w[i]
            self.bias -= self.learning_rate * (grad_b / n)
        return self

    def _raw(self, standardized_row: list[float]) -> float:
        return self.bias + sum(w * x for w, x in zip(self.weights, standardized_row))

    def predict_proba(self, features: list[list[float]]) -> list[float]:
        standardized = self._standardize(features)
        return [self._sigmoid(self._raw(row)) for row in standardized]


# ---------------------------------------------------------------------------
# Respondent representation and features
# ---------------------------------------------------------------------------
def respondent_profile(
    answers: dict[str, str],
    *,
    exclude_question: str | None,
    question_options: dict[str, list[str]],
    pair_vectors: dict[tuple[str, str], list[float]],
    option_vectors: dict[str, list[float]],
) -> tuple[list[float] | None, list[float] | None]:
    """Centroids of the (question, selected-option) pairs a respondent chose."""
    pair_selected: list[list[float]] = []
    option_selected: list[list[float]] = []
    for question_name, answer in answers.items():
        if question_name == exclude_question:
            continue
        options = question_options.get(question_name)
        if not options or answer not in options:
            continue
        pair_vector = pair_vectors.get((question_name, answer))
        if pair_vector is not None:
            pair_selected.append(pair_vector)
        option_vector = option_vectors.get(answer)
        if option_vector is not None:
            option_selected.append(option_vector)
    return centroid(pair_selected), centroid(option_selected)


def option_features(
    question_name: str,
    option_label: str,
    profile_pair: list[float] | None,
    profile_option: list[float] | None,
    pair_vectors: dict[tuple[str, str], list[float]],
    option_vectors: dict[str, list[float]],
) -> list[float]:
    pair_vector = pair_vectors.get((question_name, option_label))
    option_vector = option_vectors.get(option_label)
    return [
        cosine(pair_vector, profile_pair),
        cosine(option_vector, profile_option),
    ]


# ---------------------------------------------------------------------------
# Top-level: train across non-held-out questions, predict held-out ones
# ---------------------------------------------------------------------------
def baseline_job_id(
    survey: str,
    heldout_questions: list[str],
    respondent_ids: list[str],
    embedding_model: str,
) -> str:
    payload = {
        "survey": survey,
        "heldout": sorted(heldout_questions),
        "respondents": sorted(respondent_ids),
        "model_label": MODEL_LABEL,
        "feature_version": FEATURE_VERSION,
        "embedding_model": embedding_model,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "baseline_" + hashlib.sha256(raw.encode()).hexdigest()[:14]


def build_conditional_baseline_predictions(
    *,
    survey: str,
    questions: list[dict[str, Any]],
    answers_by_respondent: dict[str, dict[str, str]],
    respondent_ids: list[str],
    heldout_questions: list[str],
    truth: dict[str, Any],
    embedder: Embedder,
    job_id: str,
    imported_at: str,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    l2: float = 1.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    question_by_name = {str(q["question_name"]): q for q in questions}
    question_options = {
        name: [str(option) for option in (q.get("question_options") or [])]
        for name, q in question_by_name.items()
    }
    heldout_set = set(heldout_questions)

    # Collect and embed every text we need, exactly once.
    texts: list[str] = []
    for name, question in question_by_name.items():
        question_text = str(question.get("question_text") or name)
        for option in question_options.get(name, []):
            texts.append(pair_text(question_text, option))
            texts.append(option_text(option))
    index = embedding_index(texts, embedder)

    pair_vectors: dict[tuple[str, str], list[float]] = {}
    option_vectors: dict[str, list[float]] = {}
    for name, question in question_by_name.items():
        question_text = str(question.get("question_text") or name)
        for option in question_options.get(name, []):
            pair_vectors[(name, option)] = index[pair_text(question_text, option)]
            option_vectors[option] = index[option_text(option)]

    # Training set: every (respondent, non-held-out question, option) triple.
    train_features: list[list[float]] = []
    train_labels: list[int] = []
    for respondent_id, answers in answers_by_respondent.items():
        for question_name, answer in answers.items():
            if question_name in heldout_set or question_name not in question_by_name:
                continue
            options = question_options.get(question_name, [])
            if answer not in options:
                continue
            profile_pair, profile_option = respondent_profile(
                answers,
                exclude_question=question_name,
                question_options=question_options,
                pair_vectors=pair_vectors,
                option_vectors=option_vectors,
            )
            if profile_pair is None:
                continue
            for option in options:
                train_features.append(
                    option_features(
                        question_name, option, profile_pair, profile_option, pair_vectors, option_vectors
                    )
                )
                train_labels.append(1 if option == answer else 0)

    if not train_features:
        raise ValueError(
            "No training rows: need respondents with observed answers to non-held-out questions."
        )
    model = LogisticRegression(l2=l2).fit(train_features, train_labels)

    rows: list[dict[str, Any]] = []
    skipped_no_profile = 0
    skipped_no_actual = 0
    for heldout_question in heldout_questions:
        if heldout_question not in question_by_name:
            continue
        question = question_by_name[heldout_question]
        options = question_options.get(heldout_question, [])
        heldout_text = str(question.get("question_text") or heldout_question)
        marginal_probabilities = true_probabilities_for(heldout_question, truth, options) if truth else {}
        for respondent_id in respondent_ids:
            answers = answers_by_respondent.get(respondent_id, {})
            actual_answer = answers.get(heldout_question)
            if actual_answer is None:
                skipped_no_actual += 1
                continue
            profile_pair, profile_option = respondent_profile(
                answers,
                exclude_question=heldout_question,
                question_options=question_options,
                pair_vectors=pair_vectors,
                option_vectors=option_vectors,
            )
            if profile_pair is None:
                skipped_no_profile += 1
                continue
            feature_rows = [
                option_features(heldout_question, option, profile_pair, profile_option, pair_vectors, option_vectors)
                for option in options
            ]
            raw_scores = model.predict_proba(feature_rows)
            total = sum(raw_scores)
            if total <= 0:
                probabilities = [1.0 / len(options) for _ in options]
            else:
                probabilities = [score / total for score in raw_scores]
            probabilities_by_option = {option: probabilities[i] for i, option in enumerate(options)}
            metrics = one_hot_metrics(options, actual_answer, probabilities_by_option)
            marginal_metrics = (
                one_hot_metrics(options, actual_answer, marginal_probabilities)
                if marginal_probabilities
                else {}
            )
            rows.append(
                {
                    "job_id": job_id,
                    "survey": survey,
                    "respondent_id": respondent_id,
                    "heldout_question": heldout_question,
                    "heldout_question_text": heldout_text,
                    "actual_answer": actual_answer,
                    "model": BASELINE_MODEL,
                    "service": BASELINE_SERVICE,
                    "model_label": MODEL_LABEL,
                    "model_parameters": {"embedding_model": embedding_model, "feature_version": FEATURE_VERSION},
                    "option_labels": options,
                    "probabilities": probabilities_by_option,
                    "raw_probabilities": raw_scores,
                    "raw_probability_sum": total,
                    "notes": f"Conditional embedding baseline ({FEATURE_VERSION}).",
                    **metrics,
                    "empirical_marginal_probabilities": marginal_probabilities,
                    "empirical_marginal_probability_actual": marginal_metrics.get("probability_actual"),
                    "empirical_marginal_negative_log_likelihood": marginal_metrics.get("negative_log_likelihood"),
                    "empirical_marginal_brier": marginal_metrics.get("brier"),
                    "empirical_marginal_top1_correct": marginal_metrics.get("top1_correct"),
                    "marginal_probabilities": marginal_probabilities,
                    "marginal_probability_actual": marginal_metrics.get("probability_actual"),
                    "marginal_negative_log_likelihood": marginal_metrics.get("negative_log_likelihood"),
                    "marginal_brier": marginal_metrics.get("brier"),
                    "marginal_top1_correct": marginal_metrics.get("top1_correct"),
                    "imported_at": imported_at,
                }
            )

    meta = {
        "job_id": job_id,
        "model_label": MODEL_LABEL,
        "embedding_model": embedding_model,
        "feature_version": FEATURE_VERSION,
        "training_rows": len(train_features),
        "prediction_rows": len(rows),
        "heldout_questions": [q for q in heldout_questions if q in question_by_name],
        "weights": model.weights,
        "bias": model.bias,
        "skipped_no_profile": skipped_no_profile,
        "skipped_no_actual": skipped_no_actual,
        "unique_texts_embedded": len(index),
    }
    return rows, meta
