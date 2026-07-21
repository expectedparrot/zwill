from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import zwill.cli as cli
from zwill.cli import main
from zwill.twin import one_hot_metrics


def _token_hash(token: str, dim: int) -> int:
    return int(hashlib.sha256(token.encode()).hexdigest(), 16) % dim


def deterministic_embedder(texts: list[str]) -> list[list[float]]:
    dim = 64
    vectors = []
    for text in texts:
        vector = [0.0] * dim
        for token in text.lower().replace("\n", " ").split():
            vector[_token_hash(token, dim)] += 1.0
        vectors.append(vector)
    return vectors


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))


def _dataset(n: int = 40, num_questions: int = 6):
    options = ["I strongly like it", "I strongly dislike it"]
    questions = [
        {
            "question_name": f"q{i}",
            "question_type": "multiple_choice",
            "question_text": f"How do you feel about topic {i}?",
            "question_options": options,
        }
        for i in range(1, num_questions + 1)
    ]
    answers = {}
    for r in range(n):
        like = r % 2 == 0
        respondent = {}
        for i in range(1, num_questions + 1):
            pick = options[0] if like else options[1]
            if (r + i) % 7 == 0:
                pick = options[1] if like else options[0]
            respondent[f"q{i}"] = pick
        answers[f"resp{r}"] = respondent
    return questions, answers, options


def _plant_twin_job(sdir: Path, survey: str, answers: dict, options: list[str], heldout: str, job_id: str) -> None:
    """Write a synthetic frontier-twin prediction job into the survey store."""
    # Empirical marginal for the held-out question (as import would compute).
    counts = {opt: 0 for opt in options}
    for respondent in answers.values():
        counts[respondent[heldout]] += 1
    total = sum(counts.values())
    marginal = {opt: counts[opt] / total for opt in options}

    rows = []
    for rid, respondent in answers.items():
        actual = respondent[heldout]
        # A decent-but-imperfect twin: 0.7 on the actual answer.
        probs = {opt: (0.7 if opt == actual else 0.3 / (len(options) - 1)) for opt in options}
        metrics = one_hot_metrics(options, actual, probs)
        marginal_metrics = one_hot_metrics(options, actual, marginal)
        rows.append(
            {
                "job_id": job_id,
                "survey": survey,
                "respondent_id": rid,
                "heldout_question": heldout,
                "heldout_question_text": "How do you feel about topic 6?",
                "actual_answer": actual,
                "model": "gpt-5.5",
                "service": "openai",
                "model_label": "openai:gpt-5.5",
                "option_labels": options,
                "probabilities": probs,
                **metrics,
                "empirical_marginal_probabilities": marginal,
                "empirical_marginal_probability_actual": marginal_metrics["probability_actual"],
                "empirical_marginal_negative_log_likelihood": marginal_metrics["negative_log_likelihood"],
                "empirical_marginal_brier": marginal_metrics["brier"],
                "empirical_marginal_top1_correct": marginal_metrics["top1_correct"],
            }
        )
    predictions = sdir / "digital_twin_predictions.jsonl"
    _write_jsonl(predictions, rows)
    (sdir / "digital_twin_jobs" / job_id).mkdir(parents=True, exist_ok=True)
    (sdir / "digital_twin_jobs" / job_id / "import.json").write_text(json.dumps({"job_id": job_id}))


def _build_survey(tmp_path: Path) -> tuple[dict, list[str]]:
    questions, answers, options = _dataset()
    _write_jsonl(tmp_path / "questions.jsonl", questions)
    _write_jsonl(tmp_path / "respondents.jsonl", [{"respondent_id": r, "weight": 1.0, "metadata": {}} for r in answers])
    _write_jsonl(
        tmp_path / "answers.jsonl",
        [{"respondent_id": rid, "question": q, "answer": a} for rid, resp in answers.items() for q, a in resp.items()],
    )
    assert main(["init"]) == 0
    assert main(["survey", "create", "--name", "demo"]) == 0
    assert main(["question", "import", "--survey", "demo", "--input-path", str(tmp_path / "questions.jsonl")]) == 0
    assert main(["respondent", "import", "--survey", "demo", "--input-path", str(tmp_path / "respondents.jsonl")]) == 0
    assert main(["answer", "import", "--survey", "demo", "--input-path", str(tmp_path / "answers.jsonl")]) == 0
    assert main(["commit", "--survey", "demo"]) == 0
    return answers, options


def _survey_dir(tmp_path: Path) -> Path:
    matches = list((tmp_path / ".zwill").rglob("questions.jsonl"))
    assert matches
    return matches[0].parent


def test_twin_validate_runs_full_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    answers, options = _build_survey(tmp_path)
    sdir = _survey_dir(tmp_path)
    _plant_twin_job(sdir, "demo", answers, options, heldout="q6", job_id="twinjob1")

    out_dir = tmp_path / "validation"
    args = argparse.Namespace(
        survey="demo",
        job_id=["twinjob1"],
        jobs=None,
        out=str(out_dir),
        view="full",
        skip_baseline=False,
        require_baseline=False,
        skip_leakage_audit=False,
        skip_bootstrap=False,
        embedding_model="fake",
        l2=1.0,
        leakage_threshold=0.7,
        min_pair_rows=5,
        n_boot=200,
        ci=0.95,
        seed=0,
    )
    result = cli.cmd_twin_validate(args, embedder=deterministic_embedder)
    assert result["status"] == "ok"

    # Bundle artifacts all exist.
    for name in (
        "report.html",
        "bootstrap.json",
        "bootstrap-intervals.svg",
        "calibration.svg",
        "leakage_audit.json",
        "manifest.json",
    ):
        assert (out_dir / name).exists(), name

    data = result["data"]
    assert data["twin_job_ids"] == ["twinjob1"]
    assert data["baseline_job_id"]  # baseline ran and produced a job
    assert data["heldout_questions"] == ["q6"]

    # The report compares the twin against the conditional baseline.
    report = (out_dir / "report.html").read_text()
    assert "baseline:conditional-embedding" in report
    assert "Skill scores" in report

    # Bootstrap produced paired deltas vs the baseline.
    bootstrap = json.loads((out_dir / "bootstrap.json").read_text())
    assert "deltas_vs_baseline" in bootstrap
    assert bootstrap["deltas_vs_baseline"]["baseline_model"] == "baseline:conditional-embedding"


def test_twin_validate_can_skip_baseline(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    answers, options = _build_survey(tmp_path)
    sdir = _survey_dir(tmp_path)
    _plant_twin_job(sdir, "demo", answers, options, heldout="q6", job_id="twinjob1")

    out_dir = tmp_path / "validation_nobaseline"
    args = argparse.Namespace(
        survey="demo",
        job_id=["twinjob1"],
        jobs=None,
        out=str(out_dir),
        view="summary",
        skip_baseline=True,
        require_baseline=False,
        skip_leakage_audit=False,
        skip_bootstrap=False,
        embedding_model="fake",
        l2=1.0,
        leakage_threshold=0.7,
        min_pair_rows=5,
        n_boot=100,
        ci=0.95,
        seed=0,
    )
    result = cli.cmd_twin_validate(args)  # no embedder needed since baseline skipped
    assert result["status"] == "ok"
    assert result["data"]["baseline_job_id"] is None
    assert (out_dir / "report.html").exists()
    assert (out_dir / "leakage_audit.json").exists()
