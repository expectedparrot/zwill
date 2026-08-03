from __future__ import annotations

from zwill.twin_baseline import MODEL_LABEL as BASELINE
from zwill.twin_report_data import REPORT_DATA_SCHEMA_VERSION, build_report_data


def _row(job, label, q, rid, p, actual="A"):
    return {
        "job_id": job,
        "model_label": label,
        "heldout_question": q,
        "respondent_id": rid,
        "actual_answer": actual,
        "probability_actual": p,
        "negative_log_likelihood": 0.3,
        "brier": 0.2,
        "top1_correct": 1 if p >= 0.5 else 0,
        "uniform_probability_actual": 0.5,
        "uniform_negative_log_likelihood": 0.693,
        "uniform_brier": 0.5,
    }


def test_build_report_data_shape_and_sections() -> None:
    rows = (
        [_row("twinjob1", "openai:gpt-5.5", "q1", f"r{i}", 0.8) for i in range(10)]
        + [_row("basejob1", BASELINE, "q1", f"r{i}", 0.4) for i in range(10)]
    )
    rd = build_report_data(
        survey="demo",
        rows=rows,
        heldout_questions=["q1"],
        respondent_count=10,
        twin_job_ids=["twinjob1"],
        baseline_job_id="basejob1",
        bootstrap_data={"n_boot": 100},
        leakage_summary={"flagged_count": 0, "pair_count": 0},
        baseline_embedding_model="text-embedding-3-small",
    )
    assert rd["schema_version"] == REPORT_DATA_SCHEMA_VERSION
    for section in ("skill_scores", "baseline_diagnostics", "marginals", "examples", "design"):
        assert section in rd
    # skill scores present for the twin arm
    assert "openai:gpt-5.5" in rd["skill_scores"]
    # baseline diagnostics document the fitted model
    assert rd["baseline_diagnostics"]["hyperparameters"]["n_estimators"] == 300
    assert rd["baseline_diagnostics"]["embedding_model"] == "text-embedding-3-small"
    # confident hits come from the twin, not the baseline
    for hit in rd["examples"]["confident_hits"]:
        assert hit["model_label"] != BASELINE
