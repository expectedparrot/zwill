"""Skill scores: a unit-free common currency for twin validation.

Mean NLL and mean Brier are not comparable across questions with different option
counts, and mean NLL is dominated by a single confident miss (the epsilon floor
makes one p->0 contribute ~28). Two fixes:

* **Skill score** ``1 - mean(loss) / mean(baseline_loss)`` against a reference
  baseline. It is unit-free and equal-weighted across questions: 0 means "no
  better than the baseline", 1 means perfect, negative means worse. This is the
  number that actually answers "how much did the model add over knowing nothing
  (uniform) or over the population distribution (empirical marginal)?".
* **Median NLL** alongside the mean, which is robust to the outlier domination.

Top-1 accuracy is reported too, but explicitly as a sanity check: a single answer
per respondent can't validate an individual probability, and accuracy rewards
confident mode-guessing. Skill scores are the headline; accuracy is the footnote.
"""

from __future__ import annotations

import html
from statistics import median
from typing import Any

# Per-row loss fields already stored by the import/scoring path.
_MODEL_NLL = "negative_log_likelihood"
_MODEL_BRIER = "brier"
_BASELINES = {
    "uniform": ("uniform_negative_log_likelihood", "uniform_brier"),
    "marginal": ("empirical_marginal_negative_log_likelihood", "empirical_marginal_brier"),
}
# marginal fields have a legacy fallback name.
_MARGINAL_FALLBACK = {
    "empirical_marginal_negative_log_likelihood": "marginal_negative_log_likelihood",
    "empirical_marginal_brier": "marginal_brier",
}


def _value(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None and key in _MARGINAL_FALLBACK:
        value = row.get(_MARGINAL_FALLBACK[key])
    return None if value is None else float(value)


def _mean(values: list[float]) -> float | None:
    """Plain mean, used for the equal-weighted macro average across questions."""
    return sum(values) / len(values) if values else None


def _weight(row: dict[str, Any]) -> float:
    return float(row.get("weight", 1.0))


def _weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    """Weighted mean of (value, weight) pairs; plain mean when weights are all 1."""
    total_weight = sum(weight for _value, weight in pairs)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in pairs) / total_weight


def _skill(model_pairs: list[tuple[float, float]], baseline_pairs: list[tuple[float, float]]) -> float | None:
    """1 - mean(model) / mean(baseline); None if the baseline mean is not positive."""
    model_mean = _weighted_mean(model_pairs)
    baseline_mean = _weighted_mean(baseline_pairs)
    if model_mean is None or baseline_mean is None or baseline_mean <= 0:
        return None
    return 1.0 - model_mean / baseline_mean


def _paired(
    model_rows: list[dict[str, Any]], model_key: str, baseline_key: str
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Model and baseline (loss, weight) pairs over the rows where both are present."""
    model_pairs: list[tuple[float, float]] = []
    baseline_pairs: list[tuple[float, float]] = []
    for row in model_rows:
        model_loss = _value(row, model_key)
        baseline_loss = _value(row, baseline_key)
        if model_loss is None or baseline_loss is None:
            continue
        weight = _weight(row)
        model_pairs.append((model_loss, weight))
        baseline_pairs.append((baseline_loss, weight))
    return model_pairs, baseline_pairs


def _question_scores(model_rows: list[dict[str, Any]]) -> dict[str, Any]:
    model_nll = [v for row in model_rows if (v := _value(row, _MODEL_NLL)) is not None]
    model_brier = [(v, _weight(row)) for row in model_rows if (v := _value(row, _MODEL_BRIER)) is not None]
    model_nll_pairs = [(v, _weight(row)) for row in model_rows if (v := _value(row, _MODEL_NLL)) is not None]
    scores: dict[str, Any] = {
        "n": len(model_rows),
        "mean_nll": _weighted_mean(model_nll_pairs),
        "median_nll": median(model_nll) if model_nll else None,
        "mean_brier": _weighted_mean(model_brier),
        "accuracy": _weighted_mean([(float(row.get("top1_correct") or 0), _weight(row)) for row in model_rows]) if model_rows else None,
    }
    for name, (nll_key, brier_key) in _BASELINES.items():
        scores[f"nll_skill_vs_{name}"] = _skill(*_paired(model_rows, _MODEL_NLL, nll_key))
        scores[f"brier_skill_vs_{name}"] = _skill(*_paired(model_rows, _MODEL_BRIER, brier_key))
    return scores


_MACRO_KEYS = (
    "mean_nll",
    "median_nll",
    "mean_brier",
    "accuracy",
    "nll_skill_vs_uniform",
    "nll_skill_vs_marginal",
    "brier_skill_vs_uniform",
    "brier_skill_vs_marginal",
)


def skill_score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        label = str(row.get("model_label") or row.get("model") or "")
        question = str(row.get("heldout_question") or "")
        if not label or not question:
            continue
        grouped.setdefault(label, {}).setdefault(question, []).append(row)

    models: dict[str, Any] = {}
    for label, questions in grouped.items():
        per_question = {question: _question_scores(question_rows) for question, question_rows in questions.items()}
        macro: dict[str, Any] = {}
        for key in _MACRO_KEYS:
            present = [scores[key] for scores in per_question.values() if scores.get(key) is not None]
            macro[key] = _mean(present) if present else None
        macro["questions"] = len(per_question)
        models[label] = {"questions": per_question, "macro": macro}
    return {"models": models}


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def _fmt_ratio(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _fmt_skill(value: float | None) -> str:
    if value is None:
        return ""
    cls = "good" if value > 0 else ("bad" if value < 0 else "")
    return f'<span class="{cls}">{value:+.1%}</span>' if cls else f"{value:+.1%}"


def skill_score_section_html(rows: list[dict[str, Any]]) -> str:
    summary = skill_score_summary(rows)
    if not summary["models"]:
        return ""
    body = []
    for label in sorted(summary["models"]):
        macro = summary["models"][label]["macro"]
        body.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{_fmt_ratio(macro.get('mean_nll'))}</td>"
            f"<td>{_fmt_ratio(macro.get('median_nll'))}</td>"
            f"<td>{_fmt_skill(macro.get('nll_skill_vs_uniform'))}</td>"
            f"<td>{_fmt_skill(macro.get('nll_skill_vs_marginal'))}</td>"
            f"<td>{_fmt_skill(macro.get('brier_skill_vs_marginal'))}</td>"
            f"<td class=\"subtle\">{_fmt_ratio(macro.get('accuracy'))}</td>"
            "</tr>"
        )
    return f"""
<section class="panel skill-scores" style="margin-top:2rem;">
  <h2>Skill scores (unit-free, comparable across questions)</h2>
  <p class="subtle">Skill = 1 &minus; model loss / baseline loss, macro-averaged across held-out questions. 0% = no better than the baseline, 100% = perfect, negative = worse. "vs marginal" is the demanding comparison: can the model beat the population distribution on individuals?</p>
  <div class="table-wrap"><table>
    <thead><tr>
      <th>Model</th><th>mean NLL</th><th>median NLL</th>
      <th>NLL skill vs uniform</th><th>NLL skill vs marginal</th><th>Brier skill vs marginal</th>
      <th>accuracy <span class="subtle">(sanity)</span></th>
    </tr></thead>
    <tbody>{''.join(body)}</tbody>
  </table></div>
  <p class="subtle">Accuracy is a sanity check, not a headline: one answer per respondent cannot validate an individual probability, and top-1 accuracy rewards confident mode-guessing. Read the skill scores.</p>
</section>"""


# ---------------------------------------------------------------------------
# Probability granularity (data-quality check on the returned distributions)
# ---------------------------------------------------------------------------
def _grid_distance(value: float, step: float = 0.05) -> float:
    nearest = round(value / step) * step
    return abs(value - nearest)


def probability_granularity_summary(rows: list[dict[str, Any]], *, round_tol: float = 0.01) -> dict[str, Any]:
    """Measure how coarse each model's returned probabilities are.

    LLMs pile probability mass on round numbers (0.7, 0.8, 0.9). When the returned
    distributions are coarse, Brier and calibration are quantization-limited -- the
    scores can't be better than the grid the model answered on. This reports, per
    model, the share of probability values that sit on a 0.05 grid and how many
    distinct values were used, so a coarse model is visible before its scores are
    over-interpreted.
    """
    by_model: dict[str, list[float]] = {}
    for row in rows:
        label = str(row.get("model_label") or row.get("model") or "")
        if not label:
            continue
        values = [float(v) for v in (row.get("probabilities") or {}).values()]
        by_model.setdefault(label, []).extend(values)

    models: dict[str, Any] = {}
    for label, values in by_model.items():
        if not values:
            continue
        on_grid = sum(1 for v in values if _grid_distance(v) <= round_tol)
        models[label] = {
            "values": len(values),
            "distinct_values": len({round(v, 4) for v in values}),
            "round_fraction": on_grid / len(values),
            "mean_grid_distance": sum(_grid_distance(v) for v in values) / len(values),
            "warning": "coarse_probabilities" if on_grid / len(values) >= 0.8 else "",
        }
    return {"models": models, "round_tol": round_tol, "grid_step": 0.05}


def probability_granularity_section_html(rows: list[dict[str, Any]]) -> str:
    summary = probability_granularity_summary(rows)
    if not summary["models"]:
        return ""
    body = []
    for label in sorted(summary["models"]):
        info = summary["models"][label]
        flag = ' <span class="bad">coarse</span>' if info["warning"] else ""
        body.append(
            "<tr>"
            f"<td>{html.escape(label)}{flag}</td>"
            f"<td>{info['distinct_values']}</td>"
            f"<td>{info['round_fraction']:.1%}</td>"
            f"<td>{info['mean_grid_distance']:.3f}</td>"
            "</tr>"
        )
    return f"""
<section class="panel probability-granularity" style="margin-top:2rem;">
  <h2>Probability granularity (data quality)</h2>
  <p class="subtle">How coarse the returned distributions are. A high "on 0.05 grid" share means the model answered in round numbers, so Brier and calibration are quantization-limited &mdash; read those scores with that ceiling in mind.</p>
  <div class="table-wrap"><table>
    <thead><tr><th>Model</th><th>distinct values</th><th>on 0.05 grid</th><th>mean distance to grid</th></tr></thead>
    <tbody>{''.join(body)}</tbody>
  </table></div>
</section>"""
