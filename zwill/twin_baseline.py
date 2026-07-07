"""Cheap conditional baseline for digital-twin validation.

The digital-twin study asks a frontier model to predict a respondent's answer to
a held-out question from that respondent's observed answers. To know whether the
LLM is actually earning its keep, we need a cheap "individual information, no LLM"
baseline that uses the *same* observed answers.

A per-question regression cannot do this: a genuinely held-out target question has
never been seen, so there are no per-question weights to learn. This baseline
instead works entirely in embedding space. Every (question, option) pair and every
option label is embedded once; a respondent is represented by the mean of the
(question, selected-option) pairs they actually chose. A small logistic regression
maps two similarity features -- how close a candidate option is to the respondent's
profile -- to a select/not-select label.

Training is leave-one-question-out: each held-out target is scored by a model
trained only on the *other* option-bearing questions, so the target question's own
answer pattern never enters training. Because every feature is a semantic
similarity rather than a question identity, the model transfers to target
questions it has never seen -- exactly the deployment scenario a digital twin
claims to handle.

Predictions are emitted in the same row schema as real twin predictions, tagged
with a baseline ``model_label``, so they flow through the existing scoring,
diagnostics, and comparison reports unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable

import numpy as np

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
# Vector helpers (kept list-friendly for direct use and testing)
# ---------------------------------------------------------------------------
def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(va @ vb / (na * nb))


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


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
    dimensions: int | None = 512,
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
        extra = {"dimensions": dimensions} if dimensions else {}
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            response = client.embeddings.create(model=model, input=chunk, **extra)
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
        raise ValueError(f"Embedder returned {len(vectors)} vectors for {len(unique)} texts.")
    return dict(zip(unique, vectors))


# ---------------------------------------------------------------------------
# Logistic regression (numpy; small feature vector, standardized inputs)
# ---------------------------------------------------------------------------
class LogisticRegression:
    def __init__(self, *, l2: float = 1.0, learning_rate: float = 0.5, iterations: int = 500) -> None:
        self.l2 = l2
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.weights: list[float] = []
        self.bias: float = 0.0
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None

    def _standardize(self, features: np.ndarray) -> np.ndarray:
        return (features - self._mean) / self._std

    def fit(self, features: list[list[float]] | np.ndarray, labels: list[int] | np.ndarray) -> "LogisticRegression":
        x = np.asarray(features, dtype=float)
        y = np.asarray(labels, dtype=float)
        if x.size == 0:
            raise ValueError("cannot fit logistic regression on empty features")
        self._mean = x.mean(axis=0)
        std = x.std(axis=0)
        std[std == 0.0] = 1.0
        self._std = std
        standardized = self._standardize(x)
        n, dim = standardized.shape
        weights = np.zeros(dim)
        bias = 0.0
        for _ in range(self.iterations):
            predictions = 1.0 / (1.0 + np.exp(-(standardized @ weights + bias)))
            error = predictions - y
            grad_w = standardized.T @ error / n + self.l2 * weights / n
            grad_b = float(error.mean())
            weights -= self.learning_rate * grad_w
            bias -= self.learning_rate * grad_b
        self.weights = weights.tolist()
        self.bias = float(bias)
        return self

    def predict_proba(self, features: list[list[float]] | np.ndarray) -> list[float]:
        x = np.asarray(features, dtype=float)
        standardized = self._standardize(x)
        weights = np.asarray(self.weights)
        return (1.0 / (1.0 + np.exp(-(standardized @ weights + self.bias)))).tolist()


# ---------------------------------------------------------------------------
# List-based respondent profile (used directly and in tests)
# ---------------------------------------------------------------------------
def respondent_profile(
    answers: dict[str, str],
    *,
    exclude_question: str | None,
    question_options: dict[str, list[str]],
    pair_vectors: dict[tuple[str, str], list[float]],
    option_vectors: dict[str, list[float]],
) -> tuple[list[float] | None, list[float] | None]:
    """Mean of the (question, selected-option) pairs a respondent chose."""
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
    pair_profile = np.mean(pair_selected, axis=0).tolist() if pair_selected else None
    option_profile = np.mean(option_selected, axis=0).tolist() if option_selected else None
    return pair_profile, option_profile


def has_conditional_baseline(model_labels: list[str]) -> bool:
    return MODEL_LABEL in set(model_labels)


def conditional_baseline_appendix_html() -> str:
    """Explain what the conditional baseline is and why it is the fair comparison.

    Rendered as a report appendix whenever a `baseline:conditional-embedding`
    model appears alongside digital-twin predictions.
    """
    return """
<section class="appendix" style="margin-top:3rem;padding-top:1.5rem;border-top:1px solid var(--ep-border,#e0e0e0);">
  <h2>Appendix: the conditional baseline and why it is a fair comparison</h2>
  <p>
    One of the models above is labelled <code>baseline:conditional-embedding</code>.
    It is not a digital twin. It is a deliberately cheap statistical model included
    to answer one question: is the frontier-model twin adding <em>individual-level</em>
    signal, or only rediscovering patterns a trivial model could find?
  </p>
  <p>
    The two obvious baselines do not answer that. A <strong>uniform</strong> baseline
    uses no information. An <strong>empirical-marginal</strong> baseline uses the
    population distribution of the target question &mdash; it has no notion of the
    individual, and it is an <em>oracle</em>: for a genuinely new question, that
    distribution does not exist yet. The digital twin's whole claim is to predict a
    question no one has answered, using what a specific respondent said elsewhere.
    The conditional baseline is built to test exactly that claim on the cheap.
  </p>
  <h3>How it is built</h3>
  <ul>
    <li>Every (question, option) pair and every option label is embedded once.</li>
    <li>Each respondent is represented by the mean embedding of the options they
      actually chose &mdash; a compact summary of "what this person is like".</li>
    <li>A small logistic regression maps two similarity features (how close a
      candidate option is to that respondent's profile) to a select / do-not-select
      outcome, and the per-option scores are normalised into a distribution.</li>
  </ul>
  <h3>Why it is fair, not rigged</h3>
  <p>
    Training is <strong>leave-one-question-out</strong>: each held-out target is
    scored by a model trained only on the <em>other</em> questions. Nothing about the
    target &mdash; not a single respondent's answer to it, and not its marginal &mdash;
    ever enters training. The baseline therefore uses only what is genuinely available
    when predicting a new question: the wording of that question and its options, and
    the respondent's answers to <em>other</em> questions.
  </p>
  <p>
    In particular, the baseline does <strong>not</strong> use the target question's own
    empirical marginal. That would be leakage: for a real new question the marginal is
    unknown, and the twin does not get it either. Because every feature is a semantic
    similarity rather than a question identity, the fitted model transfers to target
    questions it has never seen &mdash; the same generalisation the twin claims.
  </p>
  <h3>How to read the comparison</h3>
  <p>
    A twin that <strong>cannot</strong> beat this baseline is not adding individual
    signal beyond cheap embedding similarity. A twin that <strong>does</strong> beat it
    &mdash; and beats the oracle empirical marginal &mdash; is contributing real
    predictive value about individuals, most plausibly from pretrained world knowledge
    that a small linear model cannot recover from the available questions alone.
  </p>
</section>
"""


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


# ---------------------------------------------------------------------------
# Top-level: train leave-one-question-out, predict held-out questions
# ---------------------------------------------------------------------------
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
    option_question_names = [name for name in question_by_name if question_options.get(name)]

    # Collect and embed every text we need, exactly once, then unit-normalize so
    # cosine similarity becomes a dot product.
    texts: list[str] = []
    for name in option_question_names:
        question_text = str(question_by_name[name].get("question_text") or name)
        for option in question_options[name]:
            texts.append(pair_text(question_text, option))
            texts.append(option_text(option))
    index = embedding_index(texts, embedder)
    if not index:
        raise ValueError("No option-bearing questions to embed for the conditional baseline.")

    # Per option-bearing question, unit-normalized matrices of pair and option vectors.
    pair_unit: dict[str, np.ndarray] = {}
    option_unit: dict[str, np.ndarray] = {}
    for name in option_question_names:
        question_text = str(question_by_name[name].get("question_text") or name)
        pair_unit[name] = _unit(
            np.asarray([index[pair_text(question_text, option)] for option in question_options[name]], dtype=float)
        )
        option_unit[name] = _unit(
            np.asarray([index[option_text(option)] for option in question_options[name]], dtype=float)
        )

    # Per respondent: unit pair/option vectors for the options they selected, plus
    # running sums so a profile that excludes any one question is an O(dim) update.
    selected_pair: dict[str, dict[str, np.ndarray]] = {}
    selected_option: dict[str, dict[str, np.ndarray]] = {}
    pair_sum: dict[str, np.ndarray] = {}
    option_sum: dict[str, np.ndarray] = {}
    for respondent_id, answers in answers_by_respondent.items():
        pair_map: dict[str, np.ndarray] = {}
        option_map: dict[str, np.ndarray] = {}
        for question_name, answer in answers.items():
            options = question_options.get(question_name)
            if not options or answer not in options:
                continue
            position = options.index(answer)
            pair_map[question_name] = pair_unit[question_name][position]
            option_map[question_name] = option_unit[question_name][position]
        if not pair_map:
            continue
        selected_pair[respondent_id] = pair_map
        selected_option[respondent_id] = option_map
        pair_sum[respondent_id] = np.sum(list(pair_map.values()), axis=0)
        option_sum[respondent_id] = np.sum(list(option_map.values()), axis=0)

    def profile_excluding(respondent_id: str, exclude: str) -> tuple[np.ndarray, np.ndarray] | None:
        pair_map = selected_pair.get(respondent_id)
        if not pair_map:
            return None
        count = len(pair_map)
        if exclude in pair_map:
            count -= 1
            if count <= 0:
                return None
            pair_profile = (pair_sum[respondent_id] - pair_map[exclude]) / count
            option_profile = (option_sum[respondent_id] - selected_option[respondent_id][exclude]) / count
        else:
            pair_profile = pair_sum[respondent_id] / count
            option_profile = option_sum[respondent_id] / count
        return _unit(pair_profile), _unit(option_profile)

    def feature_matrix(question_name: str, pair_profile: np.ndarray, option_profile: np.ndarray) -> np.ndarray:
        # cosine(candidate, profile) == dot(unit candidate, unit profile)
        return np.column_stack(
            (pair_unit[question_name] @ pair_profile, option_unit[question_name] @ option_profile)
        )

    # Training features grouped by source question (profile excludes its own question).
    features_by_question: dict[str, np.ndarray] = {}
    labels_by_question: dict[str, np.ndarray] = {}
    for question_name in option_question_names:
        options = question_options[question_name]
        feature_rows: list[np.ndarray] = []
        label_rows: list[np.ndarray] = []
        for respondent_id, pair_map in selected_pair.items():
            if question_name not in pair_map:
                continue
            profile = profile_excluding(respondent_id, question_name)
            if profile is None:
                continue
            feature_rows.append(feature_matrix(question_name, *profile))
            selected = answers_by_respondent[respondent_id][question_name]
            label_rows.append(np.asarray([1.0 if option == selected else 0.0 for option in options]))
        if feature_rows:
            features_by_question[question_name] = np.vstack(feature_rows)
            labels_by_question[question_name] = np.concatenate(label_rows)

    if not features_by_question:
        raise ValueError(
            "No training rows: need respondents with observed answers to option-bearing questions."
        )

    # Leave-one-question-out models.
    models: dict[str, LogisticRegression] = {}
    training_rows_by_question: dict[str, int] = {}
    for heldout_question in heldout_questions:
        if heldout_question not in features_by_question:
            continue
        train_x = np.vstack([m for q, m in features_by_question.items() if q != heldout_question])
        train_y = np.concatenate([labels_by_question[q] for q in features_by_question if q != heldout_question])
        if train_x.size == 0:
            continue
        models[heldout_question] = LogisticRegression(l2=l2).fit(train_x, train_y)
        training_rows_by_question[heldout_question] = int(train_x.shape[0])

    rows: list[dict[str, Any]] = []
    skipped_no_profile = 0
    skipped_no_actual = 0
    for heldout_question in heldout_questions:
        model = models.get(heldout_question)
        if model is None:
            continue
        question = question_by_name[heldout_question]
        options = question_options[heldout_question]
        heldout_text = str(question.get("question_text") or heldout_question)
        marginal_probabilities = true_probabilities_for(heldout_question, truth, options) if truth else {}
        weights = np.asarray(model.weights)
        for respondent_id in respondent_ids:
            answers = answers_by_respondent.get(respondent_id, {})
            actual_answer = answers.get(heldout_question)
            if actual_answer is None:
                skipped_no_actual += 1
                continue
            profile = profile_excluding(respondent_id, heldout_question)
            if profile is None:
                skipped_no_profile += 1
                continue
            standardized = (feature_matrix(heldout_question, *profile) - model._mean) / model._std
            raw_scores = 1.0 / (1.0 + np.exp(-(standardized @ weights + model.bias)))
            total = float(raw_scores.sum())
            probabilities = (
                [1.0 / len(options)] * len(options) if total <= 0 else (raw_scores / total).tolist()
            )
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
                    "raw_probabilities": raw_scores.tolist(),
                    "raw_probability_sum": total,
                    "notes": f"Conditional embedding baseline ({FEATURE_VERSION}), leave-one-question-out.",
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

    scored_questions = sorted(models)
    meta = {
        "job_id": job_id,
        "model_label": MODEL_LABEL,
        "embedding_model": embedding_model,
        "feature_version": FEATURE_VERSION,
        "training_rows": sum(training_rows_by_question.values()),
        "training_rows_by_question": training_rows_by_question,
        "prediction_rows": len(rows),
        "heldout_questions": scored_questions,
        "unscored_questions": [q for q in heldout_questions if q not in models],
        "feature_weights_by_question": {q: models[q].weights for q in scored_questions},
        "skipped_no_profile": skipped_no_profile,
        "skipped_no_actual": skipped_no_actual,
        "unique_texts_embedded": len(index),
    }
    return rows, meta
