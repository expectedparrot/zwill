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
import html
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


def _base_label(row: dict[str, Any]) -> str:
    return str(row.get("model_label") or row.get("model") or "")


def arm_labels(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Map job_id -> display arm label.

    Two twin jobs with the same model but a different construction (e.g. a prompt
    pipeline, or a with-context vs covariates-only run) share a model_label. Left
    alone they collapse into one arm and their scores merge. When a model_label
    spans more than one job_id, disambiguate the arm by a short job suffix so each
    construction is scored separately; the common one-job-per-model case is
    unchanged (arm label == model_label).
    """
    jobs_by_label: dict[str, set[str]] = {}
    label_of_job: dict[str, str] = {}
    for row in rows:
        label = _base_label(row)
        job = str(row.get("job_id") or "")
        if not label or not job:
            continue
        label_of_job[job] = label
        jobs_by_label.setdefault(label, set()).add(job)
    return {
        job: (f"{label} [{job[:8]}]" if len(jobs_by_label[label]) > 1 else label)
        for job, label in label_of_job.items()
    }


def _group_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """Group prediction rows by (arm, question) -> {respondent_id: row}.

    The arm is the model label, disambiguated by job when one model spans several
    jobs (see ``arm_labels``), so same-model construction variants stay separate.
    """
    arm_of_job = arm_labels(rows)
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        job = str(row.get("job_id") or "")
        label = arm_of_job.get(job) or _base_label(row)
        question = str(row.get("heldout_question") or "")
        respondent_id = str(row.get("respondent_id") or "")
        if not label or not question or not respondent_id:
            continue
        grouped.setdefault((label, question), {})[respondent_id] = row
    return grouped


def _metric_array(rowmap: dict[str, dict[str, Any]], respondent_ids: list[str], metric: str) -> np.ndarray:
    return np.asarray([float(rowmap[rid].get(metric) or 0.0) for rid in respondent_ids], dtype=float)


def _weight_array(rowmap: dict[str, dict[str, Any]], respondent_ids: list[str]) -> np.ndarray:
    return np.asarray([float(rowmap[rid].get("weight", 1.0)) for rid in respondent_ids], dtype=float)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    total = float(weights.sum())
    return float((values * weights).sum() / total) if total > 0 else float(values.mean())


def _bootstrap_means(values: np.ndarray, indices: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    """Weighted mean of `values` under each bootstrap resample (indices: n_boot x n).

    With uniform weights this is the ordinary resample mean; survey weights make
    each draw a population-level (weighted) estimate.
    """
    resampled = values[indices]
    if weights is None:
        return resampled.mean(axis=1)
    weight_draws = weights[indices]
    totals = weight_draws.sum(axis=1)
    totals[totals == 0] = 1.0
    return (resampled * weight_draws).sum(axis=1) / totals


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
            weights = _weight_array(rowmap, respondent_ids)
            metric_block: dict[str, Any] = {}
            for metric in metrics:
                values = _metric_array(rowmap, respondent_ids, metric)
                samples = _bootstrap_means(values, indices, weights)
                lo, hi = _percentile_ci(samples, ci)
                point = _weighted_mean(values, weights)
                metric_block[metric] = {"mean": point, "lo": lo, "hi": hi, "n": n}
                macro_samples[metric].append(samples)
                macro_point[metric].append(point)
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
        weights = _weight_array(model_map, shared)  # same respondents/weights for both models
        metric_block: dict[str, Any] = {}
        for metric in metrics:
            model_values = _metric_array(model_map, shared, metric)
            base_values = _metric_array(base_map, shared, metric)
            samples = _bootstrap_means(model_values, indices, weights) - _bootstrap_means(base_values, indices, weights)
            lo, hi = _percentile_ci(samples, ci)
            model_mean = _weighted_mean(model_values, weights)
            baseline_mean = _weighted_mean(base_values, weights)
            delta = model_mean - baseline_mean
            metric_block[metric] = {
                "delta": delta,
                "lo": lo,
                "hi": hi,
                "model_mean": model_mean,
                "baseline_mean": baseline_mean,
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


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
_CONDITIONAL_BASELINE_LABEL = "baseline:conditional-embedding"

# metric key -> (display label, number format, higher-is-better)
_REPORT_METRICS = (
    ("probability_actual", "p(actual)", "{:.3f}", True),
    ("negative_log_likelihood", "NLL", "{:.3f}", False),
    ("top1_correct", "accuracy", "{:.3f}", True),
)


def _fmt(value: float, spec: str) -> str:
    return spec.format(value)


def bootstrap_ci_section_html(
    rows: list[dict[str, Any]],
    *,
    baseline_model: str | None = None,
    n_boot: int = 500,
    seed: int = 0,
    ci: float = 0.95,
) -> str:
    """Render a bootstrap-CI panel for a set of twin prediction rows.

    Shows each model's macro score with a confidence interval and, when a
    baseline model is present, the paired delta of every other model against it
    with an interval and a significance marker. Returns '' when there is nothing
    to show (no scored models).
    """
    labels = sorted({str(row.get("model_label") or row.get("model") or "") for row in rows} - {""})
    if not labels:
        return ""
    if baseline_model is None and _CONDITIONAL_BASELINE_LABEL in labels:
        baseline_model = _CONDITIONAL_BASELINE_LABEL
    result = bootstrap_summary(rows, baseline_model=baseline_model, n_boot=n_boot, seed=seed, ci=ci)
    if not result["models"]:
        return ""

    ci_pct = int(round(ci * 100))
    header_cells = "".join(f"<th>{label}</th>" for _key, label, _spec, _hib in _REPORT_METRICS)
    score_rows = []
    for label in sorted(result["models"]):
        macro = result["models"][label]["macro"]
        cells = []
        for key, _label, spec, _hib in _REPORT_METRICS:
            stat = macro.get(key)
            if not stat:
                cells.append("<td></td>")
                continue
            cells.append(
                f"<td><b>{_fmt(stat['mean'], spec)}</b> "
                f"<span class=\"subtle\">[{_fmt(stat['lo'], spec)}, {_fmt(stat['hi'], spec)}]</span></td>"
            )
        score_rows.append(f"<tr><td>{html.escape(label)}</td>{''.join(cells)}</tr>")

    delta_html = ""
    deltas = result.get("deltas_vs_baseline")
    if deltas and deltas["models"]:
        delta_rows = []
        for label in sorted(deltas["models"]):
            macro = deltas["models"][label]["macro"]
            cells = []
            for key, _label, spec, higher_is_better in _REPORT_METRICS:
                stat = macro.get(key)
                if not stat:
                    cells.append("<td></td>")
                    continue
                excludes_zero = stat["lo"] > 0 or stat["hi"] < 0
                # A "win" is an improvement in the right direction whose CI clears zero.
                improved = (stat["delta"] > 0) if higher_is_better else (stat["delta"] < 0)
                mark = " ✓" if (excludes_zero and improved) else (" ✗" if excludes_zero else "")
                sign = "+" if stat["delta"] >= 0 else ""
                cells.append(
                    f"<td>{sign}{_fmt(stat['delta'], spec)} "
                    f"<span class=\"subtle\">[{sign if stat['lo'] >= 0 else ''}{_fmt(stat['lo'], spec)}, "
                    f"{'+' if stat['hi'] >= 0 else ''}{_fmt(stat['hi'], spec)}]</span>{mark}</td>"
                )
            delta_rows.append(f"<tr><td>{html.escape(label)}</td>{''.join(cells)}</tr>")
        delta_html = f"""
  <h3>Paired difference vs baseline <code>{html.escape(deltas['baseline_model'])}</code></h3>
  <p class="subtle">Each model minus the baseline on the respondents both scored. ✓ = improvement whose {ci_pct}% interval clears zero; ✗ = a gap the wrong way that also clears zero.</p>
  <div class="table-wrap"><table>
    <thead><tr><th>Model</th>{header_cells}</tr></thead>
    <tbody>{''.join(delta_rows)}</tbody>
  </table></div>"""

    return f"""
<section class="panel bootstrap-ci" style="margin-top:2rem;">
  <h2>Confidence intervals (bootstrap over respondents)</h2>
  <p class="subtle">{ci_pct}% intervals from {result['n_boot']} bootstrap resamples of respondents, macro-averaged across held-out questions. Intervals that exclude zero (for deltas) indicate the gap is unlikely to be sampling noise.</p>
  <div class="table-wrap"><table>
    <thead><tr><th>Model</th>{header_cells}</tr></thead>
    <tbody>{''.join(score_rows)}</tbody>
  </table></div>{delta_html}
</section>"""
