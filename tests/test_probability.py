from __future__ import annotations

import argparse
import math

from zwill.cli import balanced_by_actual, respondent_selection, selected_heldout_question_names, stratified_by_actual
from zwill.probability import (
    extract_probability_answer,
    normalized_probabilities,
    parse_probability_json,
    probability_job_id_from_payload,
    probability_metrics,
)
from zwill.twin import one_hot_metrics, select_context_questions


def test_parse_probability_json_accepts_fenced_json() -> None:
    parsed, error = parse_probability_json('```json\n{"probabilities":[0.25,0.75],"notes":"ok"}\n```')

    assert error is None
    assert parsed == {"probabilities": [0.25, 0.75], "notes": "ok"}


def test_parse_probability_json_accepts_embedded_json() -> None:
    parsed, error = parse_probability_json('Here is the estimate: {"probabilities":[0.1,0.9],"notes":"ok"}')

    assert error is None
    assert parsed == {"probabilities": [0.1, 0.9], "notes": "ok"}


def test_parse_probability_json_rejects_malformed_json() -> None:
    parsed, error = parse_probability_json('{"probabilities":[0.1,]}')

    assert parsed is None
    assert error and error.startswith("invalid_json")


def test_extract_probability_answer_uses_first_answer_value_fallback() -> None:
    values, notes, error = extract_probability_answer(
        {"answer": {"some_other_question_name": '{"probabilities":["0.2","0.8"],"notes":"fallback"}'}}
    )

    assert error is None
    assert values == [0.2, 0.8]
    assert notes == "fallback"


def test_extract_probability_answer_reports_missing_probabilities() -> None:
    values, notes, error = extract_probability_answer({"answer": {"response_probabilities": '{"notes":"missing"}'}})

    assert values is None
    assert notes == "missing"
    assert error == "missing_probabilities"


def test_normalized_probabilities_rejects_bad_lengths_and_ranges() -> None:
    assert normalized_probabilities([0.2, 0.8], 3)[2] == "wrong_probability_count"
    assert normalized_probabilities([0.2, -0.1], 2)[2] == "invalid_probability_range"
    assert normalized_probabilities([0.0, 0.0], 2)[2] == "zero_probability_sum"

    normalized, total, error = normalized_probabilities([2.0, 1.0], 2)
    assert error is None
    assert total == 3.0
    assert normalized == [2.0 / 3.0, 1.0 / 3.0]


def test_probability_metrics_include_actual_kl_divergence() -> None:
    metrics = probability_metrics([0.75, 0.25], [0.5, 0.5])

    expected_kl = 0.75 * math.log(0.75 / 0.5) + 0.25 * math.log(0.25 / 0.5)
    assert metrics["brier"] == 0.125
    assert metrics["mae"] == 0.25
    assert metrics["kl_divergence"] == expected_kl


def test_probability_job_id_is_stable_for_key_order() -> None:
    first = {"models": [{"model": "gpt-5.5"}], "scenarios": [{"source_question_name": "q1"}]}
    second = {"scenarios": [{"source_question_name": "q1"}], "models": [{"model": "gpt-5.5"}]}

    assert probability_job_id_from_payload(first) == probability_job_id_from_payload(second)


def test_twin_context_selection_excludes_heldout_and_limits() -> None:
    selected = select_context_questions({"q1": "a", "q2": "b", "q3": "c"}, ["q1", "q2", "q3"], "q2", 1)

    assert selected == ["q1"]


def test_one_hot_metrics_compare_to_uniform() -> None:
    metrics = one_hot_metrics(["yes", "no"], "yes", {"yes": 0.75, "no": 0.25})

    assert metrics["probability_actual"] == 0.75
    assert metrics["uniform_probability_actual"] == 0.5
    assert metrics["top1_correct"] == 1
    assert metrics["actual_rank"] == 1
    assert metrics["brier"] < metrics["uniform_brier"]


def test_respondent_selection_samples_with_seed() -> None:
    args = argparse.Namespace(
        respondent=None,
        respondents=None,
        sample_respondents=3,
        seed=123,
        balance_actual=False,
        stratify_actual=False,
        limit_respondents=None,
    )
    respondents = ["r1", "r2", "r3", "r4", "r5"]

    first = respondent_selection(args, respondents)
    second = respondent_selection(args, respondents)

    assert first == second
    assert len(first) == 3
    assert set(first) <= set(respondents)


def test_selected_heldout_question_names_accepts_repeated_and_csv() -> None:
    args = argparse.Namespace(heldout_question=["q1"], heldout_questions="q2,q3")
    questions = [{"question_name": "q1"}, {"question_name": "q2"}, {"question_name": "q3"}]

    assert selected_heldout_question_names(args, questions) == ["q1", "q2", "q3"]


def test_actual_answer_sampling_helpers_are_deterministic() -> None:
    respondent_ids = ["r1", "r2", "r3", "r4", "r5", "r6"]
    answers = {
        "r1": {"q1": "yes"},
        "r2": {"q1": "yes"},
        "r3": {"q1": "yes"},
        "r4": {"q1": "no"},
        "r5": {"q1": "no"},
        "r6": {"q1": "no"},
    }

    balanced = balanced_by_actual(respondent_ids, answers, "q1", 4, 7)
    stratified = stratified_by_actual(respondent_ids, answers, "q1", 4, 7)

    assert balanced == balanced_by_actual(respondent_ids, answers, "q1", 4, 7)
    assert stratified == stratified_by_actual(respondent_ids, answers, "q1", 4, 7)
    assert len(balanced) == 4
    assert len(stratified) == 4
    assert sum(1 for respondent_id in balanced if answers[respondent_id]["q1"] == "yes") == 2
    assert sum(1 for respondent_id in balanced if answers[respondent_id]["q1"] == "no") == 2
