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
        generated = find_imported_one_shot_analysis_report(
            survey=args.survey,
            job_id=getattr(args, "job_id", None),
            model=getattr(args, "model", None),
            questions=sorted({str(row.get("question")) for row in report_rows if row.get("question")}),
        )
        output = render_probability_report_html(
            args.survey,
            report_rows,
            summary,
            generated_analysis_markdown=generated.get("markdown") if generated else None,
            generation=generated.get("generation") if generated else None,
        )
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


def build_one_shot_analysis_report_context(*args, **kwargs):
    from .generated_reports import build_one_shot_analysis_report_context as impl

    return impl(*args, **kwargs)

def build_one_shot_analysis_report_prompt(*args, **kwargs):
    from .generated_reports import build_one_shot_analysis_report_prompt as impl

    return impl(*args, **kwargs)

def build_edsl_one_shot_analysis_report_job_dict(*args, **kwargs):
    from .generated_reports import build_edsl_one_shot_analysis_report_job_dict as impl

    return impl(*args, **kwargs)

def cmd_probability_results_analysis_export(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    truth_path = sdir / "committed" / "truth_marginals.json"
    if not truth_path.exists():
        raise ZwillError("not_found", "Committed truth marginals do not exist.", hint=f"Run `zwill commit --survey {args.survey}`.")
    rows = filtered_probability_prediction_rows(args)
    if not rows:
        raise ZwillError("not_found", "No probability predictions found for the requested filters.")
    payload = build_probability_report(rows, read_json(truth_path, {}))
    report_context = build_one_shot_analysis_report_context(args, payload)
    job_dict, context, prompt = _cli().build_edsl_one_shot_analysis_report_job_dict(args, report_context)
    report_id = job_dict["zwill"]["practitioner_report_id"]
    path = resolve_output_path(args.path or (Path("artifacts") / f"{args.survey}_one_shot_marginals.html"))
    data = write_practitioner_report_export(
        report_id,
        job_dict,
        context,
        prompt,
        job_path=Path(args.job_path) if args.job_path else None,
        prompt_path=resolve_output_path(args.prompt_path) if args.prompt_path else None,
        context_path_arg=resolve_output_path(args.context_path) if args.context_path else None,
    )
    return envelope(
        "zwill prob-results analysis-export",
        "ok",
        {
            **data,
            "target_html_path": str(path),
            "context_bytes": len(json.dumps(context, separators=(",", ":")).encode("utf-8")),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "raw_prediction_rows_in_prompt": False,
        },
        next_steps=[
            f"ep run {data['job_path']} --output {default_practitioner_report_paths(report_id)['dir'] / 'results.ep'}",
            f"zwill prob-results analysis-import --report-id {report_id} --input-path {default_practitioner_report_paths(report_id)['dir'] / 'results.ep'}",
            f"zwill prob-results analysis-render --report-id {report_id} --path {path}",
        ],
    )


def cmd_probability_results_analysis_import(args: argparse.Namespace) -> dict[str, Any]:
    result = cmd_twin_benchmark_practitioner_report_import(args)
    return {
        **result,
        "command": "zwill prob-results analysis-import",
        "next_steps": [
            step.replace("twin-benchmark practitioner-report-render", "prob-results analysis-render")
            for step in result.get("next_steps", [])
        ],
    }


def cmd_probability_results_analysis_render(args: argparse.Namespace) -> dict[str, Any]:
    paths = default_practitioner_report_paths(args.report_id)
    if not paths["context"].exists():
        raise ZwillError("not_found", f"No exported one-shot analysis context found for report id {args.report_id}.")
    if not paths["markdown"].exists():
        raise ZwillError(
            "not_found",
            f"No imported generated one-shot analysis Markdown found for report id {args.report_id}.",
            hint=f"Run `zwill prob-results analysis-import --report-id {args.report_id} --input-path <results.ep>`.",
        )
    context = read_json(paths["context"], {})
    report_context = context.get("one_shot_analysis_context", {})
    survey = report_context.get("survey")
    if not survey:
        raise ZwillError("invalid_input", f"Stored one-shot analysis context is incomplete for report id {args.report_id}.")
    source_filters = report_context.get("source_filters", {})
    filter_args = argparse.Namespace(
        survey=survey,
        job_id=source_filters.get("job_id"),
        probability_model=source_filters.get("probability_model"),
        model=None,
    )
    sdir = require_survey(survey)
    truth_path = sdir / "committed" / "truth_marginals.json"
    if not truth_path.exists():
        raise ZwillError("not_found", "Committed truth marginals do not exist.", hint=f"Run `zwill commit --survey {survey}`.")
    rows = filtered_probability_prediction_rows(filter_args)
    if not rows:
        raise ZwillError("not_found", "No probability prediction rows matched the stored one-shot analysis filters.", context={"source_filters": source_filters})
    payload = build_probability_report(rows, read_json(truth_path, {}))
    markdown = paths["markdown"].read_text()
    generation = {
        **context.get("generation", {}),
        "mode": "imported_results",
        "report_id": args.report_id,
        "context_path": str(paths["context"]),
        "markdown_path": str(paths["markdown"]),
        "import_path": str(paths["import"]) if paths["import"].exists() else None,
    }
    output_path = resolve_output_path(args.path) if args.path else paths["html"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_probability_report_html(
            survey,
            payload["rows"],
            payload["summary"],
            generated_analysis_markdown=markdown,
            generation=generation,
        )
    )
    return envelope(
        "zwill prob-results analysis-render",
        "ok",
        {"report_id": args.report_id, "path": str(output_path), "markdown_path": str(paths["markdown"])},
        next_steps=[f"open {output_path}"],
    )
