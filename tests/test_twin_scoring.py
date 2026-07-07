from __future__ import annotations

import math

from zwill.twin_scoring import (
    probability_granularity_summary,
    skill_score_section_html,
    skill_score_summary,
)


def _row(model, question, rid, *, nll, uniform_nll, marginal_nll, brier=0.0, uniform_brier=1.0, marginal_brier=0.5, top1=0):
    return {
        "model_label": model,
        "heldout_question": question,
        "respondent_id": rid,
        "negative_log_likelihood": nll,
        "uniform_negative_log_likelihood": uniform_nll,
        "empirical_marginal_negative_log_likelihood": marginal_nll,
        "brier": brier,
        "uniform_brier": uniform_brier,
        "empirical_marginal_brier": marginal_brier,
        "top1_correct": top1,
    }


def test_skill_score_is_one_minus_loss_ratio() -> None:
    # model NLL mean 0.5, uniform mean 1.0 -> skill 0.5; marginal mean 2.0 -> skill 0.75
    rows = [
        _row("m", "q1", "r1", nll=0.4, uniform_nll=1.0, marginal_nll=2.0),
        _row("m", "q1", "r2", nll=0.6, uniform_nll=1.0, marginal_nll=2.0),
    ]
    macro = skill_score_summary(rows)["models"]["m"]["macro"]
    assert math.isclose(macro["nll_skill_vs_uniform"], 0.5, abs_tol=1e-9)
    assert math.isclose(macro["nll_skill_vs_marginal"], 0.75, abs_tol=1e-9)


def test_negative_skill_when_worse_than_baseline() -> None:
    # model worse than uniform on NLL -> negative skill (the overconfidence signature)
    rows = [_row("m", "q1", "r1", nll=2.0, uniform_nll=1.0, marginal_nll=1.5)]
    macro = skill_score_summary(rows)["models"]["m"]["macro"]
    assert macro["nll_skill_vs_uniform"] < 0.0


def test_median_nll_is_robust_to_a_confident_miss() -> None:
    rows = [
        _row("m", "q1", "r1", nll=0.5, uniform_nll=1.0, marginal_nll=1.0),
        _row("m", "q1", "r2", nll=0.5, uniform_nll=1.0, marginal_nll=1.0),
        _row("m", "q1", "r3", nll=27.0, uniform_nll=1.0, marginal_nll=1.0),  # confident miss
    ]
    macro = skill_score_summary(rows)["models"]["m"]["macro"]
    assert math.isclose(macro["median_nll"], 0.5, abs_tol=1e-9)
    assert macro["mean_nll"] > 9.0  # the mean is wrecked by the single miss


def test_macro_averages_skill_across_questions() -> None:
    rows = [
        _row("m", "q1", "r1", nll=0.5, uniform_nll=1.0, marginal_nll=1.0),  # skill vs uniform 0.5
        _row("m", "q2", "r1", nll=0.75, uniform_nll=1.0, marginal_nll=1.0),  # skill vs uniform 0.25
    ]
    macro = skill_score_summary(rows)["models"]["m"]["macro"]
    assert math.isclose(macro["nll_skill_vs_uniform"], 0.375, abs_tol=1e-9)
    assert macro["questions"] == 2


def test_missing_baseline_fields_yield_none_not_crash() -> None:
    rows = [{"model_label": "m", "heldout_question": "q1", "respondent_id": "r1", "negative_log_likelihood": 0.5, "top1_correct": 1}]
    macro = skill_score_summary(rows)["models"]["m"]["macro"]
    assert macro["nll_skill_vs_uniform"] is None
    assert macro["mean_nll"] == 0.5


def test_section_renders_only_with_models() -> None:
    assert skill_score_section_html([]) == ""
    rows = [_row("m", "q1", "r1", nll=0.5, uniform_nll=1.0, marginal_nll=1.0)]
    html = skill_score_section_html(rows)
    assert "Skill scores" in html and "sanity" in html


def test_granularity_flags_coarse_round_number_model() -> None:
    coarse = [{"model_label": "llm", "probabilities": {"a": 0.7, "b": 0.3}} for _ in range(20)]
    fine = [{"model_label": "base", "probabilities": {"a": 0.673, "b": 0.327}} for _ in range(20)]
    summary = probability_granularity_summary(coarse + fine)
    assert summary["models"]["llm"]["round_fraction"] == 1.0
    assert summary["models"]["llm"]["warning"] == "coarse_probabilities"
    assert summary["models"]["base"]["round_fraction"] < 0.5
    assert summary["models"]["base"]["warning"] == ""


def test_granularity_counts_distinct_values() -> None:
    rows = [
        {"model_label": "m", "probabilities": {"a": 0.5, "b": 0.5}},
        {"model_label": "m", "probabilities": {"a": 0.8, "b": 0.2}},
    ]
    assert probability_granularity_summary(rows)["models"]["m"]["distinct_values"] == 3
