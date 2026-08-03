"""Hand-computed tests for the continuous (quantile) twin scoring core."""

from __future__ import annotations

import math

import pytest

from zwill.numeric import (
    crps_from_quantiles,
    mean_pinball_loss,
    parse_quantile_prediction,
    pinball_loss,
    quantile_at,
    repair_quantile_values,
    score_numeric_prediction,
    weighted_quantiles,
)


def test_pinball_loss_is_asymmetric() -> None:
    # at the median the loss is symmetric (0.5 * |error|)
    assert pinball_loss(60, 0.5, 50) == pytest.approx(5.0)
    assert pinball_loss(40, 0.5, 50) == pytest.approx(5.0)
    # a high quantile penalizes over-prediction (actual below q) less
    assert pinball_loss(60, 0.9, 50) == pytest.approx(9.0)  # 0.9 * 10
    assert pinball_loss(40, 0.9, 50) == pytest.approx(1.0)  # 0.1 * 10
    # perfect prediction is zero loss
    assert pinball_loss(50, 0.5, 50) == pytest.approx(0.0)


def test_parse_quantile_prediction_forms() -> None:
    levels, values, err = parse_quantile_prediction({"quantiles": {"0.05": 20, "0.5": 55, "0.95": 90}})
    assert err is None and levels == [0.05, 0.5, 0.95] and values == [20, 55, 90]
    # p-prefixed and percentile forms normalize to fractions, and sort by level
    assert parse_quantile_prediction({"p95": 90, "p05": 20, "p50": 55})[0] == [0.05, 0.5, 0.95]
    assert parse_quantile_prediction({"5": 20, "50": 55, "95": 90})[0] == [0.05, 0.5, 0.95]
    # failures
    assert parse_quantile_prediction({})[2] == "missing_quantiles"
    assert parse_quantile_prediction({"0.5": 55})[2] == "too_few_quantiles"
    assert parse_quantile_prediction({"0.05": "x", "0.5": 5})[2] == "invalid_quantile_value"


def test_repair_quantiles_monotone_and_bounds() -> None:
    assert repair_quantile_values([20, 55, 40, 90], bounds=(0, 100)) == [20, 55, 55, 90]
    assert repair_quantile_values([-5, 50, 120], bounds=(0, 100)) == [0, 50, 100]


def test_quantile_at_interpolates() -> None:
    levels, values = [0.05, 0.5, 0.95], [20, 55, 90]
    assert quantile_at(levels, values, 0.5) == pytest.approx(55)
    assert quantile_at(levels, values, 0.01) == pytest.approx(20)  # clamp below
    assert quantile_at(levels, values, 0.99) == pytest.approx(90)  # clamp above
    # halfway between 0.05 and 0.5 in level -> proportional in value
    assert quantile_at(levels, values, 0.275) == pytest.approx(20 + 0.5 * 35)


def test_crps_is_twice_mean_pinball() -> None:
    levels, values = [0.05, 0.25, 0.5, 0.75, 0.95], [20, 40, 55, 70, 90]
    assert crps_from_quantiles(60, levels, values) == pytest.approx(2 * mean_pinball_loss(60, levels, values))


def test_score_numeric_prediction_median_and_coverage() -> None:
    levels, values = [0.05, 0.25, 0.5, 0.75, 0.95], [20, 40, 55, 70, 90]
    s = score_numeric_prediction(60, levels, values)
    assert s["median_prediction"] == pytest.approx(55)
    assert s["absolute_error"] == pytest.approx(5)
    assert s["signed_error"] == pytest.approx(-5)
    assert s["covered_50"] == 1.0  # 60 in [40,70]
    assert s["covered_90"] == 1.0  # 60 in [20,90]
    # an actual outside the intervals is not covered
    s2 = score_numeric_prediction(95, levels, values)
    assert s2["covered_50"] == 0.0 and s2["covered_90"] == 0.0


def test_extract_numeric_rows_scores_and_flags_malformed() -> None:
    import json as _json

    from zwill.numeric_commands import extract_numeric_prediction_rows, marginal_quantile_baseline_rows

    def scenario(rid, actual):
        return {
            "respondent_id": rid,
            "heldout_question_name": "spend",
            "actual_value": actual,
            "numeric_bounds": [0, 500],
            "quantile_levels": [0.05, 0.25, 0.5, 0.75, 0.95],
        }

    def result_row(rid, actual, quantiles):
        return {
            "scenario": scenario(rid, actual),
            "model": {"model": "gpt-5.5", "inference_service": "openai"},
            "answer": {"response_probabilities": _json.dumps({"quantiles": quantiles})},
        }

    results = {
        "edsl_class_name": "Results",
        "data": [
            result_row("r1", 180, {"0.05": 100, "0.25": 150, "0.5": 190, "0.75": 230, "0.95": 300}),
            result_row("r2", 55, {"0.05": 30, "0.25": 50, "0.5": 70, "0.75": 90, "0.95": 120}),
            {"scenario": scenario("r3", 40), "model": {"model": "gpt-5.5", "inference_service": "openai"}, "answer": {"response_probabilities": "not json"}},
        ],
    }
    rows, issues = extract_numeric_prediction_rows(results, job_id="j", survey="s", weight_by_respondent={"r1": 1.5, "r2": 0.7})
    assert len(rows) == 2 and len(issues) == 1 and issues[0]["error"]
    r1 = next(r for r in rows if r["respondent_id"] == "r1")
    assert r1["absolute_error"] == pytest.approx(10)  # |180 - median 190|
    assert r1["weight"] == 1.5 and r1["covered_50"] == 1.0

    baseline = marginal_quantile_baseline_rows(rows, [0.05, 0.25, 0.5, 0.75, 0.95])
    assert len(baseline) == 2 and all(b["model_label"] == "baseline:marginal-quantile" for b in baseline)
    # the marginal baseline predicts the same quantiles for every respondent
    assert baseline[0]["quantile_values"] == baseline[1]["quantile_values"]


def test_reliability_curve_and_html_render() -> None:
    from zwill.numeric_report import numeric_report_payload, reliability_curve, render_numeric_report_html

    levels = [0.05, 0.25, 0.5, 0.75, 0.95]
    rows = [
        {"model_label": "m", "weight": 1.0, "actual_value": 3, "quantile_values": [1, 2, 4, 6, 8], "heldout_question": "q"},
        {"model_label": "m", "weight": 1.0, "actual_value": 7, "quantile_values": [1, 2, 4, 6, 8], "heldout_question": "q"},
    ]
    curve = reliability_curve(rows, levels)
    # at level 0.5 the predicted quantile is 4: actual 3 <= 4 (yes), 7 <= 4 (no) -> 0.5
    assert curve[2] == pytest.approx(0.5)
    # a heavier weight on the covered respondent shifts the empirical coverage
    rows[0]["weight"] = 3.0
    assert reliability_curve(rows, levels)[2] == pytest.approx(0.75)

    summary = {
        "models": {"m": {"rows": 2, "mean_pinball": 0.5, "mean_crps": 1.0, "mean_absolute_error": 1.0, "coverage_50": 0.5, "coverage_90": 1.0}},
        "pinball_skill_vs_marginal": {},
        "quantile_levels": levels,
    }
    html = render_numeric_report_html(numeric_report_payload(rows, summary))
    assert "Reliability (calibration) diagram" in html and "<svg" in html and "Numeric twin validation" in html


def test_weighted_quantiles_shift_with_weight() -> None:
    # equal weights -> median around the middle
    assert weighted_quantiles([10, 20, 30, 40], [1, 1, 1, 1], [0.5]) == [20]
    # heavy weight on the top pulls the weighted median up
    assert weighted_quantiles([10, 20, 30, 40], [1, 1, 1, 3], [0.5]) == [30]
    assert not math.isnan(weighted_quantiles([5.0], [1.0], [0.5])[0])
