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

from collections import defaultdict
from typing import Any

from .executive_summary import spearman
from .twin_baseline import (
    CONDITIONAL_BASELINE_FEATURES,
    CONDITIONAL_BASELINE_HYPERPARAMS,
    CONDITIONAL_BASELINE_TRAINING,
)
from .twin_baseline import (
    MODEL_LABEL as BASELINE_MODEL_LABEL,
)
from .twin_report import build_twin_report
from .twin_scoring import skill_score_summary

REPORT_DATA_SCHEMA_VERSION = 2


def _norm_arm(model_label: str) -> str:
    """`job_id / model_label` (build_twin_report) -> plain model_label."""
    return model_label.split(" / ")[-1]


def _rank_correlation(marginal_options: list[dict[str, Any]]) -> dict[str, Any]:
    """Per twin arm, per question: Spearman rank correlation between the twin's
    predicted option distribution and the true one (does the twin order the
    options the same way the population does?). Macro-averaged per arm."""
    by: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: {"pred": [], "target": []})
    for mo in marginal_options:
        key = (_norm_arm(mo["model_label"]), mo["heldout_question"])
        by[key]["pred"].append(float(mo.get("predicted_probability") or 0.0))
        by[key]["target"].append(float(mo.get("target_probability") or 0.0))
    per_arm: dict[str, dict[str, Any]] = defaultdict(lambda: {"per_question": {}})
    for (arm, question), pair in by.items():
        if len(pair["pred"]) >= 2:
            per_arm[arm]["per_question"][question] = spearman(pair["pred"], pair["target"])
    for arm, block in per_arm.items():
        vals = [v for v in block["per_question"].values() if v is not None]
        block["mean_spearman"] = (sum(vals) / len(vals)) if vals else None
    return dict(per_arm)


def _survey_instrument(marginal_options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per held-out question: text, and the real option distribution (target)."""
    by_q: dict[str, dict[str, Any]] = {}
    for mo in marginal_options:
        q = mo["heldout_question"]
        entry = by_q.setdefault(q, {"question": q, "question_text": mo.get("heldout_question_text"), "options": {}})
        entry["options"].setdefault(mo["option_label"], round(float(mo.get("target_probability") or 0.0), 4))
    return [
        {"question": q, "question_text": e["question_text"],
         "options": [{"option": o, "real_share": p} for o, p in e["options"].items()]}
        for q, e in by_q.items()
    ]


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
            "overconfident_misses": diagnostics.get("overconfident_misses", [])[:10],
            "confident_hits": _confident_hits(rows),
        },
        "per_question": report.get("summary_by_question", {}),
        "rank_correlation": _rank_correlation(diagnostics.get("marginal_options", [])),
        "calibration": {"expected_calibration_error": diagnostics.get("expected_calibration_error", {})},
        "attenuation": (diagnostics.get("joint_structure") or {}).get("attenuation", {}),
        "survey_instrument": _survey_instrument(diagnostics.get("marginal_options", [])),
    }
