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


def test_weighted_quantiles_shift_with_weight() -> None:
    # equal weights -> median around the middle
    assert weighted_quantiles([10, 20, 30, 40], [1, 1, 1, 1], [0.5]) == [20]
    # heavy weight on the top pulls the weighted median up
    assert weighted_quantiles([10, 20, 30, 40], [1, 1, 1, 3], [0.5]) == [30]
    assert not math.isnan(weighted_quantiles([5.0], [1.0], [0.5])[0])
