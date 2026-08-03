from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
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


# Rank-ish wording used by the fallback heuristic (numeric 1..N options plus one
# of these phrases in the stem). Kept as a small, explicit, survey-agnostic list
# -- prefer declaring `rank_task_id` on the rows to avoid the heuristic entirely.
RANK_STEM_PHRASES = (
    "rank",
    "ranking",
    "most appealing",
    "least appealing",
    "how appealing",
    "in order of",
    "order these",
    "order the following",
)


def stem_looks_like_rank(stem: str) -> bool:
    stem_lower = stem.lower()
    return any(phrase in stem_lower for phrase in RANK_STEM_PHRASES)


def _rank_item_label(question: dict[str, Any], split_label: str | None) -> str:
    declared = question.get("rank_item_label")
    if declared:
        return str(declared)
    if split_label:
        return split_label
    return str(question.get("question_text") or question.get("question_name") or "")


def _build_rank_task(task_id: str, stem: str, rows: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    rows = sorted(rows, key=lambda row: natural_item_sort_key(row.get("question_name")))
    items = [
        {
            "item_id": row["question_name"],
            "label": row["_rank_item_label"],
            "source_question_text": row.get("question_text"),
        }
        for row in rows
    ]
    return {
        "rank_task_id": task_id,
        "rank_task_text": stem,
        "rank_direction": direction,
        "items": items,
        "source_question_names": [item["item_id"] for item in items],
        "item_count": len(items),
    }


def detect_rank_tasks(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Merge two grouping signals into a single task keyed by a canonical id:
    #  1. Explicit `rank_task_id` on the rows (robust, recommended).
    #  2. Heuristic fallback: numeric 1..N options AND rank-ish stem wording (no
    #     survey-specific column-name regex).
    # Both run over ALL questions so a battery is grouped consistently even while
    # it is only partially annotated (e.g. items added one at a time), and items
    # explicitly claimed under one id never leak into a heuristic group.
    merged: dict[str, dict[str, Any]] = {}
    claimed: dict[str, str] = {}  # question_name -> explicit task_id

    def bucket_for(task_id: str, stem: str, direction: str) -> dict[str, Any]:
        entry = merged.setdefault(task_id, {"items": {}, "stem": stem, "direction": direction})
        if stem and not entry["stem"]:
            entry["stem"] = stem
        return entry

    # 1. Explicit declarations.
    explicit_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        declared = str(question.get("rank_task_id") or "").strip()
        if declared:
            explicit_groups[declared].append(question)
    for declared, group in explicit_groups.items():
        task_id = rank_task_slug(declared)
        for question in group:
            split = split_rank_item_text(str(question.get("question_text") or ""))
            _, split_label = split if split else (None, None)
            row = dict(question)
            row["_rank_item_label"] = _rank_item_label(question, split_label)
            stem = str(question.get("rank_task_text") or (split[0] if split else "")) or declared
            direction = str(question.get("rank_direction") or "1_is_best")
            entry = bucket_for(task_id, stem, direction)
            entry["items"].setdefault(row["question_name"], row)
            claimed[str(row["question_name"])] = task_id

    # 2. Heuristic groups by stem, over all questions.
    heuristic_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        if not is_numeric_rank_options(question.get("question_options") or []):
            continue
        split = split_rank_item_text(str(question.get("question_text") or ""))
        if not split:
            continue
        stem, item_label = split
        if not stem_looks_like_rank(stem):
            continue
        row = dict(question)
        row["_rank_item_label"] = item_label
        heuristic_groups[stem].append(row)
    for stem, group in heuristic_groups.items():
        task_id = rank_task_slug(common_rank_task_id(group, stem))
        entry = bucket_for(task_id, stem, "1_is_best")
        for row in group:
            name = str(row["question_name"])
            # An item explicitly claimed under a different id stays with its
            # explicit task; don't duplicate it into a heuristic group.
            if claimed.get(name, task_id) != task_id:
                continue
            entry["items"].setdefault(name, row)

    tasks = []
    for task_id, entry in merged.items():
        rows = list(entry["items"].values())
        if len(rows) < 2:
            continue
        tasks.append(_build_rank_task(task_id, entry["stem"] or task_id, rows, entry["direction"]))
    return sorted(tasks, key=lambda task: task["rank_task_id"])


def potential_undetected_rank_batteries(
    questions: list[dict[str, Any]], tasks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Groups of numeric-option, item-shaped questions (`q<n>_<mid>_<k>`) that the
    heuristic did NOT group into a battery -- likely a missed rank battery the user
    should declare with an explicit `rank_task_id`."""
    grouped_items = {name for task in tasks for name in task.get("source_question_names", [])}
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for question in questions:
        name = str(question.get("question_name") or "")
        if name in grouped_items:
            continue
        if not is_numeric_rank_options(question.get("question_options") or []):
            continue
        match = re.match(r"^(q\d+_.+)_\d+$", name, flags=re.IGNORECASE)
        if match:
            by_prefix[match.group(1)].append(name)
    return [
        {"suspected_prefix": prefix, "question_names": sorted(names, key=natural_item_sort_key)}
        for prefix, names in by_prefix.items()
        if len(names) >= 2
    ]


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
    # With k or fewer items every item is trivially in the top-k, so the overlap
    # is vacuously 1.0 and carries no signal -- e.g. a top-N/MaxDiff battery
    # scored on only the items a respondent ranked. Report it as N/A instead of a
    # misleading perfect score.
    if not item_ids or len(item_ids) <= k:
        return None
    actual_top = {item for item in item_ids if actual[item] <= k}
    predicted_top = {item for item in item_ids if predicted[item] <= k}
    return len(actual_top & predicted_top) / k


def top_k_identification(
    actual: dict[str, int], scores: dict[str, float], full_item_ids: list[str]
) -> float | None:
    """Did the twin identify the respondent's stated top-K items?

    For a top-N / MaxDiff battery a respondent ranks only their K most important
    items out of the full set. The internal-ordering metrics (spearman, pairwise)
    are scored on just those K items and so presume you already know which K the
    respondent chose. This instead scores identification: rank ALL battery items
    by the twin's predicted utility, take the predicted top-K, and measure the
    overlap with the K items the respondent actually ranked.

    Returns None for a full ranking (K == item count), where identification is
    vacuous and top_k_overlap already applies.
    """
    ranked = [item_id for item_id in full_item_ids if item_id in actual]
    k = len(ranked)
    total = len(full_item_ids)
    if k == 0 or k >= total:
        return None
    predicted = ranks_from_scores(scores, full_item_ids)
    predicted_top_k = {item_id for item_id in full_item_ids if predicted[item_id] <= k}
    return len(predicted_top_k & set(ranked)) / k


def top_k_identification_chance(actual: dict[str, int], full_item_ids: list[str]) -> float | None:
    """Expected top-K identification from picking K of N items at random: K / N.

    Matches the None cases of top_k_identification so the two align row-for-row.
    """
    ranked = [item_id for item_id in full_item_ids if item_id in actual]
    k = len(ranked)
    total = len(full_item_ids)
    if k == 0 or k >= total:
        return None
    return k / total


def rank_metrics(
    actual: dict[str, int],
    scores: dict[str, float],
    item_ids: list[str],
    *,
    full_item_ids: list[str] | None = None,
) -> dict[str, Any]:
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
        # Over the FULL battery, not just the ranked subset: did the twin's
        # predicted top-K catch the respondent's actual top-K? (top-N tasks only.)
        # Chance is K/N (picking K of N items at random), so the metric is
        # self-interpreting: above chance means real item-identification signal.
        "top_k_identification": top_k_identification(actual, scores, full_item_ids or item_ids),
        "top_k_identification_chance": top_k_identification_chance(actual, full_item_ids or item_ids),
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


def weighted_metric_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    """Survey-weighted mean of rows[key] (row['weight'] defaults to 1.0).

    Skips rows whose metric is None (e.g. spearman on a single-item ranking).
    With all-1.0 weights this equals the plain mean, so unweighted surveys are
    unaffected; genuine weights make the rank metrics population estimates.
    """
    total_weight = 0.0
    accumulated = 0.0
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        weight = float(row.get("weight", 1.0) or 1.0)
        accumulated += float(value) * weight
        total_weight += weight
    return accumulated / total_weight if total_weight else None


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
                "mean_spearman": weighted_metric_mean(model_rows, "spearman"),
                "mean_pairwise_order_accuracy": weighted_metric_mean(model_rows, "pairwise_order_accuracy"),
                "mean_top_3_overlap": weighted_metric_mean(model_rows, "top_3_overlap"),
                "mean_top_k_identification": weighted_metric_mean(model_rows, "top_k_identification"),
                "mean_top_k_identification_chance": weighted_metric_mean(model_rows, "top_k_identification_chance"),
                "mean_absolute_rank_error": weighted_metric_mean(model_rows, "mean_absolute_rank_error"),
                "top_1_hit_rate": weighted_metric_mean(model_rows, "top_1_hit"),
            }
            for model, model_rows in sorted(by_model.items())
        },
        "by_task": {
            task: {
                "rows": len(task_rows),
                "item_count": task_rows[0].get("item_count") if task_rows else None,
                "mean_spearman": weighted_metric_mean(task_rows, "spearman"),
                "mean_pairwise_order_accuracy": weighted_metric_mean(task_rows, "pairwise_order_accuracy"),
                "mean_top_3_overlap": weighted_metric_mean(task_rows, "top_3_overlap"),
                "mean_top_k_identification": weighted_metric_mean(task_rows, "top_k_identification"),
                "mean_top_k_identification_chance": weighted_metric_mean(task_rows, "top_k_identification_chance"),
                "mean_absolute_rank_error": weighted_metric_mean(task_rows, "mean_absolute_rank_error"),
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
