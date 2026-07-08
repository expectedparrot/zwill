from __future__ import annotations

from typing import Any

from .probability import (
    extract_probability_answer,
    extract_probability_payload,
    normalized_probabilities,
    true_probabilities_for,
)
from .twin import one_hot_metrics


def model_label(service_name: str | None, model_name: str | None) -> str:
    if service_name and model_name:
        return f"{service_name}:{model_name}"
    return str(model_name or "")


def extract_probability_prediction_rows(
    results: dict[str, Any],
    *,
    job_id: str,
    survey: str,
    stored_raw: str,
    imported_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    extracted = []
    issues = []
    for index, row in enumerate(results.get("data", [])):
        scenario = row.get("scenario", {})
        model = row.get("model", {})
        option_labels = scenario.get("option_labels", [])
        probabilities, notes, error = extract_probability_answer(row)
        normalized = None
        probability_sum = None
        if probabilities is not None:
            normalized, probability_sum, norm_error = normalized_probabilities(probabilities, len(option_labels))
            error = error or norm_error
        if error:
            issues.append(
                {
                    "row": index,
                    "question": scenario.get("source_question_name"),
                    "model": model.get("model"),
                    "error": error,
                }
            )
            continue
        probabilities_by_option = {
            option: normalized[position]
            for position, option in enumerate(option_labels)
        }
        extracted.append(
            {
                "job_id": job_id,
                "row": index,
                "survey": survey,
                "question": scenario.get("source_question_name"),
                "question_text": scenario.get("source_question_text"),
                "model": model.get("model"),
                "service": model.get("inference_service"),
                "model_parameters": model.get("parameters", {}),
                "option_labels": option_labels,
                "probabilities": probabilities_by_option,
                "raw_probabilities": probabilities,
                "raw_probability_sum": probability_sum,
                "notes": notes,
                "source_raw": stored_raw,
                "imported_at": imported_at,
            }
        )
    return extracted, issues


def extract_twin_prediction_rows(
    results: dict[str, Any],
    *,
    job_id: str,
    survey: str,
    stored_raw: str,
    imported_at: str,
    truth: dict[str, Any] | None = None,
    allow_missing_actual: bool = False,
    weight_by_respondent: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    weight_by_respondent = weight_by_respondent or {}
    truth = truth or {}
    extracted = []
    issues = []
    for index, row in enumerate(results.get("data", [])):
        scenario = row.get("scenario", {})
        model = row.get("model", {})
        options = scenario.get("heldout_options", [])
        probabilities, notes, payload, error = extract_probability_payload(row)
        normalized = None
        probability_sum = None
        if probabilities is not None:
            normalized, probability_sum, norm_error = normalized_probabilities(probabilities, len(options))
            error = error or norm_error
        actual_answer = scenario.get("actual_answer")
        has_actual_answer = actual_answer in options
        if not has_actual_answer and (actual_answer is not None or not allow_missing_actual):
            error = error or "actual_answer_not_in_options"
        if error:
            issues.append(
                {
                    "row": index,
                    "respondent_id": scenario.get("respondent_id"),
                    "heldout_question": scenario.get("heldout_question_name"),
                    "model": model_label(model.get("inference_service"), model.get("model")),
                    "error": error,
                }
            )
            continue
        probabilities_by_option = {option: normalized[position] for position, option in enumerate(options)}
        metrics = one_hot_metrics(options, actual_answer, probabilities_by_option) if has_actual_answer else {}
        marginal_probabilities = true_probabilities_for(scenario.get("heldout_question_name"), truth, options) if truth else {}
        marginal_metrics = one_hot_metrics(options, actual_answer, marginal_probabilities) if has_actual_answer and marginal_probabilities else {}
        extracted.append(
            {
                "job_id": job_id,
                "row": index,
                "survey": survey,
                "respondent_id": scenario.get("respondent_id"),
                "weight": float(weight_by_respondent.get(str(scenario.get("respondent_id")), 1.0)),
                "heldout_question": scenario.get("heldout_question_name"),
                "heldout_question_text": scenario.get("heldout_question_text"),
                "actual_answer": actual_answer,
                "model": model.get("model"),
                "service": model.get("inference_service"),
                "model_label": model_label(model.get("inference_service"), model.get("model")),
                "model_parameters": model.get("parameters", {}),
                "option_labels": options,
                "probabilities": probabilities_by_option,
                "raw_probabilities": probabilities,
                "raw_probability_sum": probability_sum,
                "observed_answers": scenario.get("observed_answers", []),
                "twin_material": scenario.get("twin_material", []),
                "twin_material_text": scenario.get("twin_material_text"),
                "notes": notes,
                "confidence": payload.get("confidence") if isinstance(payload, dict) else None,
                "evidence_summary": payload.get("evidence_summary") if isinstance(payload, dict) else None,
                **metrics,
                "empirical_marginal_probabilities": marginal_probabilities,
                "empirical_marginal_probability_actual": marginal_metrics.get("probability_actual"),
                "empirical_marginal_negative_log_likelihood": marginal_metrics.get("negative_log_likelihood"),
                "empirical_marginal_brier": marginal_metrics.get("brier"),
                "empirical_marginal_top1_correct": marginal_metrics.get("top1_correct"),
                "marginal_probabilities": marginal_probabilities,
                "marginal_probability_actual": marginal_metrics.get("probability_actual"),
                "marginal_negative_log_likelihood": marginal_metrics.get("negative_log_likelihood"),
                "marginal_brier": marginal_metrics.get("brier"),
                "marginal_top1_correct": marginal_metrics.get("top1_correct"),
                "source_raw": stored_raw,
                "imported_at": imported_at,
            }
        )
    return extracted, issues
