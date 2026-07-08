from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


def normalize_name_list(value: Any) -> list[str]:
    """Coerce a name selector into a clean list of names.

    Accepts ``None``, a comma-separated string, or a list/tuple of names (each
    item may itself be a comma-separated string). Whitespace is stripped and
    empty entries dropped. This lets plan-driven exports pass either a JSON
    list or a comma-separated string for fields like ``context_questions`` /
    ``heldout_questions`` without the downstream selectors crashing on a
    ``list.split`` call.
    """
    if value is None:
        return []
    items = list(value) if isinstance(value, (list, tuple)) else [value]
    names: list[str] = []
    for item in items:
        if item is None:
            continue
        for name in str(item).split(","):
            stripped = name.strip()
            if stripped:
                names.append(stripped)
    return names


def digital_twin_jobs_dir(sdir: Path) -> Path:
    return sdir / "digital_twin_jobs"


def digital_twin_predictions_path(sdir: Path) -> Path:
    return sdir / "digital_twin_predictions.jsonl"


def digital_twin_job_id_from_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def canonical_digital_twin_job_payload_from_job(job: dict[str, Any]) -> dict[str, Any]:
    scenarios = [
        {
            "respondent_id": scenario.get("respondent_id"),
            "heldout_question_name": scenario.get("heldout_question_name"),
            "heldout_question_text": scenario.get("heldout_question_text"),
            "heldout_options": scenario.get("heldout_options"),
            "actual_answer": scenario.get("actual_answer"),
            "observed_answers": scenario.get("observed_answers"),
            "agent_material": scenario.get("agent_material", []),
            "agent_material_text": scenario.get("agent_material_text"),
            "twin_material": scenario.get("twin_material", []),
            "twin_material_text": scenario.get("twin_material_text"),
        }
        for scenario in job.get("scenarios", [])
    ]
    models = [
        {
            "inference_service": model.get("inference_service"),
            "model": model.get("model"),
            "parameters": model.get("parameters", {}),
        }
        for model in job.get("models", [])
    ]
    return {"survey": job.get("survey", {}), "scenarios": scenarios, "models": models}


def digital_twin_job_id_from_job(job: dict[str, Any]) -> str:
    return digital_twin_job_id_from_payload(canonical_digital_twin_job_payload_from_job(job))


def canonical_digital_twin_job_payload_from_results(results: dict[str, Any]) -> dict[str, Any]:
    scenarios = []
    models = []
    seen_scenarios = set()
    seen_models = set()
    for row in results.get("data", []):
        scenario = row.get("scenario", {})
        scenario_key = (
            scenario.get("respondent_id"),
            scenario.get("heldout_question_name"),
            json.dumps(scenario.get("observed_answers", []), sort_keys=True),
            json.dumps(scenario.get("agent_material", []), sort_keys=True),
            scenario.get("agent_material_text"),
        )
        if scenario_key not in seen_scenarios:
            seen_scenarios.add(scenario_key)
            scenarios.append(
                {
                    "respondent_id": scenario.get("respondent_id"),
                    "heldout_question_name": scenario.get("heldout_question_name"),
                    "heldout_question_text": scenario.get("heldout_question_text"),
                    "heldout_options": scenario.get("heldout_options"),
                    "actual_answer": scenario.get("actual_answer"),
                    "observed_answers": scenario.get("observed_answers"),
                    "agent_material": scenario.get("agent_material", []),
                    "agent_material_text": scenario.get("agent_material_text"),
                    "twin_material": scenario.get("twin_material", []),
                    "twin_material_text": scenario.get("twin_material_text"),
                }
            )
        model = row.get("model", {})
        model_key = (model.get("inference_service"), model.get("model"), json.dumps(model.get("parameters", {}), sort_keys=True))
        if model_key not in seen_models:
            seen_models.add(model_key)
            models.append(
                {
                    "inference_service": model.get("inference_service"),
                    "model": model.get("model"),
                    "parameters": model.get("parameters", {}),
                }
            )
    return {"survey": results.get("survey", {}), "scenarios": scenarios, "models": models}


def digital_twin_job_id_from_results(results: dict[str, Any]) -> str:
    return digital_twin_job_id_from_payload(canonical_digital_twin_job_payload_from_results(results))


def select_context_questions(
    respondent_answers: dict[str, str],
    selected_questions: list[str],
    heldout_question: str,
    count: int | None,
    priority_by_question: dict[str, float] | None = None,
) -> list[str]:
    """Choose which of a respondent's answered questions to show as context.

    Candidates keep the order of `selected_questions` (i.e. questions.jsonl
    order), so column ordering decides what a count-limited twin sees. Pass
    `priority_by_question` (from a question's `context_priority` field) to pull
    high-priority questions to the front before the count cut; ties keep the
    positional order (a stable sort), so behavior is unchanged when no priorities
    are set.
    """
    candidates = [
        question_name
        for question_name in selected_questions
        if question_name != heldout_question and question_name in respondent_answers
    ]
    if priority_by_question:
        candidates.sort(key=lambda name: -float(priority_by_question.get(name, 0.0)))
    if count is not None:
        candidates = candidates[:count]
    return candidates


def one_hot_metrics(options: list[str], actual_answer: str, predicted: dict[str, float]) -> dict[str, float | int | None]:
    epsilon = 1e-12
    if not options:
        # Nothing to score against (e.g. a malformed row with no options). Return
        # unscoreable metrics rather than dividing by zero so the import can
        # quarantine the row instead of crashing the whole job.
        return {
            "probability_actual": 0.0,
            "uniform_probability_actual": None,
            "negative_log_likelihood": None,
            "uniform_negative_log_likelihood": None,
            "brier": None,
            "uniform_brier": None,
            "brier_improvement": None,
            "top1_correct": 0,
            "actual_rank": None,
        }
    actual_values = [1.0 if option == actual_answer else 0.0 for option in options]
    predicted_values = [float(predicted.get(option, 0.0)) for option in options]
    uniform_values = [1.0 / len(options) for _ in options]
    errors = [predicted - actual for actual, predicted in zip(actual_values, predicted_values)]
    uniform_errors = [predicted - actual for actual, predicted in zip(actual_values, uniform_values)]
    probability_actual = float(predicted.get(actual_answer, 0.0))
    uniform_probability_actual = 1.0 / len(options)
    ordered = sorted(((option, predicted.get(option, 0.0)) for option in options), key=lambda item: (-item[1], item[0]))
    rank = next((index + 1 for index, (option, _) in enumerate(ordered) if option == actual_answer), None)
    brier = sum(error * error for error in errors)
    uniform_brier = sum(error * error for error in uniform_errors)
    negative_log_likelihood = -math.log(max(probability_actual, epsilon))
    uniform_negative_log_likelihood = -math.log(uniform_probability_actual)
    return {
        "probability_actual": probability_actual,
        "uniform_probability_actual": uniform_probability_actual,
        "negative_log_likelihood": negative_log_likelihood,
        "uniform_negative_log_likelihood": uniform_negative_log_likelihood,
        "brier": brier,
        "uniform_brier": uniform_brier,
        "brier_improvement": uniform_brier - brier,
        "top1_correct": int(rank == 1),
        "actual_rank": rank,
    }


def calibrate_probabilities_to_marginal(
    rows: list[dict[str, Any]],
    target: dict[str, float],
    *,
    max_iter: int = 10000,
    tolerance: float = 1e-12,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """KL/IPF calibrate row probability vectors to match a target average marginal."""
    if not rows:
        raise ValueError("rows must not be empty")
    options = [str(option) for option in rows[0].get("option_labels", [])]
    if not options:
        raise ValueError("rows must include option_labels")
    for row in rows:
        if [str(option) for option in row.get("option_labels", [])] != options:
            raise ValueError("all rows in a calibration group must have identical option_labels")
    missing = [option for option in options if option not in target]
    if missing:
        raise ValueError(f"target marginal is missing options: {missing}")
    target_values = [float(target[option]) for option in options]
    target_sum = sum(target_values)
    if target_sum <= 0:
        raise ValueError("target marginal must have positive total probability")
    if any((not math.isfinite(value)) or value < 0 for value in target_values):
        raise ValueError("target marginal probabilities must be finite and non-negative")
    target_values = [value / target_sum for value in target_values]

    probabilities: list[list[float]] = []
    for row in rows:
        raw = [float(row.get("probabilities", {}).get(option, 0.0)) for option in options]
        if any((not math.isfinite(value)) or value < 0 for value in raw):
            raise ValueError("row probabilities must be finite and non-negative")
        total = sum(raw)
        if total <= 0:
            raise ValueError("row probabilities must have positive total probability")
        probabilities.append([value / total for value in raw])

    n = len(probabilities)
    iterations = 0
    max_error = math.inf
    for iterations in range(1, max_iter + 1):
        column_means = [sum(row[index] for row in probabilities) / n for index in range(len(options))]
        max_error = max(abs(column_means[index] - target_values[index]) for index in range(len(options)))
        if max_error <= tolerance:
            break
        factors = [
            target_values[index] / column_means[index] if column_means[index] > 0 else 0.0
            for index in range(len(options))
        ]
        for row_index, row in enumerate(probabilities):
            adjusted = [row[index] * factors[index] for index in range(len(options))]
            adjusted_total = sum(adjusted)
            if adjusted_total <= 0:
                raise ValueError("calibration produced a zero-probability row")
            probabilities[row_index] = [value / adjusted_total for value in adjusted]

    column_means = [sum(row[index] for row in probabilities) / n for index in range(len(options))]
    max_error = max(abs(column_means[index] - target_values[index]) for index in range(len(options)))
    return (
        [
            {option: probabilities[row_index][option_index] for option_index, option in enumerate(options)}
            for row_index in range(n)
        ],
        {
            "method": "kl_ipf",
            "iterations": iterations,
            "max_marginal_error": max_error,
            "converged": max_error <= tolerance,
            "tolerance": tolerance,
            "max_iter": max_iter,
            "target_marginal": {option: target_values[index] for index, option in enumerate(options)},
            "achieved_marginal": {option: column_means[index] for index, option in enumerate(options)},
        },
    )
