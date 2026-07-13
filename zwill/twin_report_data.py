"""Machine-readable report data for a twin validation.

`zwill twin-validate` renders an HTML report, but downstream report generators
(and anyone building on the result) previously had to reconstruct the numbers by
scraping `bootstrap.json`, the raw predictions JSONL, this package's source, and
the HTML. `build_report_data` assembles one structured JSON with everything a
report needs: skill scores (per model x question, incl. vs uniform/marginal),
conditional-baseline diagnostics, per-question real-vs-predicted marginals, and
example predictions (misses + confident hits).
"""

from __future__ import annotations

from typing import Any

from .twin_baseline import (
    CONDITIONAL_BASELINE_FEATURES,
    CONDITIONAL_BASELINE_HYPERPARAMS,
    CONDITIONAL_BASELINE_TRAINING,
    MODEL_LABEL as BASELINE_MODEL_LABEL,
)
from .twin_report import build_twin_report
from .twin_scoring import skill_score_summary

REPORT_DATA_SCHEMA_VERSION = 1


def _confident_hits(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    hits = [
        row
        for row in rows
        if str(row.get("model_label") or "") != BASELINE_MODEL_LABEL
        and row.get("top1_correct") == 1
        and isinstance(row.get("probability_actual"), (int, float))
    ]
    hits.sort(key=lambda r: float(r.get("probability_actual") or 0.0), reverse=True)
    keep = ("respondent_id", "heldout_question", "actual_answer", "probability_actual", "job_id", "model_label")
    return [{k: row.get(k) for k in keep} for row in hits[:limit]]


def build_report_data(
    *,
    survey: str,
    rows: list[dict[str, Any]],
    heldout_questions: list[str],
    respondent_count: int,
    twin_job_ids: list[str],
    baseline_job_id: str | None,
    bootstrap_data: dict[str, Any] | None,
    leakage_summary: dict[str, Any] | None,
    baseline_embedding_model: str | None,
    baseline_training_rows: dict[str, int] | None = None,
) -> dict[str, Any]:
    skill = skill_score_summary(rows).get("models", {})
    report = build_twin_report(rows)
    diagnostics = report.get("diagnostics", {})

    baseline_skill = skill.get(BASELINE_MODEL_LABEL, {})
    baseline_per_question = {
        question: {
            "nll_skill_vs_uniform": scores.get("nll_skill_vs_uniform"),
            "nll_skill_vs_marginal": scores.get("nll_skill_vs_marginal"),
            "mean_nll": scores.get("mean_nll"),
            "accuracy": scores.get("accuracy"),
        }
        for question, scores in baseline_skill.get("questions", {}).items()
    }

    return {
        "schema_version": REPORT_DATA_SCHEMA_VERSION,
        "survey": survey,
        "design": {
            "heldout_questions": heldout_questions,
            "respondent_count": respondent_count,
            "twin_job_ids": twin_job_ids,
            "baseline_job_id": baseline_job_id,
            "arms": sorted(skill.keys()),
        },
        "skill_scores": skill,
        "bootstrap": bootstrap_data or {},
        "leakage": leakage_summary or {},
        "baseline_diagnostics": {
            "model": "xgboost_conditional_embedding",
            "model_label": BASELINE_MODEL_LABEL,
            "embedding_model": baseline_embedding_model,
            "features": CONDITIONAL_BASELINE_FEATURES,
            "training": CONDITIONAL_BASELINE_TRAINING,
            "hyperparameters": CONDITIONAL_BASELINE_HYPERPARAMS,
            "training_rows_by_question": baseline_training_rows or {},
            "per_question": baseline_per_question,
        },
        "marginals": diagnostics.get("marginal_comparisons", []),
        "marginal_options": diagnostics.get("marginal_options", []),
        "examples": {
            "worst_misses": diagnostics.get("worst_misses", [])[:10],
            "confident_hits": _confident_hits(rows),
        },
        "summary_by_question": report.get("summary_by_question", {}),
    }
