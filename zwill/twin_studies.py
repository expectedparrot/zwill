from __future__ import annotations

from .cli import *  # noqa: F403


def _cli():
    from . import cli

    return cli


def twin_import_metadata(sdir: Path, job_id: str) -> dict[str, Any]:
    return read_json(digital_twin_jobs_dir(sdir) / job_id / "import.json", {})


def twin_run_manifest_path(sdir: Path) -> Path:
    return digital_twin_jobs_dir(sdir) / "manifest.json"


def read_twin_run_manifest(sdir: Path) -> list[dict[str, Any]]:
    manifest = read_json(twin_run_manifest_path(sdir), {"runs": []})
    runs = manifest.get("runs", [])
    known = {run.get("job_id") for run in runs}
    jobs_dir = digital_twin_jobs_dir(sdir)
    if jobs_dir.exists():
        for import_path in sorted(jobs_dir.glob("*/import.json")):
            metadata = read_json(import_path, {})
            job_id = metadata.get("job_id") or import_path.parent.name
            if job_id in known:
                continue
            runs.append(
                {
                    "job_id": job_id,
                    "survey": metadata.get("survey"),
                    "status": "imported",
                    "created_at": metadata.get("imported_at", ""),
                    "results_path": metadata.get("source_path"),
                    "stored_raw": metadata.get("stored_path"),
                    "row_count": metadata.get("row_count"),
                    "extracted_count": metadata.get("extracted_count"),
                    "issue_count": metadata.get("issue_count"),
                }
            )
    # Newest first, with job_id as a stable tie-break so runs sharing a
    # created_at (or an empty one) order deterministically rather than following
    # filesystem glob order.
    return sorted(runs, key=lambda item: (item.get("created_at", ""), str(item.get("job_id", ""))), reverse=True)


def twin_set_description(job_id: str, metadata: dict[str, Any], run: dict[str, Any] | None = None) -> dict[str, Any]:
    run = run or {}
    source_path = metadata.get("source_path") or run.get("results_path") or run.get("stored_raw") or metadata.get("stored_path")
    source_name = Path(str(source_path)).name if source_path else ""
    slug = source_name
    for suffix in [".json.gz", ".jsonl.gz", ".json", ".jsonl", ".gz"]:
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            break
    if slug.endswith("_results"):
        slug = slug[: -len("_results")]

    explicit_description = (
        metadata.get("description")
        or metadata.get("label")
        or run.get("description")
        or run.get("label")
        or run.get("name")
    )
    if explicit_description:
        description = str(explicit_description)
    elif "kitchen_sink_known_options" in slug:
        description = "Kitchen sink; known answer options included"
    elif "kitchen_sink" in slug or slug.endswith("_ks") or "_ks_" in slug:
        description = "Kitchen sink"
    elif "answer_commonness_confidence" in slug:
        description = "Answer commonness + confidence prompt"
    elif "context_marginal_answer_commonness" in slug:
        description = "Question marginal + answer commonness prompt"
    elif "context_marginal" in slug or "full_context_marginal" in slug:
        description = "Full context + question marginal prompt"
    elif slug:
        description = slug.replace("_", " ")
    else:
        description = job_id

    return {
        "job_id": job_id,
        "description": description,
        "source_path": source_path,
        "source_name": source_name,
        "row_count": metadata.get("row_count") or run.get("row_count"),
        "extracted_count": metadata.get("extracted_count") or run.get("extracted_count"),
        "issue_count": metadata.get("issue_count") or run.get("issue_count"),
        "created_at": metadata.get("imported_at") or run.get("created_at"),
    }


def natural_question_sort_key(value: Any) -> tuple[str, int, str]:
    text = str(value)
    prefix = "".join(ch for ch in text if not ch.isdigit())
    digits = "".join(ch for ch in text if ch.isdigit())
    return (prefix, int(digits) if digits else 10**9, text)


def build_twin_run_report_payload(sdir: Path, survey: str, job_id: str, *, example_limit: int = 6) -> dict[str, Any]:
    import_metadata = twin_import_metadata(sdir, job_id)
    if not import_metadata:
        raise ZwillError("not_found", f"No digital twin import metadata found for job id {job_id}.")
    run = next((item for item in read_twin_run_manifest(sdir) if item.get("job_id") == job_id), {})
    rows = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") == job_id]
    raw_path_text = import_metadata.get("stored_path") or run.get("stored_raw")
    raw_results = {}
    if raw_path_text and Path(raw_path_text).exists():
        raw_results = read_json_or_gzip(Path(raw_path_text))
    construction = raw_results.get("zwill", {}) if isinstance(raw_results, dict) else {}

    questions = []
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_question[str(row.get("heldout_question"))].append(row)
    for question, question_rows in sorted(by_question.items(), key=lambda item: natural_question_sort_key(item[0])):
        first = question_rows[0]
        observed_counts = [len(row.get("observed_answers", [])) for row in question_rows]
        actual_counts = Counter(str(row.get("actual_answer")) for row in question_rows if row.get("actual_answer") is not None)
        total_actual = sum(actual_counts.values())
        observed_answer_summary = ", ".join(
            f"{option}: {count} ({count / total_actual:.0%})"
            for option, count in actual_counts.most_common()
        )
        if total_actual:
            observed_answer_summary = f"{total_actual} non-missing; {observed_answer_summary}"
        else:
            observed_answer_summary = "No non-missing actual answers recorded"
        questions.append(
            {
                "question": question,
                "question_text": first.get("heldout_question_text"),
                "prediction_rows": len(question_rows),
                "respondents": len({row.get("respondent_id") for row in question_rows}),
                "option_count": len(first.get("option_labels", [])),
                "models": sorted({row.get("model_label") or model_label(row.get("service"), row.get("model")) for row in question_rows}),
                "mean_observed_answers": sum(observed_counts) / len(observed_counts) if observed_counts else 0.0,
                "observed_answer_summary": observed_answer_summary,
                "observed_non_missing_count": total_actual,
                "observed_answer_counts": dict(actual_counts),
            }
        )

    model_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        model_groups[str(row.get("model_label") or model_label(row.get("service"), row.get("model")))].append(row)
    models = [
        {
            "model_label": label,
            "rows": len(model_rows),
            "parameters": model_rows[0].get("model_parameters", {}) if model_rows else {},
        }
        for label, model_rows in sorted(model_groups.items())
    ]

    prompt_examples = []
    seen_example_keys = set()
    raw_survey_questions = []
    if isinstance(raw_results, dict) and isinstance(raw_results.get("survey"), dict):
        raw_survey_questions = raw_results.get("survey", {}).get("questions", []) or []
    default_prompt_template = None
    if raw_survey_questions and isinstance(raw_survey_questions[0], dict):
        default_prompt_template = raw_survey_questions[0].get("question_text")
    for raw_row in (raw_results.get("data", []) if isinstance(raw_results, dict) else []):
        scenario = raw_row.get("scenario", {}) or {}
        heldout_question = scenario.get("heldout_question_name")
        respondent_id = scenario.get("respondent_id")
        key = (heldout_question, respondent_id)
        if key in seen_example_keys:
            continue
        prompt = raw_row.get("prompt", {}) or {}
        system_prompt = None
        user_prompt = None
        for prompt_key, prompt_value in prompt.items():
            if not isinstance(prompt_value, dict):
                continue
            text = prompt_value.get("text")
            if prompt_key.endswith("_system_prompt") and system_prompt is None:
                system_prompt = text
            if prompt_key.endswith("_user_prompt") and user_prompt is None:
                user_prompt = text
        model = raw_row.get("model", {}) or {}
        prompt_template = None
        question_attrs = raw_row.get("question_to_attributes", {}) or {}
        if isinstance(question_attrs, dict):
            for attrs in question_attrs.values():
                if isinstance(attrs, dict) and attrs.get("question_text"):
                    prompt_template = attrs.get("question_text")
                    break
        answer = raw_row.get("answer", {}) or {}
        raw_model_response = raw_row.get("raw_model_response", {}) or {}
        raw_response_content = None
        if isinstance(raw_model_response, dict):
            for response_value in raw_model_response.values():
                if not isinstance(response_value, dict):
                    continue
                choices = response_value.get("choices") or []
                if choices and isinstance(choices[0], dict):
                    message = choices[0].get("message") or {}
                    if isinstance(message, dict) and message.get("content") is not None:
                        raw_response_content = message.get("content")
                        break
        indices = raw_row.get("indices", {}) or {}
        agent = raw_row.get("agent", {}) or {}
        prompt_examples.append(
            {
                "row": raw_row.get("row"),
                "respondent_id": respondent_id,
                "twin": {
                    "respondent_id": respondent_id,
                    "agent_index": indices.get("agent") if isinstance(indices, dict) else None,
                    "scenario_index": indices.get("scenario") if isinstance(indices, dict) else None,
                    "model_index": indices.get("model") if isinstance(indices, dict) else None,
                    "agent_traits": agent.get("traits", {}) if isinstance(agent, dict) else {},
                    "interview_hash": raw_row.get("interview_hash"),
                },
                "heldout_question": heldout_question,
                "heldout_question_text": scenario.get("heldout_question_text"),
                "model_label": model_label(model.get("inference_service"), model.get("model")),
                "observed_answer_count": len(scenario.get("observed_answers", [])),
                "agent_material_chars": len(scenario.get("agent_material_text") or ""),
                "twin_material_chars": len(scenario.get("twin_material_text") or ""),
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "prompt_template": prompt_template or default_prompt_template,
                "model_answer": answer,
                "model_response_text": raw_response_content,
                "raw_model_response": raw_model_response,
                "scenario": {
                    "respondent_id": respondent_id,
                    "heldout_question_name": heldout_question,
                    "heldout_question_text": scenario.get("heldout_question_text"),
                    "heldout_options": scenario.get("heldout_options", []),
                    "actual_answer": scenario.get("actual_answer"),
                    "observed_answers": scenario.get("observed_answers", []),
                    "agent_material_text": scenario.get("agent_material_text"),
                    "twin_material_text": scenario.get("twin_material_text"),
                },
            }
        )
        seen_example_keys.add(key)
        if len(prompt_examples) >= example_limit:
            break

    return {
        "survey": survey,
        "job_id": job_id,
        "run": run,
        "import": import_metadata,
        "construction": construction,
        "questions": questions,
        "models": models,
        "prompt_examples": prompt_examples,
        "raw_result_metadata": {
            "edsl_class_name": raw_results.get("edsl_class_name") if isinstance(raw_results, dict) else None,
            "data_rows": len(raw_results.get("data", [])) if isinstance(raw_results, dict) else None,
            "has_zwill_construction": bool(construction),
        },
    }


def write_twin_run_manifest(sdir: Path, runs: list[dict[str, Any]]) -> None:
    runs = sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True)
    write_json(twin_run_manifest_path(sdir), {"runs": runs})


def upsert_twin_run_manifest(sdir: Path, run: dict[str, Any]) -> None:
    runs = [item for item in read_twin_run_manifest(sdir) if item.get("job_id") != run.get("job_id")]
    runs.append(run)
    write_twin_run_manifest(sdir, runs)


def cmd_twin_study_run(args: argparse.Namespace) -> dict[str, Any]:
    cli = _cli()
    sdir = cli.require_survey(args.survey)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    approved_plan = require_twin_plan_approval(args, command="zwill twin-study run")

    job_dict = cli.build_edsl_digital_twin_job_dict(args.survey, args)
    if approved_plan:
        job_dict.setdefault("zwill", {})["approved_validation_plan"] = approved_plan
    job_id = job_dict.get("zwill", {}).get("digital_twin_job_id")
    if not job_id:
        raise ZwillError("invalid_output", "Digital twin job export did not include a job id.")

    job_path = Path(args.job_path) if args.job_path else output_dir / f"{args.survey}_twin_{job_id}.edsl.json"
    results_path = Path(args.results_path) if args.results_path else output_dir / f"{args.survey}_twin_{job_id}_results.json.gz"
    report_html_path = Path(args.report_html) if args.report_html else output_dir / f"{args.survey}_twin_{job_id}_report.html"
    report_json_path = Path(args.report_json) if args.report_json else None
    report_csv_path = Path(args.report_csv) if args.report_csv else None

    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps(job_dict, indent=2) + "\n")
    if args.dry_run:
        upsert_twin_run_manifest(
            sdir,
            {
                "job_id": job_id,
                "survey": args.survey,
                "status": "dry_run",
                "created_at": utc_now(),
                "job_path": str(job_path),
                "results_path": str(results_path),
                "report_paths": {},
                "heldout_questions": job_dict.get("zwill", {}).get("heldout_questions", []),
                "scenario_count": job_dict.get("zwill", {}).get("scenario_count"),
                "model_count": len(job_dict.get("models", [])),
                "models": [
                    model_label(model.get("inference_service"), model.get("model"))
                    for model in job_dict.get("models", [])
                ],
                "approved_validation_plan": approved_plan,
            },
        )
        return envelope(
            "zwill twin-study run",
            "ok",
            {
                "dry_run": True,
                "survey": args.survey,
                "job_id": job_id,
                "job_path": str(job_path),
                "scenario_count": job_dict.get("zwill", {}).get("scenario_count"),
                "model_count": len(job_dict.get("models", [])),
            },
        )

    run_result = cli.cmd_edsl_run(
        argparse.Namespace(
            job=str(job_path),
            path=str(results_path),
            n=args.n,
            progress_bar=args.progress_bar,
            fresh=args.fresh,
            stop_on_exception=args.stop_on_exception,
            check_api_keys=args.check_api_keys,
            verbose=args.verbose,
            print_exceptions=args.print_exceptions,
            offload_execution=args.offload_execution,
            use_api_proxy=args.use_api_proxy,
            run_param=args.run_param,
            dry_run=False,
        )
    )
    import_result = cli.cmd_twin_results_import(
        argparse.Namespace(
            survey=args.survey,
            path=str(results_path),
            job_id=job_id,
            replace=args.replace,
        )
    )

    report_paths: dict[str, str] = {}
    for report_format, report_path in [
        ("html", report_html_path),
        ("json", report_json_path),
        ("csv", report_csv_path),
    ]:
        if report_path is None:
            continue
        cli.cmd_twin_results_report(
            argparse.Namespace(
                survey=args.survey,
                job_id=job_id,
                model=None,
                format=report_format,
                path=str(report_path),
            )
        )
        report_paths[report_format] = str(report_path)

    upsert_twin_run_manifest(
        sdir,
        {
            "job_id": job_id,
            "survey": args.survey,
            "status": "ok",
            "created_at": utc_now(),
            "job_path": str(job_path),
            "results_path": str(results_path),
            "report_paths": report_paths,
            "heldout_questions": job_dict.get("zwill", {}).get("heldout_questions", []),
            "context_question_count": job_dict.get("zwill", {}).get("context_question_count"),
            "sample_respondents": job_dict.get("zwill", {}).get("sample_respondents"),
            "seed": job_dict.get("zwill", {}).get("seed"),
            "complete_cases": job_dict.get("zwill", {}).get("complete_cases"),
            "balance_actual": job_dict.get("zwill", {}).get("balance_actual"),
            "stratify_actual": job_dict.get("zwill", {}).get("stratify_actual"),
            "scenario_count": job_dict.get("zwill", {}).get("scenario_count"),
            "result_count": run_result["data"].get("result_count"),
            "extracted_count": import_result["data"].get("extracted_count"),
            "issue_count": import_result["data"].get("issue_count"),
            "model_count": len(job_dict.get("models", [])),
            "models": [
                model_label(model.get("inference_service"), model.get("model"))
                for model in job_dict.get("models", [])
            ],
            "approved_validation_plan": approved_plan,
        },
    )

    return envelope(
        "zwill twin-study run",
        "ok",
        {
            "survey": args.survey,
            "job_id": job_id,
            "job_path": str(job_path),
            "results_path": str(results_path),
            "report_paths": report_paths,
            "run": run_result["data"],
            "import": import_result["data"],
        },
        next_steps=[f"open {report_html_path}" if report_html_path else f"zwill twin-results report --survey {args.survey} --job-id {job_id}"],
    )


def cmd_twin_study_export_holdout(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.chunk_size <= 0:
        raise ZwillError("invalid_input", "--chunk-size must be positive.")
    approved_plan = require_twin_plan_approval(args, command="zwill twin-study export-holdout")
    args.allow_missing_actual = True
    job_dict = _cli().build_edsl_digital_twin_job_dict(args.survey, args)
    if approved_plan:
        job_dict.setdefault("zwill", {})["approved_validation_plan"] = approved_plan
    scenarios = list(job_dict.get("scenarios", []))
    if not scenarios:
        raise ZwillError("invalid_output", "Holdout export produced no scenarios.")
    prefix = args.job_id_prefix or f"{args.survey}_true_holdout"
    exported = []
    for chunk_index, start in enumerate(range(0, len(scenarios), args.chunk_size), start=1):
        chunk = scenarios[start : start + args.chunk_size]
        chunk_job = dict(job_dict)
        chunk_job["scenarios"] = chunk
        chunk_job["zwill"] = dict(job_dict.get("zwill", {}))
        chunk_job["zwill"]["source_digital_twin_job_id"] = job_dict.get("zwill", {}).get("digital_twin_job_id")
        chunk_job["zwill"]["digital_twin_job_id"] = chunked_job_id(prefix, chunk_index)
        chunk_job["zwill"]["chunk_index"] = chunk_index
        chunk_job["zwill"]["chunk_size"] = args.chunk_size
        chunk_job["zwill"]["chunk_scenario_count"] = len(chunk)
        path = output_dir / f"chunk_{chunk_index:03d}_job.edsl.json"
        path.write_text(json.dumps(chunk_job, indent=2) + "\n")
        exported.append(
            {
                "chunk_index": chunk_index,
                "job_id": chunk_job["zwill"]["digital_twin_job_id"],
                "job_path": str(path),
                "scenario_count": len(chunk),
                "default_results_path": str(output_dir / f"chunk_{chunk_index:03d}_results.json.gz"),
            }
        )
    manifest = {
        "survey": args.survey,
        "job_id_prefix": slugify(prefix).lower() or "twin_holdout",
        "created_at": utc_now(),
        "prompt_variant": getattr(args, "prompt_variant", "raw"),
        "heldout_questions": job_dict.get("zwill", {}).get("heldout_questions", []),
        "question_specs": getattr(args, "question_specs", None),
        "question_specs_workbook": getattr(args, "question_specs_workbook", None),
        "scenario_count": len(scenarios),
        "chunk_size": args.chunk_size,
        "chunk_count": len(exported),
        "approved_validation_plan": approved_plan,
        "exports": exported,
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return envelope(
        "zwill twin-study export-holdout",
        "ok",
        {
            "manifest_path": str(manifest_path),
            "output_dir": str(output_dir),
            "chunk_count": len(exported),
            "scenario_count": len(scenarios),
            "exports": exported,
        },
        next_steps=[
            f"zwill edsl-run --job {exported[0]['job_path']} --path {exported[0]['default_results_path']}" if exported else "",
            f"zwill twin-study import-results-dir --survey {args.survey} --results-dir {output_dir} --job-id-prefix {slugify(prefix).lower()} --allow-missing-actual",
        ],
    )


def cmd_twin_study_import_results_dir(args: argparse.Namespace) -> dict[str, Any]:
    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        raise ZwillError("not_found", f"Results directory does not exist: {results_dir}.")
    patterns = args.pattern or ["*results*.json.gz", "*results*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(sorted(path for path in results_dir.glob(pattern) if path.is_file()))
    deduped = []
    seen_paths = set()
    for path in paths:
        if path in seen_paths or path.name.endswith("_job.edsl.json"):
            continue
        seen_paths.add(path)
        deduped.append(path)
    if not deduped:
        raise ZwillError("not_found", "No result files found in directory.", context={"results_dir": str(results_dir), "patterns": patterns})
    prefix = slugify(args.job_id_prefix or results_dir.name).lower()
    imports = []
    for index, path in enumerate(deduped, start=1):
        label = result_chunk_label(path, index)
        job_id = f"{prefix}_{label}"
        result = _cli().cmd_twin_results_import(
            argparse.Namespace(
                survey=args.survey,
                path=str(path),
                job_id=job_id,
                replace=args.replace,
                allow_missing_actual=args.allow_missing_actual,
            )
        )
        imports.append(
            {
                "path": str(path),
                "job_id": job_id,
                "row_count": result["data"].get("row_count"),
                "extracted_count": result["data"].get("extracted_count"),
                "issue_count": result["data"].get("issue_count"),
            }
        )
    manifest = {
        "survey": args.survey,
        "results_dir": str(results_dir),
        "job_id_prefix": prefix,
        "imported_at": utc_now(),
        "result_count": len(imports),
        "imports": imports,
    }
    manifest_path = results_dir / "import_results_manifest.json"
    write_json(manifest_path, manifest)
    return envelope(
        "zwill twin-study import-results-dir",
        "ok",
        {
            "manifest_path": str(manifest_path),
            "result_count": len(imports),
            "extracted_count": sum(int(row.get("extracted_count") or 0) for row in imports),
            "issue_count": sum(int(row.get("issue_count") or 0) for row in imports),
            "imports": imports,
        },
        next_steps=[f"zwill twin-results export --survey {args.survey} --jobs {','.join(row['job_id'] for row in imports)} --path predictions.csv"],
    )


def cmd_twin_study_list(args: argparse.Namespace) -> None:
    sdir = _cli().require_survey(args.survey)
    runs = read_twin_run_manifest(sdir)
    if args.format == "json":
        print(json.dumps({"survey": args.survey, "runs": runs}, indent=2))
        return
    table = Table(title=f"{args.survey} twin studies")
    for column in ["job_id", "status", "created_at", "rows", "issues", "heldout", "models"]:
        table.add_column(column)
    for run in runs:
        table.add_row(
            str(run.get("job_id", "")),
            str(run.get("status", "")),
            str(run.get("created_at", "")),
            str(run.get("extracted_count", run.get("result_count", run.get("scenario_count", "")))),
            str(run.get("issue_count", "")),
            ",".join(run.get("heldout_questions", [])),
            ", ".join(run.get("models", [])),
        )
    Console().print(table)


def cmd_twin_study_show(args: argparse.Namespace) -> dict[str, Any]:
    sdir = _cli().require_survey(args.survey)
    runs = read_twin_run_manifest(sdir)
    run = next((item for item in runs if item.get("job_id") == args.job_id), None)
    if run is None:
        import_metadata = twin_import_metadata(sdir, args.job_id)
        if not import_metadata:
            raise ZwillError("not_found", f"No digital twin study found for job id {args.job_id}.")
        run = {"job_id": args.job_id, "survey": args.survey, "status": "imported", "import": import_metadata}
    rows = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") == args.job_id]
    data = {"run": run, "import": twin_import_metadata(sdir, args.job_id), "row_count": len(rows)}
    if args.include_summary and rows:
        report = build_twin_report(rows)
        data["summary"] = report["summary"]
        data["diagnostics"] = {
            "baseline_comparison": report["diagnostics"]["baseline_comparison"],
            "model_wins": report["diagnostics"]["model_wins"][:10],
            "empirical_wins": report["diagnostics"]["empirical_wins"][:10],
        }
    return envelope("zwill twin-study show", "ok", data)


def cmd_twin_study_compare(args: argparse.Namespace) -> None:
    sdir = _cli().require_survey(args.survey)
    selected_job_ids = args.job_id or []
    if args.jobs:
        selected_job_ids.extend(job_id.strip() for job_id in args.jobs.split(",") if job_id.strip())
    if len(selected_job_ids) < 2:
        raise ZwillError("invalid_input", "At least two --job-id values are required for comparison.")
    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    runs = []
    for job_id in selected_job_ids:
        rows = [row for row in all_rows if row.get("job_id") == job_id]
        if not rows:
            raise ZwillError("not_found", f"No digital twin predictions found for job id {job_id}.")
        report = build_twin_report(rows)
        runs.append({"job_id": job_id, "summary": report["summary"], "diagnostics": report["diagnostics"]})
    comparisons = []
    for run in runs:
        for model, values in run["summary"].items():
            comparisons.append(
                {
                    "job_id": run["job_id"],
                    "model": model,
                    "rows": values["rows"],
                    "accuracy": values["top1_accuracy"],
                    "mean_probability_actual": values["mean_probability_actual"],
                    "mean_negative_log_likelihood": values["mean_negative_log_likelihood"],
                    "mean_brier": values["mean_brier"],
                    "nll_vs_empirical": run["diagnostics"]["baseline_comparison"][model].get("nll_vs_empirical"),
                    "brier_vs_empirical": run["diagnostics"]["baseline_comparison"][model].get("brier_vs_empirical"),
                }
            )
    response_changes = []
    for index, from_job_id in enumerate(selected_job_ids):
        for to_job_id in selected_job_ids[index + 1 :]:
            response_changes.extend(_cli().paired_twin_response_changes(all_rows, from_job_id, to_job_id))
    payload = {
        "survey": args.survey,
        "job_ids": selected_job_ids,
        "comparisons": comparisons,
        "response_changes": response_changes,
    }
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "csv":
        fieldnames = list(comparisons[0].keys())
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.path).open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(comparisons)
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(comparisons)
        return
    table = Table(title=f"{args.survey} twin study comparison")
    for column in ["job_id", "model", "rows", "accuracy", "p(actual)", "nll", "brier", "nll vs empirical"]:
        table.add_column(column)
    for row in comparisons:
        table.add_row(
            row["job_id"],
            row["model"],
            str(row["rows"]),
            f"{row['accuracy']:.3f}",
            f"{row['mean_probability_actual']:.3f}",
            f"{row['mean_negative_log_likelihood']:.3f}",
            f"{row['mean_brier']:.3f}",
            f"{row['nll_vs_empirical']:+.3f}" if row["nll_vs_empirical"] is not None else "",
        )
    Console().print(table)
    if response_changes:
        change_table = Table(title="Paired top-choice changes")
        for column in ["from", "to", "model", "paired", "changed", "corrections", "regressions", "p(actual) delta", "NLL delta"]:
            change_table.add_column(column)
        for row in response_changes:
            change_table.add_row(
                str(row["from_label"]),
                str(row["to_label"]),
                str(row["model"]),
                str(row["paired_rows"]),
                f"{row['changed_top_choice']} ({row['changed_top_choice_rate']:.1%})",
                str(row["corrections"]),
                str(row["regressions"]),
                f"{row['mean_probability_actual_delta']:+.3f}",
                f"{row['mean_nll_delta']:+.3f}",
            )
        Console().print(change_table)
