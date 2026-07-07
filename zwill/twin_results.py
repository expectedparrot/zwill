from __future__ import annotations

import csv
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from typing import Any


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return slug or "item"


def model_label(service_name: str | None, model_name: str | None) -> str:
    if service_name and model_name:
        return f"{service_name}:{model_name}"
    return str(model_name or "")


def filter_prediction_rows(
    rows: list[dict[str, Any]],
    *,
    job_ids: set[str] | None = None,
    model: str | None = None,
    questions: set[str] | None = None,
) -> list[dict[str, Any]]:
    filtered = rows
    if job_ids:
        filtered = [row for row in filtered if row.get("job_id") in job_ids]
    if model:
        filtered = [
            row for row in filtered
            if row.get("model") == model or row.get("model_label") == model
        ]
    if questions:
        filtered = [row for row in filtered if row.get("heldout_question") in questions]
    return filtered


def job_ids_from_manifest(path: Path) -> list[str]:
    payload = json.loads(path.read_text())
    job_ids: list[str] = []
    for section in ("imports", "exports", "jobs"):
        rows = payload.get(section, []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("job_id"):
                job_id = str(row["job_id"])
                if job_id not in job_ids:
                    job_ids.append(job_id)
    if isinstance(payload, dict) and payload.get("job_id"):
        job_id = str(payload["job_id"])
        if job_id not in job_ids:
            job_ids.append(job_id)
    return job_ids


def top_prediction(probabilities: dict[str, float]) -> tuple[str | None, float | None]:
    if not probabilities:
        return None, None
    option, probability = max(probabilities.items(), key=lambda item: item[1])
    return option, probability


def twin_prediction_export_rows(rows: list[dict[str, Any]], export_format: str) -> list[dict[str, Any]]:
    output_rows = []
    for row in rows:
        probabilities = {str(option): float(value) for option, value in row.get("probabilities", {}).items()}
        top_choice, top_probability = top_prediction(probabilities)
        base = {
            "job_id": row.get("job_id"),
            "survey": row.get("survey"),
            "respondent_id": row.get("respondent_id"),
            "heldout_question": row.get("heldout_question"),
            "heldout_question_text": row.get("heldout_question_text"),
            "actual_answer": row.get("actual_answer"),
            "model": row.get("model"),
            "service": row.get("service"),
            "model_label": row.get("model_label") or model_label(row.get("service"), row.get("model")),
            "confidence": row.get("confidence"),
            "evidence_summary": row.get("evidence_summary"),
            "notes": row.get("notes"),
            "top_choice": top_choice,
            "top_probability": top_probability,
        }
        if export_format == "wide":
            wide = dict(base)
            for option in row.get("option_labels", list(probabilities)):
                wide[f"probability_{slugify(str(option))}"] = probabilities.get(str(option), 0.0)
            output_rows.append(wide)
        else:
            for index, option in enumerate(row.get("option_labels", list(probabilities))):
                option = str(option)
                output_rows.append(
                    {
                        **base,
                        "option_index": index,
                        "option_label": option,
                        "probability": probabilities.get(option, 0.0),
                    }
                )
    return output_rows


def write_csv_rows(path: Path | None, output_rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    for output_row in output_rows:
        for key in output_row:
            if key not in fieldnames:
                fieldnames.append(key)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(output_rows)
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)
    return fieldnames


def zip_csv(csv_path: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(csv_path, arcname=csv_path.name)


def aggregate_twin_marginals(
    rows: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Aggregate per-respondent twin distributions into an implied population marginal.

    Committed truth marginals are weighted by respondent weight, so the twin-implied
    marginal must be weighted the same way or every marginal L1/JS/Brier comparison
    against truth is biased by a pure weighting mismatch that has nothing to do with
    prediction quality. Pass `weights` (respondent_id -> weight); omit it to weight
    every respondent equally (the previous behaviour).
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        question = row.get("heldout_question")
        if not question:
            continue
        label = row.get("model_label") or model_label(row.get("service"), row.get("model"))
        grouped.setdefault((str(question), label), []).append(row)
    aggregates = {}
    for (question, label), group_rows in grouped.items():
        options = list(group_rows[0].get("option_labels", []))
        if not options:
            options = sorted({option for row in group_rows for option in row.get("probabilities", {})})
        totals = {str(option): 0.0 for option in options}
        weight_total = 0.0
        for row in group_rows:
            # Default missing weights to 1.0, matching how truth marginals treat
            # respondents without an explicit weight.
            weight = float(weights.get(str(row.get("respondent_id")), 1.0)) if weights else 1.0
            probabilities = row.get("probabilities", {})
            for option in options:
                totals[str(option)] += weight * float(probabilities.get(str(option), 0.0))
            weight_total += weight
        aggregates[(question, label)] = {
            "question": question,
            "question_text": group_rows[0].get("heldout_question_text"),
            "model_label": label,
            "respondent_count": len(group_rows),
            "weighted_respondents": weight_total,
            "options": [str(option) for option in options],
            "probabilities": (
                {option: totals[option] / weight_total for option in totals} if weight_total > 0 else totals
            ),
        }
    return aggregates


def distribution_distance_metrics(predicted: dict[str, float], target: dict[str, float]) -> dict[str, float]:
    epsilon = 1e-12
    options = sorted(set(predicted) | set(target))
    pred = [max(float(predicted.get(option, 0.0)), 0.0) for option in options]
    targ = [max(float(target.get(option, 0.0)), 0.0) for option in options]
    pred_total = sum(pred)
    targ_total = sum(targ)
    if pred_total > 0:
        pred = [value / pred_total for value in pred]
    if targ_total > 0:
        targ = [value / targ_total for value in targ]
    midpoint = [(p + t) / 2.0 for p, t in zip(pred, targ)]
    return {
        "l1": sum(abs(p - t) for p, t in zip(pred, targ)),
        "mae": sum(abs(p - t) for p, t in zip(pred, targ)) / len(options) if options else 0.0,
        "brier": sum((p - t) * (p - t) for p, t in zip(pred, targ)),
        "kl_target_to_predicted": sum(t * math.log(max(t, epsilon) / max(p, epsilon)) for p, t in zip(pred, targ) if t > 0),
        "kl_predicted_to_target": sum(p * math.log(max(p, epsilon) / max(t, epsilon)) for p, t in zip(pred, targ) if p > 0),
        "js_divergence": 0.5
        * (
            sum(t * math.log(max(t, epsilon) / max(m, epsilon)) for t, m in zip(targ, midpoint) if t > 0)
            + sum(p * math.log(max(p, epsilon) / max(m, epsilon)) for p, m in zip(pred, midpoint) if p > 0)
        ),
    }
