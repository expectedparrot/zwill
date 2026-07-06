from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .probability import parse_probability_json


def rank_twin_jobs_dir(sdir: Path) -> Path:
    return sdir / "rank_twin_jobs"


def rank_twin_predictions_path(sdir: Path) -> Path:
    return sdir / "rank_twin_predictions.jsonl"


def rank_task_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text).lower()).strip("_")
    return slug or "rank_task"


def is_numeric_rank_options(options: list[Any]) -> bool:
    if len(options) < 2:
        return False
    values = []
    for option in options:
        text = str(option).strip()
        match = re.match(r"^(\d+)(?:\D.*)?$", text)
        if not match:
            return False
        values.append(int(match.group(1)))
    return len(set(values)) == len(values) and min(values) == 1 and max(values) == len(values)


def split_rank_item_text(question_text: str) -> tuple[str, str] | None:
    if " - " not in question_text:
        return None
    stem, item = question_text.rsplit(" - ", 1)
    stem = stem.strip()
    item = item.strip()
    if not stem or not item:
        return None
    return stem, item


def detect_rank_tasks(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        options = question.get("question_options") or []
        if not is_numeric_rank_options(options):
            continue
        split = split_rank_item_text(str(question.get("question_text") or ""))
        if not split:
            continue
        stem, item_label = split
        stem_lower = stem.lower()
        source_name = str(question.get("question_name") or "")
        looks_like_rank = (
            "rank" in stem_lower
            or "most appealing" in stem_lower
            or re.search(r"q0?(11|21)_.*_\d+$", source_name.lower()) is not None
        )
        if not looks_like_rank:
            continue
        row = dict(question)
        row["_rank_stem"] = stem
        row["_rank_item_label"] = item_label
        groups[stem].append(row)

    tasks = []
    for stem, rows in groups.items():
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda row: natural_item_sort_key(row.get("question_name")))
        task_id = rank_task_slug(common_rank_task_id(rows, stem))
        items = [
            {
                "item_id": row["question_name"],
                "label": row["_rank_item_label"],
                "source_question_text": row.get("question_text"),
            }
            for row in rows
        ]
        tasks.append(
            {
                "rank_task_id": task_id,
                "rank_task_text": stem,
                "rank_direction": "1_is_best",
                "items": items,
                "source_question_names": [item["item_id"] for item in items],
                "item_count": len(items),
            }
        )
    return sorted(tasks, key=lambda task: task["rank_task_id"])


def annotate_rank_items(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tasks = detect_rank_tasks(questions)
    task_by_item = {
        item["item_id"]: task
        for task in tasks
        for item in task.get("items", [])
    }
    item_by_id = {
        item["item_id"]: item
        for task in tasks
        for item in task.get("items", [])
    }
    annotated = []
    for question in questions:
        item_id = question.get("question_name")
        task = task_by_item.get(item_id)
        if not task:
            annotated.append(question)
            continue
        item = item_by_id[item_id]
        updated = dict(question)
        updated["question_type"] = "rank_item"
        updated["rank_task_id"] = task["rank_task_id"]
        updated["rank_task_text"] = task["rank_task_text"]
        updated["rank_direction"] = task["rank_direction"]
        updated["rank_item_label"] = item["label"]
        updated["rank_item_count"] = task["item_count"]
        updated.setdefault("source", {})
        if isinstance(updated["source"], dict):
            note = updated["source"].get("note")
            rank_note = f"Rank battery item for {task['rank_task_id']}."
            updated["source"]["note"] = f"{note} {rank_note}".strip() if note else rank_note
        annotated.append(updated)
    return annotated, tasks


def synthetic_rank_questions(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "question_name": task["rank_task_id"],
            "question_type": "rank",
            "question_text": task["rank_task_text"],
            "question_options": [item["label"] for item in task["items"]],
            "rank_direction": task["rank_direction"],
            "source": {
                "note": "Synthetic joint rank question collapsed from item-level rank columns.",
                "source_question_names": task["source_question_names"],
            },
        }
        for task in tasks
    ]


def common_rank_task_id(rows: list[dict[str, Any]], fallback: str) -> str:
    names = [str(row.get("question_name") or "") for row in rows]
    if not names:
        return fallback
    middle_tokens = []
    for name in names:
        match = re.match(r"^q\d+_(.+)_\d+$", name, flags=re.IGNORECASE)
        if not match:
            middle_tokens = []
            break
        middle_tokens.append(match.group(1))
    if middle_tokens and len(set(middle_tokens)) == 1:
        return middle_tokens[0]
    tokenized = [re.sub(r"_\d+$", "", name) for name in names]
    if len(set(tokenized)) == 1:
        return tokenized[0]
    prefix = names[0]
    for name in names[1:]:
        while prefix and not name.startswith(prefix):
            prefix = prefix[:-1]
    return prefix.strip("_") or fallback


def natural_item_sort_key(value: Any) -> tuple[str, int, str]:
    text = str(value or "")
    match = re.search(r"(\d+)(?!.*\d)", text)
    return (text[: match.start()] if match else text, int(match.group(1)) if match else 10**9, text)


def selected_rank_tasks(args: Any, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    task_ids = []
    for value in getattr(args, "rank_task_id", None) or []:
        task_ids.append(str(value))
    if getattr(args, "rank_task_ids", None):
        task_ids.extend(value.strip() for value in str(args.rank_task_ids).split(",") if value.strip())
    heldout_items = []
    for value in getattr(args, "heldout_question", None) or []:
        heldout_items.append(str(value))
    if getattr(args, "heldout_questions", None):
        heldout_items.extend(value.strip() for value in str(args.heldout_questions).split(",") if value.strip())

    if not task_ids and not heldout_items:
        raise ValueError("at least one rank task or held-out rank item is required")

    selected = []
    unknown_tasks = []
    for task_id in task_ids:
        match = next((task for task in tasks if task["rank_task_id"] == task_id), None)
        if match is None:
            unknown_tasks.append(task_id)
        elif match not in selected:
            selected.append(match)
    unknown_items = []
    for item_id in heldout_items:
        matches = [task for task in tasks if item_id in task.get("source_question_names", [])]
        if not matches:
            unknown_items.append(item_id)
            continue
        for match in matches:
            if match not in selected:
                selected.append(match)
    if unknown_tasks or unknown_items:
        available = {
            task["rank_task_id"]: task.get("source_question_names", [])
            for task in tasks
        }
        raise ValueError(json.dumps({"unknown_tasks": unknown_tasks, "unknown_items": unknown_items, "available": available}))
    return selected


def rank_job_id_from_job(job: dict[str, Any]) -> str:
    payload = {
        "survey": job.get("survey", {}),
        "scenarios": job.get("scenarios", []),
        "models": job.get("models", []),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def rank_job_id_from_results(results: dict[str, Any]) -> str:
    payload = [
        {
            "scenario": row.get("scenario", {}),
            "model": row.get("model", {}),
            "answer_keys": sorted((row.get("answer") or {}).keys()) if isinstance(row.get("answer"), dict) else [],
        }
        for row in results.get("data", [])
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def extract_rank_payload(row: dict[str, Any], question_name: str = "rank_utility_scores") -> tuple[dict[str, float] | None, float | None, str | None, str | None]:
    answer = row.get("answer", {})
    raw_answer = None
    if isinstance(answer, dict):
        raw_answer = answer.get(question_name)
        if raw_answer is None and answer:
            raw_answer = next(iter(answer.values()))
    parsed, error = parse_probability_json(raw_answer)
    if error:
        return None, None, None, error
    scores = parsed.get("scores") if isinstance(parsed, dict) else None
    if not isinstance(scores, dict):
        return None, parsed.get("confidence") if isinstance(parsed, dict) else None, parsed.get("notes") if isinstance(parsed, dict) else None, "missing_scores"
    normalized_scores = {}
    for key, value in scores.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None, parsed.get("confidence"), parsed.get("notes"), f"invalid_score:{key}"
        if not math.isfinite(numeric):
            return None, parsed.get("confidence"), parsed.get("notes"), f"invalid_score:{key}"
        normalized_scores[str(key)] = max(0.0, min(100.0, numeric))
    return normalized_scores, parsed.get("confidence"), parsed.get("notes"), None


def ranks_from_scores(scores: dict[str, float], item_ids: list[str]) -> dict[str, int]:
    ordered = sorted(item_ids, key=lambda item_id: (-float(scores.get(item_id, float("-inf"))), item_id))
    return {item_id: index + 1 for index, item_id in enumerate(ordered)}


def spearman(actual: dict[str, int], predicted: dict[str, int], item_ids: list[str]) -> float | None:
    n = len(item_ids)
    if n < 2:
        return None
    mean = (n + 1) / 2
    numerator = sum((actual[item] - mean) * (predicted[item] - mean) for item in item_ids)
    denominator_left = sum((actual[item] - mean) ** 2 for item in item_ids)
    denominator_right = sum((predicted[item] - mean) ** 2 for item in item_ids)
    denominator = math.sqrt(denominator_left * denominator_right)
    return numerator / denominator if denominator else None


def pairwise_order_accuracy(actual: dict[str, int], predicted: dict[str, int], item_ids: list[str]) -> tuple[float | None, int, int]:
    correct = 0
    total = 0
    for left_index, left in enumerate(item_ids):
        for right in item_ids[left_index + 1 :]:
            actual_order = actual[left] - actual[right]
            predicted_order = predicted[left] - predicted[right]
            if actual_order == 0 or predicted_order == 0:
                continue
            total += 1
            if actual_order * predicted_order > 0:
                correct += 1
    return (correct / total if total else None, correct, total)


def top_k_overlap(actual: dict[str, int], predicted: dict[str, int], item_ids: list[str], k: int = 3) -> float | None:
    if not item_ids:
        return None
    k = min(k, len(item_ids))
    actual_top = {item for item in item_ids if actual[item] <= k}
    predicted_top = {item for item in item_ids if predicted[item] <= k}
    return len(actual_top & predicted_top) / k if k else None


def rank_metrics(actual: dict[str, int], scores: dict[str, float], item_ids: list[str]) -> dict[str, Any]:
    predicted = ranks_from_scores(scores, item_ids)
    pair_acc, pair_correct, pair_total = pairwise_order_accuracy(actual, predicted, item_ids)
    top1_actual = min(item_ids, key=lambda item: (actual[item], item)) if item_ids else None
    top1_predicted = min(item_ids, key=lambda item: (predicted[item], item)) if item_ids else None
    return {
        "predicted_ranks": predicted,
        "spearman": spearman(actual, predicted, item_ids),
        "pairwise_order_accuracy": pair_acc,
        "pairwise_correct": pair_correct,
        "pairwise_total": pair_total,
        "top_1_hit": int(top1_actual == top1_predicted) if top1_actual and top1_predicted else None,
        "top_3_overlap": top_k_overlap(actual, predicted, item_ids, 3),
        "mean_absolute_rank_error": (
            sum(abs(predicted[item] - actual[item]) for item in item_ids) / len(item_ids)
            if item_ids
            else None
        ),
        "score_spread": (max(scores.values()) - min(scores.values())) if scores else None,
        "tie_count": len(scores) - len(set(scores.values())),
    }


def mean(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def build_rank_report(rows: list[dict[str, Any]], job_id: str | None = None) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_model[str(row.get("model_label") or row.get("model") or "")].append(row)
        by_task[str(row.get("rank_task_id") or "")].append(row)
    summary = {
        "row_count": len(rows),
        "respondent_count": len({row.get("respondent_id") for row in rows}),
        "task_count": len(by_task),
        "model_count": len(by_model),
        "by_model": {
            model: {
                "rows": len(model_rows),
                "mean_spearman": mean([row.get("spearman") for row in model_rows]),
                "mean_pairwise_order_accuracy": mean([row.get("pairwise_order_accuracy") for row in model_rows]),
                "mean_top_3_overlap": mean([row.get("top_3_overlap") for row in model_rows]),
                "mean_absolute_rank_error": mean([row.get("mean_absolute_rank_error") for row in model_rows]),
                "top_1_hit_rate": mean([row.get("top_1_hit") for row in model_rows]),
            }
            for model, model_rows in sorted(by_model.items())
        },
        "by_task": {
            task: {
                "rows": len(task_rows),
                "item_count": task_rows[0].get("item_count") if task_rows else None,
                "mean_spearman": mean([row.get("spearman") for row in task_rows]),
                "mean_pairwise_order_accuracy": mean([row.get("pairwise_order_accuracy") for row in task_rows]),
                "mean_top_3_overlap": mean([row.get("top_3_overlap") for row in task_rows]),
                "mean_absolute_rank_error": mean([row.get("mean_absolute_rank_error") for row in task_rows]),
            }
            for task, task_rows in sorted(by_task.items())
        },
    }
    item_rows = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for item in row.get("items", []):
            grouped[(str(row.get("rank_task_id")), str(item.get("item_id")))].append({**row, "item": item})
    for (task_id, item_id), item_group in sorted(grouped.items()):
        labels = [entry["item"].get("label") for entry in item_group if entry["item"].get("label")]
        actual_ranks = [entry.get("actual_ranks", {}).get(item_id) for entry in item_group]
        predicted_ranks = [entry.get("predicted_ranks", {}).get(item_id) for entry in item_group]
        predicted_scores = [entry.get("predicted_scores", {}).get(item_id) for entry in item_group]
        item_rows.append(
            {
                "rank_task_id": task_id,
                "item_id": item_id,
                "label": labels[0] if labels else item_id,
                "rows": len(item_group),
                "mean_actual_rank": mean(actual_ranks),
                "mean_predicted_rank": mean(predicted_ranks),
                "mean_predicted_score": mean(predicted_scores),
                "mean_rank_error": mean(
                    [
                        (predicted - actual)
                        for predicted, actual in zip(predicted_ranks, actual_ranks)
                        if predicted is not None and actual is not None
                    ]
                ),
            }
        )
    return {"job_id": job_id, "rows": rows, "summary": summary, "items": item_rows}
