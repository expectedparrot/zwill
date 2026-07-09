from __future__ import annotations

import math

from zwill.cli import build_twin_report
from zwill.executive_summary import build_executive_summary
from zwill.reporting import render_twin_summary_report_html
from zwill.twin_baseline import MODEL_LABEL as BASELINE_MODEL_LABEL


def _row(model_label: str, respondent: str, actual: str, nll: float, correct: int, p_actual: float = 0.8) -> dict:
    other = "B" if actual == "A" else "A"
    probs = {actual: p_actual, other: 1.0 - p_actual}
    return {
        "job_id": "twin1" if model_label != BASELINE_MODEL_LABEL else "base1",
        "survey": "demo",
        "respondent_id": respondent,
        "heldout_question": "q1",
        "heldout_question_text": "Pick one",
        "actual_answer": actual,
        "model": model_label.split(":")[-1],
        "service": model_label.split(":")[0],
        "model_label": model_label,
        "option_labels": ["A", "B"],
        "probabilities": probs,
        "raw_probabilities": [probs["A"], probs["B"]],
        "probability_actual": p_actual,
        "uniform_probability_actual": 0.5,
        "uniform_negative_log_likelihood": math.log(2),
        "negative_log_likelihood": nll,
        "uniform_brier": 0.5,
        "brier": 0.1,
        "brier_improvement": 0.4,
        "top1_correct": correct,
        "actual_rank": 1 if correct else 2,
        "empirical_marginal_probabilities": {"A": 0.5, "B": 0.5},
        "empirical_marginal_probability_actual": 0.5,
        "empirical_marginal_negative_log_likelihood": math.log(2),
        "empirical_marginal_brier": 0.5,
        "empirical_marginal_top1_correct": 1,
        "observed_answers": [],
    }


def _render(rows):
    payload = build_twin_report(rows)
    return render_twin_summary_report_html(
        "demo", payload["rows"], payload["summary"], payload["diagnostics"], {"job_ids": ["twin1"]}
    )


def test_conditional_baseline_surfaces_in_per_question_table() -> None:
    # Twin clearly beats the XGBoost baseline (lower NLL).
    twin = [_row("openai:gpt-5.5", "r1", "A", 0.20, 1), _row("openai:gpt-5.5", "r2", "B", 0.25, 1)]
    baseline = [_row(BASELINE_MODEL_LABEL, "r1", "A", 0.55, 1), _row(BASELINE_MODEL_LABEL, "r2", "B", 0.60, 0)]
    html = _render(twin + baseline)

    # The decisive comparison column and the baseline reference row are present.
    assert "NLL improvement vs conditional baseline" in html
    assert "Conditional baseline (XGBoost)" in html
    # The twin's verdict is judged against the baseline, not the trivial floor.
    assert "conditional baseline" in html.lower()
    assert "Beats conditional baseline" in html


def test_no_conditional_column_when_baseline_absent() -> None:
    # Backward compatible: without a baseline, no new column appears.
    twin = [_row("openai:gpt-5.5", "r1", "A", 0.20, 1), _row("openai:gpt-5.5", "r2", "B", 0.25, 1)]
    html = _render(twin)
    assert "NLL improvement vs conditional baseline" not in html
    assert "Conditional baseline (XGBoost)" not in html


def test_executive_summary_features_conditional_baseline(tmp_path) -> None:
    twin = [_row("openai:gpt-5.5", "r1", "A", 0.20, 1, p_actual=0.8), _row("openai:gpt-5.5", "r2", "B", 0.25, 1, p_actual=0.8)]
    baseline = [_row(BASELINE_MODEL_LABEL, "r1", "A", 0.55, 1, p_actual=0.5), _row(BASELINE_MODEL_LABEL, "r2", "B", 0.60, 0, p_actual=0.5)]
    result = build_executive_summary(
        twin,
        survey="demo",
        path=tmp_path / "exec.html",
        markdown_path=None,
        simulations=25,
        seed=1,
        baseline_rows=baseline,
    )
    html = (tmp_path / "exec.html").read_text()
    # Main Evidence gains the conditional baseline as a column + a lead sentence.
    assert "Conditional baseline (XGBoost)" in html
    assert "Versus the deployable bar" in html
    # The comparison is exposed for the index tile.
    comp = result["conditional_comparison"]
    assert comp is not None and comp["matched_rows"] == 2
    # Twin (p_actual 0.8/0.8) clearly beats baseline here.
    assert comp["share_twin_better"] == 1.0


def test_executive_summary_unchanged_without_baseline(tmp_path) -> None:
    twin = [_row("openai:gpt-5.5", "r1", "A", 0.20, 1), _row("openai:gpt-5.5", "r2", "B", 0.25, 1)]
    result = build_executive_summary(
        twin, survey="demo", path=tmp_path / "exec.html", markdown_path=None, simulations=25, seed=1
    )
    html = (tmp_path / "exec.html").read_text()
    assert "Conditional baseline (XGBoost)" not in html
    assert result["conditional_comparison"] is None
