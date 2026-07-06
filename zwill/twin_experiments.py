from __future__ import annotations

from .cli import *  # noqa: F403


def twin_experiments_path(sdir: Path) -> Path:
    return digital_twin_jobs_dir(sdir) / "experiments.json"


def read_twin_experiments(sdir: Path) -> list[dict[str, Any]]:
    payload = read_json(twin_experiments_path(sdir), {"experiments": []})
    return payload.get("experiments", [])


def write_twin_experiments(sdir: Path, experiments: list[dict[str, Any]]) -> None:
    experiments = sorted(experiments, key=lambda item: item.get("created_at", ""), reverse=True)
    write_json(twin_experiments_path(sdir), {"experiments": experiments})


def update_twin_experiments(sdir: Path, updater) -> list[dict[str, Any]]:
    path = twin_experiments_path(sdir)
    with file_lock(path):
        experiments = read_twin_experiments(sdir)
        updated = updater(experiments)
        write_twin_experiments(sdir, updated)
        return updated


def upsert_twin_experiment(sdir: Path, experiment: dict[str, Any]) -> None:
    def updater(experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered = [item for item in experiments if item.get("experiment_id") != experiment.get("experiment_id")]
        filtered.append(experiment)
        return filtered

    update_twin_experiments(sdir, updater)


def twin_plan_note_from_experiments(experiments: list[dict[str, Any]]) -> str:
    for experiment in experiments:
        note = str(experiment.get("plan", {}).get("notes") or "").strip()
        if note:
            return note
    return ""


def set_twin_plan_note(sdir: Path, plan_id: str, notes: str) -> None:
    def updater(experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matched = False
        now = utc_now()
        for experiment in experiments:
            if experiment.get("plan", {}).get("plan_id") != plan_id:
                continue
            matched = True
            plan = dict(experiment.get("plan") or {})
            plan["notes"] = notes
            plan["notes_updated_at"] = now
            experiment["plan"] = plan
        if not matched:
            raise ZwillError("not_found", f"No twin experiment plan records found for plan id {plan_id}.")
        return experiments

    update_twin_experiments(sdir, updater)


def twin_experiment_description(args: argparse.Namespace) -> str:
    if args.description_path:
        return Path(args.description_path).read_text().strip()
    return (args.description or "").strip()


def cmd_twin_experiment_note(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    experiments = twin_plan_experiments(sdir, args.plan_id)
    note = markdown_from_note_args(args)
    if note is None:
        return envelope("zwill twin-experiment note", "ok", {"plan_id": args.plan_id, "notes": twin_plan_note_from_experiments(experiments)})
    set_twin_plan_note(sdir, args.plan_id, note)
    return envelope(
        "zwill twin-experiment note",
        "ok",
        {"plan_id": args.plan_id, "notes": note},
        next_steps=[f"zwill twin-experiment dashboard --survey {args.survey} --plan-id {args.plan_id}"],
    )


def experiment_id_from_job_and_approach(job_id: str, approach: str) -> str:
    base = f"{job_id}:{approach}"
    return hashlib.sha256(base.encode()).hexdigest()[:12]


def cmd_twin_experiment_record(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    rows = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") == args.job_id]
    run = next((item for item in read_twin_run_manifest(sdir) if item.get("job_id") == args.job_id), None)
    if not rows and run is None:
        raise ZwillError("not_found", f"No digital twin study found for job id {args.job_id}.")
    experiment_id = args.experiment_id or experiment_id_from_job_and_approach(args.job_id, args.approach)
    experiment = {
        "experiment_id": experiment_id,
        "survey": args.survey,
        "job_id": args.job_id,
        "approach": args.approach,
        "description": twin_experiment_description(args),
        "tags": sorted(set(normalize_tags(args.tag))),
        "primary_metric": args.primary_metric,
        "created_at": utc_now(),
        "run": run or {},
    }
    upsert_twin_experiment(sdir, experiment)
    return envelope(
        "zwill twin-experiment record",
        "ok",
        {"experiment": experiment},
        next_steps=[f"zwill twin-experiment compare --survey {args.survey} --metric {args.primary_metric}"],
    )


def cmd_twin_experiment_list(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    experiments = read_twin_experiments(sdir)
    if args.format == "json":
        print(json.dumps({"survey": args.survey, "experiments": experiments}, indent=2))
        return
    table = Table(title=f"{args.survey} twin experiments")
    for column in ["experiment_id", "job_id", "approach", "metric", "tags", "created_at"]:
        table.add_column(column)
    for experiment in experiments:
        table.add_row(
            experiment.get("experiment_id", ""),
            experiment.get("job_id", ""),
            experiment.get("approach", ""),
            experiment.get("primary_metric", ""),
            ", ".join(experiment.get("tags", [])),
            experiment.get("created_at", ""),
        )
    Console().print(table)


def merge_plan_dicts(*items: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in items:
        if not item:
            continue
        for key, value in item.items():
            if value is not None:
                merged[key] = value
    return merged


def list_or_none(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def resolve_plan_file_list(value: Any, base_dir: Path) -> list[str] | None:
    values = list_or_none(value)
    if not values:
        return values
    resolved = []
    for raw in values:
        path = Path(raw)
        resolved.append(str(path if path.is_absolute() else base_dir / path))
    return resolved


def twin_export_namespace_from_plan(config: dict[str, Any], *, survey: str, plan_dir: Path) -> argparse.Namespace:
    models = config.get("models", config.get("model"))
    return argparse.Namespace(
        survey=survey,
        target="twin-probability-job",
        path=None,
        question=None,
        questions=None,
        exclude_question=None,
        limit=None,
        heldout_question=list_or_none(config.get("heldout_question")),
        heldout_questions=config.get("heldout_questions"),
        respondent=list_or_none(config.get("respondent")),
        respondents=config.get("respondents"),
        sample_respondents=config.get("sample_respondents"),
        seed=config.get("seed"),
        complete_cases=bool(config.get("complete_cases", False)),
        balance_actual=bool(config.get("balance_actual", False)),
        stratify_actual=bool(config.get("stratify_actual", False)),
        limit_respondents=config.get("limit_respondents"),
        context_question=list_or_none(config.get("context_question")),
        context_questions=config.get("context_questions"),
        exclude_context_question=list_or_none(config.get("exclude_context_question")),
        leakage_exclusion=list_or_none(config.get("leakage_exclusion")),
        context_question_count=config.get("context_question_count"),
        include_survey_context=False,
        include_agent_material=bool(config.get("include_agent_material", False)),
        agent_material_kind=list_or_none(config.get("agent_material_kind")),
        agent_material_tag=list_or_none(config.get("agent_material_tag")),
        max_agent_material_chars=config.get("max_agent_material_chars"),
        twin_material=resolve_plan_file_list(config.get("twin_material"), plan_dir),
        max_twin_material_chars=config.get("max_twin_material_chars"),
        traits_presentation_template=None,
        traits_presentation_template_path=None,
        no_default_traits_presentation_template=False,
        model=list_or_none(models),
        models=None,
        service_name=config.get("service_name"),
        model_param=list_or_none(config.get("model_param")),
        job_question_name=config.get("job_question_name", "response_probabilities"),
    )


def normalize_plan_heldout_questions(plan: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ["heldout_question", "heldout_questions"]:
        raw = plan.get(key)
        if raw is None:
            continue
        if isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
        else:
            values.extend(item.strip() for item in str(raw).split(",") if item.strip())
    return values


def estimate_plan_prediction_count(plan: dict[str, Any]) -> int | None:
    heldout_count = len(normalize_plan_heldout_questions(plan))
    arms = plan.get("arms") or plan.get("approaches") or []
    arm_count = len(arms) if isinstance(arms, list) and arms else 1
    defaults = plan.get("defaults") if isinstance(plan.get("defaults"), dict) else {}
    sample = plan.get("sample_respondents", defaults.get("sample_respondents"))
    models = plan.get("models", plan.get("model", defaults.get("models", defaults.get("model"))))
    if isinstance(models, str):
        model_count = len([item for item in models.split(",") if item.strip()])
    elif isinstance(models, list):
        model_count = len(models)
    else:
        model_count = 1
    if not heldout_count or sample is None:
        return None
    return int(sample) * heldout_count * arm_count * model_count


def edsl_job_prediction_count(job_dict: dict[str, Any]) -> int:
    zwill_meta = job_dict.get("zwill") if isinstance(job_dict.get("zwill"), dict) else {}
    scenario_count = zwill_meta.get("scenario_count")
    if scenario_count is None:
        scenario_count = len(job_dict.get("scenarios", []) or [])
    model_count = len(job_dict.get("models", []) or []) or 1
    return int(scenario_count or 0) * model_count


def prediction_count_check(approved_estimate: int | None, exported_count: int | None) -> dict[str, Any]:
    delta = None if approved_estimate is None or exported_count is None else int(exported_count) - int(approved_estimate)
    delta_share = None
    if delta is not None and approved_estimate:
        delta_share = delta / approved_estimate
    return {
        "approved_prediction_count_estimate": approved_estimate,
        "exported_prediction_count": exported_count,
        "delta": delta,
        "delta_share": delta_share,
        "requires_reapproval": delta not in (None, 0),
    }


def plan_approval_record(plan: dict[str, Any]) -> dict[str, Any]:
    approval = plan.get("approval")
    if isinstance(approval, dict):
        return approval
    if plan.get("approved") is True:
        return {"approved": True}
    return {}


def is_plan_approved(plan: dict[str, Any]) -> bool:
    return plan_approval_record(plan).get("approved") is True


def approved_plan_metadata(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    plan_path = Path(path)
    plan = load_object_file(plan_path, kind="Approved twin validation plan")
    if not is_plan_approved(plan):
        raise ZwillError(
            "approval_required",
            "Twin validation plan is not approved.",
            context={"plan_path": str(plan_path), "plan_id": plan.get("plan_id")},
            hint=f"Review the plan, then run `zwill twin-experiment approve --path {plan_path}`.",
        )
    return {
        "plan_id": plan.get("plan_id") or plan_id_from_config(plan, plan_path),
        "plan_path": str(plan_path),
        "approval": plan_approval_record(plan),
        "heldout_questions": normalize_plan_heldout_questions(plan),
        "prediction_count_estimate": estimate_plan_prediction_count(plan),
    }


def require_twin_plan_approval(args: argparse.Namespace, *, command: str) -> dict[str, Any] | None:
    if getattr(args, "allow_unapproved", False):
        return None
    metadata = approved_plan_metadata(getattr(args, "approved_plan", None))
    if metadata:
        return metadata
    raise ZwillError(
        "approval_required",
        f"{command} requires an approved validation plan.",
        hint="Pass `--approved-plan <plan.json>` after `zwill twin-experiment approve --path <plan.json>`, or pass `--allow-unapproved` for an explicit ad hoc/leakage/debug run.",
    )


def plan_id_from_config(plan: dict[str, Any], plan_path: Path) -> str:
    raw = json.dumps({"path": str(plan_path), "plan": plan}, sort_keys=True, default=str)
    return twin_approach_id(str(plan.get("plan_id") or plan.get("name") or hashlib.sha256(raw.encode()).hexdigest()[:12]))


def cmd_twin_experiment_init_plan(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    questions = questions_by_name(sdir)
    heldout = list_or_none(args.heldout_question) or []
    if args.heldout_questions:
        heldout.extend(name.strip() for name in args.heldout_questions.split(",") if name.strip())
    if not heldout:
        mc_questions = [
            name
            for name, question in questions.items()
            if question.get("question_type") == "multiple_choice" and question.get("question_options")
        ]
        if not mc_questions:
            raise ZwillError("invalid_input", "No multiple-choice questions are available for held-out validation.")
        heldout = [mc_questions[0]]
    unknown = [name for name in heldout if name not in questions]
    if unknown:
        raise ZwillError("invalid_input", "Unknown held-out questions.", context={"unknown_questions": unknown})
    approaches = [twin_approach_id(value) for value in (args.approach_id or [])]
    if not approaches:
        approaches = ["baseline"]
    plan = {
        "plan_id": args.plan_id,
        "survey": args.survey,
        "heldout_questions": ",".join(heldout),
        "primary_metric": args.primary_metric,
        "defaults": {
            "sample_respondents": args.sample_respondents,
            "seed": args.seed,
            "complete_cases": True,
            "context_question_count": args.context_question_count,
            "model": list_or_none(args.model) or ["openai:gpt-5.5"],
        },
        "arms": [{"approach_id": approach_id} for approach_id in approaches],
        "approval": {
            "approved": False,
            "status": "draft",
            "required_before": ["twin-experiment export-plan", "twin-study run", "twin-study export-holdout", "edsl-export --target twin-probability-job"],
        },
    }
    estimate = estimate_plan_prediction_count(plan)
    if estimate is not None:
        plan["prediction_count_estimate"] = estimate
    plan["defaults"] = {key: value for key, value in plan["defaults"].items() if value is not None}
    path = Path(args.path or f"{args.plan_id}.json")
    write_json(path, plan)
    return envelope(
        "zwill twin-experiment init-plan",
        "ok",
        {"path": str(path), "plan": plan},
        next_steps=[f"zwill twin-experiment approve --path {path}", f"zwill twin-experiment export-plan --path {path}"],
    )


def cmd_twin_experiment_approve(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = Path(args.path)
    plan = load_object_file(plan_path, kind="Twin experiment plan")
    survey = args.survey or plan.get("survey")
    if survey:
        require_survey(str(survey))
    approval = {
        "approved": True,
        "status": "approved",
        "approved_at": utc_now(),
        "approved_by": args.approved_by or "user",
    }
    if args.note:
        approval["note"] = args.note
    if args.estimated_cost:
        approval["estimated_cost"] = args.estimated_cost
    if args.estimated_time:
        approval["estimated_time"] = args.estimated_time
    plan["approval"] = approval
    plan["approved"] = True
    estimate = estimate_plan_prediction_count(plan)
    if estimate is not None:
        plan["prediction_count_estimate"] = estimate
    write_json(plan_path, plan)
    return envelope(
        "zwill twin-experiment approve",
        "ok",
        {
            "path": str(plan_path),
            "plan_id": plan.get("plan_id") or plan_id_from_config(plan, plan_path),
            "survey": survey,
            "approval": approval,
            "prediction_count_estimate": estimate,
        },
        next_steps=[f"zwill twin-experiment export-plan --path {plan_path}"],
    )


def cmd_twin_experiment_export_plan(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = Path(args.path)
    plan = load_object_file(plan_path, kind="Twin experiment plan")
    survey = args.survey or plan.get("survey")
    if not survey:
        raise ZwillError("invalid_input", "Twin experiment plan needs a survey, or pass --survey.")
    sdir = require_survey(str(survey))
    plan_id = args.plan_id or plan_id_from_config(plan, plan_path)
    if not is_plan_approved(plan) and not getattr(args, "allow_unapproved", False):
        raise ZwillError(
            "approval_required",
            "Twin experiment plan must be approved before export.",
            context={"plan_path": str(plan_path), "plan_id": plan_id},
            hint=f"Review the plan, then run `zwill twin-experiment approve --path {plan_path}`.",
        )
    output_dir = Path(args.output_dir) if args.output_dir else digital_twin_jobs_dir(sdir) / "plans" / plan_id
    output_dir.mkdir(parents=True, exist_ok=True)
    approval = plan_approval_record(plan)
    approved_estimate = estimate_plan_prediction_count(plan)

    registered = {item["approach_id"]: item for item in read_twin_approaches(sdir)}
    defaults = dict(plan.get("defaults") or {})
    plan_heldout = {
        key: plan[key]
        for key in ["heldout_question", "heldout_questions"]
        if key in plan
    }
    arms = plan.get("arms") or plan.get("approaches")
    if not isinstance(arms, list) or not arms:
        raise ZwillError("invalid_input", "Twin experiment plan needs a non-empty arms or approaches list.")

    exported = []
    experiment_records = []
    for index, arm in enumerate(arms, start=1):
        if isinstance(arm, str):
            arm = {"approach_id": arm}
        if not isinstance(arm, dict):
            raise ZwillError("invalid_input", "Twin experiment plan arms must be strings or objects.")
        approach = None
        if arm.get("approach_id"):
            approach = registered.get(twin_approach_id(str(arm["approach_id"])))
            if not approach and not arm.get("name"):
                raise ZwillError("not_found", f"Twin approach not found: {arm['approach_id']}.")
        inline = normalize_twin_approach_record(arm) if not approach else None
        source_approach = approach or inline or normalize_twin_approach_record(arm)
        construction = merge_plan_dicts(
            defaults,
            plan_heldout,
            source_approach.get("construction", {}),
            arm.get("construction") if isinstance(arm.get("construction"), dict) else None,
            {key: arm[key] for key in TWIN_APPROACH_CONSTRUCTION_KEYS if key in arm},
        )
        export_args = twin_export_namespace_from_plan(construction, survey=str(survey), plan_dir=plan_path.parent)
        job_dict = build_edsl_digital_twin_job_dict(str(survey), export_args)
        job_dict["zwill"]["approved_validation_plan"] = {
            "plan_id": plan_id,
            "plan_path": str(plan_path),
            "approval": approval,
            "prediction_count_estimate": approved_estimate,
        }
        job_id = job_dict.get("zwill", {}).get("digital_twin_job_id") or digital_twin_job_id_from_job(job_dict)
        approach_id = source_approach["approach_id"]
        job_path = output_dir / f"{index:02d}_{approach_id}_{job_id}.edsl.json"
        write_json(job_path, job_dict)
        experiment_id = twin_approach_id(str(arm.get("experiment_id") or f"{plan_id}-{approach_id}"))
        experiment = {
            "experiment_id": experiment_id,
            "survey": str(survey),
            "job_id": job_id,
            "approach": source_approach.get("name") or approach_id,
            "approach_id": approach_id,
            "description": source_approach.get("description", ""),
            "notes": source_approach.get("notes", ""),
            "tags": sorted(set(source_approach.get("tags", []) + normalize_tags(arm.get("tag") or arm.get("tags")))),
            "primary_metric": arm.get("primary_metric") or plan.get("primary_metric") or defaults.get("primary_metric") or "nll",
            "created_at": utc_now(),
            "plan": {
                "plan_id": plan_id,
                "plan_path": str(plan_path),
                "job_path": str(job_path),
                "construction": construction,
                "approval": approval,
            },
            "run": {},
        }
        upsert_twin_experiment(sdir, experiment)
        experiment_records.append(experiment)
        exported.append(
            {
                "approach_id": approach_id,
                "experiment_id": experiment_id,
                "job_id": job_id,
                "job_path": str(job_path),
                "approach": experiment["approach"],
                "scenario_count": job_dict.get("zwill", {}).get("scenario_count"),
                "model_count": len(job_dict.get("models", []) or []) or 1,
                "prediction_count_exported": edsl_job_prediction_count(job_dict),
            }
        )

    exported_prediction_count = sum(int(row.get("prediction_count_exported") or 0) for row in exported)
    export_count_check = prediction_count_check(approved_estimate, exported_prediction_count)
    for row in exported:
        job_path = Path(str(row["job_path"]))
        job_dict = read_json(job_path, {})
        approved_meta = job_dict.setdefault("zwill", {}).setdefault("approved_validation_plan", {})
        approved_meta["export_count_check"] = export_count_check
        write_json(job_path, job_dict)

    manifest = {
        "kind": "twin_experiment_plan_export",
        "plan_id": plan_id,
        "survey": str(survey),
        "plan_path": str(plan_path),
        "output_dir": str(output_dir),
        "primary_metric": plan.get("primary_metric") or defaults.get("primary_metric") or "nll",
        "created_at": utc_now(),
        "approval": approval,
        "prediction_count_estimate": approved_estimate,
        "prediction_count_exported": exported_prediction_count,
        "export_count_check": export_count_check,
        "exports": exported,
        "experiment_count": len(exported),
        "duplicate_job_ids": sorted(
            job_id
            for job_id, count in Counter(str(row.get("job_id")) for row in exported).items()
            if job_id and count > 1
        ),
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return envelope(
        "zwill twin-experiment export-plan",
        "ok",
        {"manifest_path": str(manifest_path), **manifest},
        next_steps=[
            (
                f"zwill twin-experiment approve --path {plan_path}"
                if export_count_check.get("requires_reapproval")
                else f"zwill edsl-run --job {exported[0]['job_path']} --path <results.json.gz>"
            )
            if exported
            else "",
            f"zwill twin-results import --survey {survey} --path <results.json.gz>",
            f"zwill twin-experiment compare --survey {survey} --metric {manifest['primary_metric']}",
        ],
    )


def twin_plan_experiments(sdir: Path, plan_id: str) -> list[dict[str, Any]]:
    experiments = [
        experiment
        for experiment in read_twin_experiments(sdir)
        if experiment.get("plan", {}).get("plan_id") == plan_id
    ]
    if not experiments:
        raise ZwillError("not_found", f"No twin experiment plan records found for plan id {plan_id}.")
    return experiments


def infer_results_job_id(path: Path) -> str | None:
    try:
        payload = read_json_or_gzip(path)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("edsl_class_name") != "Results":
        return None
    return payload.get("zwill", {}).get("digital_twin_job_id") or digital_twin_job_id_from_results(payload)


def twin_plan_status_payload(sdir: Path, plan_id: str) -> dict[str, Any]:
    experiments = twin_plan_experiments(sdir, plan_id)
    runs = {run.get("job_id"): run for run in read_twin_run_manifest(sdir)}
    predictions = read_jsonl(digital_twin_predictions_path(sdir))
    predictions_by_job = Counter(str(row.get("job_id")) for row in predictions)
    rows = []
    for experiment in sorted(experiments, key=lambda item: item.get("experiment_id", "")):
        job_id = experiment.get("job_id")
        run = runs.get(job_id, {})
        rows.append(
            {
                "experiment_id": experiment.get("experiment_id"),
                "approach_id": experiment.get("approach_id"),
                "approach": experiment.get("approach"),
                "job_id": job_id,
                "job_path": experiment.get("plan", {}).get("job_path"),
                "status": "imported" if run else "exported",
                "imported": bool(run),
                "prediction_rows": predictions_by_job.get(str(job_id), 0),
                "models": run.get("models", []),
                "heldout_questions": run.get("heldout_questions", []),
                "issue_count": run.get("issue_count"),
                "results_path": run.get("results_path"),
            }
        )
    return {
        "plan_id": plan_id,
        "survey": sdir.name,
        "experiment_count": len(rows),
        "imported_count": sum(1 for row in rows if row["imported"]),
        "ready_for_comparison": sum(1 for row in rows if row["imported"]) >= 2,
        "rows": rows,
    }


def cmd_twin_experiment_plan_status(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    payload = twin_plan_status_payload(sdir, args.plan_id)
    if args.format == "json":
        print(json.dumps(payload, indent=2))
        return
    table = Table(title=f"{args.survey} twin plan status: {args.plan_id}")
    for column in ["experiment", "approach", "job_id", "status", "rows", "models", "held-out"]:
        table.add_column(column)
    for row in payload["rows"]:
        table.add_row(
            str(row.get("experiment_id")),
            str(row.get("approach")),
            str(row.get("job_id")),
            str(row.get("status")),
            str(row.get("prediction_rows")),
            ", ".join(row.get("models") or []),
            ", ".join(row.get("heldout_questions") or []),
        )
    Console().print(table)


def cmd_twin_experiment_import_plan_results(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_object_file(Path(args.manifest), kind="Twin experiment plan manifest")
    survey = args.survey or manifest.get("survey")
    if not survey:
        raise ZwillError("invalid_input", "Plan manifest does not include a survey; pass --survey.")
    require_survey(str(survey))
    expected_jobs = {str(row.get("job_id")) for row in manifest.get("exports", []) if row.get("job_id")}
    if not expected_jobs:
        raise ZwillError("invalid_input", "Plan manifest has no exported job ids.")
    results_dir = Path(args.results_dir)
    if not results_dir.exists() or not results_dir.is_dir():
        raise ZwillError("not_found", f"Results directory does not exist: {results_dir}.")
    candidates = sorted(
        [
            path
            for path in results_dir.iterdir()
            if path.is_file() and (path.suffix == ".json" or path.name.endswith(".json.gz"))
        ]
    )
    imports = []
    unmatched = []
    seen_jobs = set()
    for path in candidates:
        job_id = infer_results_job_id(path)
        if not job_id or job_id not in expected_jobs:
            unmatched.append(str(path))
            continue
        if job_id in seen_jobs:
            unmatched.append(str(path))
            continue
        result = cmd_twin_results_import(
            argparse.Namespace(
                survey=str(survey),
                path=str(path),
                job_id=job_id,
                replace=args.replace,
            )
        )
        imports.append({"path": str(path), **result["data"]})
        seen_jobs.add(job_id)
    missing_jobs = sorted(expected_jobs - seen_jobs)
    return envelope(
        "zwill twin-experiment import-plan-results",
        "ok" if imports else "warning",
        {
            "survey": str(survey),
            "plan_id": manifest.get("plan_id"),
            "import_count": len(imports),
            "imports": imports,
            "missing_jobs": missing_jobs,
            "unmatched_paths": unmatched,
        },
        warnings=[f"{len(missing_jobs)} plan jobs have no imported results."] if missing_jobs else [],
        next_steps=[
            f"zwill twin-experiment plan-status --survey {survey} --plan-id {manifest.get('plan_id')}",
            f"zwill twin-experiment compare --survey {survey} --jobs {','.join(sorted(seen_jobs))}",
        ],
    )


def copy_package_artifact(source: Path, destination: Path) -> str | None:
    if not source.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(destination)


def render_twin_experiment_package_runbook(manifest: dict[str, Any], package_manifest: dict[str, Any]) -> str:
    survey = manifest.get("survey")
    plan_id = manifest.get("plan_id")
    env_arg = f" --env-path {package_manifest['env_path']}" if package_manifest.get("env_path") else ""
    lines = [
        f"# Twin Experiment Package: {plan_id}",
        "",
        f"Survey: `{survey}`",
        f"Plan id: `{plan_id}`",
        "",
        "## Contents",
        "",
        "- `manifest.json`: package artifact index",
        "- `export_manifest.json`: original `zwill twin-experiment export-plan` manifest",
        "- `plan.json`: experiment plan used to export jobs, when available",
        "- `approaches.json`: registered approach records for this survey, when available",
        "- `jobs/`: serialized EDSL Jobs objects, one per arm",
        "- `results/`: suggested destination for serialized EDSL Results objects",
        "",
            "## Run Jobs",
            "",
            "From this package directory, run each exported job and write Results into `results/`:",
        "",
        "```bash",
    ]
    for job in package_manifest.get("jobs", []):
        job_path = job.get("package_job_path")
        result_path = job.get("suggested_results_path")
        if job_path and result_path:
            package_job_path = Path("jobs") / Path(str(job_path)).name
            package_result_path = Path("results") / Path(str(result_path)).name
            lines.append(f"zwill edsl-run --job {package_job_path} --path {package_result_path}{env_arg}")
    lines.extend(
        [
            "```",
            "",
            "## Import Results",
            "",
            "After the jobs finish, return to the zwill project directory that contains the original survey and import completed Results files:",
            "",
            "```bash",
            f"zwill twin-experiment import-plan-results --manifest <package-dir>/export_manifest.json --results-dir <package-dir>/results",
            f"zwill twin-experiment plan-status --survey {survey} --plan-id {plan_id}",
            "```",
            "",
            "## Build Analysis Bundle",
            "",
            "```bash",
            f"zwill twin-experiment bundle --survey {survey} --plan-id {plan_id} --output-dir bundle --report-export",
            "zwill twin-experiment bundle-show --manifest bundle/manifest.json",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def cmd_twin_experiment_package(args: argparse.Namespace) -> dict[str, Any]:
    export_manifest_path = Path(args.manifest)
    export_manifest = load_object_file(export_manifest_path, kind="Twin experiment plan manifest")
    survey = args.survey or export_manifest.get("survey")
    plan_id = args.plan_id or export_manifest.get("plan_id")
    if not survey or not plan_id:
        raise ZwillError("invalid_input", "Plan manifest must include survey and plan_id, or pass --survey/--plan-id.")
    sdir = require_survey(str(survey))
    output_dir = Path(args.output_dir or f"{plan_id}_package")
    output_dir.mkdir(parents=True, exist_ok=True)

    copied_export_manifest = copy_package_artifact(export_manifest_path, output_dir / "export_manifest.json")
    plan_path = resolve_manifest_artifact_path(export_manifest.get("plan_path"), export_manifest_path)
    copied_plan = copy_package_artifact(plan_path, output_dir / "plan.json") if plan_path else None
    approaches_path = twin_approaches_path(sdir)
    copied_approaches = copy_package_artifact(approaches_path, output_dir / "approaches.json")
    env_path = Path(args.env_path) if getattr(args, "env_path", None) else find_local_env()

    job_rows = []
    for export in export_manifest.get("exports", []):
        source = resolve_manifest_artifact_path(export.get("job_path"), export_manifest_path)
        if not source or not source.exists():
            job_rows.append({**export, "package_job_path": None, "missing": True})
            continue
        destination = output_dir / "jobs" / source.name
        copied = copy_package_artifact(source, destination)
        result_path = output_dir / "results" / f"{export.get('approach_id') or export.get('job_id')}_results.json.gz"
        job_rows.append(
            {
                **export,
                "package_job_path": copied,
                "suggested_results_path": str(result_path),
                "missing": False,
            }
        )
    (output_dir / "results").mkdir(exist_ok=True)

    package_manifest = {
        "kind": "twin_experiment_run_package",
        "survey": str(survey),
        "plan_id": str(plan_id),
        "created_at": utc_now(),
        "source_manifest_path": str(export_manifest_path),
        "export_manifest_path": copied_export_manifest,
        "plan_path": copied_plan,
        "approaches_path": copied_approaches,
        "env_path": str(env_path) if env_path else None,
        "jobs": job_rows,
        "missing_job_count": sum(1 for row in job_rows if row.get("missing")),
        "results_dir": str(output_dir / "results"),
    }
    package_manifest_path = output_dir / "manifest.json"
    write_json(package_manifest_path, package_manifest)
    runbook_path = output_dir / "RUN.md"
    runbook_path.write_text(render_twin_experiment_package_runbook(export_manifest, package_manifest), encoding="utf-8")
    return envelope(
        "zwill twin-experiment package",
        "ok" if package_manifest["missing_job_count"] == 0 else "warning",
        {
            "package_dir": str(output_dir),
            "manifest_path": str(package_manifest_path),
            "runbook_path": str(runbook_path),
            **package_manifest,
        },
        warnings=[f"{package_manifest['missing_job_count']} job files were missing."] if package_manifest["missing_job_count"] else [],
        next_steps=[
            f"open {runbook_path}",
            f"zwill twin-experiment import-plan-results --manifest {copied_export_manifest} --results-dir {output_dir / 'results'}",
        ],
    )


def cmd_twin_experiment_bundle(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    experiments = twin_plan_experiments(sdir, args.plan_id)
    job_ids = [str(experiment.get("job_id")) for experiment in experiments if experiment.get("job_id")]
    jobs_arg = ",".join(job_ids)
    output_dir = Path(args.output_dir) if args.output_dir else digital_twin_jobs_dir(sdir) / "plans" / args.plan_id / "bundle"
    output_dir.mkdir(parents=True, exist_ok=True)

    select_args = argparse.Namespace(experiment_id=None, job_id=None, jobs=jobs_arg)
    selected = selected_twin_experiments(select_args, sdir)
    comparison_rows, metric_info = twin_experiment_comparison_rows(sdir, selected, args.metric, args.model)
    if not comparison_rows:
        raise ZwillError("not_found", "No scored experiment rows found for this plan.", hint="Import plan results first.")
    comparison_payload = {
        "survey": args.survey,
        "plan_id": args.plan_id,
        "metric": {"name": args.metric, **metric_info},
        "comparisons": comparison_rows,
        "selected": comparison_rows[0],
        "response_changes": twin_experiment_response_changes(sdir, comparison_rows, args.model),
    }
    comparison_path = output_dir / "comparison.json"
    write_json(comparison_path, comparison_payload)

    plot_manifest_path = None
    if len(comparison_rows) >= 2:
        plots = write_twin_experiment_plots(
            argparse.Namespace(
                survey=args.survey,
                experiment_id=None,
                job_id=None,
                jobs=jobs_arg,
                model=args.model,
                metric=args.metric,
                path=str(output_dir / "plots"),
                plot_id=None,
            )
        )
        plot_manifest_path = plots["manifest_path"]

    microdata = write_twin_experiment_microdata(
        argparse.Namespace(
            survey=args.survey,
            experiment_id=None,
            job_id=None,
            jobs=jobs_arg,
            model=args.model,
            metric=args.metric,
            path=str(output_dir / "microdata.html"),
            json_path=str(output_dir / "microdata.json"),
            microdata_id=None,
            title=f"{args.survey} {args.plan_id} Twin Experiment Microdata",
        )
    )

    report_export = None
    if args.report_export:
        report_export = cmd_twin_experiment_report_export(
            argparse.Namespace(
                survey=args.survey,
                experiment_id=None,
                job_id=None,
                jobs=jobs_arg,
                model=args.model,
                metric=args.metric,
                job_path=str(output_dir / "report_job.edsl.json"),
                prompt_path=str(output_dir / "report_prompt.md"),
                context_path=str(output_dir / "report_context.json"),
                include_plots=[plot_manifest_path] if plot_manifest_path else None,
                report_model=args.report_model,
                model_param=args.model_param,
                models=args.models,
                service_name=args.service_name,
            )
        )
    manifest = {
        "kind": "twin_experiment_bundle",
        "survey": args.survey,
        "plan_id": args.plan_id,
        "metric": args.metric,
        "model": args.model,
        "created_at": utc_now(),
        "output_dir": str(output_dir),
        "comparison_path": str(comparison_path),
        "plot_manifest_path": plot_manifest_path,
        "microdata_html_path": microdata["html_path"],
        "microdata_json_path": microdata["json_path"],
        "report_export": report_export["data"] if report_export else None,
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return envelope(
        "zwill twin-experiment bundle",
        "ok",
        {"manifest_path": str(manifest_path), **manifest},
        next_steps=[
            f"open {microdata['html_path']}",
            f"zwill twin-experiment dashboard --survey {args.survey} --plan-id {args.plan_id} --metric {args.metric} --bundle-manifest {manifest_path}",
        ],
    )


def resolve_manifest_artifact_path(raw_path: Any, manifest_path: Path) -> Path | None:
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if path.is_absolute() or path.exists():
        return path
    return manifest_path.parent / path


def cmd_twin_experiment_bundle_show(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    manifest = load_object_file(manifest_path, kind="Twin experiment bundle manifest")
    comparison_path = resolve_manifest_artifact_path(manifest.get("comparison_path"), manifest_path)
    comparison = read_json(comparison_path, {}) if comparison_path and comparison_path.exists() else {}
    selected = comparison.get("selected", {})
    payload = {
        "manifest_path": str(manifest_path),
        "bundle": manifest,
        "selected": selected,
        "artifacts": {
            "comparison": manifest.get("comparison_path"),
            "plot_manifest": manifest.get("plot_manifest_path"),
            "microdata_html": manifest.get("microdata_html_path"),
            "microdata_json": manifest.get("microdata_json_path"),
            "report_job": (manifest.get("report_export") or {}).get("job_path"),
            "report_prompt": (manifest.get("report_export") or {}).get("prompt_path"),
            "report_context": (manifest.get("report_export") or {}).get("context_path"),
        },
        "next_steps": [
            f"open {manifest.get('microdata_html_path')}" if manifest.get("microdata_html_path") else None,
            (
                f"zwill edsl-run --job {(manifest.get('report_export') or {}).get('job_path')} "
                f"--path {(manifest.get('report_export') or {}).get('report_dir')}/results.json.gz"
                if manifest.get("report_export")
                else None
            ),
        ],
    }
    payload["next_steps"] = [step for step in payload["next_steps"] if step]
    if args.format == "json":
        print(json.dumps(payload, indent=2))
        return
    table = Table(title=f"Twin experiment bundle: {manifest.get('plan_id')}")
    table.add_column("field")
    table.add_column("value")
    table.add_row("survey", str(manifest.get("survey")))
    table.add_row("metric", str(manifest.get("metric")))
    table.add_row("model", str(manifest.get("model") or "all"))
    table.add_row("selected approach", str(selected.get("approach", "")))
    table.add_row("selected value", f"{selected.get('metric_value'):.4f}" if selected.get("metric_value") is not None else "")
    for key, value in payload["artifacts"].items():
        if value:
            table.add_row(key, str(value))
    Console().print(table)
    if payload["next_steps"]:
        Console().print("Next steps:")
        for step in payload["next_steps"]:
            Console().print(f"  {step}")


def rel_link(path: str | None, base_path: Path) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    try:
        return os.path.relpath(candidate, start=base_path.parent.resolve())
    except ValueError:
        return str(candidate)


def resolve_bundle_manifest_for_dashboard(args: argparse.Namespace, sdir: Path) -> tuple[Path | None, dict[str, Any]]:
    if args.bundle_manifest:
        path = Path(args.bundle_manifest)
    else:
        path = digital_twin_jobs_dir(sdir) / "plans" / args.plan_id / "bundle" / "manifest.json"
    if path.exists():
        return path, load_object_file(path, kind="Twin experiment bundle manifest")
    return None, {}


def render_twin_experiment_dashboard_html(payload: dict[str, Any], *, output_path: Path) -> str:
    status_rows = []
    for row in payload["status"]["rows"]:
        status_rows.append(
            "<tr>"
            f"<td><code>{html_escape(row.get('experiment_id'))}</code></td>"
            f"<td>{html_escape(row.get('approach'))}<div class=\"muted\">{html_escape(row.get('approach_id'))}</div></td>"
            f"<td><code>{html_escape(row.get('job_id'))}</code></td>"
            f"<td><span class=\"pill {html_escape(row.get('status'))}\">{html_escape(row.get('status'))}</span></td>"
            f"<td class=\"num\">{html_escape(row.get('prediction_rows'))}</td>"
            f"<td>{html_escape(', '.join(row.get('models') or []))}</td>"
            f"<td>{html_escape(', '.join(row.get('heldout_questions') or []))}</td>"
            "</tr>"
        )
    comparison_rows = []
    for row in payload.get("comparisons", []):
        selected = " selected" if row.get("selected") else ""
        comparison_rows.append(
            f"<tr class=\"{selected}\">"
            f"<td class=\"num\">{row.get('rank')}</td>"
            f"<td>{html_escape(row.get('approach'))}<div class=\"muted\"><code>{html_escape(row.get('job_id'))}</code></div></td>"
            f"<td>{html_escape(row.get('model'))}</td>"
            f"<td class=\"num\">{float(row.get('metric_value') or 0):.4f}</td>"
            f"<td class=\"num\">{float(row.get('accuracy') or 0):.3f}</td>"
            f"<td class=\"num\">{float(row.get('mean_probability_actual') or 0):.3f}</td>"
            f"<td class=\"num\">{float(row.get('mean_negative_log_likelihood') or 0):.3f}</td>"
            f"<td class=\"num\">{float(row.get('mean_brier') or 0):.3f}</td>"
            "</tr>"
        )
    change_rows = []
    for row in payload.get("response_changes", []):
        change_rows.append(
            "<tr>"
            f"<td>{html_escape(row.get('from_label'))}</td>"
            f"<td>{html_escape(row.get('to_label'))}</td>"
            f"<td>{html_escape(row.get('model'))}</td>"
            f"<td class=\"num\">{html_escape(row.get('paired_rows'))}</td>"
            f"<td class=\"num\">{html_escape(row.get('changed_top_choice'))}</td>"
            f"<td class=\"num\">{html_escape(row.get('corrections'))}</td>"
            f"<td class=\"num\">{html_escape(row.get('regressions'))}</td>"
            f"<td class=\"num\">{float(row.get('mean_probability_actual_delta') or 0):+.3f}</td>"
            "</tr>"
        )
    artifact_links = []
    for label, path in payload.get("artifacts", {}).items():
        href = rel_link(path, output_path)
        if href:
            artifact_links.append(f'<li><a href="{html_escape(href)}">{html_escape(label.replace("_", " ").title())}</a><div class="muted">{html_escape(path)}</div></li>')
    note_cards = []
    if payload.get("plan_notes"):
        note_cards.append(f"<div class=\"note\"><h3>Plan note</h3><pre>{html_escape(payload['plan_notes'])}</pre></div>")
    for note in payload.get("approach_notes", []):
        if note.get("notes"):
            note_cards.append(
                f"<div class=\"note\"><h3>{html_escape(note.get('approach'))}</h3>"
                f"<div class=\"muted\">{html_escape(note.get('approach_id'))}</div>"
                f"<pre>{html_escape(note.get('notes'))}</pre></div>"
            )
    selected = payload.get("selected") or {}
    metric = payload.get("metric") or {}
    direction = metric.get("direction")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{html_escape(payload['survey'])} twin experiment dashboard</title>
  <style>
    body {{ font: 15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin: 0; color:#17202a; background:#f6f7f9; }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 34px 28px 54px; }}
    h1 {{ font-size: 36px; margin: 0 0 4px; }}
    h2 {{ font-size: 22px; margin: 0 0 14px; }}
    .muted {{ color:#64748b; }}
    .grid {{ display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:12px; margin:22px 0; }}
    .card {{ background:#fff; border:1px solid #d8dee6; border-radius:8px; padding:18px; }}
    .card label {{ display:block; color:#64748b; font-size:12px; text-transform:uppercase; letter-spacing:.04em; margin-bottom:6px; }}
    .card strong {{ font-size:24px; }}
    .note {{ border:1px solid #dfe3e6; border-radius:8px; padding:14px; margin:10px 0; background:#fbfcfd; }}
    .note h3 {{ margin:0 0 4px; font-size:16px; }}
    .note pre {{ white-space:pre-wrap; margin:8px 0 0; font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; margin-bottom:22px; }}
    th,td {{ border:1px solid #dfe3e6; padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#f0f3f4; }}
    tr.selected td {{ background:#f3faf5; }}
    .num {{ text-align:right; font-variant-numeric: tabular-nums; }}
    .pill {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#eef2f6; font-size:12px; }}
    .imported {{ background:#e7f3eb; color:#1f6f43; }}
    .exported {{ background:#fff4cc; color:#6f4e00; }}
    a {{ color:#0f5e9c; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    ul.artifacts {{ padding-left:18px; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
{copy_markdown_control()}
<main>
  <h1>Twin Experiment Dashboard</h1>
  <div class="muted">{html_escape(payload['survey'])} / {html_escape(payload['plan_id'])}</div>
  <section class="grid">
    <div class="card"><label>Plan arms</label><strong>{payload['status']['experiment_count']}</strong></div>
    <div class="card"><label>Imported arms</label><strong>{payload['status']['imported_count']}</strong></div>
    <div class="card"><label>Metric</label><strong>{html_escape(metric.get('label') or payload.get('metric_name'))}</strong><div class="muted">{html_escape(direction or '')} is better</div></div>
    <div class="card"><label>Selected</label><strong>{html_escape(selected.get('approach') or 'Not scored')}</strong><div class="muted">{float(selected.get('metric_value') or 0):.4f}</div></div>
  </section>

  <section class="card">
    <h2>Plan Status</h2>
    <table>
      <thead><tr><th>Experiment</th><th>Approach</th><th>Job</th><th>Status</th><th>Rows</th><th>Models</th><th>Held-out</th></tr></thead>
      <tbody>{''.join(status_rows)}</tbody>
    </table>
  </section>

  <section class="card">
    <h2>Notes</h2>
    {''.join(note_cards) or '<div class="muted">No notes recorded for this plan or its approaches.</div>'}
  </section>

  <section class="card">
    <h2>Performance</h2>
    <div class="muted">{html_escape(metric.get('meaning') or 'Import results to populate comparison metrics.')}</div>
    <table>
      <thead><tr><th>Rank</th><th>Approach</th><th>Model</th><th>{html_escape(metric.get('label') or 'Metric')}</th><th>Accuracy</th><th>Mean p(actual)</th><th>NLL</th><th>Brier</th></tr></thead>
      <tbody>{''.join(comparison_rows) or '<tr><td colspan="8">No scored rows yet.</td></tr>'}</tbody>
    </table>
  </section>

  <section class="card">
    <h2>Paired Response Changes</h2>
    <table>
      <thead><tr><th>From</th><th>To</th><th>Model</th><th>Paired rows</th><th>Changed</th><th>Corrections</th><th>Regressions</th><th>Mean p(actual) delta</th></tr></thead>
      <tbody>{''.join(change_rows) or '<tr><td colspan="8">No paired response-change diagnostics yet.</td></tr>'}</tbody>
    </table>
  </section>

  <section class="card">
    <h2>Artifacts</h2>
    <ul class="artifacts">{''.join(artifact_links) or '<li>No bundle artifacts linked yet.</li>'}</ul>
  </section>
</main>
</body>
</html>
"""


def cmd_twin_experiment_dashboard(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    status = twin_plan_status_payload(sdir, args.plan_id)
    experiments = twin_plan_experiments(sdir, args.plan_id)
    registered_notes = {item.get("approach_id"): item.get("notes", "") for item in read_twin_approaches(sdir)}
    approach_notes = []
    for experiment in experiments:
        note = experiment.get("notes") or registered_notes.get(experiment.get("approach_id")) or ""
        approach_notes.append(
            {
                "approach_id": experiment.get("approach_id"),
                "approach": experiment.get("approach"),
                "experiment_id": experiment.get("experiment_id"),
                "notes": note,
            }
        )
    comparison_rows, metric_info = twin_experiment_comparison_rows(sdir, experiments, args.metric, args.model)
    response_changes = twin_experiment_response_changes(sdir, comparison_rows, args.model) if comparison_rows else []
    bundle_manifest_path, bundle_manifest = resolve_bundle_manifest_for_dashboard(args, sdir)
    artifacts = {}
    if bundle_manifest:
        artifacts = {
            "bundle_manifest": str(bundle_manifest_path),
            "comparison": bundle_manifest.get("comparison_path"),
            "plot_manifest": bundle_manifest.get("plot_manifest_path"),
            "microdata_html": bundle_manifest.get("microdata_html_path"),
            "microdata_json": bundle_manifest.get("microdata_json_path"),
            "report_job": (bundle_manifest.get("report_export") or {}).get("job_path"),
            "report_prompt": (bundle_manifest.get("report_export") or {}).get("prompt_path"),
            "report_context": (bundle_manifest.get("report_export") or {}).get("context_path"),
        }
    output_path = Path(args.path) if args.path else digital_twin_jobs_dir(sdir) / "plans" / args.plan_id / "dashboard.html"
    payload = {
        "survey": args.survey,
        "plan_id": args.plan_id,
        "metric_name": args.metric,
        "metric": {"name": args.metric, **metric_info},
        "model": args.model,
        "status": status,
        "comparisons": comparison_rows,
        "selected": comparison_rows[0] if comparison_rows else None,
        "response_changes": response_changes,
        "plan_notes": twin_plan_note_from_experiments(experiments),
        "approach_notes": approach_notes,
        "bundle_manifest_path": str(bundle_manifest_path) if bundle_manifest_path else None,
        "artifacts": {key: value for key, value in artifacts.items() if value},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_twin_experiment_dashboard_html(payload, output_path=output_path), encoding="utf-8")
    json_path = Path(args.json_path) if args.json_path else output_path.with_suffix(".json")
    write_json(json_path, payload)
    return envelope(
        "zwill twin-experiment dashboard",
        "ok",
        {"path": str(output_path), "json_path": str(json_path), "selected": payload["selected"], "status": status},
        next_steps=[f"open {output_path}"],
    )


def twin_experiment_comparison_rows(
    sdir: Path,
    experiments: list[dict[str, Any]],
    metric: str,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    metric_info = TWIN_EXPERIMENT_METRICS[metric]
    metric_field = metric_info["field"]
    predictions = read_jsonl(digital_twin_predictions_path(sdir))
    rows: list[dict[str, Any]] = []
    for experiment in experiments:
        job_id = experiment.get("job_id")
        job_rows = [row for row in predictions if row.get("job_id") == job_id]
        if not job_rows:
            continue
        report = build_twin_report(job_rows)
        for model_label_key, values in report["summary"].items():
            if model and model_label_key != model:
                continue
            baseline = report["diagnostics"]["baseline_comparison"].get(model_label_key, {})
            metric_value = values.get(metric_field, baseline.get(metric_field))
            if metric_value is None:
                continue
            rows.append(
                {
                    "experiment_id": experiment.get("experiment_id"),
                    "job_id": job_id,
                    "approach": experiment.get("approach"),
                    "description": experiment.get("description", ""),
                    "tags": experiment.get("tags", []),
                    "model": model_label_key,
                    "rows": values["rows"],
                    "metric": metric,
                    "metric_label": metric_info["label"],
                    "metric_direction": metric_info["direction"],
                    "metric_value": metric_value,
                    "accuracy": values["top1_accuracy"],
                    "mean_probability_actual": values["mean_probability_actual"],
                    "mean_negative_log_likelihood": values["mean_negative_log_likelihood"],
                    "mean_brier": values["mean_brier"],
                    "nll_vs_empirical": baseline.get("nll_vs_empirical"),
                    "brier_vs_empirical": baseline.get("brier_vs_empirical"),
                }
            )
    reverse = metric_info["direction"] == "higher"
    rows.sort(key=lambda row: row["metric_value"], reverse=reverse)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["selected"] = index == 1
    return rows, metric_info


def twin_experiment_response_changes(
    sdir: Path,
    comparison_rows: list[dict[str, Any]],
    model: str | None = None,
) -> list[dict[str, Any]]:
    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    changes: list[dict[str, Any]] = []
    for better_index, better in enumerate(comparison_rows):
        for worse in comparison_rows[better_index + 1 :]:
            if better.get("model") != worse.get("model"):
                continue
            changes.extend(
                paired_twin_response_changes(
                    all_rows,
                    str(worse["job_id"]),
                    str(better["job_id"]),
                    from_label=str(worse.get("approach") or worse["job_id"]),
                    to_label=str(better.get("approach") or better["job_id"]),
                    model=model or str(better.get("model")),
                )
            )
    return changes


def selected_twin_experiments(args: argparse.Namespace, sdir: Path) -> list[dict[str, Any]]:
    experiments = read_twin_experiments(sdir)
    selected_ids = args.experiment_id or []
    selected_jobs = args.job_id or []
    if args.jobs:
        selected_jobs.extend(job_id.strip() for job_id in args.jobs.split(",") if job_id.strip())
    if selected_ids:
        experiments = [item for item in experiments if item.get("experiment_id") in selected_ids]
    if selected_jobs:
        experiments = [item for item in experiments if item.get("job_id") in selected_jobs]
    if not experiments:
        raise ZwillError("not_found", "No twin experiments found for the requested filters.", hint="Run `zwill twin-experiment record --survey <survey> --job-id <job_id> --approach <name>`.")
    return experiments


def output_twin_experiment_comparison(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    rows = payload["comparisons"]
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "csv":
        fieldnames = [
            "rank",
            "selected",
            "experiment_id",
            "job_id",
            "approach",
            "model",
            "rows",
            "metric",
            "metric_direction",
            "metric_value",
            "accuracy",
            "mean_probability_actual",
            "mean_negative_log_likelihood",
            "mean_brier",
            "nll_vs_empirical",
            "brier_vs_empirical",
        ]
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.path).open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: row.get(key) for key in fieldnames})
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key) for key in fieldnames})
        return
    table = Table(title=f"{payload['survey']} twin experiment comparison")
    for column in ["rank", "selected", "approach", "job_id", "model", "metric", "value", "accuracy", "nll", "brier"]:
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row["rank"]),
            "*" if row["selected"] else "",
            str(row["approach"]),
            str(row["job_id"]),
            str(row["model"]),
            row["metric_label"],
            f"{row['metric_value']:.4f}",
            f"{row['accuracy']:.3f}",
            f"{row['mean_negative_log_likelihood']:.3f}",
            f"{row['mean_brier']:.3f}",
        )
    Console().print(table)
    Console().print(f"{payload['metric']['label']}: {payload['metric']['meaning']} Direction: {payload['metric']['direction']} is better.")
    if payload.get("response_changes"):
        change_table = Table(title="Paired top-choice changes")
        for column in ["from", "to", "model", "paired", "changed", "corrections", "regressions", "p(actual) delta", "NLL delta"]:
            change_table.add_column(column)
        for row in payload["response_changes"]:
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


def cmd_twin_experiment_compare(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    experiments = selected_twin_experiments(args, sdir)
    rows, metric_info = twin_experiment_comparison_rows(sdir, experiments, args.metric, args.model)
    if not rows:
        raise ZwillError("not_found", "No scored experiment rows found for the requested filters.")
    payload = {
        "survey": args.survey,
        "metric": {"name": args.metric, **metric_info},
        "comparisons": rows,
        "selected": rows[0],
        "response_changes": twin_experiment_response_changes(sdir, rows, args.model),
    }
    output_twin_experiment_comparison(args, payload)


def twin_experiment_plot_id(args: argparse.Namespace, comparison_rows: list[dict[str, Any]]) -> str:
    payload = {
        "survey": args.survey,
        "metric": args.metric,
        "model": args.model,
        "experiment_id": args.experiment_id,
        "job_id": args.job_id,
        "jobs": args.jobs,
        "comparison_jobs": [row.get("job_id") for row in comparison_rows],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def plot_category_style(category: str) -> tuple[str, str]:
    styles = {
        "correction": ("Correction", "#247a48"),
        "regression": ("Regression", "#b23a2e"),
        "unchanged_correct": ("Unchanged correct", "#315f93"),
        "unchanged_wrong": ("Unchanged wrong", "#7a8594"),
        "changed_wrong_to_wrong": ("Changed wrong to wrong", "#a66a1f"),
        "changed_correct_to_correct": ("Changed correct to correct", "#5c6f2a"),
    }
    return styles.get(category, (category.replace("_", " ").title(), "#475569"))


def render_paired_probability_scatter_svg(
    pairs: list[dict[str, Any]],
    *,
    title: str,
    from_label: str,
    to_label: str,
    width: int = 760,
    height: int = 560,
) -> str:
    margin_left, margin_right, margin_top, margin_bottom = 72, 32, 70, 76
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    def sx(value: Any) -> float:
        return margin_left + max(0.0, min(1.0, float(value or 0.0))) * plot_width

    def sy(value: Any) -> float:
        return margin_top + (1.0 - max(0.0, min(1.0, float(value or 0.0)))) * plot_height

    ticks = []
    for tick in [0, 0.25, 0.5, 0.75, 1.0]:
        x = sx(tick)
        y = sy(tick)
        ticks.append(f'<line x1="{x:.1f}" y1="{margin_top}" x2="{x:.1f}" y2="{height - margin_bottom}" class="grid"/>')
        ticks.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" class="grid"/>')
        ticks.append(f'<text x="{x:.1f}" y="{height - margin_bottom + 22}" text-anchor="middle" class="tick">{tick:.2f}</text>')
        ticks.append(f'<text x="{margin_left - 12}" y="{y + 4:.1f}" text-anchor="end" class="tick">{tick:.2f}</text>')

    points = []
    for index, row in enumerate(pairs):
        label, color = plot_category_style(str(row.get("category")))
        x = sx(row.get("from_probability_actual"))
        y = sy(row.get("to_probability_actual"))
        tooltip = (
            f"{row.get('respondent_id')} | {label}\\n"
            f"actual: {row.get('actual_answer')}\\n"
            f"{from_label}: {row.get('from_top_choice')} p(actual)={float(row.get('from_probability_actual') or 0):.3f}\\n"
            f"{to_label}: {row.get('to_top_choice')} p(actual)={float(row.get('to_probability_actual') or 0):.3f}"
        )
        radius = 5.5 if row.get("changed_top_choice") else 4.2
        points.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{color}" fill-opacity="0.82" stroke="#ffffff" stroke-width="1.2">'
            f"<title>{html_escape(tooltip)}</title></circle>"
        )
        if index > 800:
            break

    category_counts = Counter(str(row.get("category")) for row in pairs)
    legend_items = []
    legend_x = margin_left
    legend_y = height - 28
    offset = 0
    for category in ["unchanged_correct", "unchanged_wrong", "correction", "regression", "changed_wrong_to_wrong", "changed_correct_to_correct"]:
        count = category_counts.get(category, 0)
        if not count:
            continue
        label, color = plot_category_style(category)
        item_x = legend_x + offset
        legend_items.append(f'<circle cx="{item_x}" cy="{legend_y}" r="5" fill="{color}"/>')
        legend_items.append(f'<text x="{item_x + 9}" y="{legend_y + 4}" class="legend">{html_escape(label)} ({count})</text>')
        offset += 138

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{html_escape(title)}">
  <style>
    .title {{ font: 700 18px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#17202a; }}
    .subtitle {{ font: 13px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#607080; }}
    .axis {{ stroke:#17202a; stroke-width:1.2; }}
    .diag {{ stroke:#202124; stroke-width:1.4; stroke-dasharray:5 5; opacity:.72; }}
    .grid {{ stroke:#dfe5ec; stroke-width:1; }}
    .tick,.legend {{ font: 12px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#4a5563; }}
    .label {{ font: 13px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#202124; font-weight:650; }}
  </style>
  <rect width="{width}" height="{height}" fill="#ffffff"/>
  <text x="{margin_left}" y="30" class="title">{html_escape(title)}</text>
  <text x="{margin_left}" y="52" class="subtitle">Each point is the same respondent/question/model in both arms. Above the diagonal means higher p(actual) after the change.</text>
  {''.join(ticks)}
  <line x1="{margin_left}" y1="{height - margin_bottom}" x2="{width - margin_right}" y2="{height - margin_bottom}" class="axis"/>
  <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{height - margin_bottom}" class="axis"/>
  <line x1="{margin_left}" y1="{height - margin_bottom}" x2="{width - margin_right}" y2="{margin_top}" class="diag"/>
  <text x="{margin_left + plot_width / 2:.1f}" y="{height - 42}" text-anchor="middle" class="label">{html_escape(from_label)} p(actual)</text>
  <text transform="translate(20 {margin_top + plot_height / 2:.1f}) rotate(-90)" text-anchor="middle" class="label">{html_escape(to_label)} p(actual)</text>
  {''.join(points)}
  {''.join(legend_items)}
</svg>
"""


def render_top_choice_change_svg(
    summary: dict[str, Any],
    *,
    title: str,
    width: int = 760,
    height: int = 260,
) -> str:
    categories = [
        ("unchanged_correct", "Unchanged correct", int(summary.get("unchanged_correct", 0))),
        ("unchanged_wrong", "Unchanged wrong", int(summary.get("unchanged_wrong", 0))),
        ("corrections", "Corrections", int(summary.get("corrections", 0))),
        ("regressions", "Regressions", int(summary.get("regressions", 0))),
        ("changed_wrong_to_wrong", "Changed wrong to wrong", int(summary.get("changed_wrong_to_wrong", 0))),
        ("changed_correct_to_correct", "Changed correct to correct", int(summary.get("changed_correct_to_correct", 0))),
    ]
    total = max(1, int(summary.get("paired_rows", 0)))
    x0, y0, bar_width, bar_height = 48, 104, width - 96, 34
    segments = []
    cursor = x0
    for key, label, count in categories:
        if not count:
            continue
        style_key = {
            "corrections": "correction",
            "regressions": "regression",
        }.get(key, key)
        _, color = plot_category_style(style_key)
        segment_width = bar_width * (count / total)
        segments.append(
            f'<rect x="{cursor:.1f}" y="{y0}" width="{segment_width:.1f}" height="{bar_height}" fill="{color}"><title>{html_escape(label)}: {count}</title></rect>'
        )
        cursor += segment_width
    legend = []
    lx, ly, offset = x0, 170, 0
    for key, label, count in categories:
        if not count:
            continue
        style_key = {"corrections": "correction", "regressions": "regression"}.get(key, key)
        _, color = plot_category_style(style_key)
        item_x = lx + offset
        legend.append(f'<rect x="{item_x}" y="{ly}" width="11" height="11" rx="2" fill="{color}"/>')
        legend.append(f'<text x="{item_x + 16}" y="{ly + 10}" class="legend">{html_escape(label)} ({count})</text>')
        offset += 180
        if offset > width - 220:
            offset = 0
            ly += 24
    changed = int(summary.get("changed_top_choice", 0))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{html_escape(title)}">
  <style>
    .title {{ font: 700 18px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#17202a; }}
    .subtitle,.legend {{ font: 13px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#4a5563; }}
    .big {{ font: 700 24px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#17202a; }}
  </style>
  <rect width="{width}" height="{height}" fill="#ffffff"/>
  <text x="{x0}" y="34" class="title">{html_escape(title)}</text>
  <text x="{x0}" y="58" class="subtitle">Top-choice changes among paired twins from {html_escape(summary.get('from_label'))} to {html_escape(summary.get('to_label'))}.</text>
  <text x="{x0}" y="90" class="big">{changed} of {total} changed top choice ({changed / total:.1%})</text>
  <rect x="{x0}" y="{y0}" width="{bar_width}" height="{bar_height}" rx="6" fill="#eef2f6"/>
  {''.join(segments)}
  <rect x="{x0}" y="{y0}" width="{bar_width}" height="{bar_height}" rx="6" fill="none" stroke="#d8dee6"/>
  {''.join(legend)}
</svg>
"""


def write_twin_experiment_plots(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    experiments = selected_twin_experiments(args, sdir)
    comparison_rows, metric_info = twin_experiment_comparison_rows(sdir, experiments, args.metric, args.model)
    if len(comparison_rows) < 2:
        raise ZwillError("not_found", "At least two scored experiment rows are required to make comparison plots.")
    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    plot_id = args.plot_id or twin_experiment_plot_id(args, comparison_rows)
    output_dir = Path(args.path) if args.path else digital_twin_jobs_dir(sdir) / "plots" / plot_id
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []
    plot_summaries = []
    pair_index = 0
    for better_index, better in enumerate(comparison_rows):
        for worse in comparison_rows[better_index + 1 :]:
            if better.get("model") != worse.get("model"):
                continue
            pair_index += 1
            from_label = str(worse.get("approach") or worse["job_id"])
            to_label = str(better.get("approach") or better["job_id"])
            pair_rows = paired_twin_response_pair_rows(
                all_rows,
                str(worse["job_id"]),
                str(better["job_id"]),
                from_label=from_label,
                to_label=to_label,
                model=str(better.get("model")),
            )
            summaries = paired_twin_response_changes(
                all_rows,
                str(worse["job_id"]),
                str(better["job_id"]),
                from_label=from_label,
                to_label=to_label,
                model=str(better.get("model")),
            )
            if not pair_rows or not summaries:
                continue
            summary = summaries[0]
            pair_slug = f"pair_{pair_index}_{summary['model'].replace(':', '_').replace('/', '_')}"
            data_path = output_dir / f"{pair_slug}_data.json"
            scatter_path = output_dir / f"{pair_slug}_p_actual_scatter.svg"
            change_path = output_dir / f"{pair_slug}_top_choice_changes.svg"
            microdata_path = output_dir / f"{pair_slug}_microdata.html"
            microdata_data_path = output_dir / f"{pair_slug}_microdata.json"
            title_base = f"{summary['model']}: {from_label} vs {to_label}"
            microdata_rows, microdata_metadata = paired_twin_microdata_rows(sdir, all_rows, pair_rows)
            write_json(
                data_path,
                {
                    "summary": summary,
                    "pairs": pair_rows,
                    "metric": {"name": args.metric, **metric_info},
                },
            )
            scatter_svg = render_paired_probability_scatter_svg(
                pair_rows,
                title=f"Paired probability movement: {title_base}",
                from_label=from_label,
                to_label=to_label,
            )
            change_svg = render_top_choice_change_svg(summary, title=f"Top-choice changes: {title_base}")
            microdata_html = render_twin_microdata_table_html(
                microdata_rows,
                title=f"Twin microdata: {title_base}",
                include_title=False,
            )
            scatter_path.write_text(scatter_svg)
            change_path.write_text(change_svg)
            microdata_path.write_text(microdata_html)
            write_json(
                microdata_data_path,
                {
                    "summary": summary,
                    "metadata": microdata_metadata,
                    "rows": microdata_rows,
                },
            )
            plot_summaries.append(summary)
            artifacts.extend(
                [
                    {
                        "plot_id": f"{pair_slug}_p_actual_scatter",
                        "kind": "paired_probability_scatter",
                        "title": f"Paired probability movement: {title_base}",
                        "path": str(scatter_path),
                        "data_path": str(data_path),
                        "summary": summary,
                    },
                    {
                        "plot_id": f"{pair_slug}_top_choice_changes",
                        "kind": "top_choice_change_summary",
                        "title": f"Top-choice changes: {title_base}",
                        "path": str(change_path),
                        "data_path": str(data_path),
                        "summary": summary,
                    },
                    {
                        "plot_id": f"{pair_slug}_microdata",
                        "kind": "paired_microdata_table",
                        "title": f"Twin microdata: {title_base}",
                        "path": str(microdata_path),
                        "data_path": str(microdata_data_path),
                        "summary": summary,
                    },
                ]
            )
    if not artifacts:
        raise ZwillError("not_found", "No paired respondent/question/model rows were available for plots.")
    manifest = {
        "plot_id": plot_id,
        "survey": args.survey,
        "metric": {"name": args.metric, **metric_info},
        "created_at": utc_now(),
        "comparison_rows": comparison_rows,
        "response_changes": plot_summaries,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return {
        "plot_id": plot_id,
        "plot_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }


def normalize_plot_manifest_paths(paths: list[str] | None) -> list[str]:
    return [str(Path(path)) for path in paths or [] if str(path).strip()]


def load_plot_summaries(paths: list[str] | None) -> list[dict[str, Any]]:
    summaries = []
    for path_text in normalize_plot_manifest_paths(paths):
        manifest_path = Path(path_text)
        if not manifest_path.exists():
            raise ZwillError("not_found", f"Plot manifest does not exist: {manifest_path}.")
        manifest = read_json(manifest_path, {})
        summaries.append(
            {
                "manifest_path": str(manifest_path),
                "plot_id": manifest.get("plot_id"),
                "survey": manifest.get("survey"),
                "metric": manifest.get("metric"),
                "response_changes": manifest.get("response_changes", []),
                "artifacts": [
                    {
                        "plot_id": artifact.get("plot_id"),
                        "kind": artifact.get("kind"),
                        "title": artifact.get("title"),
                        "path": artifact.get("path"),
                        "summary": artifact.get("summary"),
                    }
                    for artifact in manifest.get("artifacts", [])
                ],
            }
        )
    return summaries


def attach_plot_artifacts_to_payload(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    report_context = context.get("report_context", {}) if isinstance(context.get("report_context"), dict) else {}
    manifests = context.get("plot_manifests") or report_context.get("plot_manifests", [])
    if not manifests:
        return payload
    payload = {**payload}
    plot_artifacts = []
    for path_text in manifests:
        manifest_path = Path(path_text)
        manifest = read_json(manifest_path, {})
        for artifact in manifest.get("artifacts", []):
            artifact_path = Path(str(artifact.get("path", "")))
            if not artifact_path.exists():
                continue
            artifact_text = artifact_path.read_text()
            artifact_payload = {
                "plot_id": artifact.get("plot_id"),
                "kind": artifact.get("kind"),
                "title": artifact.get("title"),
                "path": str(artifact_path),
                "summary": artifact.get("summary"),
            }
            if str(artifact.get("kind")) == "paired_microdata_table":
                artifact_payload["html"] = artifact_text
            else:
                artifact_payload["svg"] = artifact_text
            plot_artifacts.append(
                artifact_payload
            )
    payload["plot_artifacts"] = plot_artifacts
    payload["plots"] = context.get("plot_summaries") or report_context.get("plot_summaries", payload.get("plots", []))
    return payload


def cmd_twin_experiment_plots(args: argparse.Namespace) -> dict[str, Any]:
    data = write_twin_experiment_plots(args)
    return envelope(
        "zwill twin-experiment plots",
        "ok",
        data,
        next_steps=[
            f"zwill twin-experiment report-export --survey {args.survey} --include-plots {data['manifest_path']}",
        ],
    )


def cmd_twin_experiment_select(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    experiments = selected_twin_experiments(args, sdir)
    rows, metric_info = twin_experiment_comparison_rows(sdir, experiments, args.metric, args.model)
    if not rows:
        raise ZwillError("not_found", "No scored experiment rows found for the requested filters.")
    return envelope(
        "zwill twin-experiment select",
        "ok",
        {
            "survey": args.survey,
            "metric": {"name": args.metric, **metric_info},
            "selected": rows[0],
            "candidate_count": len(rows),
        },
    )

