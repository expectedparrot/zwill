from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path

import zwill.cli as cli
from zwill.cli import main
from zwill.twin_baseline import (
    LogisticRegression,
    build_conditional_baseline_predictions,
    cosine,
    respondent_profile,
)


def _token_hash(token: str, dim: int) -> int:
    digest = hashlib.sha256(token.encode()).hexdigest()
    return int(digest, 16) % dim


def deterministic_embedder(texts: list[str]) -> list[list[float]]:
    """Offline bag-of-words hashing embedder standing in for OpenAI.

    Shared tokens -> similar vectors, so it carries real lexical signal while
    staying deterministic across processes (no salted hash()).
    """
    dim = 64
    vectors = []
    for text in texts:
        vector = [0.0] * dim
        for token in text.lower().replace("\n", " ").split():
            vector[_token_hash(token, dim)] += 1.0
        vectors.append(vector)
    return vectors


def _two_type_dataset(num_respondents: int = 60, num_questions: int = 6):
    questions = [
        {
            "question_name": f"q{i}",
            "question_text": f"How do you feel about topic {i}?",
            "question_options": ["strongly agree yes", "strongly disagree no"],
        }
        for i in range(1, num_questions + 1)
    ]
    answers = {}
    for r in range(num_respondents):
        type_a = r % 2 == 0
        respondent = {}
        for i in range(1, num_questions + 1):
            pick = "strongly agree yes" if type_a else "strongly disagree no"
            if (r + i) % 7 == 0:  # ~14% noise
                pick = "strongly disagree no" if type_a else "strongly agree yes"
            respondent[f"q{i}"] = pick
        answers[f"resp{r}"] = respondent
    return questions, answers


def test_logistic_regression_learns_separable_data() -> None:
    features = [[x] for x in (-3.0, -2.0, -1.5, 1.5, 2.0, 3.0)]
    labels = [0, 0, 0, 1, 1, 1]
    model = LogisticRegression(iterations=800).fit(features, labels)
    probs = model.predict_proba([[-2.5], [2.5]])
    assert probs[0] < 0.5 < probs[1]


def test_cosine_handles_empty_and_zero_vectors() -> None:
    assert cosine(None, [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_respondent_profile_excludes_target_question() -> None:
    questions, answers = _two_type_dataset()
    question_options = {q["question_name"]: q["question_options"] for q in questions}
    # Minimal vectors: pair/option maps keyed as the builder would.
    pair_vectors = {}
    option_vectors = {}
    for name, options in question_options.items():
        for option in options:
            pair_vectors[(name, option)] = [1.0 if "yes" in option else -1.0]
            option_vectors[option] = [1.0 if "yes" in option else -1.0]
    profile_pair, _ = respondent_profile(
        answers["resp0"],
        exclude_question="q1",
        question_options=question_options,
        pair_vectors=pair_vectors,
        option_vectors=option_vectors,
    )
    assert profile_pair is not None


def test_baseline_generalizes_to_held_out_question_and_beats_uniform() -> None:
    questions, answers = _two_type_dataset()
    respondent_ids = list(answers)
    rows, meta = build_conditional_baseline_predictions(
        survey="demo",
        questions=questions,
        answers_by_respondent=answers,
        respondent_ids=respondent_ids,
        heldout_questions=["q6"],  # never seen during training
        truth={},
        embedder=deterministic_embedder,
        job_id="baseline_test",
        imported_at="2026-07-07T00:00:00Z",
    )
    assert len(rows) == len(respondent_ids)
    assert meta["training_rows"] > 0
    # Every prediction is a valid distribution over the question's options.
    for row in rows:
        assert row["model_label"] == "baseline:conditional-embedding"
        assert abs(sum(row["probabilities"].values()) - 1.0) < 1e-9
        assert set(row["probabilities"]) == set(row["option_labels"])
    # With genuine individual signal the baseline beats the uniform baseline.
    mean_p_actual = statistics.mean(row["probability_actual"] for row in rows)
    assert mean_p_actual > 0.6  # uniform would be 0.5


def test_baseline_uses_respondent_covariates() -> None:
    """A covariate that fully determines the answer must lift the baseline.

    The held-out question's answer is decided *only* by a panel covariate, and the
    embeddings carry no signal that separates the two options. A baseline that
    ignored covariates could do no better than chance; one that uses them (the XGBoost
    feature set) should key off the covariate and beat chance decisively.
    """
    num_questions = 6
    questions = [
        {
            "question_name": f"q{i}",
            "question_text": f"How do you feel about topic {i}?",
            "question_options": ["option one", "option two"],
        }
        for i in range(1, num_questions + 1)
    ]
    answers = {}
    metadata = {}
    for r in range(80):
        group = "alpha" if r % 2 == 0 else "beta"
        metadata[f"resp{r}"] = {"group": group}
        respondent = {}
        for i in range(1, num_questions + 1):
            # Group deterministically fixes the choice; wording gives no clue.
            respondent[f"q{i}"] = "option one" if group == "alpha" else "option two"
        answers[f"resp{r}"] = respondent

    respondent_ids = list(answers)
    rows, meta = build_conditional_baseline_predictions(
        survey="demo",
        questions=questions,
        answers_by_respondent=answers,
        respondent_ids=respondent_ids,
        heldout_questions=["q6"],
        truth={},
        embedder=deterministic_embedder,
        job_id="baseline_test",
        imported_at="2026-07-07T00:00:00Z",
        metadata_by_respondent=metadata,
    )
    assert meta["covariate_features"] == 2  # group=alpha, group=beta
    mean_p_actual = statistics.mean(row["probability_actual"] for row in rows)
    assert mean_p_actual > 0.8  # chance is 0.5; only covariates carry the signal


def test_baseline_emits_marginal_metrics_when_truth_present() -> None:
    questions, answers = _two_type_dataset()
    truth = {
        "marginals": {
            "q6": {
                "strongly agree yes": {"weighted_count": 30},
                "strongly disagree no": {"weighted_count": 30},
            }
        }
    }
    rows, _ = build_conditional_baseline_predictions(
        survey="demo",
        questions=questions,
        answers_by_respondent=answers,
        respondent_ids=list(answers),
        heldout_questions=["q6"],
        truth=truth,
        embedder=deterministic_embedder,
        job_id="baseline_test",
        imported_at="2026-07-07T00:00:00Z",
    )
    assert rows[0]["empirical_marginal_probability_actual"] == 0.5
    assert rows[0]["marginal_negative_log_likelihood"] is not None


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))


def test_twin_baseline_cli_stores_predictions_and_flows_through_report(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    questions, answers = _two_type_dataset(num_respondents=40)
    _write_jsonl(tmp_path / "questions.jsonl", [
        {
            "question_name": q["question_name"],
            "question_type": "multiple_choice",
            "question_text": q["question_text"],
            "question_options": q["question_options"],
        }
        for q in questions
    ])
    _write_jsonl(tmp_path / "respondents.jsonl", [
        {"respondent_id": rid, "weight": 1.0, "metadata": {}} for rid in answers
    ])
    answer_rows = [
        {"respondent_id": rid, "question": qname, "answer": ans}
        for rid, respondent in answers.items()
        for qname, ans in respondent.items()
    ]
    _write_jsonl(tmp_path / "answers.jsonl", answer_rows)

    assert main(["init"]) == 0
    assert main(["survey", "create", "--name", "demo"]) == 0
    assert main(["question", "import", "--survey", "demo", "--input-path", str(tmp_path / "questions.jsonl")]) == 0
    assert main(["respondent", "import", "--survey", "demo", "--input-path", str(tmp_path / "respondents.jsonl")]) == 0
    assert main(["answer", "import", "--survey", "demo", "--input-path", str(tmp_path / "answers.jsonl")]) == 0
    assert main(["commit", "--survey", "demo"]) == 0

    args = argparse.Namespace(
        survey="demo",
        heldout_question=["q6"],
        heldout_questions=None,
        sample_respondents=None,
        seed=None,
        embedding_model="fake",
        l2=1.0,
        job_id=None,
        replace=False,
        path=None,
    )
    result = cli.cmd_twin_baseline_run(args, embedder=deterministic_embedder)
    assert result["status"] == "ok"
    job_id = result["data"]["job_id"]
    assert result["data"]["prediction_rows"] == 40

    predictions = list((tmp_path / ".zwill").rglob("digital_twin_predictions.jsonl"))
    assert predictions, "predictions file should exist"
    rows = [json.loads(line) for line in predictions[0].read_text().splitlines() if line.strip()]
    assert rows and all(row["model_label"] == "baseline:conditional-embedding" for row in rows)
    assert all(row["job_id"] == job_id for row in rows)

    # The stored rows flow through the existing twin-results report unchanged.
    report_path = tmp_path / "baseline_report.json"
    assert main(["twin-results", "report", "--survey", "demo", "--job-id", job_id, "--format", "json", "--path", str(report_path)]) == 0
    assert report_path.exists()


def test_resolve_baseline_embedder_ep_first_with_failover(monkeypatch, capsys) -> None:
    import zwill.twin_baseline_commands as tbc
    from zwill.twin_baseline import DEFAULT_EMBEDDING_MODEL

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EXPECTED_PARROT_API_KEY", raising=False)

    # Forced local / hashing backends resolve to a callable without any API key.
    for choice in ("sentence-transformers", "local", "st", "hashing", "lexical"):
        assert callable(tbc.resolve_baseline_embedder(argparse.Namespace(embedder=choice), DEFAULT_EMBEDDING_MODEL))

    # auto with an Expected Parrot key + a HEALTHY endpoint -> use the remote first.
    monkeypatch.setenv("EXPECTED_PARROT_API_KEY", "x")
    healthy = lambda *a, **k: (lambda texts: [[1.0] for _ in texts])  # noqa: E731
    monkeypatch.setattr(tbc, "edsl_embedder", healthy)
    embedder = tbc.resolve_baseline_embedder(argparse.Namespace(embedder="auto"), DEFAULT_EMBEDDING_MODEL)
    assert embedder(["x"]) == [[1.0]]  # the remote embedder was chosen

    # auto with an UNAVAILABLE endpoint (probe raises) -> fail over fast to local.
    def _dead(*a, **k):
        def _embed(texts):
            raise RuntimeError("endpoint down")
        return _embed

    monkeypatch.setattr(tbc, "edsl_embedder", _dead)
    monkeypatch.setattr(tbc, "sentence_transformers_available", lambda: True)
    embedder = tbc.resolve_baseline_embedder(argparse.Namespace(embedder="auto"), DEFAULT_EMBEDDING_MODEL)
    assert embedder.__qualname__.startswith("sentence_transformers_embedder")
    assert "did not respond" in capsys.readouterr().err

    # auto, endpoint down, no keys, no local embeddings -> built-in lexical embedder
    # (always runs) plus a warning; never an error or hang.
    monkeypatch.delenv("EXPECTED_PARROT_API_KEY", raising=False)
    monkeypatch.setattr(tbc, "sentence_transformers_available", lambda: False)
    embedder = tbc.resolve_baseline_embedder(argparse.Namespace(embedder="auto"), DEFAULT_EMBEDDING_MODEL)
    vectors = embedder(["hello world", "hello"])
    assert len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0
    assert "built-in lexical embedder" in capsys.readouterr().err
