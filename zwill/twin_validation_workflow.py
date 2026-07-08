"""One-command digital-twin validation.

The methodology pieces built for issue #2 — the conditional baseline, skill
scores, bootstrap confidence intervals, the leakage audit, and the calibration
diagnostics — each live behind their own command. A practitioner following the
evaluation guide has to run four-plus commands in the right order and remember to
wire the baseline into the comparison. `zwill twin-validate` runs the whole flow
in one gated step:

1. **Leakage audit** — flag context questions that near-deterministically predict
   a target (the top validity threat), before trusting any score.
2. **Conditional baseline** — fit the cheap embedding baseline on the *same*
   respondents the twin jobs scored, so every model is compared on equal footing.
3. **Bootstrap CIs** — resample respondents for confidence intervals on each
   model's scores and on the paired twin-minus-baseline deltas.
4. **Report** — render the HTML validation report, which already embeds skill
   scores, the bootstrap panel, probability granularity, and the correlation-
   attenuation verdict, plus the baseline appendix.

Outputs land in one bundle directory (`report.html`, `bootstrap.json`,
`leakage_audit.json`, `manifest.json`) and the command returns a compact
practitioner summary.
"""

from __future__ import annotations

import argparse

from .cli import *  # noqa: F403
from .twin_baseline import MODEL_LABEL as BASELINE_MODEL_LABEL


def _resolve_twin_jobs(args: argparse.Namespace, sdir) -> tuple[list[str], list[dict[str, Any]]]:
    job_ids: list[str] = []
    for value in getattr(args, "job_id", None) or []:
        job_ids.append(str(value))
    if getattr(args, "jobs", None):
        job_ids.extend(item.strip() for item in str(args.jobs).split(",") if item.strip())
    job_ids = list(dict.fromkeys(job_ids))
    if not job_ids:
        raise ZwillError(
            "invalid_input",
            "twin-validate needs at least one imported twin job (--job-id/--jobs).",
        )
    job_set = set(job_ids)
    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    twin_rows = [row for row in all_rows if row.get("job_id") in job_set]
    if not twin_rows:
        raise ZwillError(
            "not_found",
            "No twin predictions found for the requested job ids.",
            context={"job_ids": job_ids},
        )
    return job_ids, twin_rows


def cmd_twin_validate(args: argparse.Namespace, *, embedder=None) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    twin_job_ids, twin_rows = _resolve_twin_jobs(args, sdir)

    heldout_questions = sorted({str(row["heldout_question"]) for row in twin_rows if row.get("heldout_question")})
    respondent_ids = sorted({str(row["respondent_id"]) for row in twin_rows if row.get("respondent_id")})

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: dict[str, Any] = {}
    warnings: list[dict[str, Any]] = []

    # The empirical-frequency baseline is attached at import time from committed
    # truth marginals. If the survey was committed AFTER these predictions were
    # imported, the baseline is silently absent -- flag it so the user can re-import.
    from .twin_result_commands import empirical_marginal_targets

    committed_marginals = empirical_marginal_targets(sdir)
    if (
        any(question in committed_marginals for question in heldout_questions)
        and not any(row.get("empirical_marginal_probability_actual") is not None for row in twin_rows)
    ):
        warnings.append(
            {
                "code": "empirical_baseline_missing",
                "message": (
                    "Committed truth marginals exist for the held-out question(s), but these predictions were "
                    "imported before the survey was committed, so the empirical-frequency baseline is absent from "
                    "the report. Re-import with `twin-results import --replace` to add it."
                ),
            }
        )

    # 1. Leakage audit -------------------------------------------------------
    if not getattr(args, "skip_leakage_audit", False):
        leakage_path = out_dir / "leakage_audit.json"
        leakage_args = argparse.Namespace(
            survey=args.survey,
            target=heldout_questions,
            targets=None,
            job_id=None,
            jobs=None,
            threshold=float(getattr(args, "leakage_threshold", 0.7) or 0.7),
            min_pair_rows=int(getattr(args, "min_pair_rows", 30) or 30),
            path=str(leakage_path),
        )
        leakage_result = cmd_twin_results_leakage_audit(leakage_args)
        data = leakage_result["data"]
        steps["leakage_audit"] = {
            "path": str(leakage_path),
            "pair_count": data["pair_count"],
            "flagged_count": data["flagged_count"],
            "flagged": data["flagged"][:10],
        }
        warnings.extend(leakage_result.get("warnings", []))

    # 2. Conditional baseline ------------------------------------------------
    baseline_job_id = None
    if not getattr(args, "skip_baseline", False):
        baseline_args = argparse.Namespace(
            survey=args.survey,
            heldout_question=heldout_questions,
            heldout_questions=None,
            restrict_respondent_ids=respondent_ids,
            sample_respondents=None,
            seed=int(getattr(args, "seed", 0) or 0),
            embedding_model=getattr(args, "embedding_model", None),
            embedder=getattr(args, "embedder", None),
            l2=float(getattr(args, "l2", 1.0) or 1.0),
            job_id=None,
            replace=True,
            path=None,
        )
        try:
            baseline_result = cmd_twin_baseline_run(baseline_args, embedder=embedder)
        except (RuntimeError, ZwillError) as exc:
            # The embedder raises when it has no credentials (RuntimeError from the
            # provider client, or a ZwillError from embedder resolution).
            if getattr(args, "require_baseline", False):
                raise ZwillError(
                    "missing_dependency",
                    f"Conditional baseline could not run: {exc}",
                    hint=(
                        "Set OPENAI_API_KEY, or set EXPECTED_PARROT_API_KEY and pass "
                        "--embedder edsl to route embeddings through Expected Parrot, "
                        "or pass --skip-baseline to run without the baseline."
                    ),
                ) from exc
            warnings.append(
                {"code": "baseline_skipped", "message": f"Conditional baseline skipped: {exc}"}
            )
            steps["baseline"] = {"ran": False, "reason": str(exc)}
        else:
            data = baseline_result["data"]
            baseline_job_id = data["job_id"]
            steps["baseline"] = {
                "ran": True,
                "job_id": baseline_job_id,
                "model_label": data["model_label"],
                "embedding_model": data["embedding_model"],
                "prediction_rows": data["prediction_rows"],
                "scored_questions": data["scored_questions"],
            }

    report_job_ids = twin_job_ids + ([baseline_job_id] if baseline_job_id else [])

    # 3. Bootstrap confidence intervals --------------------------------------
    if not getattr(args, "skip_bootstrap", False):
        bootstrap_path = out_dir / "bootstrap.json"
        bootstrap_args = argparse.Namespace(
            survey=args.survey,
            job_id=report_job_ids,
            jobs=None,
            manifest=None,
            model=None,
            question=None,
            questions=None,
            baseline_model=BASELINE_MODEL_LABEL if baseline_job_id else None,
            n_boot=int(getattr(args, "n_boot", 1000) or 1000),
            seed=int(getattr(args, "seed", 0) or 0),
            ci=float(getattr(args, "ci", 0.95) or 0.95),
            path=str(bootstrap_path),
        )
        bootstrap_result = cmd_twin_results_bootstrap(bootstrap_args)
        data = bootstrap_result["data"]
        steps["bootstrap"] = {
            "path": str(bootstrap_path),
            "n_boot": data["n_boot"],
            "ci": data["ci"],
            "macro_deltas_vs_baseline": data.get("macro_deltas_vs_baseline", {}),
        }
        warnings.extend(bootstrap_result.get("warnings", []))

    # 4. HTML report ---------------------------------------------------------
    report_path = out_dir / "report.html"
    report_args = argparse.Namespace(
        survey=args.survey,
        job_id=report_job_ids,
        jobs=None,
        model=None,
        format="html",
        view=getattr(args, "view", "full"),
        path=str(report_path),
    )
    cmd_twin_results_report(report_args)
    steps["report"] = {"path": str(report_path), "view": getattr(args, "view", "full")}

    # Rank batteries are validated through a separate rank-utility flow, not this
    # probability-job gate. If the survey has rank tasks, say so loudly and point
    # at that flow so ranking questions aren't silently left unvalidated.
    rank_tasks = detect_rank_tasks(read_jsonl(sdir / "questions.jsonl"))
    if rank_tasks:
        rank_predictions_path = rank_twin_predictions_path(sdir)
        rank_predictions_imported = rank_predictions_path.exists() and bool(read_jsonl(rank_predictions_path))
        steps["rank_coverage"] = {
            "rank_task_count": len(rank_tasks),
            "rank_task_ids": [task["rank_task_id"] for task in rank_tasks],
            "rank_predictions_imported": rank_predictions_imported,
            "covered_by_twin_validate": False,
        }
        warnings.append(
            {
                "code": "rank_tasks_not_validated_here",
                "message": (
                    f"This survey has {len(rank_tasks)} rank battery(ies) that twin-validate does NOT cover "
                    "(it gates twin-probability-jobs only). Validate ranking separately via the rank-utility "
                    "flow: `edsl-export --target rank-utility-twin-job` -> `edsl-run` -> "
                    "`twin-results import` -> `twin-results rank-report`."
                ),
                "rank_task_ids": [task["rank_task_id"] for task in rank_tasks],
            }
        )

    write_json(
        out_dir / "manifest.json",
        {
            "survey": args.survey,
            "twin_job_ids": twin_job_ids,
            "baseline_job_id": baseline_job_id,
            "heldout_questions": heldout_questions,
            "respondent_count": len(respondent_ids),
            "steps": steps,
            "created_at": utc_now(),
        },
    )

    return envelope(
        "zwill twin-validate",
        "ok",
        {
            "bundle_dir": str(out_dir),
            "twin_job_ids": twin_job_ids,
            "baseline_job_id": baseline_job_id,
            "heldout_questions": heldout_questions,
            "respondent_count": len(respondent_ids),
            "steps": steps,
        },
        warnings=warnings,
        next_steps=[f"open {report_path}"],
    )
