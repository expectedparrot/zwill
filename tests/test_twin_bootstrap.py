from __future__ import annotations

from zwill.twin_bootstrap import bootstrap_summary


def _rows(model_label: str, question: str, values: list[float], *, metric: str = "probability_actual"):
    rows = []
    for i, value in enumerate(values):
        row = {
            "model_label": model_label,
            "heldout_question": question,
            "respondent_id": f"r{i}",
            "probability_actual": 0.0,
            "negative_log_likelihood": 0.0,
            "brier": 0.0,
            "top1_correct": 0,
        }
        row[metric] = value
        rows.append(row)
    return rows


def test_ci_brackets_the_point_estimate_and_narrows_with_n() -> None:
    small = _rows("m", "q1", [0.2, 0.8] * 10)  # n=20
    large = _rows("m", "q1", [0.2, 0.8] * 500)  # n=1000, same mean
    res_small = bootstrap_summary(small, n_boot=400, seed=1)
    res_large = bootstrap_summary(large, n_boot=400, seed=1)
    s = res_small["models"]["m"]["macro"]["probability_actual"]
    lrg = res_large["models"]["m"]["macro"]["probability_actual"]
    assert abs(s["mean"] - 0.5) < 1e-9 and abs(lrg["mean"] - 0.5) < 1e-9
    assert s["lo"] <= s["mean"] <= s["hi"]
    # More respondents -> tighter interval.
    assert (lrg["hi"] - lrg["lo"]) < (s["hi"] - s["lo"])


def test_paired_delta_ci_excludes_zero_for_a_real_effect() -> None:
    # model A scores 0.7 on every respondent, baseline scores 0.3 on the same ones.
    rows = _rows("modelA", "q1", [0.7] * 200) + _rows("baseline", "q1", [0.3] * 200)
    res = bootstrap_summary(rows, baseline_model="baseline", n_boot=500, seed=2)
    delta = res["deltas_vs_baseline"]["models"]["modelA"]["macro"]["probability_actual"]
    assert abs(delta["delta"] - 0.4) < 1e-9
    assert delta["lo"] > 0.0  # the +0.4 gap is unambiguously above zero


def test_paired_delta_uses_only_shared_respondents() -> None:
    model_rows = _rows("modelA", "q1", [0.6] * 50)
    baseline_rows = _rows("baseline", "q1", [0.4] * 30)  # only r0..r29 overlap
    res = bootstrap_summary(model_rows + baseline_rows, baseline_model="baseline", n_boot=200, seed=3)
    block = res["deltas_vs_baseline"]["models"]["modelA"]["questions"]["q1"]["probability_actual"]
    assert block["n_shared"] == 30
    assert abs(block["delta"] - 0.2) < 1e-9


def test_results_are_reproducible_with_fixed_seed() -> None:
    rows = _rows("m", "q1", [0.1, 0.5, 0.9] * 20)
    a = bootstrap_summary(rows, n_boot=300, seed=7)["models"]["m"]["macro"]["probability_actual"]
    b = bootstrap_summary(rows, n_boot=300, seed=7)["models"]["m"]["macro"]["probability_actual"]
    assert a == b


def test_macro_averages_across_questions() -> None:
    rows = _rows("m", "q1", [0.4] * 40) + _rows("m", "q2", [0.6] * 40)
    macro = bootstrap_summary(rows, n_boot=200, seed=0)["models"]["m"]["macro"]["probability_actual"]
    assert abs(macro["mean"] - 0.5) < 0.02
    assert macro["questions"] == 2
