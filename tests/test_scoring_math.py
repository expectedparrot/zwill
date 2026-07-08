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


def test_weighted_row_mean_and_bootstrap_use_weights() -> None:
    from zwill.twin_bootstrap import bootstrap_summary
    from zwill.twin_report import weighted_row_mean

    rows = [{"x": 1.0, "weight": 3.0}, {"x": 0.0, "weight": 1.0}]
    assert weighted_row_mean(rows, "x") == pytest.approx(0.75)  # (1*3 + 0*1)/4, not 0.5
    assert weighted_row_mean([{"x": 1.0}, {"x": 3.0}], "x") == pytest.approx(2.0)  # missing weight -> 1.0
    assert weighted_row_mean([{"y": 2.0}], "x", "y") == pytest.approx(2.0)  # fallback key

    # the bootstrap point estimate is the weighted (population) mean
    pred = [
        {"model_label": "m", "heldout_question": "q", "respondent_id": "r1", "probability_actual": 1.0, "weight": 3.0},
        {"model_label": "m", "heldout_question": "q", "respondent_id": "r2", "probability_actual": 0.0, "weight": 1.0},
    ]
    res = bootstrap_summary(pred, metrics=("probability_actual",), n_boot=50, seed=1)
    assert res["models"]["m"]["questions"]["q"]["probability_actual"]["mean"] == pytest.approx(0.75)


def test_top_k_overlap_is_na_when_not_enough_items() -> None:
    from zwill.rank import top_k_overlap

    # 3 items with k=3: every item is trivially in the top-3 -> vacuous -> None
    assert top_k_overlap({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 2, "c": 3}, ["a", "b", "c"], k=3) is None
    # informative case: 5 items, actual top-3 {a,b,c}, predicted top-3 {a,b,d} -> 2/3
    actual = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
    predicted = {"a": 1, "b": 2, "c": 4, "d": 3, "e": 5}
    assert top_k_overlap(actual, predicted, ["a", "b", "c", "d", "e"], k=3) == pytest.approx(2 / 3)


def test_spearman_bounded_even_with_tied_actual_ranks() -> None:
    from zwill.rank import spearman

    s = spearman({"a": 1, "b": 1, "c": 3}, {"a": 1, "b": 2, "c": 3}, ["a", "b", "c"])
    assert s is None or -1.0 - 1e-9 <= s <= 1.0 + 1e-9


# --------------------------------------------------------------------------
# build_twin_calibration — reliability bins + ECE
# --------------------------------------------------------------------------
def test_calibration_reliability_svg_renders() -> None:
    from zwill.twin_report_html import _calibration_reliability_svg

    cal = {"gpt-5.5": [{"bin": "0.8-0.9", "rows": 50, "mean_confidence": 0.85, "accuracy": 0.7}, {"bin": "0.9-1.0", "rows": 100, "mean_confidence": 0.95, "accuracy": 0.82}]}
    svg = _calibration_reliability_svg(cal)
    assert "<svg" in svg and "confidence vs accuracy" in svg and "diag" in svg
    # nothing to plot -> empty (no data, or bins with zero rows)
    assert _calibration_reliability_svg({}) == ""
    assert _calibration_reliability_svg({"m": [{"rows": 0, "mean_confidence": 0.5, "accuracy": 0.5}]}) == ""


def test_build_twin_calibration_bins_and_ece() -> None:
    from zwill.twin_report import build_twin_calibration

    rows = [
        {"probabilities": {"A": 1.0, "B": 0.0}, "top1_correct": 1},  # confidence 1.0
        {"probabilities": {"A": 0.85, "B": 0.15}, "top1_correct": 0},  # confidence 0.85
        {"probabilities": {"A": 0.85, "B": 0.15}, "top1_correct": 1},  # confidence 0.85
    ]
    calib, ece = build_twin_calibration(rows, bins=10)
    assert len(calib) == 10
    # confidence 1.0 must land in the top bin (0.9-1.0), not overflow the index
    assert calib[9]["rows"] == 1
    assert calib[9]["mean_confidence"] == pytest.approx(1.0)
    assert calib[8]["rows"] == 2
    assert calib[8]["accuracy"] == pytest.approx(0.5)
    # ECE = sum bin_weight * |accuracy - mean_confidence|
    assert ece == pytest.approx((2 / 3) * 0.35)
    assert 0.0 <= ece <= 1.0


def test_build_twin_calibration_tolerates_unscored_row() -> None:
    from zwill.twin_report import build_twin_calibration

    # A row without top1_correct must not crash calibration.
    build_twin_calibration([{"probabilities": {"A": 0.8, "B": 0.2}}], bins=10)


def test_individual_signal_permutation_detects_and_rejects() -> None:
    from zwill.executive_summary import individual_signal_permutation

    # Perfect per-respondent twin: 1.0 on each respondent's own answer. Uses 6
    # distinct answers so the permutation floor (1/n!) is well below 0.05 --
    # with too few respondents the identity permutation alone keeps p high.
    letters = ["A", "B", "C", "D", "E", "F"]
    perfect = [
        {"heldout_question": "q", "actual_answer": letter, "probabilities": {other: (1.0 if other == letter else 0.0) for other in letters}}
        for letter in letters
    ]
    res = individual_signal_permutation(perfect, simulations=200, seed=1)
    assert res["observed_mean_p_actual"] == pytest.approx(1.0)
    assert res["p_value_mean_p_actual"] < 0.05  # individual signal is detected
    assert 0 < res["p_value_mean_p_actual"] <= 1

    # Respondent-blind twin: identical distribution for everyone -> shuffling the
    # actual answers can't change the score, so there is no detectable signal.
    blind = [
        {"heldout_question": "q", "actual_answer": ans, "probabilities": {"A": 0.5, "B": 0.3, "C": 0.2}}
        for ans in ("A", "B", "C")
    ]
    res_blind = individual_signal_permutation(blind, simulations=200, seed=1)
    assert res_blind["p_value_mean_p_actual"] == pytest.approx(1.0)


def test_top_prediction_tiebreak_matches_scoring() -> None:
    from zwill.twin import one_hot_metrics
    from zwill.twin_report import top_probability_option, twin_top_prediction

    probs = {"A": 0.5, "B": 0.5, "C": 0.5}  # three-way tie
    # scoring puts the alphabetically-first tied option at rank 1
    assert one_hot_metrics(["A", "B", "C"], "A", probs)["top1_correct"] == 1
    assert one_hot_metrics(["A", "B", "C"], "B", probs)["top1_correct"] == 0
    # the display helpers must agree, or a row shows top choice B while scored correct with A
    assert twin_top_prediction({"probabilities": probs})[0] == "A"
    assert top_probability_option(probs)[0] == "A"
    # a non-tied case is unaffected
    assert twin_top_prediction({"probabilities": {"A": 0.2, "B": 0.8}})[0] == "B"


# --------------------------------------------------------------------------
# cramers_v_from_joint — powers the leakage audit gate
# --------------------------------------------------------------------------
def test_cramers_v_independence_and_perfect_association() -> None:
    from zwill.twin_diagnostics import cramers_v_from_joint

    independent = {("a", "x"): 0.25, ("a", "y"): 0.25, ("b", "x"): 0.25, ("b", "y"): 0.25}
    assert cramers_v_from_joint(independent) == pytest.approx(0.0, abs=1e-12)

    associated = {("a", "x"): 0.5, ("b", "y"): 0.5}
    assert cramers_v_from_joint(associated) == pytest.approx(1.0)

    # degenerate tables (a single row/column, or empty) are unscoreable
    assert cramers_v_from_joint({("a", "x"): 0.5, ("a", "y"): 0.5}) is None
    assert cramers_v_from_joint({}) is None


def test_cramers_v_stays_in_unit_interval() -> None:
    from zwill.twin_diagnostics import cramers_v_from_joint

    joint = {
        ("a", "x"): 0.30, ("a", "y"): 0.10,
        ("b", "x"): 0.05, ("b", "y"): 0.25,
        ("c", "x"): 0.20, ("c", "y"): 0.10,
    }
    v = cramers_v_from_joint(joint)
    assert v is not None and 0.0 <= v <= 1.0 + 1e-9


# --------------------------------------------------------------------------
# parse_probability_json / extract_probability_payload — messy provider output
# --------------------------------------------------------------------------
def test_parse_probability_json_handles_fences_prose_and_dicts() -> None:
    from zwill.probability import parse_probability_json

    assert parse_probability_json('{"probabilities":[0.5,0.5]}')[0]["probabilities"] == [0.5, 0.5]
    assert parse_probability_json('```json\n{"probabilities":[0.6,0.4]}\n```')[0]["probabilities"] == [0.6, 0.4]
    assert parse_probability_json("```\n{\"probabilities\":[0.1,0.9]}\n```")[0]["probabilities"] == [0.1, 0.9]
    # prose on both sides of the object
    assert parse_probability_json('Sure! {"probabilities":[0.7,0.3]} hope it helps')[0]["probabilities"] == [0.7, 0.3]
    # already-parsed dict passes through
    assert parse_probability_json({"probabilities": [1.0]})[0] == {"probabilities": [1.0]}
    assert parse_probability_json(None)[1] == "empty_answer"
    assert parse_probability_json("not json at all")[1] is not None


def test_extract_probability_payload_reads_and_validates() -> None:
    from zwill.probability import extract_probability_payload

    values, notes, _payload, error = extract_probability_payload(
        {"answer": {"response_probabilities": '{"probabilities":[0.2,0.8],"notes":"hi"}'}}
    )
    assert values == [0.2, 0.8]
    assert notes == "hi"
    assert error is None

    assert extract_probability_payload({"answer": {"response_probabilities": '{"notes":"x"}'}})[3] == "missing_probabilities"
    assert extract_probability_payload({"answer": {"response_probabilities": '{"probabilities":["a","b"]}'}})[3] == "invalid_probability_value"


# --------------------------------------------------------------------------
# stratified_by_actual — respondent sampling for --stratify-actual
# --------------------------------------------------------------------------
def _stratify_setup():
    ids = [f"r{i}" for i in range(20)]
    groups = {**{f"r{i}": "A" for i in range(10)}, **{f"r{i}": "B" for i in range(10, 16)}, **{f"r{i}": "C" for i in range(16, 20)}}
    answers = {rid: {"Q": groups[rid]} for rid in ids}
    return ids, answers, groups


def test_stratified_by_actual_returns_sample_size_and_proportional_strata() -> None:
    from zwill.twin_jobs import stratified_by_actual

    ids, answers, groups = _stratify_setup()
    selected = stratified_by_actual(ids, answers, "Q", 10, seed=1)
    assert len(selected) == 10
    assert len(set(selected)) == 10  # no duplicates
    from collections import Counter

    by_group = Counter(groups[rid] for rid in selected)
    # 20 -> 10 halves each stratum: A 10->5, B 6->3, C 4->2
    assert by_group == Counter({"A": 5, "B": 3, "C": 2})


def test_stratified_by_actual_caps_at_available() -> None:
    from zwill.twin_jobs import stratified_by_actual

    ids, answers, _ = _stratify_setup()
    selected = stratified_by_actual(ids, answers, "Q", 100, seed=1)
    assert len(selected) == len(set(selected)) == 20  # can't exceed the pool
