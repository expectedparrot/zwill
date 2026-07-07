from __future__ import annotations

import json
from pathlib import Path

import pytest

from zwill.cli import build_twin_report
from zwill.errors import ZwillError
from zwill.reporting import render_twin_report_html, render_twin_summary_report_html
from zwill.twin_diagnostics import (
    build_twin_conditional_consistency_diagnostics,
    build_twin_joint_structure_diagnostics,
    build_twin_subgroup_marginal_diagnostics,
)
from zwill.twin_jobs import (
    answer_commonness_by_question,
    answer_commonness_text,
    digital_twin_question_text,
    expand_question_text_fields,
    normalize_question_spec,
    read_question_specs,
    selected_heldout_question_names,
    workbook_option_label,
)
from zwill.twin_results import (
    aggregate_twin_marginals,
    distribution_distance_metrics,
    job_ids_from_manifest,
    top_prediction,
    twin_prediction_export_rows,
)


def test_question_spec_normalization_accepts_aliases_and_provenance() -> None:
    spec = normalize_question_spec(
        {"name": "q1", "text": "Pick one", "options": [" A ", "B", ""]},
        source_note="external spec",
    )

    assert spec["question_name"] == "q1"
    assert spec["question_text"] == "Pick one"
    assert spec["question_options"] == ["A", "B"]
    assert spec["question_type"] == "multiple_choice"
    assert spec["source"]["note"] == "external spec"


def test_question_spec_normalization_rejects_missing_options() -> None:
    with pytest.raises(ZwillError) as excinfo:
        normalize_question_spec({"question_name": "q1", "question_text": "Pick one", "question_options": []})

    assert excinfo.value.code == "invalid_input"


def test_read_question_specs_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "specs.jsonl"
    path.write_text(json.dumps({"question_name": "q1", "question_text": "Pick one", "question_options": ["yes", "no"]}) + "\n")

    specs = read_question_specs(path)

    assert specs[0]["question_name"] == "q1"
    assert specs[0]["source"]["note"] == f"External question spec: {path}"


def test_workbook_option_label_expands_known_likert_codes() -> None:
    labels = "1=lowest agreement/likelihood/appeal/excitement; 7=highest"

    assert workbook_option_label("1", labels) == "1 - lowest agreement/likelihood/appeal/excitement"
    assert workbook_option_label("4", labels) == "4 - Likert scale point 4"
    assert workbook_option_label("7", labels) == "7 - highest agreement/likelihood/appeal/excitement"
    assert workbook_option_label("Other", labels) == "Other"


def test_expand_question_text_fields_uses_respondent_answers() -> None:
    questions = {
        "q1": {
            "question_name": "q1",
            "question_text": "Likelihood for [Field-Top_feature]",
            "source": {"raw_label": "Q11"},
        },
        "q2": {
            "question_name": "q2",
            "question_text": "Top feature",
            "source": {"raw_label": "Top_feature"},
        },
    }

    expanded = expand_question_text_fields(
        "Likelihood for [Field-Top_feature]",
        {"q2": "Premium support"},
        questions,
    )

    assert expanded == "Likelihood for Premium support"


def test_expand_question_text_fields_uses_question_names() -> None:
    questions = {
        "q1": {
            "question_name": "q1",
            "question_text": "Likelihood for [Field-q2]",
            "source": {},
        },
        "q2": {
            "question_name": "q2",
            "question_text": "Top feature",
            "source": {"raw_label": "Top_feature"},
        },
    }

    expanded = expand_question_text_fields(
        "Likelihood for [Field-q2]",
        {"q2": "Premium support"},
        questions,
    )

    assert expanded == "Likelihood for Premium support"


def test_expand_question_text_fields_keeps_unknown_placeholders() -> None:
    assert expand_question_text_fields("Likelihood for [Field-Missing]", {}, {}) == "Likelihood for [Field-Missing]"


def test_selected_heldout_question_names_validates_and_deduplicates() -> None:
    args = type("Args", (), {"heldout_question": ["q1", "q2"], "heldout_questions": "q1,q3"})()
    questions = [{"question_name": "q1"}, {"question_name": "q2"}, {"question_name": "q3"}]

    assert selected_heldout_question_names(args, questions) == ["q1", "q2", "q3"]


def test_answer_commonness_prompt_helpers() -> None:
    counts = answer_commonness_by_question({"r1": {"q1": "yes"}, "r2": {"q1": "no"}, "r3": {"q1": "yes"}})

    assert answer_commonness_text("q1", "yes", counts) == "Answer commonness: 2/3 respondents (66.7%) gave this answer."
    prompt = digital_twin_question_text("answer-commonness-confidence")
    assert '"confidence": 0.64' in prompt
    assert "They are not statistics for the held-out question" in prompt


def test_twin_prediction_export_rows_long_and_wide() -> None:
    rows = [
        {
            "job_id": "job1",
            "survey": "demo",
            "respondent_id": "r1",
            "heldout_question": "q1",
            "heldout_question_text": "Pick one",
            "model": "gpt-5.5",
            "service": "openai",
            "option_labels": ["yes", "no"],
            "probabilities": {"yes": 0.8, "no": 0.2},
            "confidence": 0.7,
        }
    ]

    long_rows = twin_prediction_export_rows(rows, "long")
    wide_rows = twin_prediction_export_rows(rows, "wide")

    assert len(long_rows) == 2
    assert long_rows[0]["model_label"] == "openai:gpt-5.5"
    assert long_rows[0]["option_label"] == "yes"
    assert long_rows[0]["top_choice"] == "yes"
    assert wide_rows[0]["probability_yes"] == 0.8


def test_manifest_job_id_extraction_supports_imports_exports_and_top_level(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "job_id": "top",
                "imports": [{"job_id": "chunk_001"}, {"job_id": "chunk_002"}],
                "exports": [{"job_id": "chunk_001"}, {"job_id": "chunk_003"}],
            }
        )
    )

    assert job_ids_from_manifest(path) == ["chunk_001", "chunk_002", "chunk_003", "top"]


def test_aggregate_marginals_and_distribution_metrics() -> None:
    rows = [
        {"heldout_question": "q1", "model_label": "m", "option_labels": ["A", "B"], "probabilities": {"A": 0.8, "B": 0.2}},
        {"heldout_question": "q1", "model_label": "m", "option_labels": ["A", "B"], "probabilities": {"A": 0.6, "B": 0.4}},
    ]

    aggregate = aggregate_twin_marginals(rows)[("q1", "m")]
    metrics = distribution_distance_metrics(aggregate["probabilities"], {"A": 0.5, "B": 0.5})

    assert aggregate["respondent_count"] == 2
    assert aggregate["probabilities"] == {"A": 0.7, "B": 0.30000000000000004}
    assert metrics["l1"] == pytest.approx(0.4)
    assert top_prediction(aggregate["probabilities"]) == ("A", 0.7)


def test_twin_report_includes_question_marginal_diagnostics() -> None:
    rows = [
        {
            "job_id": "job1",
            "survey": "demo",
            "respondent_id": "r1",
            "heldout_question": "q1",
            "heldout_question_text": "Pick one",
            "actual_answer": "A",
            "model": "gpt-5.5",
            "service": "openai",
            "model_label": "openai:gpt-5.5",
            "option_labels": ["A", "B"],
            "probabilities": {"A": 0.8, "B": 0.2},
            "raw_probabilities": [0.8, 0.2],
            "probability_actual": 0.8,
            "uniform_probability_actual": 0.5,
            "uniform_negative_log_likelihood": 0.6931471805599453,
            "negative_log_likelihood": 0.2231435513142097,
            "uniform_brier": 0.5,
            "brier": 0.08,
            "brier_improvement": 0.42,
            "top1_correct": 1,
            "actual_rank": 1,
            "empirical_marginal_probabilities": {"A": 0.5, "B": 0.5},
            "empirical_marginal_probability_actual": 0.5,
            "empirical_marginal_negative_log_likelihood": 0.6931471805599453,
            "empirical_marginal_brier": 0.5,
            "empirical_marginal_top1_correct": 1,
            "observed_answers": [],
        },
        {
            "job_id": "job1",
            "survey": "demo",
            "respondent_id": "r2",
            "heldout_question": "q1",
            "heldout_question_text": "Pick one",
            "actual_answer": "B",
            "model": "gpt-5.5",
            "service": "openai",
            "model_label": "openai:gpt-5.5",
            "option_labels": ["A", "B"],
            "probabilities": {"A": 0.6, "B": 0.4},
            "raw_probabilities": [0.6, 0.4],
            "probability_actual": 0.4,
            "uniform_probability_actual": 0.5,
            "uniform_negative_log_likelihood": 0.6931471805599453,
            "negative_log_likelihood": 0.916290731874155,
            "uniform_brier": 0.5,
            "brier": 0.72,
            "brier_improvement": -0.22,
            "top1_correct": 0,
            "actual_rank": 2,
            "empirical_marginal_probabilities": {"A": 0.5, "B": 0.5},
            "empirical_marginal_probability_actual": 0.5,
            "empirical_marginal_negative_log_likelihood": 0.6931471805599453,
            "empirical_marginal_brier": 0.5,
            "empirical_marginal_top1_correct": 1,
            "observed_answers": [],
        },
    ]

    payload = build_twin_report(rows)
    marginal = payload["diagnostics"]["marginal_comparisons"][0]
    html = render_twin_report_html("demo", payload["rows"], payload["summary"], payload["diagnostics"], payload.get("health"))

    assert marginal["heldout_question"] == "q1"
    assert marginal["predicted_top_option"] == "A"
    assert marginal["target_top_option"] == "A"
    assert marginal["l1"] == pytest.approx(0.4)
    assert payload["diagnostics"]["marginal_options"][0]["predicted_probability"] == pytest.approx(0.7)
    assert "Question Marginals" in html
    assert "Blue bars are the twin-implied population distribution" in html
    assert "twin-bar" in html
    assert "target-bar" in html

    summary_html = render_twin_summary_report_html(
        "demo",
        payload["rows"],
        payload["summary"],
        payload["diagnostics"],
        {"job_id": "job1", "import": {"row_count": 2, "extracted_count": 2, "issue_count": 0}},
    )
    assert "Demo Digital Twin Validation" in summary_html
    assert "Metric Definitions" in summary_html
    assert "Uniform-random NLL minus model NLL" in summary_html
    assert "NLL improvement vs uniform" in summary_html
    assert "NLL improvement vs empirical oracle" in summary_html
    assert "uses the true answer distribution" in summary_html
    assert "Confidence gap" in summary_html
    assert "Marginal L1" in summary_html
    assert "Takeaway" in summary_html
    assert "Strong individual signal" in summary_html
    assert "Uniform baseline" in summary_html
    assert "Model Performance" in summary_html
    assert "question-performance-block" in summary_html
    assert "question-dist-chart" in summary_html
    assert "Empirical" in summary_html
    assert "Uniform (50.0%)" in summary_html
    assert "uniform-marker" in summary_html
    assert "dist-track" in summary_html
    assert "Uniform random" in summary_html
    assert "Marginal Divergence" in summary_html
    assert "<th>Largest option deltas</th>" in summary_html
    assert "A: +20.0%" in summary_html
    assert "B: -20.0%" in summary_html
    assert "twin-summary-report-data" in summary_html
    assert "twin-report-data" not in summary_html


def test_twin_report_keeps_multiple_jobs_as_separate_twin_sets() -> None:
    base_rows = [
        {
            "job_id": "job1",
            "survey": "demo",
            "respondent_id": "r1",
            "heldout_question": "q1",
            "heldout_question_text": "Pick one",
            "actual_answer": "A",
            "model": "gpt-5.5",
            "service": "openai",
            "model_label": "openai:gpt-5.5",
            "option_labels": ["A", "B"],
            "probabilities": {"A": 0.8, "B": 0.2},
            "raw_probabilities": [0.8, 0.2],
            "probability_actual": 0.8,
            "uniform_probability_actual": 0.5,
            "uniform_negative_log_likelihood": 0.6931471805599453,
            "negative_log_likelihood": 0.2231435513142097,
            "uniform_brier": 0.5,
            "brier": 0.08,
            "brier_improvement": 0.42,
            "top1_correct": 1,
            "actual_rank": 1,
            "empirical_marginal_probabilities": {"A": 0.5, "B": 0.5},
            "empirical_marginal_probability_actual": 0.5,
            "empirical_marginal_negative_log_likelihood": 0.6931471805599453,
            "empirical_marginal_brier": 0.5,
            "empirical_marginal_top1_correct": 1,
            "observed_answers": [],
        }
    ]
    comparison_rows = base_rows + [
        {
            **base_rows[0],
            "job_id": "job2",
            "probabilities": {"A": 0.4, "B": 0.6},
            "raw_probabilities": [0.4, 0.6],
            "probability_actual": 0.4,
            "negative_log_likelihood": 0.916290731874155,
            "brier": 0.72,
            "brier_improvement": -0.22,
            "top1_correct": 0,
        }
    ]

    payload = build_twin_report(comparison_rows)
    payload["diagnostics"]["twin_set_descriptions"] = {
        "job1 / openai:gpt-5.5": {
            "description": "Kitchen sink",
            "job_id": "job1",
            "model_label": "openai:gpt-5.5",
            "source_name": "kitchen_sink_results.json.gz",
        },
        "job2 / openai:gpt-5.5": {
            "description": "Kitchen sink; known answer options included",
            "job_id": "job2",
            "model_label": "openai:gpt-5.5",
            "source_name": "kitchen_sink_known_options_results.json.gz",
        },
    }
    labels = {row["model_label"] for row in payload["diagnostics"]["marginal_comparisons"]}
    html = render_twin_summary_report_html("demo", payload["rows"], payload["summary"], payload["diagnostics"], {"job_ids": ["job1", "job2"]})

    assert labels == {"job1 / openai:gpt-5.5", "job2 / openai:gpt-5.5"}
    assert set(payload["summary"]) == labels
    assert "Twin set" in html
    assert "Kitchen sink" in html
    assert "Kitchen sink; known answer options included" in html
    assert "job: job1" in html
    assert "model: openai:gpt-5.5" in html
    assert "source: kitchen_sink_known_options_results.json.gz" in html


def test_twin_specific_diagnostics_score_joint_subgroup_and_conditional_structure() -> None:
    rows = []
    respondent_specs = [
        ("r1", "yes", "high", "remote"),
        ("r2", "yes", "high", "remote"),
        ("r3", "no", "low", "office"),
        ("r4", "no", "low", "office"),
    ]
    for respondent_id, q1_actual, q2_actual, segment in respondent_specs:
        q1_probs = {"yes": 0.8, "no": 0.2} if segment == "remote" else {"yes": 0.2, "no": 0.8}
        q2_probs = {"high": 0.75, "low": 0.25} if segment == "remote" else {"high": 0.25, "low": 0.75}
        rows.extend(
            [
                {
                    "respondent_id": respondent_id,
                    "heldout_question": "q1",
                    "heldout_question_text": "Approve?",
                    "actual_answer": q1_actual,
                    "model_label": "openai:gpt-5.5",
                    "probabilities": q1_probs,
                    "observed_answers": [{"question_name": "segment", "question_text": "Segment", "answer": segment}],
                },
                {
                    "respondent_id": respondent_id,
                    "heldout_question": "q2",
                    "heldout_question_text": "Confidence?",
                    "actual_answer": q2_actual,
                    "model_label": "openai:gpt-5.5",
                    "probabilities": q2_probs,
                    "observed_answers": [{"question_name": "segment", "question_text": "Segment", "answer": segment}],
                },
            ]
        )

    joint = build_twin_joint_structure_diagnostics(rows, min_pair_rows=1)
    subgroup = build_twin_subgroup_marginal_diagnostics(rows, min_cell_rows=1)
    conditional = build_twin_conditional_consistency_diagnostics(rows, min_cell_rows=1)

    assert joint["pair_count"] == 1
    assert joint["rows"][0]["left_question"] == "q1"
    assert joint["rows"][0]["right_question"] == "q2"
    assert joint["rows"][0]["respondents"] == 4
    assert joint["rows"][0]["joint_l1"] >= 0
    assert subgroup["cell_count"] >= 2
    assert {row["segment_value"] for row in subgroup["rows"]} == {"office", "remote"}
    assert conditional["cell_count"] >= 2
    assert {row["condition_question"] for row in conditional["rows"]} == {"q1", "q2"}


def _marginal_rows():
    # Two respondents: one certain "yes", one certain "no".
    return [
        {"heldout_question": "q1", "model_label": "m", "respondent_id": "r1",
         "option_labels": ["yes", "no"], "probabilities": {"yes": 1.0, "no": 0.0}},
        {"heldout_question": "q1", "model_label": "m", "respondent_id": "r2",
         "option_labels": ["yes", "no"], "probabilities": {"yes": 0.0, "no": 1.0}},
    ]


def test_aggregate_twin_marginals_unweighted_is_simple_average() -> None:
    agg = aggregate_twin_marginals(_marginal_rows())
    probs = agg[("q1", "m")]["probabilities"]
    assert probs["yes"] == 0.5 and probs["no"] == 0.5


def test_aggregate_twin_marginals_respects_respondent_weights() -> None:
    # r1 (yes) weighted 3x r2 (no) -> implied marginal 0.75 yes, matching a weighted
    # truth marginal built the same way.
    agg = aggregate_twin_marginals(_marginal_rows(), {"r1": 3.0, "r2": 1.0})
    probs = agg[("q1", "m")]["probabilities"]
    assert probs["yes"] == 0.75 and probs["no"] == 0.25
    assert agg[("q1", "m")]["weighted_respondents"] == 4.0


def test_aggregate_twin_marginals_defaults_missing_weight_to_one() -> None:
    # r2 absent from the weight map -> treated as weight 1.0, not dropped.
    agg = aggregate_twin_marginals(_marginal_rows(), {"r1": 1.0})
    probs = agg[("q1", "m")]["probabilities"]
    assert probs["yes"] == 0.5 and probs["no"] == 0.5
    assert agg[("q1", "m")]["weighted_respondents"] == 2.0
