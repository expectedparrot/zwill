from __future__ import annotations

from .cli import *  # noqa: F403
from .costs import results_cost_summary


def _cli():
    from . import cli

    return cli


def cmd_probability_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.input_path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.input_path}.")
    results = read_edsl_results(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")

    job_id = args.job_id or results.get("zwill", {}).get("probability_job_id") or probability_job_id_from_results(results)
    jdir = probability_jobs_dir(sdir) / job_id
    if jdir.exists() and not args.replace:
        raise ZwillError(
            "already_exists",
            f"Probability results already imported for job id {job_id}.",
            hint="Use --replace to overwrite this import.",
        )
    if jdir.exists():
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)

    existing = [row for row in read_jsonl(probability_predictions_path(sdir)) if row.get("job_id") != job_id]
    imported_at = utc_now()
    extracted, issues = extract_probability_prediction_rows(
        results,
        job_id=job_id,
        survey=args.survey,
        stored_raw=str(stored_raw),
        imported_at=imported_at,
    )

    rewrite_jsonl(probability_predictions_path(sdir), existing + extracted)
    write_json(
        jdir / "import.json",
        {
            "job_id": job_id,
            "survey": args.survey,
            "source_path": str(source),
            "source_hash": sha256(source),
            "stored_path": str(stored_raw),
            "stored_hash": sha256(stored_raw),
            "row_count": len(results.get("data", [])),
            "extracted_count": len(extracted),
            "issue_count": len(issues),
            "issues": issues,
            "imported_at": imported_at,
        },
    )
    upsert_twin_run_manifest(
        sdir,
        {
            "job_id": job_id,
            "survey": args.survey,
            "status": "imported",
            "created_at": imported_at,
            "results_path": str(source),
            "stored_raw": str(stored_raw),
            "row_count": len(results.get("data", [])),
            "extracted_count": len(extracted),
            "issue_count": len(issues),
            "models": sorted({row.get("model_label") or model_label(row.get("service"), row.get("model")) for row in extracted}),
            "heldout_questions": sorted({row.get("heldout_question") for row in extracted if row.get("heldout_question")}),
        },
    )
    return envelope(
        "zwill prob-results import",
        "ok",
        {
            "job_id": job_id,
            "stored_raw": str(stored_raw),
            "row_count": len(results.get("data", [])),
            "extracted_count": len(extracted),
            "issue_count": len(issues),
            "issues": issues,
            "cost": results_cost_summary(results),
        },
        next_steps=[f"zwill prob-results report --survey {args.survey} --job-id {job_id}"],
    )


def cmd_probability_results_report(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    truth_path = sdir / "committed" / "truth_marginals.json"
    if not truth_path.exists():
        raise ZwillError("not_found", "Committed truth marginals do not exist.", hint=f"Run `zwill commit --survey {args.survey}`.")
    truth = read_json(truth_path, {})
    rows = filtered_probability_prediction_rows(args)
    if not rows:
        raise ZwillError("not_found", "No probability predictions found for the requested filters.")

    payload = build_probability_report(rows, truth)
    report_rows = payload["rows"]
    summary = payload["summary"]
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output + "\n")
        print(output)
        return

    if args.format == "csv":
        fieldnames = [
            "job_id",
            "question",
            "question_text",
            "service",
            "model",
            "mae",
            "uniform_mae",
            "brier",
            "uniform_brier",
            "brier_improvement",
            "brier_percent_improvement",
            "kl_divergence",
            "uniform_kl_divergence",
            "kl_improvement",
            "kl_percent_improvement",
        ]
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            with resolve_output_path(args.path).open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                for row in report_rows:
                    writer.writerow({key: row.get(key) for key in fieldnames})
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            for row in report_rows:
                writer.writerow({key: row.get(key) for key in fieldnames})
        return

    if args.format == "html":
        output = render_probability_report_html(args.survey, report_rows, summary)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output)
        else:
            print(output)
        return

    if args.format == "svg":
        output = render_probability_report_svg(args.survey, report_rows)
        if args.path:
            path = resolve_output_path(args.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output + "\n")
        else:
            print(output)
        return

    table = Table(title=f"{args.survey} probability report")
    for column in ["question", "model", "actual", "predicted", "uniform", "brier", "uniform_brier", "brier_delta", "kl", "uniform_kl"]:
        table.add_column(column)
    for row in report_rows:
        table.add_row(
            row["question"],
            row["model"],
            fmt_probs(row["actual"]),
            fmt_probs(row["predicted"]),
            fmt_probs(row["uniform"]),
            f"{row['brier']:.4f}",
            f"{row['uniform_brier']:.4f}",
            f"{row['brier_improvement']:.4f}",
            f"{row['kl_divergence']:.4f}",
            f"{row['uniform_kl_divergence']:.4f}",
        )
    Console().print(table)


def filtered_probability_prediction_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    sdir = require_survey(args.survey)
    rows = read_jsonl(probability_predictions_path(sdir))
    job_id = getattr(args, "job_id", None)
    if job_id:
        rows = [row for row in rows if row.get("job_id") == job_id]
    if hasattr(args, "probability_model"):
        model = getattr(args, "probability_model", None)
    else:
        model = getattr(args, "model", None)
    if model:
        rows = [row for row in rows if row.get("model") == model or row.get("model_label") == model]
    return rows
