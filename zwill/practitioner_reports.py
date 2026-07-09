from __future__ import annotations

from .cli import *  # noqa: F403


def _cli():
    from . import cli

    return cli


def load_twin_benchmark_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    config = read_json(config_path, {})
    if not isinstance(config, dict) or not isinstance(config.get("studies"), list):
        raise ZwillError("invalid_input", "Benchmark config must be a JSON object with a studies list.")
    config["_config_path"] = str(config_path)
    return config


def benchmark_name(config: dict[str, Any]) -> str:
    return str(config.get("name") or "twin_benchmark")


def benchmark_output_dir(config: dict[str, Any], override: str | None = None) -> Path:
    if override:
        return Path(override)
    if config.get("output_dir"):
        return Path(config["output_dir"])
    return Path(config["_config_path"]).parent


def benchmark_manifest_path(config: dict[str, Any], output_dir: Path) -> Path:
    return output_dir / f"{benchmark_name(config)}_run.json"


def list_value(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def benchmark_study_namespace(config: dict[str, Any], study: dict[str, Any], output_dir: Path, dry_run: bool, replace: bool) -> argparse.Namespace:
    defaults = config.get("defaults", {})
    models = study.get("models", config.get("models", defaults.get("models")))
    model_params = study.get("model_params", config.get("model_params", defaults.get("model_params")))
    return argparse.Namespace(
        survey=study["survey"],
        output_dir=str(output_dir),
        job_path=None,
        results_path=None,
        report_html=None,
        report_json=None,
        report_csv=None,
        replace=replace,
        dry_run=dry_run,
        approved_plan=study.get("approved_plan", config.get("approved_plan", defaults.get("approved_plan"))),
        allow_unapproved=bool(study.get("allow_unapproved", config.get("allow_unapproved", defaults.get("allow_unapproved", False)))),
        question=None,
        questions=None,
        exclude_question=None,
        heldout_question=list_value(study.get("heldout_question")),
        heldout_questions=study.get("heldout_questions"),
        respondent=list_value(study.get("respondent")),
        respondents=study.get("respondents"),
        sample_respondents=study.get("sample_respondents", defaults.get("sample_respondents")),
        seed=study.get("seed", defaults.get("seed")),
        complete_cases=bool(study.get("complete_cases", defaults.get("complete_cases", False))),
        balance_actual=bool(study.get("balance_actual", defaults.get("balance_actual", False))),
        stratify_actual=bool(study.get("stratify_actual", defaults.get("stratify_actual", False))),
        limit_respondents=study.get("limit_respondents", defaults.get("limit_respondents")),
        context_question=list_value(study.get("context_question")),
        context_questions=study.get("context_questions"),
        exclude_context_question=list_value(study.get("exclude_context_question")),
        leakage_exclusion=list_value(study.get("leakage_exclusion", defaults.get("leakage_exclusion"))),
        context_question_count=study.get("context_question_count", defaults.get("context_question_count")),
        twin_material=list_value(study.get("twin_material", defaults.get("twin_material"))),
        max_twin_material_chars=study.get("max_twin_material_chars", defaults.get("max_twin_material_chars")),
        model=list_value(models),
        models=None,
        service_name=study.get("service_name", config.get("service_name", defaults.get("service_name"))),
        model_param=list_value(model_params),
        job_question_name=study.get("job_question_name", defaults.get("job_question_name", "response_probabilities")),
        n=None,
        progress_bar=False,
        fresh=False,
        stop_on_exception=False,
        check_api_keys=False,
        verbose=None,
        print_exceptions=None,
        offload_execution=False,
        use_api_proxy=False,
        run_param=None,
    )


def build_twin_benchmark_report(config: dict[str, Any], studies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    report_rows = []
    source_studies = studies or config["studies"]
    for study in source_studies:
        survey = study["survey"]
        job_id = study.get("job_id")
        if not job_id:
            raise ZwillError("invalid_input", "Each benchmark study needs a job_id for report generation.", context={"study": study})
        sdir = require_survey(survey)
        rows = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") == job_id]
        if not rows:
            raise ZwillError("not_found", f"No digital twin predictions found for benchmark job {job_id}.")
        twin_report = build_twin_report(rows)
        option_counts = sorted({len(row.get("option_labels", [])) for row in rows})
        heldout_questions = sorted({str(row.get("heldout_question")) for row in rows})
        for model, values in twin_report["summary"].items():
            baseline = twin_report["diagnostics"]["baseline_comparison"][model]
            report_rows.append(
                {
                    "benchmark": benchmark_name(config),
                    "survey": survey,
                    "job_id": job_id,
                    "heldout_questions": ",".join(heldout_questions),
                    "option_count": option_counts[0] if len(option_counts) == 1 else None,
                    "model": model,
                    "rows": values["rows"],
                    "accuracy": values["top1_accuracy"],
                    "p_actual": values["mean_probability_actual"],
                    "nll": values["mean_negative_log_likelihood"],
                    "nll_p95": values.get("negative_log_likelihood_p95"),
                    "brier": values["mean_brier"],
                    "ece": values.get("expected_calibration_error"),
                    "nll_vs_empirical": baseline.get("nll_vs_empirical"),
                    "brier_vs_empirical": baseline.get("brier_vs_empirical"),
                }
            )
    summary = {}
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report_rows:
        by_model[row["model"]].append(row)
    for model, model_rows in by_model.items():
        valid_nll = [row for row in model_rows if row.get("nll_vs_empirical") is not None]
        summary[model] = {
            "survey_count": len(model_rows),
            "mean_accuracy": sum(row["accuracy"] for row in model_rows) / len(model_rows),
            "mean_nll": sum(row["nll"] for row in model_rows) / len(model_rows),
            "mean_brier": sum(row["brier"] for row in model_rows) / len(model_rows),
            "mean_ece": sum(row["ece"] for row in model_rows if row.get("ece") is not None) / len([row for row in model_rows if row.get("ece") is not None]),
            "mean_nll_vs_empirical": sum(row["nll_vs_empirical"] for row in valid_nll) / len(valid_nll) if valid_nll else None,
        }
    return {"benchmark": benchmark_name(config), "rows": report_rows, "summary": summary, "config": {k: v for k, v in config.items() if not k.startswith("_")}}


def build_single_survey_practitioner_payload(survey: str, job_id: str) -> dict[str, Any]:
    sdir = require_survey(survey)
    rows = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") == job_id]
    if not rows:
        raise ZwillError("not_found", f"No digital twin predictions found for job id {job_id}.")
    twin_report = build_twin_report(rows)
    option_counts = sorted({len(row.get("option_labels", [])) for row in rows})
    heldout_questions = sorted({str(row.get("heldout_question")) for row in rows})
    report_rows = []
    for model, values in twin_report["summary"].items():
        baseline = twin_report["diagnostics"]["baseline_comparison"][model]
        report_rows.append(
            {
                "benchmark": f"{survey}_twin_validation",
                "survey": survey,
                "job_id": job_id,
                "heldout_questions": ",".join(heldout_questions),
                "option_count": option_counts[0] if len(option_counts) == 1 else None,
                "model": model,
                "rows": values["rows"],
                "accuracy": values["top1_accuracy"],
                "p_actual": values["mean_probability_actual"],
                "nll": values["mean_negative_log_likelihood"],
                "nll_p95": values.get("negative_log_likelihood_p95"),
                "brier": values["mean_brier"],
                "ece": values.get("expected_calibration_error"),
                "nll_vs_empirical": baseline.get("nll_vs_empirical"),
                "brier_vs_empirical": baseline.get("brier_vs_empirical"),
            }
        )
    return {
        "benchmark": f"{survey} digital twin validation",
        "report_kind": "single_survey_twin_validation",
        "survey": survey,
        "job_id": job_id,
        "rows": report_rows,
        "summary": twin_report["summary"],
        "summary_by_question": twin_report.get("summary_by_question", {}),
        "diagnostics": twin_report.get("diagnostics", {}),
        "config": {"kind": "single_survey_twin_validation", "survey": survey, "job_id": job_id},
    }


def cmd_twin_benchmark_run(args: argparse.Namespace) -> dict[str, Any]:
    config = load_twin_benchmark_config(args.config)
    output_dir = benchmark_output_dir(config, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Load API keys before the studies make model calls (parity with edsl-run).
    # Skipped for --dry-run, which only exports jobs.
    loaded_env = None
    if not args.dry_run:
        env_path = Path(args.env_path) if getattr(args, "env_path", None) else None
        loaded_env = load_local_env(env_path)
    runs = []
    for study in config["studies"]:
        run_args = benchmark_study_namespace(config, study, output_dir, args.dry_run, args.replace)
        result = _cli().cmd_twin_study_run(run_args)
        data = result["data"]
        runs.append(
            {
                **study,
                "job_id": data["job_id"],
                "status": "dry_run" if args.dry_run else "ok",
                "job_path": data.get("job_path"),
                "results_path": data.get("results_path"),
                "report_paths": data.get("report_paths", {}),
            }
        )
    manifest = {
        "benchmark": benchmark_name(config),
        "config_path": config["_config_path"],
        "created_at": utc_now(),
        "dry_run": args.dry_run,
        "runs": runs,
    }
    manifest_path = Path(args.manifest) if args.manifest else benchmark_manifest_path(config, output_dir)
    write_json(manifest_path, manifest)
    return envelope(
        "zwill twin-benchmark run",
        "ok",
        {"benchmark": benchmark_name(config), "manifest_path": str(manifest_path), "runs": runs, "loaded_env": loaded_env},
        next_steps=[f"zwill twin-benchmark report --manifest {manifest_path} --format html --path {output_dir / (benchmark_name(config) + '_report.html')}"],
    )


def cmd_twin_benchmark_report(args: argparse.Namespace) -> None:
    if args.manifest:
        manifest = read_json(Path(args.manifest), {})
        config = {"name": manifest.get("benchmark", "twin_benchmark"), "studies": manifest.get("runs", []), "_config_path": args.manifest}
        studies = manifest.get("runs", [])
    elif args.config:
        config = load_twin_benchmark_config(args.config)
        studies = config["studies"]
    else:
        raise ZwillError("invalid_input", "Use --config or --manifest.")
    payload = build_twin_benchmark_report(config, studies)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "csv":
        fieldnames = list(payload["rows"][0]) if payload["rows"] else []
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            with resolve_output_path(args.path).open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(payload["rows"])
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(payload["rows"])
        return
    output = render_twin_benchmark_report_html(payload)
    if args.path:
        resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
        resolve_output_path(args.path).write_text(output)
    else:
        print(output)


def compact_prediction_row(row: dict[str, Any]) -> dict[str, Any]:
    predicted = row.get("predicted_option")
    if not predicted and row.get("probabilities"):
        predicted = max(row["probabilities"].items(), key=lambda item: float(item[1]))[0]
    return {
        "survey": row.get("survey"),
        "job_id": row.get("job_id"),
        "respondent_id": row.get("respondent_id"),
        "heldout_question": row.get("heldout_question"),
        "heldout_question_text": row.get("heldout_question_text"),
        "actual_answer": row.get("actual_answer"),
        "predicted_option": predicted,
        "probability_actual": row.get("probability_actual"),
        "negative_log_likelihood": row.get("negative_log_likelihood"),
        "top1_correct": row.get("top1_correct"),
        "model": row.get("model_label") or model_label(row.get("service"), row.get("model")),
        "raw_model_response": row.get("raw_model_response"),
    }


def build_practitioner_report_context(payload: dict[str, Any], studies: list[dict[str, Any]]) -> dict[str, Any]:
    by_study = []
    for study in studies:
        survey = study["survey"]
        job_id = study.get("job_id")
        if not job_id:
            continue
        sdir = require_survey(survey)
        questions = questions_by_name(sdir)
        prediction_rows = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") == job_id]
        twin_report = build_twin_report(prediction_rows) if prediction_rows else {}
        heldout_names = sorted({str(row.get("heldout_question")) for row in prediction_rows})
        heldout_questions = [
            {
                "question_name": name,
                "question_text": questions.get(name, {}).get("question_text"),
                "question_options": questions.get(name, {}).get("question_options", []),
            }
            for name in heldout_names
        ]
        diagnostics = twin_report.get("diagnostics", {})
        by_study.append(
            {
                "survey": survey,
                "survey_summary": survey_summary(survey),
                "survey_context": context_path(sdir).read_text().strip() if context_path(sdir).exists() else "",
                "raw_files": read_json(sdir / "raw_files.json", []),
                "job_id": job_id,
                "study_config": study,
                "run_manifest": next((run for run in read_twin_run_manifest(sdir) if run.get("job_id") == job_id), {}),
                "import_metadata": twin_import_metadata(sdir, job_id),
                "heldout_questions": heldout_questions,
                "summary_by_model": twin_report.get("summary", {}),
                "summary_by_question": twin_report.get("summary_by_question", {}),
                "baseline_comparison": diagnostics.get("baseline_comparison", {}),
                "model_wins_over_group_average": diagnostics.get("model_wins", [])[:10],
                "group_average_wins": diagnostics.get("empirical_wins", [])[:10],
                "overconfident_misses": [compact_prediction_row(row) for row in diagnostics.get("overconfident_misses", [])[:10]],
                "worst_misses": [compact_prediction_row(row) for row in diagnostics.get("worst_misses", [])[:10]],
                "confusion": diagnostics.get("confusion", {}),
            }
        )
    return {
        "benchmark": payload,
        "report_kind": payload.get("report_kind", "cross_survey_benchmark"),
        "studies": by_study,
        "notes": {
            "group_average_guessing": "The empirical marginal baseline: guessing from how the whole sample answered the held-out question. It is available for observed held-out questions but not for genuinely new questions.",
            "accuracy": "How often the twin's highest-probability answer matched the real respondent answer.",
            "confidence_quality": "Whether the model's confidence matched reality. Overconfident misses are especially important when using rankings or probability cutoffs.",
        },
    }


def practitioner_report_skill_text() -> str:
    path = installed_skill_path("digital-twin-practitioner-report") / "SKILL.md"
    return path.read_text()


def build_practitioner_report_prompt(report_context: dict[str, Any]) -> str:
    report_kind = report_context.get("report_kind")
    if report_kind == "single_survey_twin_validation":
        scope_guidance = (
            "This is a single-survey twin validation report, not a cross-survey benchmark. "
            "Frame the report around the uploaded survey, its source/context, the respondent sample, "
            "the held-out question or questions, and what this validation says about using twins for "
            "new questions from the same survey domain. The executive summary should give that context "
            "before making recommendations. The first paragraph of the executive summary must start by "
            "describing what was validated: the survey/source, the held-out question family, the number "
            "of tested respondent-question cases, and the model or models. Then explain what uses the "
            "evidence supports. Prefer phrases like \"this survey's twins\" or \"the climate-policy "
            "validation\" over broad phrases like \"these twins.\" Do not present the report as a "
            "collection of unrelated cross-survey exercises."
        )
    else:
        scope_guidance = (
            "This report may contain multiple distinct twin exercises. Do not write as if there is one "
            "homogeneous set of twins with one overall use recommendation. Separate claims by survey, "
            "held-out question family, option structure, respondent sample size, and model when those differ. "
            "Prefer wording such as \"the climate-policy exercise,\" \"the multi-option skill-importance "
            "exercise,\" or \"the vignette exercise\" over broad phrases like \"these twins\" when the claim "
            "is exercise-specific."
        )
    return f"""You are writing a detailed practitioner-focused report about survey digital twins.

Follow this report-writing guidance exactly:

{practitioner_report_skill_text()}

Use the recorded Expected Parrot study context and validation data below. Do not invent data. If a finding depends on a small sample, say so. Explain the survey context, study design, performance, baselines, where twins worked, where they failed, and how a practitioner should use the results. Lead with decisions and implications, but include enough concrete evidence to support the recommendations.

Do not write a general explainer about what digital twins are, do not cite the academic literature, do not explain persona-based reasoning, do not explain Expected Parrot, and do not write a generic explanation of why held-out questions are used. The HTML wrapper inserts canned sections for that.

Do not write a generic decision-stakes ladder, generic calibration warning, generic discussion of infeasible survey targets, or generic discussion of rank ordering versus exact levels. The HTML wrapper inserts canned guidance about matching evidence to intended use, infeasible direct measurement, rank ordering, surfacing considerations, reading results by exercise, and the hold-out study design. Your report should instead focus on the concrete survey context, study design, performance evidence, where twins worked, where they failed, and the specific implications of those results.

{scope_guidance}

When the reusable ideas matter, apply them to this benchmark rather than re-explaining them. Organize adequacy primarily by intended use: exact quantitative estimates, ranking/prioritization, and exploration/surfacing considerations. Do not use low-/medium-/high-stakes categories as the primary structure of the report or as the first explanation for why an exercise is usable. Stakes can be mentioned briefly as a secondary reason to seek more validation. For example: say which tested question families are credible enough for exact quantitative estimates, which are only credible for ranking or prioritization, which are useful mainly for surfacing considerations, whether held-out performance is a good proxy for the kinds of new questions a practitioner cares about, and whether any observed confidence failures would make rankings, thresholds, or targeting risky.

Write the report in Markdown only. Do not include markdown fences. Do not include a top-level Markdown title; the HTML wrapper supplies the title. Do not mention that you are an AI. Do not mention the internal tool name "zwill"; refer to Expected Parrot, EDSL, or the recorded study artifacts instead. Make it detailed enough that a practitioner can understand what was tested and how to use the results.

Recorded Expected Parrot study context:

{json.dumps(report_context, indent=2)}
"""


def practitioner_report_id_from_job(job_dict: dict[str, Any]) -> str:
    payload = {key: value for key, value in job_dict.items() if key != "zwill"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_edsl_practitioner_report_job_dict(
    args: argparse.Namespace,
    payload: dict[str, Any],
    studies: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    report_context = build_practitioner_report_context(payload, studies)
    prompt = build_practitioner_report_prompt(report_context)
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = load_edsl_job_classes()
    question_name = "practitioner_report_markdown"
    question = QuestionFreeText(question_name=question_name, question_text=prompt)
    model_params = parse_model_params(args)
    model_specs = parse_model_specs(args)
    job = Jobs(
        survey=Survey(questions=[question]),
        scenarios=ScenarioList([Scenario({})]),
        models=ModelList(
            [
                Model(
                    model_name=model_name,
                    service_name=service_name,
                    **model_kwargs_for(model_name, service_name, model_params),
                )
                for model_name, service_name in model_specs
            ]
        ),
    )
    job_dict = job.to_dict()
    report_id = practitioner_report_id_from_job(job_dict)
    job_dict["zwill"] = {
        **job_dict.get("zwill", {}),
        "practitioner_report_id": report_id,
        "practitioner_report_question_name": question_name,
    }
    generation = {
        "mode": "job_exported",
        "report_id": report_id,
        "model": model_label(model_specs[0][1], model_specs[0][0]) if model_specs else None,
        "models": [model_label(service_name, model_name) for model_name, service_name in model_specs],
    }
    context = {
        "report_id": report_id,
        "benchmark_payload": payload,
        "report_context": report_context,
        "studies": studies,
        "prompt": prompt,
        "generation": generation,
    }
    return job_dict, context, prompt


def default_practitioner_report_paths(report_id: str) -> dict[str, Path]:
    rdir = practitioner_report_dir(report_id)
    return {
        "dir": rdir,
        "job": rdir / "job.edsl.json",
        "prompt": rdir / "prompt.md",
        "context": rdir / "context.json",
        "markdown": rdir / "report.md",
        "html": rdir / "report.html",
        "import": rdir / "import.json",
        "raw": rdir / "raw",
    }


def write_practitioner_report_export(
    report_id: str,
    job_dict: dict[str, Any],
    context: dict[str, Any],
    prompt: str,
    *,
    job_path: Path | None = None,
    prompt_path: Path | None = None,
    context_path_arg: Path | None = None,
) -> dict[str, str]:
    paths = default_practitioner_report_paths(report_id)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    stored_job_path = job_path or paths["job"]
    stored_prompt_path = prompt_path or paths["prompt"]
    stored_context_path = context_path_arg or paths["context"]
    write_json(stored_job_path, job_dict)
    stored_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    stored_prompt_path.write_text(prompt)
    write_json(stored_context_path, context)
    if stored_job_path != paths["job"]:
        write_json(paths["job"], job_dict)
    if stored_prompt_path != paths["prompt"]:
        paths["prompt"].write_text(prompt)
    if stored_context_path != paths["context"]:
        write_json(paths["context"], context)
    return {
        "report_id": report_id,
        "report_dir": str(paths["dir"]),
        "job_path": str(stored_job_path),
        "stored_job_path": str(paths["job"]),
        "prompt_path": str(stored_prompt_path),
        "stored_prompt_path": str(paths["prompt"]),
        "context_path": str(stored_context_path),
        "stored_context_path": str(paths["context"]),
    }


def extract_free_text_answer(results_dict: dict[str, Any], question_name: str, *, allow_fallback: bool = True) -> str:
    inspected: list[dict[str, Any]] = []
    for row in results_dict.get("data", []):
        answer = row.get("answer", {})
        if isinstance(answer, dict):
            value = answer.get(question_name)
            if value is None and answer and allow_fallback:
                value = next(iter(answer.values()))
        else:
            value = answer
        inspected.append(
            {
                "answer_type": type(value).__name__,
                "answer_is_null": value is None,
                "answer_keys": sorted(answer) if isinstance(answer, dict) else [],
                "raw_model_response_empty": not bool(row.get("raw_model_response")),
                "generated_tokens": row.get("generated_tokens") or row.get("usage", {}).get("completion_tokens"),
            }
        )
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ZwillError(
        "edsl_run_failed",
        "Report-writing job ran but returned no Markdown text.",
        hint="Inspect the stored Results object. If answers are null or raw_model_response is empty, rerun with a smaller compact context or a report model with a larger context/output budget.",
        context={"question_name": question_name, "rows": inspected[:10], "row_count": len(results_dict.get("data", []))},
    )


def extract_free_text_answers(results_dict: dict[str, Any], question_names: list[str]) -> dict[str, str]:
    answers = {}
    errors = []
    for question_name in question_names:
        try:
            answers[question_name] = extract_free_text_answer(results_dict, question_name, allow_fallback=False)
        except ZwillError as exc:
            errors.append({"question_name": question_name, "error": exc.message, "context": exc.context})
    if errors:
        raise ZwillError(
            "edsl_run_failed",
            "Report-writing job ran but one or more sections returned no Markdown text.",
            hint="Inspect the stored Results object. If a section is empty, rerun with smaller section context or lower reasoning effort.",
            context={"section_errors": errors, "row_count": len(results_dict.get("data", []))},
        )
    return answers


def cmd_twin_benchmark_practitioner_report_export(args: argparse.Namespace) -> dict[str, Any]:
    config, studies = load_twin_benchmark_report_source(args)
    payload = build_twin_benchmark_report(config, studies)
    job_dict, context, prompt = _cli().build_edsl_practitioner_report_job_dict(args, payload, studies)
    report_id = job_dict["zwill"]["practitioner_report_id"]
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
        "zwill twin-benchmark practitioner-report-export",
        "ok",
        data,
        next_steps=[
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-benchmark practitioner-report-import --report-id {report_id} --input-path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-benchmark practitioner-report-render --report-id {report_id}",
        ],
    )


def cmd_twin_study_practitioner_report_export(args: argparse.Namespace) -> dict[str, Any]:
    payload = build_single_survey_practitioner_payload(args.survey, args.job_id)
    studies = [{"survey": args.survey, "job_id": args.job_id}]
    job_dict, context, prompt = _cli().build_edsl_practitioner_report_job_dict(args, payload, studies)
    report_id = job_dict["zwill"]["practitioner_report_id"]
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
        "zwill twin-study practitioner-report-export",
        "ok",
        data,
        next_steps=[
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-study practitioner-report-import --report-id {report_id} --input-path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-study practitioner-report-render --report-id {report_id}",
        ],
    )


def cmd_twin_benchmark_practitioner_report_import(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.input_path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.input_path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    report_id = args.report_id or results.get("zwill", {}).get("practitioner_report_id")
    if not report_id:
        raise ZwillError("invalid_input", "Could not determine practitioner report id.", hint="Pass --report-id.")
    paths = default_practitioner_report_paths(report_id)
    if not paths["context"].exists():
        raise ZwillError("not_found", f"No exported practitioner report context found for report id {report_id}.")
    if paths["import"].exists() and not args.replace:
        raise ZwillError("already_exists", f"Practitioner report results already imported for report id {report_id}.", hint="Use --replace.")
    raw_dir = paths["raw"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)
    question_names = results.get("zwill", {}).get("practitioner_report_question_names")
    if isinstance(question_names, list) and question_names:
        section_answers = extract_free_text_answers(results, [str(name) for name in question_names])
        markdown = "\n\n".join(section_answers[str(name)] for name in question_names)
        question_name = ",".join(str(name) for name in question_names)
    else:
        question_name = results.get("zwill", {}).get("practitioner_report_question_name", "practitioner_report_markdown")
        markdown = extract_free_text_answer(results, question_name)
    paths["markdown"].write_text(markdown + "\n")
    write_json(
        paths["import"],
        {
            "report_id": report_id,
            "source_path": str(source),
            "source_hash": sha256(source),
            "stored_path": str(stored_raw),
            "stored_hash": sha256(stored_raw),
            "row_count": len(results.get("data", [])),
            "question_name": question_name,
            "markdown_path": str(paths["markdown"]),
            "imported_at": utc_now(),
        },
    )
    return envelope(
        "zwill twin-benchmark practitioner-report-import",
        "ok",
        {
            "report_id": report_id,
            "stored_raw": str(stored_raw),
            "markdown_path": str(paths["markdown"]),
            "row_count": len(results.get("data", [])),
        },
        next_steps=[f"zwill twin-benchmark practitioner-report-render --report-id {report_id}"],
    )


def cmd_twin_study_practitioner_report_import(args: argparse.Namespace) -> dict[str, Any]:
    result = cmd_twin_benchmark_practitioner_report_import(args)
    return {
        **result,
        "command": "zwill twin-study practitioner-report-import",
        "next_steps": [
            step.replace("twin-benchmark", "twin-study")
            for step in result.get("next_steps", [])
        ],
    }


def cmd_twin_benchmark_practitioner_report_render(args: argparse.Namespace) -> None:
    report_id = args.report_id
    paths = default_practitioner_report_paths(report_id)
    if not paths["context"].exists():
        raise ZwillError("not_found", f"No exported practitioner report context found for report id {report_id}.")
    if not paths["markdown"].exists():
        raise ZwillError(
            "not_found",
            f"No imported practitioner report Markdown found for report id {report_id}.",
            hint=f"Run `zwill twin-benchmark practitioner-report-import --report-id {report_id} --input-path <results.json.gz>`.",
        )
    context = read_json(paths["context"], {})
    payload = context.get("benchmark_payload")
    if not payload:
        raise ZwillError("invalid_input", f"Stored practitioner report context is missing benchmark payload for report id {report_id}.")
    payload = attach_plot_artifacts_to_payload(payload, context)
    generation = {
        **context.get("generation", {}),
        "mode": "imported_results",
        "report_id": report_id,
        "context_path": str(paths["context"]),
        "markdown_path": str(paths["markdown"]),
        "import_path": str(paths["import"]) if paths["import"].exists() else None,
    }
    markdown = paths["markdown"].read_text()
    output = render_twin_practitioner_report_html(payload, markdown, generation)
    output_path = resolve_output_path(args.path) if args.path else paths["html"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output)
    if not args.path:
        print(str(output_path))


def cmd_twin_study_practitioner_report_render(args: argparse.Namespace) -> None:
    cmd_twin_benchmark_practitioner_report_render(args)


def generate_practitioner_report_markdown(
    args: argparse.Namespace,
    payload: dict[str, Any],
    studies: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    output_path = resolve_output_path(args.path) if args.path else None
    prompt_path = resolve_output_path(args.prompt_path) if args.prompt_path else (output_path.with_suffix(".prompt.md") if output_path else None)
    job_path = Path(args.job_path) if args.job_path else (output_path.with_suffix(".report_job.edsl.json") if output_path else None)
    job_dict, context, prompt = _cli().build_edsl_practitioner_report_job_dict(args, payload, studies)
    report_id = job_dict["zwill"]["practitioner_report_id"]
    export_data = write_practitioner_report_export(
        report_id,
        job_dict,
        context,
        prompt,
        job_path=job_path,
        prompt_path=prompt_path,
    )
    default_paths = default_practitioner_report_paths(report_id)
    results_path = resolve_output_path(args.results_path) if args.results_path else default_paths["dir"] / "results.json.gz"
    run_result = _cli().cmd_edsl_run(
        argparse.Namespace(
            job=export_data["job_path"],
            path=str(results_path),
            dry_run=False,
            n=None,
            progress_bar=False,
            fresh=False,
            stop_on_exception=False,
            check_api_keys=False,
            verbose=None,
            print_exceptions=None,
            offload_execution=False,
            use_api_proxy=False,
            run_param=None,
        )
    )
    import_result = _cli().cmd_twin_benchmark_practitioner_report_import(
        argparse.Namespace(input_path=str(results_path), report_id=report_id, replace=True)
    )
    markdown = default_paths["markdown"].read_text().strip()
    markdown_path = resolve_output_path(args.markdown_path) if args.markdown_path else (output_path.with_suffix(".md") if output_path else None)
    if markdown_path:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown + "\n")
    return markdown, {
        **context.get("generation", {}),
        "mode": "model_generated_via_export_import",
        "report_id": report_id,
        "prompt_path": export_data["prompt_path"],
        "stored_prompt_path": export_data["stored_prompt_path"],
        "job_path": export_data["job_path"],
        "stored_job_path": export_data["stored_job_path"],
        "context_path": export_data["stored_context_path"],
        "results_path": str(results_path),
        "stored_raw": import_result["data"]["stored_raw"],
        "markdown_path": str(markdown_path) if markdown_path else None,
        "stored_markdown_path": import_result["data"]["markdown_path"],
        "env": run_result["data"].get("loaded_env"),
    }


def load_twin_benchmark_report_source(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if args.manifest:
        manifest = read_json(Path(args.manifest), {})
        config = {"name": manifest.get("benchmark", "twin_benchmark"), "studies": manifest.get("runs", []), "_config_path": args.manifest}
        return config, manifest.get("runs", [])
    if args.config:
        config = load_twin_benchmark_config(args.config)
        return config, config["studies"]
    raise ZwillError("invalid_input", "Use --config or --manifest.")


def cmd_twin_benchmark_practitioner_report(args: argparse.Namespace) -> None:
    config, studies = load_twin_benchmark_report_source(args)
    payload = build_twin_benchmark_report(config, studies)
    markdown, generation = _cli().generate_practitioner_report_markdown(args, payload, studies)
    output = render_twin_practitioner_report_html(payload, markdown, generation)
    if args.path:
        resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
        resolve_output_path(args.path).write_text(output)
    else:
        print(output)


def cmd_twin_study_practitioner_report(args: argparse.Namespace) -> None:
    payload = build_single_survey_practitioner_payload(args.survey, args.job_id)
    studies = [{"survey": args.survey, "job_id": args.job_id}]
    markdown, generation = _cli().generate_practitioner_report_markdown(args, payload, studies)
    output = render_twin_practitioner_report_html(payload, markdown, generation)
    if args.path:
        resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
        resolve_output_path(args.path).write_text(output)
    else:
        print(output)


def build_twin_experiment_report_context(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    experiments = selected_twin_experiments(args, sdir)
    comparison_rows, metric_info = twin_experiment_comparison_rows(sdir, experiments, args.metric, args.model)
    if not comparison_rows:
        raise ZwillError("not_found", "No scored experiment rows found for the requested filters.")
    response_changes = twin_experiment_response_changes(sdir, comparison_rows, args.model)
    questions = questions_by_name(sdir)
    rows_by_job = read_jsonl(digital_twin_predictions_path(sdir))
    experiment_details = []
    for experiment in experiments:
        job_id = experiment.get("job_id")
        prediction_rows = [row for row in rows_by_job if row.get("job_id") == job_id]
        run_manifest = next((run for run in read_twin_run_manifest(sdir) if run.get("job_id") == job_id), {})
        heldout_names = sorted({str(row.get("heldout_question")) for row in prediction_rows})
        heldout_questions = [
            {
                "question_name": name,
                "question_text": questions.get(name, {}).get("question_text"),
                "question_options": questions.get(name, {}).get("question_options", []),
            }
            for name in heldout_names
        ]
        scenario_material_examples = []
        for row in prediction_rows[:3]:
            materials = row.get("twin_material", [])
            if materials:
                scenario_material_examples.append(
                    {
                        "respondent_id": row.get("respondent_id"),
                        "heldout_question": row.get("heldout_question"),
                        "twin_material": materials,
                    }
                )
        if not scenario_material_examples and run_manifest.get("job_path"):
            job_path = Path(run_manifest["job_path"])
            if job_path.exists():
                job_dict = read_json(job_path, {})
                for scenario in job_dict.get("scenarios", [])[:3]:
                    materials = scenario.get("twin_material", [])
                    if materials:
                        scenario_material_examples.append(
                            {
                                "respondent_id": scenario.get("respondent_id"),
                                "heldout_question": scenario.get("heldout_question_name"),
                                "twin_material": materials,
                                "twin_material_text": scenario.get("twin_material_text"),
                                "source": "exported_job_scenario",
                            }
                        )
        experiment_details.append(
            {
                "experiment": experiment,
                "run_manifest": run_manifest,
                "import_metadata": twin_import_metadata(sdir, str(job_id)),
                "heldout_questions": heldout_questions,
                "prediction_row_count": len(prediction_rows),
                "scenario_material_examples": scenario_material_examples,
            }
        )
    payload_rows = []
    for row in comparison_rows:
        payload_rows.append(
            {
                "survey": args.survey,
                "job_id": row["job_id"],
                "experiment_id": row["experiment_id"],
                "approach": row["approach"],
                "heldout_questions": ",".join(
                    sorted({str(pred.get("heldout_question")) for pred in rows_by_job if pred.get("job_id") == row["job_id"]})
                ),
                "option_count": None,
                "model": row["model"],
                "rows": row["rows"],
                "accuracy": row["accuracy"],
                "p_actual": row["mean_probability_actual"],
                "nll": row["mean_negative_log_likelihood"],
                "brier": row["mean_brier"],
                "ece": None,
                "nll_vs_empirical": row["nll_vs_empirical"],
                "brier_vs_empirical": row["brier_vs_empirical"],
                "selected": row["selected"],
                "rank": row["rank"],
                "metric": row["metric"],
                "metric_value": row["metric_value"],
            }
        )
    summary = {}
    for row in comparison_rows:
        summary[row["experiment_id"]] = {
            "approach": row["approach"],
            "model": row["model"],
            "rank": row["rank"],
            "selected": row["selected"],
            "metric": row["metric"],
            "metric_value": row["metric_value"],
            "accuracy": row["accuracy"],
            "mean_negative_log_likelihood": row["mean_negative_log_likelihood"],
            "mean_brier": row["mean_brier"],
            "nll_vs_empirical": row["nll_vs_empirical"],
            "brier_vs_empirical": row["brier_vs_empirical"],
        }
    payload = {
        "benchmark": f"{args.survey} twin experiment comparison",
        "report_kind": "twin_experiment_comparison",
        "survey": args.survey,
        "metric": {"name": args.metric, **metric_info},
        "selected": comparison_rows[0],
        "rows": payload_rows,
        "summary": summary,
        "response_changes": response_changes,
        "plots": load_plot_summaries(getattr(args, "include_plots", None)),
        "config": {
            "kind": "twin_experiment_comparison",
            "survey": args.survey,
            "metric": args.metric,
            "model": args.model,
            "experiment_ids": args.experiment_id,
            "job_ids": args.job_id,
            "jobs": args.jobs,
        },
    }
    return {
        "benchmark": payload,
        "report_kind": "twin_experiment_comparison",
        "survey": args.survey,
        "survey_summary": survey_summary(args.survey),
        "survey_context": context_path(sdir).read_text().strip() if context_path(sdir).exists() else "",
        "raw_files": read_json(sdir / "raw_files.json", []),
        "metric": {"name": args.metric, **metric_info},
        "comparisons": comparison_rows,
        "selected": comparison_rows[0],
        "response_changes": response_changes,
        "plot_manifests": normalize_plot_manifest_paths(getattr(args, "include_plots", None)),
        "plot_summaries": load_plot_summaries(getattr(args, "include_plots", None)),
        "experiments": experiment_details,
        "notes": {
            "experiment": "A twin experiment is a recorded approach over an existing digital twin job.",
            "metric_direction": f"For {args.metric}, {metric_info['direction']} is better.",
            "empirical_marginal": "The empirical marginal is an oracle-style benchmark for already-observed held-out questions, not something available for a genuinely new question.",
        },
    }


def build_twin_experiment_report_prompt(report_context: dict[str, Any]) -> str:
    return f"""You are writing a detailed report about a digital twin development experiment.

Write for a practitioner who wants to understand which twin-construction approach performed better and why.

The report must:
- Describe the survey, held-out question or questions, respondent sample, model, and scoring metric.
- Describe each recorded approach in plain language, including what information was added or withheld.
- Compare the approaches using the provided metrics. Explain whether lower or higher is better for the selected metric.
- Explain whether the winning approach improved probability quality, top-choice accuracy, or both.
- Use the paired response-change diagnostics when available: say whether the same twins changed their top-choice answers, whether changes were corrections or regressions, and whether the approach mostly changed confidence rather than answers.
- Discuss baselines, especially the empirical marginal baseline when present.
- Discuss important caveats, including small sample size, public benchmark leakage, or when injected material resembles an oracle marginal.
- Make clear what this experiment does and does not prove for future twin development.

Do not invent data. Do not mention the internal tool name "zwill". Write Markdown only. Do not include markdown fences. Do not include a top-level title; the HTML wrapper supplies it.

Recorded experiment context:

{json.dumps(report_context, indent=2)}
"""


def experiment_report_id_from_job(job_dict: dict[str, Any]) -> str:
    payload = {key: value for key, value in job_dict.items() if key != "zwill"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_edsl_twin_experiment_report_job_dict(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], str]:
    report_context = build_twin_experiment_report_context(args)
    prompt = build_twin_experiment_report_prompt(report_context)
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = load_edsl_job_classes()
    question_name = "experiment_report_markdown"
    question = QuestionFreeText(question_name=question_name, question_text=prompt)
    model_params = parse_model_params(args)
    model_args = argparse.Namespace(**vars(args))
    model_args.model = getattr(args, "report_model", None)
    model_specs = parse_model_specs(model_args)
    job = Jobs(
        survey=Survey(questions=[question]),
        scenarios=ScenarioList([Scenario({})]),
        models=ModelList(
            [
                Model(
                    model_name=model_name,
                    service_name=service_name,
                    **model_kwargs_for(model_name, service_name, model_params),
                )
                for model_name, service_name in model_specs
            ]
        ),
    )
    job_dict = job.to_dict()
    report_id = experiment_report_id_from_job(job_dict)
    job_dict["zwill"] = {
        **job_dict.get("zwill", {}),
        "practitioner_report_id": report_id,
        "practitioner_report_question_name": question_name,
        "report_kind": "twin_experiment_comparison",
    }
    generation = {
        "mode": "job_exported",
        "report_id": report_id,
        "report_kind": "twin_experiment_comparison",
        "model": model_label(model_specs[0][1], model_specs[0][0]) if model_specs else None,
        "models": [model_label(service_name, model_name) for model_name, service_name in model_specs],
    }
    context = {
        "report_id": report_id,
        "benchmark_payload": report_context["benchmark"],
        "report_context": report_context,
        "studies": [],
        "prompt": prompt,
        "generation": generation,
    }
    return job_dict, context, prompt


def cmd_twin_experiment_report_export(args: argparse.Namespace) -> dict[str, Any]:
    job_dict, context, prompt = build_edsl_twin_experiment_report_job_dict(args)
    report_id = job_dict["zwill"]["practitioner_report_id"]
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
        "zwill twin-experiment report-export",
        "ok",
        data,
        next_steps=[
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-experiment report-import --report-id {report_id} --input-path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-experiment report-render --report-id {report_id}",
        ],
    )


def cmd_twin_experiment_report_import(args: argparse.Namespace) -> dict[str, Any]:
    result = cmd_twin_benchmark_practitioner_report_import(args)
    return {
        **result,
        "command": "zwill twin-experiment report-import",
        "next_steps": [
            step.replace("twin-benchmark practitioner-report", "twin-experiment report")
            for step in result.get("next_steps", [])
        ],
    }


def cmd_twin_experiment_report_render(args: argparse.Namespace) -> None:
    cmd_twin_benchmark_practitioner_report_render(args)


def cmd_twin_experiment_report(args: argparse.Namespace) -> None:
    job_dict, context, prompt = build_edsl_twin_experiment_report_job_dict(args)
    report_id = job_dict["zwill"]["practitioner_report_id"]
    output_path = resolve_output_path(args.path) if args.path else None
    prompt_path = resolve_output_path(args.prompt_path) if args.prompt_path else (output_path.with_suffix(".prompt.md") if output_path else None)
    job_path = Path(args.job_path) if args.job_path else (output_path.with_suffix(".report_job.edsl.json") if output_path else None)
    export_data = write_practitioner_report_export(
        report_id,
        job_dict,
        context,
        prompt,
        job_path=job_path,
        prompt_path=prompt_path,
    )
    default_paths = default_practitioner_report_paths(report_id)
    results_path = resolve_output_path(args.results_path) if args.results_path else default_paths["dir"] / "results.json.gz"
    cmd_edsl_run(
        argparse.Namespace(
            job=export_data["job_path"],
            path=str(results_path),
            dry_run=False,
            n=None,
            progress_bar=False,
            fresh=False,
            stop_on_exception=False,
            check_api_keys=False,
            verbose=None,
            print_exceptions=None,
            offload_execution=False,
            use_api_proxy=False,
            run_param=None,
        )
    )
    cmd_twin_experiment_report_import(argparse.Namespace(input_path=str(results_path), report_id=report_id, replace=True))
    markdown = default_paths["markdown"].read_text()
    generation = {
        **context.get("generation", {}),
        "mode": "model_generated_via_export_import",
        "report_id": report_id,
        "prompt_path": export_data["prompt_path"],
        "job_path": export_data["job_path"],
        "context_path": export_data["stored_context_path"],
        "results_path": str(results_path),
        "markdown_path": str(default_paths["markdown"]),
    }
    output = render_twin_practitioner_report_html(attach_plot_artifacts_to_payload(context["benchmark_payload"], context), markdown, generation)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output)
    else:
        print(output)
