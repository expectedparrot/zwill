from __future__ import annotations

import json
from html import unescape

from zwill.executive_summary import remove_leading_executive_summary_heading
from zwill.diagnostic_svg import render_bootstrap_forest_svg, render_marginal_diagnostics_svg
from zwill.reporting import (
    PRACTITIONER_DECISION_GUIDANCE_MARKDOWN,
    PRACTITIONER_EXPLAINER_MARKDOWN,
    PRACTITIONER_HOLDOUT_MARKDOWN,
    build_probability_report,
    markdown_to_html,
    remove_redundant_report_title,
    remove_reusable_practitioner_guidance,
    render_probability_report_html,
    render_probability_report_svg,
    render_twin_job_comparison_report_html,
    render_twin_practitioner_report_html,
    render_twin_report_html,
    render_twin_run_report_html,
)


def test_build_probability_report_compares_model_to_truth_and_uniform() -> None:
    truth = {
        "survey": "demo",
        "marginals": {
            "q1": {
                "yes": {"weighted_count": 3},
                "no": {"weighted_count": 1},
            }
        },
    }
    predictions = [
        {
            "job_id": "job1",
            "question": "q1",
            "question_text": "Do it?",
            "service": "openai",
            "model": "gpt-5.5",
            "option_labels": ["yes", "no"],
            "probabilities": {"yes": 0.7, "no": 0.3},
        }
    ]

    report = build_probability_report(predictions, truth)
    row = report["rows"][0]

    assert row["actual"] == {"yes": 0.75, "no": 0.25}
    assert row["uniform"] == {"yes": 0.5, "no": 0.5}
    assert row["brier"] < row["uniform_brier"]
    assert row["kl_divergence"] < row["uniform_kl_divergence"]
    assert report["summary"]["gpt-5.5"]["rows"] == 1


def test_probability_report_svg_compares_observed_and_model_marginals() -> None:
    rows = [
        {
            "question": "q1",
            "question_text": "How often?",
            "service": "openai",
            "model": "gpt-5.5",
            "actual": {"Never": 0.25, "Often": 0.75},
            "predicted": {"Never": 0.4, "Often": 0.6},
        }
    ]

    svg = render_probability_report_svg("demo", rows)

    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "Observed survey marginal vs. one-shot prediction" in svg
    assert "openai:gpt-5.5" in svg
    assert "How often?" in svg
    assert "75.0%" in svg


def test_generated_executive_summary_heading_is_not_duplicated() -> None:
    markdown = "## Executive Summary\n\nThe permutation test is null.\n\n## Recommendation\n\nUse for ranking."

    cleaned = remove_leading_executive_summary_heading(markdown)

    assert cleaned.startswith("The permutation test is null.")
    assert "## Recommendation" in cleaned
    assert "## Executive Summary" not in cleaned


def test_twin_job_comparison_report_uses_shared_branding_and_copy_control() -> None:
    html = render_twin_job_comparison_report_html(
        {
            "survey": "pew_w130_july2023",
            "rows": [],
            "summary": {},
            "diagnostics": {},
            "job_ids": [],
        }
    )

    assert "E[🦜] Expected Parrot" in html
    assert "Copy as Markdown" in html
    assert "data-copy-markdown" in html
    assert "execCommand(\"copy\")" in html
    assert "Pew W130 July2023 Twin Job Comparison" in html
    assert "Survey id:" in html


def test_twin_run_report_uses_shared_branding_and_copy_control() -> None:
    html = render_twin_run_report_html(
        {
            "survey": "pew_w130_july2023",
            "job_id": "job-1",
            "construction": {"heldout_questions": ["q1"], "context_question_count": None},
            "import": {},
            "run": {},
            "questions": [
                {
                    "question": "q1",
                    "question_text": "Pick one",
                    "prediction_rows": 30,
                    "respondents": 30,
                    "option_count": 3,
                    "observed_answer_summary": "30 non-missing; yes: 18 (60%), no: 12 (40%)",
                    "models": ["openai:gpt-5.5"],
                }
            ],
            "models": [],
            "prompt_examples": [],
        }
    )

    assert "E[🦜] Expected Parrot" in html
    assert "Copy as Markdown" in html
    assert "data-copy-markdown" in html
    assert "execCommand(\"copy\")" in html
    assert "Pew W130 July2023 Twin Run Report" in html
    assert "Survey id:" in html
    assert "All available non-held-out questions" in html
    assert "Observed target answers" in html
    assert "30 non-missing; yes: 18 (60%), no: 12 (40%)" in html
    assert "Mean observed answers" not in html


def test_markdown_to_html_renders_common_report_markdown() -> None:
    html = markdown_to_html(
        "# Title\n\n"
        "Use **openai:gpt-5.5** for `w158_ccpolicy`.\n\n"
        "- **Low stakes:** move quickly.\n"
        "- Check [results](https://www.expectedparrot.com/content/abc).\n\n"
        "| model | result |\n"
        "|---|---:|\n"
        "| `openai:gpt-5.5` | **84%** |\n\n"
        "---\n"
    )

    assert "<h1>Title</h1>" in html
    assert "<strong>openai:gpt-5.5</strong>" in html
    assert "<code>w158_ccpolicy</code>" in html
    assert "<li><strong>Low stakes:</strong> move quickly.</li>" in html
    assert '<a href="https://www.expectedparrot.com/content/abc">results</a>' in html
    assert "<td><code>openai:gpt-5.5</code></td>" in html
    assert "<td><strong>84%</strong></td>" in html
    assert "<hr>" in html


def test_practitioner_report_includes_canned_explainer_and_copied_markdown() -> None:
    payload = {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "rows": [
            {
                "survey": "demo",
                "heldout_questions": "q1",
                "model": "openai:gpt-5.5",
                "rows": 2,
                "option_count": 2,
                "accuracy": 1.0,
                "ece": 0.0,
                "nll_vs_empirical": 0.1,
            }
        ],
        "summary": {"openai:gpt-5.5": {"mean_ece": 0.0, "mean_nll": 0.1}},
    }
    markdown = (
        "# Model Report\n\n"
        "## 1. Executive summary\n\n"
        "Generic stakes ladder that should be supplied by the wrapper.\n\n"
        "## 2. Study setup\n\n"
        "Use **twins** carefully for this specific benchmark."
    )
    html = render_twin_practitioner_report_html(payload, markdown, {"mode": "test"})

    assert "What This Report Means by Digital Twins" in html
    assert "Expected Parrot is the company" in html
    assert "https://arxiv.org/abs/2209.06899" in html
    assert "Expected Parrot EDSL documentation" in html
    assert "How to Use This Report" in html
    assert "Expected Parrot" in html
    assert "Survey Digital Twin Report" in html
    assert "E[🦜]" in html
    assert "<h1>Cross-Survey Digital Twin Evaluation</h1>" in html
    assert "<h1>cross_survey_twin_benchmark_seed789 Practitioner Report</h1>" not in html
    assert "Benchmark ID:" in html
    assert "Why This Report Uses Held-Out Questions" in html
    assert "highly correlated questions" in html
    assert "Match Evidence to the Intended Use" in html
    assert "Exact levels or public quantitative claims" in html
    assert "Read Performance by Exercise" in html
    assert "not the same test" in html
    assert "survey research is infeasible" in html
    assert "rank ordering from exact levels" in html
    assert "surface considerations" in html
    assert "Copy Markdown" in html
    markdown_payload = html.split('id="markdown-report">', 1)[1].split("</script>", 1)[0]
    assert PRACTITIONER_EXPLAINER_MARKDOWN.splitlines()[0] in markdown_payload
    assert PRACTITIONER_HOLDOUT_MARKDOWN.splitlines()[0] in markdown_payload
    assert PRACTITIONER_DECISION_GUIDANCE_MARKDOWN.splitlines()[0] in markdown_payload
    assert "# Model Report" in markdown_payload
    assert "Generic stakes ladder" not in html
    assert "Generated from recorded zwill" not in html


def test_remove_reusable_practitioner_guidance_strips_old_exec_section() -> None:
    markdown = (
        "# Report\n\n"
        "## 1. Executive summary\n\n"
        "Generic stakes ladder.\n\n"
        "## 2. Study setup\n\n"
        "Specific study details.\n"
    )

    stripped = remove_reusable_practitioner_guidance(markdown)

    assert "Generic stakes ladder" not in stripped
    assert "## 2. Study setup" in stripped
    assert "Specific study details" in stripped


def test_remove_reusable_practitioner_guidance_keeps_specific_exec_section() -> None:
    markdown = (
        "# Report\n\n"
        "## 1. Executive summary\n\n"
        "Use these twins most confidently for binary climate-policy questions because accuracy was 83.8%.\n\n"
        "## 2. Study setup\n\n"
        "Specific study details.\n"
    )

    stripped = remove_reusable_practitioner_guidance(markdown)

    assert "83.8%" in stripped
    assert "## 1. Executive summary" in stripped
    assert "Specific study details" in stripped


def test_remove_redundant_report_title_strips_model_title() -> None:
    markdown = "# Practitioner report: cross-survey benchmark\n\n## 1. Executive summary\n\nSpecific results."

    stripped = remove_redundant_report_title(markdown)

    assert "Practitioner report" not in stripped
    assert stripped.startswith("## 1. Executive summary")


def test_html_report_contains_embedded_data_and_baseline_arrows() -> None:
    rows = [
        {
            "job_id": "job1",
            "question": "q1",
            "question_text": "Do it?",
            "service": "openai",
            "model": "gpt-5.5",
            "actual": {"yes": 0.75, "no": 0.25},
            "predicted": {"yes": 0.7, "no": 0.3},
            "uniform": {"yes": 0.5, "no": 0.5},
            "mae": 0.05,
            "brier": 0.005,
            "kl_divergence": 0.007,
            "uniform_mae": 0.25,
            "uniform_brier": 0.125,
            "uniform_kl_divergence": 0.13,
            "brier_improvement": 0.12,
            "kl_improvement": 0.123,
            "brier_percent_improvement": 96,
            "kl_percent_improvement": 94,
        }
    ]
    summary = {
        "gpt-5.5": {
            "rows": 1,
            "mean_mae": 0.05,
            "mean_brier": 0.005,
            "mean_kl_divergence": 0.007,
            "mean_uniform_brier": 0.125,
            "mean_uniform_kl_divergence": 0.13,
            "mean_brier_improvement": 0.12,
            "mean_kl_improvement": 0.123,
            "mean_brier_percent_improvement": 96,
            "mean_kl_percent_improvement": 94,
        }
    }

    html = render_probability_report_html("demo", rows, summary)

    assert "<h2>How to read this evidence</h2>" in html
    assert "committed, weighted survey marginals" in html
    assert "Best Fits" not in html
    assert "Weakest Fits" not in html
    assert "KL divergence (lower is better)" in html
    assert "perf-row-baseline" in html
    assert "perf-arrow" in html
    assert "green arrows beat uniform and red arrows are worse" in html
    marker = '<script type="application/json" id="report-data">'
    assert marker in html
    encoded = html.split(marker, 1)[1].split("</script>", 1)[0]
    assert json.loads(unescape(encoded))["rows"][0]["question"] == "q1"


def test_twin_html_report_contains_embedded_data() -> None:
    rows = [
        {
            "job_id": "twin1",
            "respondent_id": "r1",
            "heldout_question": "q1",
            "heldout_question_text": "Do it?",
            "actual_answer": "yes",
            "model": "gpt-5.5",
            "service": "openai",
            "model_label": "openai:gpt-5.5",
            "probability_actual": 0.8,
            "uniform_probability_actual": 0.5,
            "negative_log_likelihood": 0.223,
            "uniform_negative_log_likelihood": 0.693,
            "empirical_marginal_probability_actual": 0.75,
            "empirical_marginal_negative_log_likelihood": 0.288,
            "empirical_marginal_brier": 0.125,
            "empirical_marginal_top1_correct": 1,
            "marginal_probability_actual": 0.75,
            "marginal_negative_log_likelihood": 0.288,
            "marginal_brier": 0.125,
            "marginal_top1_correct": 1,
            "brier": 0.08,
            "uniform_brier": 0.5,
            "brier_improvement": 0.42,
            "top1_correct": 1,
            "actual_rank": 1,
            "option_labels": ["yes", "no"],
        }
    ]
    summary = {
        "openai:gpt-5.5": {
            "rows": 1,
            "mean_probability_actual": 0.8,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.223,
            "mean_uniform_negative_log_likelihood": 0.693,
            "mean_empirical_marginal_probability_actual": 0.75,
            "mean_empirical_marginal_negative_log_likelihood": 0.288,
            "mean_empirical_marginal_brier": 0.125,
            "empirical_marginal_top1_accuracy": 1.0,
            "mean_marginal_probability_actual": 0.75,
            "mean_marginal_negative_log_likelihood": 0.288,
            "mean_marginal_brier": 0.125,
            "marginal_top1_accuracy": 1.0,
            "mean_brier": 0.08,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.42,
            "top1_accuracy": 1.0,
            "expected_calibration_error": 0.2,
            "negative_log_likelihood_p95": 0.223,
            "mean_top_confidence": 0.8,
        }
    }

    diagnostics = {
        "baseline_comparison": {
            "openai:gpt-5.5": {
                "p_actual_vs_empirical": 0.05,
                "nll_vs_empirical": 0.065,
                "brier_vs_empirical": 0.045,
            }
        },
        "model_wins": [
            {"heldout_question": "q1", "model": "openai:gpt-5.5", "nll_vs_empirical": 0.065}
        ],
        "empirical_wins": [],
        "calibration": {
            "openai:gpt-5.5": [
                {"bin": "0.8-0.9", "rows": 1, "mean_confidence": 0.8, "accuracy": 1.0}
            ]
        },
        "worst_misses": rows,
        "overconfident_misses": rows,
        "confusion": {"q1::openai:gpt-5.5": {"yes": {"yes": 1}}},
    }
    health = {"job_id": "twin1", "import": {"row_count": 1, "extracted_count": 1, "issue_count": 0}}

    html = render_twin_report_html("demo", rows, summary, diagnostics, health)

    assert "Digital Twin Report" in html
    assert "Study Summary" in html
    assert "Held-out question" in html
    assert "Do it?" in html
    assert "Random-choice accuracy" in html
    assert "Random NLL" in html
    assert "Brier delta" in html
    assert "Performance by Held-out Question" in html
    assert "Empirical marginal baseline" in html
    assert "openai:gpt-5.5" in html
    assert "Wrong only" in html
    assert "Lowest p(actual)" in html
    assert "Metric Definitions" in html
    assert "Copy as Markdown" in html
    assert "data-copy-markdown" in html
    assert "Run Health" in html
    assert "Diagnostics" in html
    assert "Largest Misses" in html
    assert "Overconfident Misses" in html
    assert "Option Confusion" in html
    assert "Mean confidence" in html
    assert "ECE" in html
    assert "NLL vs empirical" in html
    assert "Negative log likelihood" in html
    assert "Error rate" in html
    assert "0.000" in html
    assert "Predicted option probabilities" in html
    assert "Raw model response" in html
    assert "actual" in html
    marker = '<script type="application/json" id="twin-report-data">'
    assert marker in html
    encoded = html.split(marker, 1)[1].split("</script>", 1)[0]
    embedded = json.loads(unescape(encoded))
    assert embedded["row_count"] == 1
    assert embedded["raw_prediction_rows_included"] is False
    assert "rows" not in embedded


def test_twin_report_performance_row_survives_missing_empirical_marginal() -> None:
    # Regression: when the empirical-marginal baseline is absent (e.g. twin-validate
    # --skip-baseline), the performance row must still render fully with the twin's
    # own metrics and the random-baseline comparison, not collapse to an empty cell.
    rows = [
        {
            "job_id": "twin1",
            "respondent_id": "r1",
            "heldout_question": "q1",
            "heldout_question_text": "Do it?",
            "actual_answer": "yes",
            "model": "gpt-5.5",
            "service": "openai",
            "model_label": "openai:gpt-5.5",
            "probabilities": {"yes": 0.8, "no": 0.2},
            "probability_actual": 0.8,
            "uniform_probability_actual": 0.5,
            "negative_log_likelihood": 0.223,
            "uniform_negative_log_likelihood": 0.693,
            "brier": 0.08,
            "uniform_brier": 0.5,
            "top1_correct": 1,
            "actual_rank": 1,
            "option_labels": ["yes", "no"],
        }
    ]
    summary = {
        "openai:gpt-5.5": {
            "rows": 1,
            "mean_probability_actual": 0.8,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.223,
            "mean_uniform_negative_log_likelihood": 0.693,
            "mean_brier": 0.08,
            "mean_uniform_brier": 0.5,
            "top1_accuracy": 1.0,
            "expected_calibration_error": 0.2,
            # no empirical/marginal keys at all
        }
    }
    html = render_twin_report_html("demo", rows, summary, {}, None)
    # the performance row renders as a full <tr> starting with the model and its
    # own metrics (not collapsed to a bare cell by the old ternary-precedence bug)
    assert '<tr><td>openai:gpt-5.5</td><td class="numeric">1</td>' in html
    assert ">0.800<" in html  # p(actual)
    assert ">0.500<" in html  # random p
    # absent empirical marginal is shown as an em-dash placeholder, not a broken row
    assert "&mdash;" in html
    # the plain-English verdict answers "beats random chance?"
    assert "Does the twin beat chance?" in html
    assert "beats random chance" in html


def test_executive_summary_shows_na_for_ordering_when_no_ordered_pairs() -> None:
    from pathlib import Path

    from zwill.executive_summary import render_html

    def _render(ordered_pairs: int) -> str:
        return render_html(
            survey="demo",
            metrics={
                "row_count": 10,
                "question_count": 1,
                "mean_probability_actual": 0.7,
                "mean_uniform_probability_actual": 0.5,
                "mean_negative_log_likelihood": 0.5,
                "mean_uniform_negative_log_likelihood": 0.69,
                "mean_brier": 0.2,
                "mean_uniform_brier": 0.5,
            },
            questions=[{"question": "q1", "text": "Pick one"}],
            lift_svg=Path("lift.svg"),
            empirical_lift_svg=None,
            lift={"mean_lift": 1.4, "median_lift": 1.3, "share_above_1": 0.8},
            empirical_lift=None,
            individual={
                "p_value_mean_p_actual": 0.01,
                "observed_mean_p_actual": 0.7,
                "null_mean_p_actual_mean": 0.5,
                "observed_mean_nll": 0.5,
                "null_mean_nll_mean": 0.69,
                "p_value_mean_nll": 0.01,
                "per_question": [],
            },
            pairwise_svg=Path("pw.svg"),
            pairwise={"summary": {"pairwise_order_accuracy": 0.62, "total_ordered_option_pairs": ordered_pairs}},
            spearman_detail={"summary": {"mean_spearman": 0.4, "questions": 1 if ordered_pairs else 0}},
        )

    # single-select: no ordered pairs -> N/A wording, never a misleading 0% / percentage
    na_html = _render(0)
    assert "do not apply and were not evaluated" in na_html
    assert "62%" not in na_html and "0% of the time" not in na_html
    # when ordering data exists, the real percentage is shown
    ok_html = _render(50)
    assert "62% of the time" in ok_html
    assert "Mean Spearman rank correlation is 0.40" in ok_html


def test_executive_summary_metrics_are_survey_weighted(tmp_path) -> None:
    from pathlib import Path

    from zwill.executive_summary import build_executive_summary

    def _row(rid, p, weight):
        return {
            "job_id": "j", "respondent_id": rid, "heldout_question": "q1", "heldout_question_text": "Pick",
            "actual_answer": "yes", "model_label": "openai:gpt-5.5", "option_labels": ["yes", "no"],
            "probabilities": {"yes": p, "no": 1 - p},
            "probability_actual": p, "uniform_probability_actual": 0.5,
            "negative_log_likelihood": 0.4, "uniform_negative_log_likelihood": 0.69,
            "brier": 0.2, "uniform_brier": 0.5, "weight": weight,
        }

    # heavy weight on the low-p respondent should pull the weighted mean below 0.7
    rows = [_row("r1", 0.9, 1.0), _row("r2", 0.5, 3.0)]
    result = build_executive_summary(
        rows, survey="demo", path=Path(tmp_path) / "exec.html", markdown_path=None, simulations=10, seed=1
    )
    # weighted: (0.9*1 + 0.5*3)/4 = 0.6 ; unweighted would be 0.7
    assert abs(result["metrics"]["mean_probability_actual"] - 0.6) < 1e-9  # not the unweighted 0.7


def test_twin_benchmark_report_renders_with_missing_empirical_baseline() -> None:
    from zwill.twin_report_html import render_twin_benchmark_report_html

    # nll_vs_empirical is None on surveys without committed marginals -- the report
    # must render an em-dash, not crash on None >= 0 / None format.
    payload = {
        "benchmark": "bench1",
        "rows": [
            {
                "survey": "kora", "heldout_questions": "Q3", "option_count": 4, "model": "openai:gpt-5.5",
                "rows": 5, "accuracy": 0.2, "nll": 1.4, "nll_p95": 2.0, "brier": 0.7, "ece": 0.1,
                "nll_vs_empirical": None,
            }
        ],
        "summary": {
            "openai:gpt-5.5": {
                "survey_count": 1, "mean_accuracy": 0.2, "mean_nll": 1.4, "mean_brier": 0.7, "mean_ece": 0.1,
                "mean_nll_vs_empirical": None,
            }
        },
    }
    html = render_twin_benchmark_report_html(payload)
    assert "kora" in html and "openai:gpt-5.5" in html
    assert "&mdash;" in html  # missing empirical rendered as em-dash, not a crash


def test_diagnostic_svg_renderers_produce_standalone_charts() -> None:
    bootstrap = {
        "deltas_vs_baseline": {
            "baseline_model": "baseline:conditional-embedding",
            "models": {
                "openai:gpt-5.5": {
                    "macro": {
                        "negative_log_likelihood": {"delta": -0.18, "lo": -0.37, "hi": -0.03},
                        "brier": {"delta": -0.09, "lo": -0.19, "hi": 0.01},
                    }
                }
            },
        }
    }
    forest = render_bootstrap_forest_svg(bootstrap)
    assert forest.startswith("<svg")
    assert "Paired bootstrap differences" in forest
    assert "NLL" in forest

    marginal = render_marginal_diagnostics_svg(
        {
            "options": [
                {
                    "heldout_question": "carbon_tax",
                    "model_label": "openai:gpt-5.5",
                    "option_label": "Favor",
                    "target_probability": 0.61,
                    "predicted_probability": 0.69,
                }
            ]
        }
    )
    assert marginal.startswith("<svg")
    assert "Twin-implied versus target marginals" in marginal
    assert "Favor" in marginal
