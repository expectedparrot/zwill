from __future__ import annotations

from collections import defaultdict
from typing import Any

from .twin_diagnostics import (
    build_twin_conditional_consistency_diagnostics,
    build_twin_joint_structure_diagnostics,
    build_twin_subgroup_marginal_diagnostics,
)
from .twin_results import distribution_distance_metrics, model_label


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def twin_top_prediction(row: dict[str, Any]) -> tuple[str | None, float]:
    predicted = row.get("probabilities", {})
    if not predicted:
        return None, 0.0
    # Break probability ties toward the alphabetically-first option, matching
    # one_hot_metrics' rank-1 tie-break, so the displayed top choice agrees with
    # the scored top1_correct.
    option, probability = min(predicted.items(), key=lambda item: (-float(item[1]), str(item[0])))
    return str(option), float(probability)


def top_probability_option(probabilities: dict[str, float]) -> tuple[str | None, float | None]:
    if not probabilities:
        return None, None
    option, probability = min(probabilities.items(), key=lambda item: (-float(item[1]), str(item[0])))
    return str(option), float(probability)


def weighted_row_mean(rows: list[dict[str, Any]], key: str, fallback_key: str | None = None) -> float | None:
    """Survey-weighted mean of row[key] (row["weight"] defaults to 1.0).

    With all-1.0 weights this equals the plain mean, so it is a no-op for
    unweighted surveys; genuine survey weights make it a population estimate.
    """
    total_weight = 0.0
    accumulated = 0.0
    for row in rows:
        value = row.get(key)
        if value is None and fallback_key is not None:
            value = row.get(fallback_key)
        if value is None:
            continue
        weight = float(row.get("weight", 1.0))
        accumulated += float(value) * weight
        total_weight += weight
    return accumulated / total_weight if total_weight > 0 else None


def summarize_twin_rows(model_rows: list[dict[str, Any]]) -> dict[str, Any]:
    marginal_rows = [
        row
        for row in model_rows
        if row.get("empirical_marginal_probability_actual", row.get("marginal_probability_actual")) is not None
    ]
    nll_values = [float(row["negative_log_likelihood"]) for row in model_rows]
    total_weight = sum(float(row.get("weight", 1.0)) for row in model_rows)
    mean_top_confidence = (
        sum(twin_top_prediction(row)[1] * float(row.get("weight", 1.0)) for row in model_rows) / total_weight
        if total_weight > 0
        else None
    )
    values = {
        "rows": len(model_rows),
        "mean_probability_actual": weighted_row_mean(model_rows, "probability_actual"),
        "mean_uniform_probability_actual": weighted_row_mean(model_rows, "uniform_probability_actual"),
        "mean_negative_log_likelihood": weighted_row_mean(model_rows, "negative_log_likelihood"),
        "negative_log_likelihood_p50": percentile(nll_values, 0.50),
        "negative_log_likelihood_p90": percentile(nll_values, 0.90),
        "negative_log_likelihood_p95": percentile(nll_values, 0.95),
        "negative_log_likelihood_max": max(nll_values),
        "mean_top_confidence": mean_top_confidence,
        "mean_uniform_negative_log_likelihood": weighted_row_mean(model_rows, "uniform_negative_log_likelihood"),
        "mean_brier": weighted_row_mean(model_rows, "brier"),
        "mean_uniform_brier": weighted_row_mean(model_rows, "uniform_brier"),
        "mean_brier_improvement": weighted_row_mean(model_rows, "brier_improvement"),
        "top1_accuracy": weighted_row_mean(model_rows, "top1_correct"),
    }
    if marginal_rows:
        mean_empirical_marginal_probability_actual = weighted_row_mean(
            marginal_rows, "empirical_marginal_probability_actual", "marginal_probability_actual"
        )
        mean_empirical_marginal_negative_log_likelihood = weighted_row_mean(
            marginal_rows, "empirical_marginal_negative_log_likelihood", "marginal_negative_log_likelihood"
        )
        mean_empirical_marginal_brier = weighted_row_mean(
            marginal_rows, "empirical_marginal_brier", "marginal_brier"
        )
        empirical_marginal_top1_accuracy = weighted_row_mean(
            marginal_rows, "empirical_marginal_top1_correct", "marginal_top1_correct"
        )
        values.update(
            {
                "mean_empirical_marginal_probability_actual": mean_empirical_marginal_probability_actual,
                "mean_empirical_marginal_negative_log_likelihood": mean_empirical_marginal_negative_log_likelihood,
                "mean_empirical_marginal_brier": mean_empirical_marginal_brier,
                "empirical_marginal_top1_accuracy": empirical_marginal_top1_accuracy,
                "mean_marginal_probability_actual": mean_empirical_marginal_probability_actual,
                "mean_marginal_negative_log_likelihood": mean_empirical_marginal_negative_log_likelihood,
                "mean_marginal_brier": mean_empirical_marginal_brier,
                "marginal_top1_accuracy": empirical_marginal_top1_accuracy,
            }
        )
    return values


def build_twin_calibration(model_rows: list[dict[str, Any]], bins: int = 10) -> tuple[list[dict[str, Any]], float]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in model_rows:
        confidence = twin_top_prediction(row)[1]
        index = min(bins - 1, int(confidence * bins))
        grouped[index].append(row)
    calibration = []
    ece = 0.0
    for index in range(bins):
        bin_rows = grouped.get(index, [])
        low = index / bins
        high = (index + 1) / bins
        mean_confidence = (
            sum(twin_top_prediction(row)[1] for row in bin_rows) / len(bin_rows)
            if bin_rows
            else None
        )
        # Average top-1 accuracy over the rows that were scored against an actual
        # answer; a row missing top1_correct (e.g. imported without an actual
        # answer) is skipped rather than crashing the report.
        top1_values = [row["top1_correct"] for row in bin_rows if row.get("top1_correct") is not None]
        accuracy = sum(top1_values) / len(top1_values) if top1_values else None
        if bin_rows and mean_confidence is not None and accuracy is not None:
            ece += (len(bin_rows) / len(model_rows)) * abs(accuracy - mean_confidence)
        calibration.append(
            {
                "bin": f"{low:.1f}-{high:.1f}",
                "low": low,
                "high": high,
                "rows": len(bin_rows),
                "mean_confidence": mean_confidence,
                "accuracy": accuracy,
            }
        )
    return calibration, ece


def build_twin_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, dict[str, Any]] = {}
    by_question: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_question_model: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    job_ids = {str(row.get("job_id")) for row in rows if row.get("job_id")}
    multiple_jobs = len(job_ids) > 1
    for row in rows:
        label = row.get("model_label") or model_label(row.get("service"), row.get("model"))
        row["model_label"] = label
        twin_set_label = f"{row.get('job_id')} / {label}" if multiple_jobs and row.get("job_id") else label
        row["twin_set_label"] = twin_set_label
        by_model[twin_set_label].append(row)
        by_question_model[(row["heldout_question"], twin_set_label)].append(row)
    for model, model_rows in by_model.items():
        summary[model] = summarize_twin_rows(model_rows)
    for (question, model), model_rows in by_question_model.items():
        by_question[question][model] = summarize_twin_rows(model_rows)
    calibration_by_model = {}
    ece_by_model = {}
    for model, model_rows in by_model.items():
        calibration, ece = build_twin_calibration(model_rows)
        calibration_by_model[model] = calibration
        ece_by_model[model] = ece

    marginal_comparisons = []
    marginal_option_rows = []
    aggregate_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("heldout_question"):
            aggregate_groups[(str(row.get("heldout_question")), str(row.get("twin_set_label")))].append(row)
    for (question, label), group_rows in aggregate_groups.items():
        options = list(group_rows[0].get("option_labels", []))
        if not options:
            options = sorted({option for row in group_rows for option in row.get("probabilities", {})})
        totals = {str(option): 0.0 for option in options}
        for row in group_rows:
            for option in options:
                totals[str(option)] += float(row.get("probabilities", {}).get(str(option), 0.0))
        predicted = {option: totals[option] / len(group_rows) for option in totals} if group_rows else totals
        target = None
        for row in by_question_model.get((question, label), []):
            candidate = row.get("empirical_marginal_probabilities") or row.get("marginal_probabilities")
            if candidate:
                target = {str(option): float(value) for option, value in candidate.items()}
                break
        predicted_top, predicted_top_probability = top_probability_option(predicted)
        target_top = None
        target_top_probability = None
        metrics = {}
        if target:
            target_top, target_top_probability = top_probability_option(target)
            metrics = distribution_distance_metrics(predicted, target)
        comparison = {
            "heldout_question": question,
            "heldout_question_text": group_rows[0].get("heldout_question_text") if group_rows else None,
            "model_label": label,
            "job_id": group_rows[0].get("job_id") if group_rows else None,
            "respondent_count": len(group_rows),
            "predicted_top_option": predicted_top,
            "predicted_top_probability": predicted_top_probability,
            "target_top_option": target_top,
            "target_top_probability": target_top_probability,
            "top_option_agrees": int(predicted_top == target_top) if target_top is not None else None,
            **metrics,
        }
        marginal_comparisons.append(comparison)
        for option in options or sorted(set(predicted) | set(target or {})):
            option = str(option)
            predicted_probability = predicted.get(option, 0.0)
            target_probability = target.get(option, 0.0) if target else None
            marginal_option_rows.append(
                {
                    "heldout_question": question,
                    "heldout_question_text": group_rows[0].get("heldout_question_text") if group_rows else None,
                    "model_label": label,
                    "job_id": group_rows[0].get("job_id") if group_rows else None,
                    "option_label": option,
                    "predicted_probability": predicted_probability,
                    "target_probability": target_probability,
                    "difference": predicted_probability - target_probability if target_probability is not None else None,
                    "abs_difference": abs(predicted_probability - target_probability) if target_probability is not None else None,
                }
            )
    marginal_comparisons.sort(
        key=lambda item: (
            item.get("l1") is None,
            -(item.get("l1") or 0.0),
            str(item.get("heldout_question")),
            str(item.get("model_label")),
        )
    )

    diagnostics = {
        "calibration": calibration_by_model,
        "expected_calibration_error": ece_by_model,
        "summary_by_question": by_question,
        "marginal_comparisons": marginal_comparisons,
        "marginal_options": marginal_option_rows,
        "worst_misses": sorted(
            rows,
            key=lambda row: (
                row.get("top1_correct", 0),
                row.get("probability_actual", 0.0),
                -row.get("negative_log_likelihood", 0.0),
            ),
        )[:20],
        "baseline_comparison": {},
        "empirical_wins": [],
        "model_wins": [],
        "overconfident_misses": [],
        "confusion": {},
    }
    diagnostics["overconfident_misses"] = sorted(
        [row for row in rows if not row.get("top1_correct")],
        key=lambda row: (-twin_top_prediction(row)[1], row.get("probability_actual", 0.0)),
    )[:20]
    confusion: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for row in rows:
        predicted_option, _ = twin_top_prediction(row)
        key = f"{row.get('heldout_question')}::{row.get('twin_set_label', row.get('model_label'))}"
        confusion[key][str(row.get("actual_answer"))][str(predicted_option)] += 1
    diagnostics["confusion"] = {
        key: {actual: dict(predicted) for actual, predicted in actuals.items()}
        for key, actuals in confusion.items()
    }
    for model, values in summary.items():
        values["expected_calibration_error"] = ece_by_model.get(model)
        empirical_nll = values.get("mean_empirical_marginal_negative_log_likelihood", values.get("mean_marginal_negative_log_likelihood"))
        empirical_brier = values.get("mean_empirical_marginal_brier", values.get("mean_marginal_brier"))
        empirical_p = values.get("mean_empirical_marginal_probability_actual", values.get("mean_marginal_probability_actual"))
        diagnostics["baseline_comparison"][model] = {
            "p_actual_vs_uniform": values["mean_probability_actual"] - values["mean_uniform_probability_actual"],
            "nll_vs_uniform": values["mean_uniform_negative_log_likelihood"] - values["mean_negative_log_likelihood"],
            "brier_vs_uniform": values["mean_uniform_brier"] - values["mean_brier"],
            "p_actual_vs_empirical": values["mean_probability_actual"] - empirical_p if empirical_p is not None else None,
            "nll_vs_empirical": empirical_nll - values["mean_negative_log_likelihood"] if empirical_nll is not None else None,
            "brier_vs_empirical": empirical_brier - values["mean_brier"] if empirical_brier is not None else None,
        }
    for (question, model), model_rows in by_question_model.items():
        values = summarize_twin_rows(model_rows)
        empirical_nll = values.get("mean_empirical_marginal_negative_log_likelihood", values.get("mean_marginal_negative_log_likelihood"))
        if empirical_nll is None:
            continue
        item = {
            "heldout_question": question,
            "model": model,
            "rows": values["rows"],
            "model_nll": values["mean_negative_log_likelihood"],
            "empirical_nll": empirical_nll,
            "nll_vs_empirical": empirical_nll - values["mean_negative_log_likelihood"],
        }
        if item["nll_vs_empirical"] >= 0:
            diagnostics["model_wins"].append(item)
        else:
            diagnostics["empirical_wins"].append(item)
    diagnostics["model_wins"].sort(key=lambda item: item["nll_vs_empirical"], reverse=True)
    diagnostics["empirical_wins"].sort(key=lambda item: item["nll_vs_empirical"])
    diagnostics["joint_structure"] = build_twin_joint_structure_diagnostics(rows)
    diagnostics["subgroup_marginals"] = build_twin_subgroup_marginal_diagnostics(rows)
    diagnostics["conditional_consistency"] = build_twin_conditional_consistency_diagnostics(rows)
    return {"rows": rows, "summary": summary, "summary_by_question": by_question, "diagnostics": diagnostics}
