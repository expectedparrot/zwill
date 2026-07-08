"""Bug-hunting tests for the numeric scoring core.

These assert hand-computed values and mathematical invariants for the twin
scoring / calibration functions. A failure here is a real correctness bug in the
science, not a wiring issue.
"""

from __future__ import annotations

import math

import pytest

from zwill.probability import normalized_probabilities, probability_metrics
from zwill.twin import calibrate_probabilities_to_marginal, one_hot_metrics
from zwill.twin_results import distribution_distance_metrics


# --------------------------------------------------------------------------
# one_hot_metrics — per-respondent scoring against the actual answer
# --------------------------------------------------------------------------
def test_one_hot_metrics_hand_computed() -> None:
    m = one_hot_metrics(["A", "B"], "A", {"A": 0.7, "B": 0.3})
    assert m["probability_actual"] == pytest.approx(0.7)
    assert m["negative_log_likelihood"] == pytest.approx(-math.log(0.7))
    assert m["brier"] == pytest.approx(0.09 + 0.09)  # (0.7-1)^2 + (0.3-0)^2
    assert m["uniform_probability_actual"] == pytest.approx(0.5)
    assert m["uniform_negative_log_likelihood"] == pytest.approx(math.log(2))
    assert m["uniform_brier"] == pytest.approx(0.25 + 0.25)
    assert m["brier_improvement"] == pytest.approx(0.5 - 0.18)
    assert m["top1_correct"] == 1
    assert m["actual_rank"] == 1


def test_one_hot_metrics_actual_not_in_options() -> None:
    m = one_hot_metrics(["A", "B"], "C", {"A": 0.6, "B": 0.4})
    assert m["probability_actual"] == 0.0
    assert m["actual_rank"] is None
    assert m["top1_correct"] == 0
    # brier = (0.6-0)^2 + (0.4-0)^2 since the one-hot actual is all zeros
    assert m["brier"] == pytest.approx(0.36 + 0.16)


def test_one_hot_metrics_empty_options_should_not_crash() -> None:
    # A question with no options must not raise ZeroDivisionError deep in scoring.
    m = one_hot_metrics([], "A", {})
    assert m["probability_actual"] == 0.0


# --------------------------------------------------------------------------
# calibrate_probabilities_to_marginal — IPF/raking to a target marginal
# --------------------------------------------------------------------------
def _rows(options, dists):
    return [{"option_labels": options, "probabilities": dict(zip(options, d))} for d in dists]


def test_calibrate_hits_target_column_means_and_row_sums() -> None:
    options = ["A", "B", "C"]
    rows = _rows(
        options,
        [[0.7, 0.2, 0.1], [0.1, 0.8, 0.1], [0.3, 0.3, 0.4], [0.2, 0.2, 0.6]],
    )
    target = {"A": 0.4, "B": 0.35, "C": 0.25}
    calibrated, info = calibrate_probabilities_to_marginal(rows, target)

    for row in calibrated:  # each row stays a distribution
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-9)

    n = len(calibrated)
    for opt in options:  # column means match the requested marginal
        col_mean = sum(row[opt] for row in calibrated) / n
        assert col_mean == pytest.approx(target[opt], abs=1e-6), (opt, col_mean, info)
    assert info["converged"] is True


def test_calibrate_single_option_is_trivially_converged() -> None:
    calibrated, info = calibrate_probabilities_to_marginal(_rows(["A"], [[1.0], [1.0]]), {"A": 1.0})
    assert info["converged"] is True
    assert all(row["A"] == pytest.approx(1.0) for row in calibrated)


def test_calibrate_already_on_target_is_stable() -> None:
    options = ["A", "B"]
    rows = _rows(options, [[0.4, 0.6], [0.4, 0.6]])
    calibrated, info = calibrate_probabilities_to_marginal(rows, {"A": 0.4, "B": 0.6})
    assert info["converged"] is True
    for row in calibrated:
        assert row["A"] == pytest.approx(0.4, abs=1e-9)


def test_calibrate_infeasible_target_terminates_not_converged() -> None:
    # No row has any mass on C, so a target demanding C can never be reached.
    options = ["A", "B", "C"]
    rows = _rows(options, [[0.5, 0.5, 0.0], [0.7, 0.3, 0.0]])
    calibrated, info = calibrate_probabilities_to_marginal(rows, {"A": 0.25, "B": 0.25, "C": 0.5})
    assert info["converged"] is False
    for row in calibrated:  # still valid distributions
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------------------
# distribution_distance_metrics & normalized_probabilities
# --------------------------------------------------------------------------
def test_distribution_distance_identical_is_zero() -> None:
    d = distribution_distance_metrics({"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5})
    assert d["l1"] == pytest.approx(0.0)
    assert d["brier"] == pytest.approx(0.0)
    assert d["kl_target_to_predicted"] == pytest.approx(0.0)
    assert d["js_divergence"] == pytest.approx(0.0)


def test_distribution_distance_normalizes_unnormalized_inputs() -> None:
    # counts, not probabilities, must be normalized before comparison
    d = distribution_distance_metrics({"A": 2, "B": 2}, {"A": 1, "B": 1})
    assert d["l1"] == pytest.approx(0.0)


def test_normalized_probabilities_rejects_and_normalizes() -> None:
    assert normalized_probabilities([1.0, 3.0], 2)[0] == pytest.approx([0.25, 0.75])
    assert normalized_probabilities([1.0], 2) == (None, 1.0, "wrong_probability_count")
    assert normalized_probabilities([-1.0, 2.0], 2)[2] == "invalid_probability_range"
    assert normalized_probabilities([0.0, 0.0], 2)[2] == "zero_probability_sum"


def test_probability_metrics_hand_computed() -> None:
    m = probability_metrics([1.0, 0.0], [0.25, 0.75])
    assert m["mae"] == pytest.approx((0.75 + 0.75) / 2)
    assert m["brier"] == pytest.approx(0.75**2 + 0.75**2)


# --------------------------------------------------------------------------
# rank scoring — ranks_from_scores / spearman / pairwise / rank_metrics
# --------------------------------------------------------------------------
def test_ranks_from_scores_orders_desc_with_stable_tiebreak() -> None:
    from zwill.rank import ranks_from_scores

    assert ranks_from_scores({"a": 3.0, "b": 2.0, "c": 1.0}, ["a", "b", "c"]) == {"a": 1, "b": 2, "c": 3}
    # equal scores break by item id
    assert ranks_from_scores({"a": 1.0, "b": 1.0}, ["b", "a"]) == {"a": 1, "b": 2}


def test_spearman_perfect_and_reversed() -> None:
    from zwill.rank import spearman

    items = ["a", "b", "c"]
    assert spearman({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 2, "c": 3}, items) == pytest.approx(1.0)
    assert spearman({"a": 1, "b": 2, "c": 3}, {"a": 3, "b": 2, "c": 1}, items) == pytest.approx(-1.0)


def test_rank_metrics_swap_case_hand_computed() -> None:
    from zwill.rank import rank_metrics

    actual = {"a": 1, "b": 2, "c": 3, "d": 4}
    # scores yield predicted ranks a:1, b:2, d:3, c:4 (last two swapped vs actual)
    scores = {"a": 4.0, "b": 3.0, "c": 1.0, "d": 2.0}
    items = ["a", "b", "c", "d"]
    m = rank_metrics(actual, scores, items)
    assert m["predicted_ranks"] == {"a": 1, "b": 2, "c": 4, "d": 3}
    assert m["spearman"] == pytest.approx(0.8)  # 1 - 6*2/(4*15)
    assert m["pairwise_order_accuracy"] == pytest.approx(5 / 6)
    assert m["top_1_hit"] == 1
    assert m["top_3_overlap"] == pytest.approx(2 / 3)
    assert m["mean_absolute_rank_error"] == pytest.approx(0.5)


def test_spearman_bounded_even_with_tied_actual_ranks() -> None:
    from zwill.rank import spearman

    s = spearman({"a": 1, "b": 1, "c": 3}, {"a": 1, "b": 2, "c": 3}, ["a", "b", "c"])
    assert s is None or -1.0 - 1e-9 <= s <= 1.0 + 1e-9
