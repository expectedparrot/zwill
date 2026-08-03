from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


def probability_jobs_dir(sdir: Path) -> Path:
    return sdir / "probability_jobs"


def probability_predictions_path(sdir: Path) -> Path:
    return sdir / "probability_predictions.jsonl"


def canonical_probability_job_payload_from_results(results: dict[str, Any]) -> dict[str, Any]:
    rows = results.get("data", [])
    scenarios = []
    models = []
    seen_scenarios = set()
    seen_models = set()
    for row in rows:
        scenario = row.get("scenario", {})
        scenario_key = scenario.get("source_question_name")
        if scenario_key and scenario_key not in seen_scenarios:
            seen_scenarios.add(scenario_key)
            scenarios.append(
                {
                    "source_question_name": scenario.get("source_question_name"),
                    "source_question_text": scenario.get("source_question_text"),
                    "option_labels": scenario.get("option_labels"),
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
    return {
        "survey": results.get("survey", {}),
        "scenarios": scenarios,
        "models": models,
    }


def probability_job_id_from_results(results: dict[str, Any]) -> str:
    payload = canonical_probability_job_payload_from_results(results)
    return probability_job_id_from_payload(payload)


def probability_job_id_from_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def canonical_probability_job_payload_from_job(job: dict[str, Any]) -> dict[str, Any]:
    scenarios = [
        {
            "source_question_name": scenario.get("source_question_name"),
            "source_question_text": scenario.get("source_question_text"),
            "option_labels": scenario.get("option_labels"),
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
    return {
        "survey": job.get("survey", {}),
        "scenarios": scenarios,
        "models": models,
    }


def probability_job_id_from_job(job: dict[str, Any]) -> str:
    return probability_job_id_from_payload(canonical_probability_job_payload_from_job(job))


def parse_probability_json(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(raw, dict):
        return raw, None
    if raw is None:
        return None, "empty_answer"
    text = str(raw).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1]), None
            except json.JSONDecodeError as exc:
                return None, f"invalid_json: {exc}"
        return None, "invalid_json"


def extract_probability_payload(
    row: dict[str, Any], scored_question_name: str | None = None
) -> tuple[list[float] | None, str | None, dict[str, Any] | None, str | None]:
    answer = row.get("answer", {})
    raw_answer = None
    if isinstance(answer, dict):
        # For a prompt pipeline the scored answer is the final step, not the first
        # answer value (which would be an intermediate reasoning step).
        if scored_question_name and scored_question_name in answer:
            raw_answer = answer.get(scored_question_name)
        else:
            raw_answer = answer.get("response_probabilities")
            if raw_answer is None and answer:
                raw_answer = next(iter(answer.values()))
    parsed, error = parse_probability_json(raw_answer)
    if error:
        return None, None, parsed, error
    probabilities = parsed.get("probabilities") if parsed else None
    notes = parsed.get("notes") if isinstance(parsed, dict) else None
    if not isinstance(probabilities, list):
        return None, notes, parsed, "missing_probabilities"
    try:
        values = [float(value) for value in probabilities]
    except (TypeError, ValueError):
        return None, notes, parsed, "invalid_probability_value"
    return values, notes, parsed, None


def extract_probability_answer(row: dict[str, Any]) -> tuple[list[float] | None, str | None, str | None]:
    values, notes, _payload, error = extract_probability_payload(row)
    return values, notes, error


def normalized_probabilities(probabilities: list[float], option_count: int) -> tuple[list[float] | None, float, str | None]:
    if len(probabilities) != option_count:
        return None, sum(probabilities), "wrong_probability_count"
    if any((not math.isfinite(value)) or value < 0 for value in probabilities):
        return None, sum(probabilities), "invalid_probability_range"
    total = sum(probabilities)
    if total <= 0:
        return None, total, "zero_probability_sum"
    return [value / total for value in probabilities], total, None


def prediction_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["job_id"], row["question"], row["model"])


def true_probabilities_for(question_name: str, truth: dict[str, Any], options: list[str]) -> dict[str, float]:
    marginal = truth.get("marginals", {}).get(question_name, {})
    weighted = {option: float(marginal.get(option, {}).get("weighted_count", 0.0)) for option in options}
    total = sum(weighted.values())
    if total <= 0:
        return {option: 0.0 for option in options}
    return {option: weighted[option] / total for option in options}


def probability_metrics(true_probs: list[float], predicted_probs: list[float]) -> dict[str, float]:
    epsilon = 1e-12
    errors = [predicted - actual for actual, predicted in zip(true_probs, predicted_probs)]
    return {
        "mae": sum(abs(error) for error in errors) / len(errors),
        "brier": sum(error * error for error in errors),
        "kl_divergence": sum(
            actual * math.log(max(actual, epsilon) / max(predicted, epsilon))
            for actual, predicted in zip(true_probs, predicted_probs)
            if actual > 0
        ),
    }
