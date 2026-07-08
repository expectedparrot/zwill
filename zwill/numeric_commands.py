"""Import, score, and summarize continuous (quantile) twin predictions."""

from __future__ import annotations

import argparse
import shutil
from collections import defaultdict
from typing import Any

from .cli import *  # noqa: F403
from .numeric import (
    DEFAULT_QUANTILE_LEVELS,
    parse_quantile_prediction,
    repair_quantile_values,
    score_numeric_prediction,
    weighted_quantiles,
)
from .probability import parse_probability_json
from .twin_bootstrap import bootstrap_summary
from .twin_report import weighted_row_mean

_NUMERIC_METRICS = ("pinball", "crps", "absolute_error")
_MARGINAL_BASELINE = "baseline:marginal-quantile"


def numeric_predictions_path(sdir: "Path") -> "Path":  # noqa: F821
    return sdir / "numeric_twin_predictions.jsonl"


def extract_numeric_prediction_rows(
    results: dict[str, Any],
    *,
    job_id: str,
    survey: str,
    weight_by_respondent: dict[str, float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, row in enumerate(results.get("data", [])):
        scenario = row.get("scenario", {}) or {}
        model = row.get("model", {}) or {}
        label = model_label(model.get("inference_service"), model.get("model"))
        answer = row.get("answer", {}) or {}
        raw_answer = next((value for value in answer.values() if value is not None), None) if isinstance(answer, dict) else answer
        actual = scenario.get("actual_value")
        parsed, parse_error = parse_probability_json(raw_answer)
        error = parse_error
        levels: list[float] = []
        values: list[float] = []
        if not error:
            levels, values, error = parse_quantile_prediction(parsed)
        if actual is None:
            error = error or "missing_actual_value"
        if error:
            issues.append(
                {"row": index, "respondent_id": scenario.get("respondent_id"), "heldout_question": scenario.get("heldout_question_name"), "model": label, "error": error}
            )
            continue
        bounds = scenario.get("numeric_bounds") or [None, None]
        values = repair_quantile_values(values, (bounds[0], bounds[1]))
        actual_value = float(actual)
        metrics = score_numeric_prediction(actual_value, levels, values)
        rows.append(
            {
                "job_id": job_id,
                "row": index,
                "survey": survey,
                "respondent_id": scenario.get("respondent_id"),
                "weight": float(weight_by_respondent.get(str(scenario.get("respondent_id")), 1.0)),
                "heldout_question": scenario.get("heldout_question_name"),
                "actual_value": actual_value,
                "quantile_levels": levels,
                "quantile_values": values,
                "model": model.get("model"),
                "service": model.get("inference_service"),
                "model_label": label,
                **metrics,
            }
        )
    return rows, issues


def marginal_quantile_baseline_rows(twin_rows: list[dict[str, Any]], levels: list[float]) -> list[dict[str, Any]]:
    """Score every respondent's actual against the weighted population quantiles.

    This is the unconditional 'climatology' baseline: it ignores the respondent and
    predicts the same marginal distribution for everyone.
    """
    by_question: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in twin_rows:
        by_question[row["heldout_question"]].setdefault(row["respondent_id"], row)
    baseline: list[dict[str, Any]] = []
    for question, respondents in by_question.items():
        actuals = [row["actual_value"] for row in respondents.values()]
        weights = [row["weight"] for row in respondents.values()]
        base_values = weighted_quantiles(actuals, weights, levels)
        for row in respondents.values():
            metrics = score_numeric_prediction(row["actual_value"], levels, base_values)
            baseline.append(
                {
                    "job_id": row["job_id"],
                    "survey": row["survey"],
                    "respondent_id": row["respondent_id"],
                    "weight": row["weight"],
                    "heldout_question": question,
                    "actual_value": row["actual_value"],
                    "quantile_levels": levels,
                    "quantile_values": base_values,
                    "model": _MARGINAL_BASELINE,
                    "model_label": _MARGINAL_BASELINE,
                    **metrics,
                }
            )
    return baseline


def _model_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "mean_pinball": weighted_row_mean(rows, "pinball"),
        "mean_crps": weighted_row_mean(rows, "crps"),
        "mean_absolute_error": weighted_row_mean(rows, "absolute_error"),
        "coverage_50": weighted_row_mean(rows, "covered_50"),
        "coverage_90": weighted_row_mean(rows, "covered_90"),
    }


def summarize_numeric_predictions(rows: list[dict[str, Any]], *, n_boot: int = 500, seed: int = 0) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_model[row["model_label"]].append(row)
    models = {label: _model_summary(model_rows) for label, model_rows in sorted(by_model.items())}
    boot = bootstrap_summary(rows, baseline_model=_MARGINAL_BASELINE, metrics=_NUMERIC_METRICS, n_boot=n_boot, seed=seed)
    # pinball skill vs the marginal baseline, per twin model
    baseline_pinball = models.get(_MARGINAL_BASELINE, {}).get("mean_pinball")
    skill = {}
    for label, summary in models.items():
        if label == _MARGINAL_BASELINE or not baseline_pinball:
            continue
        mp = summary.get("mean_pinball")
        skill[label] = 1.0 - mp / baseline_pinball if mp is not None else None
    return {
        "models": models,
        "pinball_skill_vs_marginal": skill,
        "deltas_vs_baseline": boot.get("deltas_vs_baseline"),
        "baseline_model": _MARGINAL_BASELINE,
        "quantile_levels": rows[0]["quantile_levels"] if rows else list(DEFAULT_QUANTILE_LEVELS),
    }


def cmd_numeric_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    job_id = args.job_id or results.get("zwill", {}).get("numeric_twin_job_id") or digital_twin_job_id_from_results(results)

    weight_by_respondent = {
        str(row["respondent_id"]): float(row.get("weight", 1.0))
        for row in read_jsonl(sdir / "respondents.jsonl")
        if row.get("respondent_id") is not None
    }
    extracted, issues = extract_numeric_prediction_rows(results, job_id=job_id, survey=args.survey, weight_by_respondent=weight_by_respondent)
    levels = list(results.get("zwill", {}).get("quantile_levels") or (extracted[0]["quantile_levels"] if extracted else DEFAULT_QUANTILE_LEVELS))
    baseline = marginal_quantile_baseline_rows(extracted, levels) if extracted else []

    jdir = digital_twin_jobs_dir(sdir) / job_id
    if jdir.exists() and not getattr(args, "replace", False):
        raise ZwillError("already_exists", f"Numeric results already imported for job id {job_id}.", hint="Use --replace to overwrite.")
    if jdir.exists():
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)

    existing = [row for row in read_jsonl(numeric_predictions_path(sdir)) if row.get("job_id") != job_id]
    rewrite_jsonl(numeric_predictions_path(sdir), existing + extracted + baseline)
    write_json(jdir / "import.json", {"job_id": job_id, "survey": args.survey, "kind": "numeric_twin", "extracted_count": len(extracted), "baseline_count": len(baseline), "issue_count": len(issues), "issues": issues, "imported_at": utc_now()})

    summary = summarize_numeric_predictions(extracted + baseline) if extracted else {}
    return envelope(
        "zwill numeric-results import",
        "ok",
        {"job_id": job_id, "extracted_count": len(extracted), "baseline_count": len(baseline), "issue_count": len(issues), "summary": summary},
        next_steps=[f"zwill numeric-results report --survey {args.survey} --job-id {job_id}"],
    )


def cmd_numeric_results_report(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    rows = [row for row in read_jsonl(numeric_predictions_path(sdir)) if not args.job_id or row.get("job_id") == args.job_id]
    if not rows:
        raise ZwillError("not_found", "No numeric predictions found for this survey/job.")
    summary = summarize_numeric_predictions(rows)
    if getattr(args, "format", "json") == "html":
        from .numeric_report import numeric_report_payload, render_numeric_report_html

        payload = numeric_report_payload(rows, summary)
        html = render_numeric_report_html(payload)
        if getattr(args, "path", None):
            Path(args.path).write_text(html)
            return envelope("zwill numeric-results report", "ok", {"job_id": args.job_id, "format": "html", "path": str(args.path), "models": list(summary.get("models", {}))})
        print(html)
        return None  # type: ignore[return-value]
    return envelope("zwill numeric-results report", "ok", {"job_id": args.job_id, **summary})
