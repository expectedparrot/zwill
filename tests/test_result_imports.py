from __future__ import annotations

import pytest

from zwill.result_imports import extract_probability_prediction_rows, extract_twin_prediction_rows


def test_extract_probability_prediction_rows_normalizes_probabilities() -> None:
    results = {
        "data": [
            {
                "scenario": {
                    "source_question_name": "q1",
                    "source_question_text": "Pick one",
                    "option_labels": ["A", "B"],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {"temperature": 0}},
                "answer": {"response_probabilities": '{"probabilities":[2,1],"notes":"ok"}'},
            }
        ]
    }

    rows, issues = extract_probability_prediction_rows(
        results,
        job_id="job1",
        survey="demo",
        stored_raw="raw/results.json",
        imported_at="now",
    )

    assert issues == []
    assert rows[0]["probabilities"] == {"A": pytest.approx(2 / 3), "B": pytest.approx(1 / 3)}
    assert rows[0]["raw_probability_sum"] == 3
    assert rows[0]["notes"] == "ok"
    assert rows[0]["source_raw"] == "raw/results.json"


def test_extract_probability_prediction_rows_reports_malformed_json_issue() -> None:
    results = {
        "data": [
            {
                "scenario": {"source_question_name": "q1", "option_labels": ["A", "B"]},
                "model": {"model": "gpt-5.5"},
                "answer": {"response_probabilities": "not json"},
            }
        ]
    }

    rows, issues = extract_probability_prediction_rows(
        results,
        job_id="job1",
        survey="demo",
        stored_raw="raw/results.json",
        imported_at="now",
    )

    assert rows == []
    assert issues == [{"row": 0, "question": "q1", "model": "gpt-5.5", "error": "invalid_json"}]


def test_extract_twin_prediction_rows_missing_actual_fails_by_default() -> None:
    results = {
        "data": [
            {
                "scenario": {
                    "respondent_id": "r1",
                    "heldout_question_name": "q1",
                    "heldout_options": ["A", "B"],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai"},
                "answer": {"response_probabilities": '{"probabilities":[0.7,0.3],"notes":"ok"}'},
            }
        ]
    }

    rows, issues = extract_twin_prediction_rows(
        results,
        job_id="job1",
        survey="demo",
        stored_raw="raw/results.json",
        imported_at="now",
    )

    assert rows == []
    assert issues == [
        {
            "row": 0,
            "respondent_id": "r1",
            "heldout_question": "q1",
            "model": "openai:gpt-5.5",
            "error": "actual_answer_not_in_options",
        }
    ]


def test_extract_twin_prediction_rows_allows_true_holdout_and_preserves_confidence() -> None:
    results = {
        "data": [
            {
                "scenario": {
                    "respondent_id": "r1",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["A", "B"],
                    "observed_answers": [{"question_name": "q0", "answer": "yes"}],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {
                    "response_probabilities": '{"probabilities":[0.7,0.3],"confidence":0.62,"evidence_summary":"leans A","notes":"ok"}'
                },
            }
        ]
    }

    rows, issues = extract_twin_prediction_rows(
        results,
        job_id="job1",
        survey="demo",
        stored_raw="raw/results.json",
        imported_at="now",
        allow_missing_actual=True,
    )

    assert issues == []
    assert rows[0]["actual_answer"] is None
    assert rows[0]["probabilities"] == {"A": 0.7, "B": 0.3}
    assert rows[0]["confidence"] == 0.62
    assert rows[0]["evidence_summary"] == "leans A"
    assert "probability_actual" not in rows[0]


def test_extract_twin_prediction_rows_scores_actual_and_empirical_marginal() -> None:
    results = {
        "data": [
            {
                "scenario": {
                    "respondent_id": "r1",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["A", "B"],
                    "actual_answer": "A",
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.8,0.2],"notes":"ok"}'},
            }
        ]
    }
    truth = {"marginals": {"q1": {"A": {"weighted_count": 3}, "B": {"weighted_count": 1}}}}

    rows, issues = extract_twin_prediction_rows(
        results,
        job_id="job1",
        survey="demo",
        stored_raw="raw/results.json",
        imported_at="now",
        truth=truth,
    )

    assert issues == []
    assert rows[0]["probability_actual"] == 0.8
    assert rows[0]["empirical_marginal_probability_actual"] == 0.75
