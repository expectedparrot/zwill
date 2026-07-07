"""Bootstrap confidence intervals for digital-twin validation scores.

Point estimates like "mean p(actual) = 0.31 vs 0.20" hide sampling noise, and the
twin workflow encourages trying many models and questions -- a garden-of-forking-
paths where the "best" is partly luck. Respondents are the natural independent
unit, so this module resamples respondents with replacement to put confidence
intervals on:

* each model's mean score, per held-out question and macro-averaged, and
* the *paired* difference between a model and a chosen baseline, so a headline
  like "twin - baseline = +0.116 [0.09, 0.14]" states whether the gap is real.

The paired delta resamples the respondents that both models scored, so it controls
for which respondents happened to be in the sample.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np


def _stable_offset(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)

# Higher-is-better for p(actual)/accuracy; lower-is-better for NLL/Brier. The CI
# machinery is direction-agnostic; direction only matters when reading deltas.
DEFAULT_METRICS = (
    "probability_actual",
    "negative_log_likelihood",
    "brier",
    "top1_correct",
)


def _percentile_ci(samples: np.ndarray, ci: float) -> tuple[float, float]:
    lo = (1.0 - ci) / 2.0 * 100.0
    hi = (1.0 + ci) / 2.0 * 100.0
    return float(np.percentile(samples, lo)), float(np.percentile(samples, hi))


def _group_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """Group prediction rows by (model_label, question) -> {respondent_id: row}."""
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        label = str(row.get("model_label") or row.get("model") or "")
        question = str(row.get("heldout_question") or "")
        respondent_id = str(row.get("respondent_id") or "")
        if not label or not question or not respondent_id:
            continue
        grouped.setdefault((label, question), {})[respondent_id] = row
    return grouped


def _metric_array(rowmap: dict[str, dict[str, Any]], respondent_ids: list[str], metric: str) -> np.ndarray:
    return np.asarray([float(rowmap[rid].get(metric) or 0.0) for rid in respondent_ids], dtype=float)


def _bootstrap_means(values: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Mean of `values` under each bootstrap resample (indices shape: n_boot x n)."""
    return values[indices].mean(axis=1)


def bootstrap_summary(
    rows: list[dict[str, Any]],
    *,
    baseline_model: str | None = None,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
    n_boot: int = 1000,
    seed: int = 0,
    ci: float = 0.95,
) -> dict[str, Any]:
    grouped = _group_rows(rows)
    models = sorted({label for (label, _question) in grouped})
    questions = sorted({question for (_label, question) in grouped})
    rng = np.random.default_rng(seed)

    def model_block(label: str) -> dict[str, Any]:
        per_question: dict[str, Any] = {}
        macro_samples: dict[str, list[np.ndarray]] = {metric: [] for metric in metrics}
        macro_point: dict[str, list[float]] = {metric: [] for metric in metrics}
        for question in questions:
            rowmap = grouped.get((label, question))
            if not rowmap:
                continue
            respondent_ids = sorted(rowmap)
            n = len(respondent_ids)
            indices = rng.integers(0, n, size=(n_boot, n))
            metric_block: dict[str, Any] = {}
            for metric in metrics:
                values = _metric_array(rowmap, respondent_ids, metric)
                samples = _bootstrap_means(values, indices)
                lo, hi = _percentile_ci(samples, ci)
                metric_block[metric] = {"mean": float(values.mean()), "lo": lo, "hi": hi, "n": n}
                macro_samples[metric].append(samples)
                macro_point[metric].append(float(values.mean()))
            per_question[question] = metric_block
        macro: dict[str, Any] = {}
        for metric in metrics:
            if not macro_samples[metric]:
                continue
            stacked = np.vstack(macro_samples[metric]).mean(axis=0)
            lo, hi = _percentile_ci(stacked, ci)
            macro[metric] = {
                "mean": float(np.mean(macro_point[metric])),
                "lo": lo,
                "hi": hi,
                "questions": len(macro_samples[metric]),
            }
        return {"questions": per_question, "macro": macro}

    result: dict[str, Any] = {
        "n_boot": n_boot,
        "ci": ci,
        "seed": seed,
        "metrics": list(metrics),
        "models": {label: model_block(label) for label in models},
    }

    if baseline_model and baseline_model in models:
        result["deltas_vs_baseline"] = {
            "baseline_model": baseline_model,
            "models": {
                label: _delta_block(grouped, questions, label, baseline_model, metrics, n_boot, seed, ci)
                for label in models
                if label != baseline_model
            },
        }
    return result


def _delta_block(
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]],
    questions: list[str],
    model_label: str,
    baseline_model: str,
    metrics: tuple[str, ...],
    n_boot: int,
    seed: int,
    ci: float,
) -> dict[str, Any]:
    # Separate rng per model keeps results reproducible regardless of iteration order.
    rng = np.random.default_rng(seed + _stable_offset(model_label))
    per_question: dict[str, Any] = {}
    macro_samples: dict[str, list[np.ndarray]] = {metric: [] for metric in metrics}
    macro_point: dict[str, list[float]] = {metric: [] for metric in metrics}
    for question in questions:
        model_map = grouped.get((model_label, question))
        base_map = grouped.get((baseline_model, question))
        if not model_map or not base_map:
            continue
        shared = sorted(set(model_map) & set(base_map))
        if not shared:
            continue
        n = len(shared)
        indices = rng.integers(0, n, size=(n_boot, n))
        metric_block: dict[str, Any] = {}
        for metric in metrics:
            model_values = _metric_array(model_map, shared, metric)
            base_values = _metric_array(base_map, shared, metric)
            samples = _bootstrap_means(model_values, indices) - _bootstrap_means(base_values, indices)
            lo, hi = _percentile_ci(samples, ci)
            delta = float(model_values.mean() - base_values.mean())
            metric_block[metric] = {
                "delta": delta,
                "lo": lo,
                "hi": hi,
                "model_mean": float(model_values.mean()),
                "baseline_mean": float(base_values.mean()),
                "n_shared": n,
            }
            macro_samples[metric].append(samples)
            macro_point[metric].append(delta)
        per_question[question] = metric_block
    macro: dict[str, Any] = {}
    for metric in metrics:
        if not macro_samples[metric]:
            continue
        stacked = np.vstack(macro_samples[metric]).mean(axis=0)
        lo, hi = _percentile_ci(stacked, ci)
        macro[metric] = {
            "delta": float(np.mean(macro_point[metric])),
            "lo": lo,
            "hi": hi,
            "questions": len(macro_samples[metric]),
        }
    return {"questions": per_question, "macro": macro}
