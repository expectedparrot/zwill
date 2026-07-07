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
    return sum(values) / len(values) if values else None


def _skill(model_losses: list[float], baseline_losses: list[float]) -> float | None:
    """1 - mean(model) / mean(baseline); None if the baseline mean is not positive."""
    model_mean = _mean(model_losses)
    baseline_mean = _mean(baseline_losses)
    if model_mean is None or baseline_mean is None or baseline_mean <= 0:
        return None
    return 1.0 - model_mean / baseline_mean


def _paired(model_rows: list[dict[str, Any]], model_key: str, baseline_key: str) -> tuple[list[float], list[float]]:
    """Model and baseline loss lists over the rows where both are present."""
    model_losses: list[float] = []
    baseline_losses: list[float] = []
    for row in model_rows:
        model_loss = _value(row, model_key)
        baseline_loss = _value(row, baseline_key)
        if model_loss is None or baseline_loss is None:
            continue
        model_losses.append(model_loss)
        baseline_losses.append(baseline_loss)
    return model_losses, baseline_losses


def _question_scores(model_rows: list[dict[str, Any]]) -> dict[str, Any]:
    model_nll = [v for row in model_rows if (v := _value(row, _MODEL_NLL)) is not None]
    model_brier = [v for row in model_rows if (v := _value(row, _MODEL_BRIER)) is not None]
    scores: dict[str, Any] = {
        "n": len(model_rows),
        "mean_nll": _mean(model_nll),
        "median_nll": median(model_nll) if model_nll else None,
        "mean_brier": _mean(model_brier),
        "accuracy": _mean([float(row.get("top1_correct") or 0) for row in model_rows]) if model_rows else None,
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
