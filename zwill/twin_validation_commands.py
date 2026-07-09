from __future__ import annotations

from .cli import *  # noqa: F403


def _cli():
    from . import cli

    return cli


def selected_twin_result_job_ids(args: argparse.Namespace) -> list[str]:
    selected_job_ids = []
    if getattr(args, "job_id", None):
        selected_job_ids.extend(args.job_id if isinstance(args.job_id, list) else [args.job_id])
    if getattr(args, "jobs", None):
        selected_job_ids.extend(job_id.strip() for job_id in str(args.jobs).split(",") if job_id.strip())
    return list(dict.fromkeys(selected_job_ids))


def attach_twin_set_descriptions(sdir: Path, payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    runs_by_job_id = {str(run.get("job_id")): run for run in read_twin_run_manifest(sdir) if run.get("job_id")}
    descriptions_by_job_id: dict[str, dict[str, Any]] = {}
    for job_id in sorted({str(row.get("job_id")) for row in rows if row.get("job_id")}):
        descriptions_by_job_id[job_id] = twin_set_description(
            job_id,
            twin_import_metadata(sdir, job_id),
            runs_by_job_id.get(job_id),
        )
    twin_set_descriptions = {}
    for row in payload["rows"]:
        job_id = row.get("job_id")
        twin_set_label = row.get("twin_set_label") or row.get("model_label") or row.get("model")
        if not job_id or not twin_set_label:
            continue
        description = dict(descriptions_by_job_id.get(str(job_id), {}))
        description["model_label"] = row.get("model_label") or model_label(row.get("service"), row.get("model"))
        twin_set_descriptions[str(twin_set_label)] = description
    payload.setdefault("diagnostics", {})["twin_set_descriptions"] = twin_set_descriptions


def build_twin_job_comparison_report_payload(
    sdir: Path,
    survey: str,
    job_ids: list[str],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    if len(job_ids) < 2:
        raise ZwillError("invalid_input", "At least two digital twin job ids are required.", hint="Pass repeated --job-id values or --jobs job1,job2.")
    rows = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") in set(job_ids)]
    if model:
        rows = [
            row
            for row in rows
            if row.get("model") == model
            or row.get("model_label") == model
            or model_label(row.get("service"), row.get("model")) == model
        ]
    present_jobs = {str(row.get("job_id")) for row in rows if row.get("job_id")}
    missing_jobs = [job_id for job_id in job_ids if job_id not in present_jobs]
    if missing_jobs:
        raise ZwillError("not_found", f"No digital twin predictions found for job id(s): {', '.join(missing_jobs)}.")
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    payload = build_twin_report(rows)
    attach_twin_set_descriptions(sdir, payload, rows)
    payload["survey"] = survey
    payload["job_ids"] = job_ids
    payload["model_filter"] = model
    payload["health"] = {"job_ids": job_ids}
    return payload


def cmd_twin_results_report(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    rows = read_jsonl(digital_twin_predictions_path(sdir))
    selected_job_ids = selected_twin_result_job_ids(args)
    if selected_job_ids:
        selected_job_set = set(selected_job_ids)
        rows = [row for row in rows if row.get("job_id") in selected_job_set]
    if args.model:
        rows = [row for row in rows if row.get("model") == args.model]
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")

    payload = build_twin_report(rows)
    attach_twin_set_descriptions(sdir, payload, rows)
    if len(selected_job_ids) == 1:
        payload["health"] = {
            "job_id": selected_job_ids[0],
            "import": twin_import_metadata(sdir, selected_job_ids[0]),
        }
    else:
        payload["health"] = {
            "job_ids": selected_job_ids or sorted({row.get("job_id") for row in rows}),
        }
    report_rows = payload["rows"]
    summary = payload["summary"]
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output + "\n")
        print(output)
        return

    fieldnames = [
        "job_id",
        "respondent_id",
        "heldout_question",
        "actual_answer",
        "service",
        "model",
        "model_label",
        "probability_actual",
        "uniform_probability_actual",
        "marginal_probability_actual",
        "empirical_marginal_probability_actual",
        "negative_log_likelihood",
        "uniform_negative_log_likelihood",
        "marginal_negative_log_likelihood",
        "empirical_marginal_negative_log_likelihood",
        "brier",
        "uniform_brier",
        "marginal_brier",
        "empirical_marginal_brier",
        "brier_improvement",
        "top1_correct",
        "marginal_top1_correct",
        "empirical_marginal_top1_correct",
        "actual_rank",
    ]
    if args.format == "csv":
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
        if getattr(args, "view", "full") == "summary":
            output = render_twin_summary_report_html(args.survey, report_rows, summary, payload.get("diagnostics"), payload.get("health"))
        else:
            output = render_twin_report_html(args.survey, report_rows, summary, payload.get("diagnostics"), payload.get("health"))
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output)
        else:
            print(output)
        return

    table = Table(title=f"{args.survey} digital twin report")
    for column in ["respondent", "heldout", "actual", "model", "p(actual)", "uniform", "empirical", "nll", "brier", "top1"]:
        table.add_column(column)
    for row in report_rows:
        table.add_row(
            str(row["respondent_id"]),
            str(row["heldout_question"]),
            str(row["actual_answer"]),
            str(row.get("model_label") or row["model"]),
            f"{row['probability_actual']:.3f}",
            f"{row['uniform_probability_actual']:.3f}",
            f"{row.get('empirical_marginal_probability_actual', row.get('marginal_probability_actual')):.3f}"
            if row.get("empirical_marginal_probability_actual", row.get("marginal_probability_actual")) is not None
            else "",
            f"{row['negative_log_likelihood']:.3f}",
            f"{row['brier']:.3f}",
            str(row["top1_correct"]),
        )
    Console().print(table)

    summary_table = Table(title="model summary")
    for column in ["model", "rows", "p(actual)", "uniform p", "empirical p", "nll", "uniform nll", "empirical nll", "brier", "uniform brier", "empirical brier", "top1"]:
        summary_table.add_column(column)
    for model, values in summary.items():
        summary_table.add_row(
            model,
            str(values["rows"]),
            f"{values['mean_probability_actual']:.3f}",
            f"{values['mean_uniform_probability_actual']:.3f}",
            f"{values.get('mean_empirical_marginal_probability_actual', values.get('mean_marginal_probability_actual')):.3f}"
            if values.get("mean_empirical_marginal_probability_actual", values.get("mean_marginal_probability_actual")) is not None
            else "",
            f"{values['mean_negative_log_likelihood']:.3f}",
            f"{values['mean_uniform_negative_log_likelihood']:.3f}",
            f"{values.get('mean_empirical_marginal_negative_log_likelihood', values.get('mean_marginal_negative_log_likelihood')):.3f}"
            if values.get("mean_empirical_marginal_negative_log_likelihood", values.get("mean_marginal_negative_log_likelihood")) is not None
            else "",
            f"{values['mean_brier']:.3f}",
            f"{values['mean_uniform_brier']:.3f}",
            f"{values.get('mean_empirical_marginal_brier', values.get('mean_marginal_brier')):.3f}"
            if values.get("mean_empirical_marginal_brier", values.get("mean_marginal_brier")) is not None
            else "",
            f"{values['top1_accuracy']:.3f}",
        )
    Console().print(summary_table)


def cmd_twin_results_executive_summary(args: argparse.Namespace) -> dict[str, Any]:
    rows = filtered_twin_prediction_rows(args)
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    path = resolve_output_path(args.path or (Path("artifacts") / f"{args.survey}_executive_summary.html"))
    markdown_path = resolve_output_path(args.markdown_path) if args.markdown_path else None
    result = build_executive_summary(
        rows,
        survey=args.survey,
        path=path,
        markdown_path=markdown_path,
        simulations=args.permutations,
        seed=args.seed,
    )
    return envelope(
        "zwill twin-results executive-summary",
        "ok",
        {"survey": args.survey, **result},
        next_steps=[f"open {result['path']}"],
    )


def build_executive_summary_report_context(*args, **kwargs):
    from .generated_reports import build_executive_summary_report_context as impl

    return impl(*args, **kwargs)

def build_executive_summary_report_prompt(*args, **kwargs):
    from .generated_reports import build_executive_summary_report_prompt as impl

    return impl(*args, **kwargs)

def build_executive_summary_report_section_prompts(*args, **kwargs):
    from .generated_reports import build_executive_summary_report_section_prompts as impl

    return impl(*args, **kwargs)

def build_edsl_executive_summary_report_job_dict(*args, **kwargs):
    from .generated_reports import build_edsl_executive_summary_report_job_dict as impl

    return impl(*args, **kwargs)

def cmd_twin_results_executive_summary_export(args: argparse.Namespace) -> dict[str, Any]:
    filter_args = argparse.Namespace(**vars(args))
    filter_args.model = getattr(args, "prediction_model", None)
    rows = filtered_twin_prediction_rows(filter_args)
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    path = resolve_output_path(args.path or (Path("artifacts") / f"{args.survey}_executive_summary.html"))
    markdown_path = resolve_output_path(args.markdown_path) if args.markdown_path else None
    result = build_executive_summary(
        rows,
        survey=args.survey,
        path=path,
        markdown_path=markdown_path,
        simulations=args.permutations,
        seed=args.seed,
    )
    report_context = build_executive_summary_report_context(args, rows, result)
    job_dict, context, prompt = _cli().build_edsl_executive_summary_report_job_dict(args, report_context)
    report_id = job_dict["zwill"]["practitioner_report_id"]
    context_bytes = len(json.dumps(context, separators=(",", ":")).encode("utf-8"))
    prompt_bytes = len(prompt.encode("utf-8"))
    section_prompt_bytes = [
        {
            "question_name": question.get("question_name"),
            "prompt_bytes": len(str(question.get("question_text") or "").encode("utf-8")),
        }
        for question in ((job_dict.get("survey") or {}).get("questions") or [])
        if isinstance(question, dict)
    ]
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
        "zwill twin-results executive-summary-export",
        "ok",
        {
            **data,
            "diagnostic_html_path": str(path),
            "diagnostic_markdown_path": str(markdown_path or path.with_suffix(".md")),
            "context_bytes": context_bytes,
            "prompt_bytes": prompt_bytes,
            "section_prompt_bytes": section_prompt_bytes,
            "raw_prediction_rows_in_prompt": False,
        },
        next_steps=[
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-results executive-summary-import --report-id {report_id} --input-path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-results executive-summary-render --report-id {report_id} --path {path}",
        ],
    )


def cmd_twin_results_executive_summary_import(args: argparse.Namespace) -> dict[str, Any]:
    result = cmd_twin_benchmark_practitioner_report_import(args)
    return {
        **result,
        "command": "zwill twin-results executive-summary-import",
        "next_steps": [
            step.replace("twin-benchmark practitioner-report-render", "twin-results executive-summary-render")
            for step in result.get("next_steps", [])
        ],
    }


def cmd_twin_results_executive_summary_render(args: argparse.Namespace) -> dict[str, Any]:
    paths = default_practitioner_report_paths(args.report_id)
    if not paths["context"].exists():
        raise ZwillError("not_found", f"No exported executive summary report context found for report id {args.report_id}.")
    if not paths["markdown"].exists():
        raise ZwillError(
            "not_found",
            f"No imported generated executive summary Markdown found for report id {args.report_id}.",
            hint=f"Run `zwill twin-results executive-summary-import --report-id {args.report_id} --input-path <results.json.gz>`.",
        )
    context = read_json(paths["context"], {})
    report_context = context.get("executive_report_context", {})
    survey = report_context.get("survey")
    if not survey:
        raise ZwillError("invalid_input", f"Stored executive summary context is incomplete for report id {args.report_id}.")
    source_filters = report_context.get("source_filters", {})
    filter_args = argparse.Namespace(
        survey=survey,
        job_id=source_filters.get("job_id"),
        jobs=source_filters.get("jobs"),
        model=source_filters.get("prediction_model"),
        question=source_filters.get("question"),
        questions=source_filters.get("questions"),
    )
    rows = filtered_twin_prediction_rows(filter_args)
    if not rows:
        raise ZwillError(
            "not_found",
            "No digital twin prediction rows matched the stored executive report filters.",
            context={"source_filters": source_filters},
        )
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
    markdown_path = resolve_output_path(args.markdown_path) if args.markdown_path else output_path.with_suffix(".md")
    result = build_executive_summary(
        rows,
        survey=survey,
        path=output_path,
        markdown_path=markdown_path,
        simulations=int(report_context.get("executive_diagnostics", {}).get("individual_signal", {}).get("simulations") or args.permutations),
        seed=args.seed,
        generated_markdown=markdown,
        generation=generation,
    )
    return envelope(
        "zwill twin-results executive-summary-render",
        "ok",
        {"report_id": args.report_id, **result},
        next_steps=[f"open {result['path']}"],
    )


def cmd_twin_results_compare_report(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    job_ids = selected_twin_result_job_ids(args)
    payload = build_twin_job_comparison_report_payload(
        sdir,
        args.survey,
        job_ids,
        model=getattr(args, "model", None),
    )
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_twin_job_comparison_report_html(payload)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output)
        else:
            print(output)
        return

    table = Table(title=f"{args.survey} twin job comparison")
    for column in ["twin set", "rows", "accuracy", "p(actual)", "nll", "brier", "nll vs uniform", "nll vs empirical"]:
        table.add_column(column)
    for label, values in sorted(payload["summary"].items(), key=lambda item: item[1].get("mean_negative_log_likelihood", 0.0)):
        baseline = payload.get("diagnostics", {}).get("baseline_comparison", {}).get(label, {})
        table.add_row(
            label,
            str(values.get("rows", 0)),
            f"{values.get('top1_accuracy', 0.0):.3f}",
            f"{values.get('mean_probability_actual', 0.0):.3f}",
            f"{values.get('mean_negative_log_likelihood', 0.0):.3f}",
            f"{values.get('mean_brier', 0.0):.3f}",
            f"{baseline.get('nll_vs_uniform', 0.0):+.3f}",
            f"{baseline.get('nll_vs_empirical', 0.0):+.3f}" if baseline.get("nll_vs_empirical") is not None else "",
        )
    Console().print(table)


def cmd_twin_results_run_report(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    payload = build_twin_run_report_payload(
        sdir,
        args.survey,
        args.job_id,
        example_limit=getattr(args, "example_limit", 6),
    )
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_twin_run_report_html(payload)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output)
        else:
            print(output)
        return

    table = Table(title=f"{args.survey} twin run {args.job_id}")
    for column in ["question", "rows", "respondents", "options", "observed target answers", "models"]:
        table.add_column(column)
    for row in payload.get("questions", []):
        table.add_row(
            str(row.get("question")),
            str(row.get("prediction_rows")),
            str(row.get("respondents")),
            str(row.get("option_count")),
            str(row.get("observed_answer_summary") or ""),
            ", ".join(row.get("models", [])),
        )
    Console().print(table)
