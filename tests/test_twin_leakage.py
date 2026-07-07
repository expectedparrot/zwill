from __future__ import annotations

from zwill.twin_diagnostics import build_context_leakage_diagnostics, observed_pair_joint


def _questions(names):
    return [{"question_name": n, "question_text": f"text {n}", "question_options": ["a", "b"]} for n in names]


def test_perfectly_predictive_context_is_flagged() -> None:
    # context q2 exactly equals target q1 -> Cramer's V = 1 -> flagged.
    answers = {f"r{i}": {"q1": ("a" if i % 2 else "b"), "q2": ("a" if i % 2 else "b")} for i in range(60)}
    diag = build_context_leakage_diagnostics(_questions(["q1", "q2"]), answers, ["q1"], warn_threshold=0.7)
    top = diag["rows"][0]
    assert top["context_question"] == "q2"
    assert top["cramers_v"] > 0.99
    assert top["warning"] == "possible_leakage"
    assert diag["flagged_count"] == 1


def test_independent_context_is_not_flagged() -> None:
    # q2 alternates on a different cycle, roughly independent of q1.
    answers = {f"r{i}": {"q1": ("a" if i % 2 else "b"), "q2": ("a" if i % 3 == 0 else "b")} for i in range(120)}
    diag = build_context_leakage_diagnostics(_questions(["q1", "q2"]), answers, ["q1"], warn_threshold=0.7)
    assert diag["flagged_count"] == 0


def test_min_pair_rows_skips_sparse_pairs() -> None:
    answers = {f"r{i}": {"q1": "a", "q2": "a"} for i in range(5)}  # only 5 co-answered
    diag = build_context_leakage_diagnostics(_questions(["q1", "q2"]), answers, ["q1"], min_pair_rows=30)
    assert diag["pair_count"] == 0


def test_observed_pair_joint_counts_only_co_answered() -> None:
    answers = {
        "r1": {"q1": "a", "q2": "a"},
        "r2": {"q1": "b"},  # no q2 -> excluded
        "r3": {"q1": "a", "q2": "b"},
    }
    joint, n = observed_pair_joint(answers, "q1", "q2")
    assert n == 2
    assert abs(sum(joint.values()) - 1.0) < 1e-9


def test_bias_correction_defuses_high_cardinality_false_positive() -> None:
    # Context q2 has a near-unique value per respondent but is unrelated to the
    # binary target q1. Raw Cramer's V would be ~1 (cardinality artifact); the
    # bias-corrected V used by the audit must not flag it.
    answers = {f"r{i}": {"q1": ("a" if i % 2 else "b"), "q2": f"unique_{i}"} for i in range(200)}
    diag = build_context_leakage_diagnostics(_questions(["q1", "q2"]), answers, ["q1"], warn_threshold=0.7)
    pair = next(r for r in diag["rows"] if r["context_question"] == "q2")
    assert pair["context_distinct_answers"] == 200
    assert pair["cramers_v"] < 0.5  # deflated well below the flag threshold
    assert diag["flagged_count"] == 0
