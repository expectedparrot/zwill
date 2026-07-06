from __future__ import annotations

from .cli import *  # noqa: F403


def load_workflow_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ZwillError("not_found", f"Workflow file does not exist: {path}.")
    data = load_object_file(path, kind="Workflow")
    if not isinstance(data.get("steps"), list) or not data["steps"]:
        raise ZwillError("invalid_input", "Workflow file must contain a non-empty steps list.")
    return data


def load_object_file(path: Path, *, kind: str = "Config") -> dict[str, Any]:
    if not path.exists():
        raise ZwillError("not_found", f"{kind} file does not exist: {path}.")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text())
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise ZwillError(
                "missing_dependency",
                f"YAML {kind.lower()} files require PyYAML.",
                hint=f"Install project dependencies or use a JSON {kind.lower()} file.",
            ) from exc
        data = yaml.safe_load(path.read_text())
    else:
        raise ZwillError("invalid_input", f"{kind} file must be .json, .yaml, or .yml.")
    if not isinstance(data, dict):
        raise ZwillError("invalid_input", f"{kind} file must contain an object.")
    return data


VAR_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")


def workflow_vars(config: dict[str, Any], overrides: list[str] | None = None) -> dict[str, str]:
    values = {str(key): str(value) for key, value in (config.get("vars") or {}).items()}
    for item in overrides or []:
        if "=" not in item:
            raise ZwillError("invalid_input", f"Invalid workflow variable override: {item}.", hint="Use key=value.")
        key, value = item.split("=", 1)
        values[key] = value
    return values


def render_workflow_value(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                raise ZwillError("invalid_input", f"Unknown workflow variable: {name}.")
            return values[name]

        return VAR_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [render_workflow_value(item, values) for item in value]
    if isinstance(value, dict):
        return {key: render_workflow_value(item, values) for key, item in value.items()}
    return value


def workflow_step_id(step: dict[str, Any], index: int) -> str:
    return slugify(str(step.get("id") or step.get("name") or f"step-{index + 1:02d}"))


def rendered_workflow_steps(config: dict[str, Any], values: dict[str, str]) -> list[dict[str, Any]]:
    steps = []
    for index, raw_step in enumerate(config["steps"]):
        if not isinstance(raw_step, dict):
            raise ZwillError("invalid_input", "Each workflow step must be an object.", context={"index": index})
        step = render_workflow_value(raw_step, values)
        if not step.get("run"):
            raise ZwillError("invalid_input", "Each workflow step needs a run command.", context={"index": index})
        step["id"] = workflow_step_id(step, index)
        step["index"] = index
        ok_codes = step.get("ok_return_codes", [0])
        if not isinstance(ok_codes, list):
            ok_codes = [ok_codes]
        step["ok_return_codes"] = [int(code) for code in ok_codes]
        if step.get("env") and not isinstance(step["env"], dict):
            raise ZwillError("invalid_input", "Workflow step env must be an object.", context={"step": step["id"]})
        steps.append(step)
    return steps


def default_workflow_artifacts_dir(config: dict[str, Any]) -> Path:
    name = slugify(str(config.get("name") or "workflow"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return active_project_dir() / "workflows" / f"{name}-{stamp}"


def workflow_manifest_path(artifacts_dir: Path) -> Path:
    return artifacts_dir / "manifest.json"


def workflow_base_payload(path: Path, config: dict[str, Any], values: dict[str, str], steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "workflow_path": str(path),
        "name": config.get("name"),
        "description": config.get("description"),
        "vars": values,
        "step_count": len(steps),
        "steps": [
            {
                "id": step["id"],
                "name": step.get("name"),
                "run": step["run"],
                "cwd": step.get("cwd"),
                "continue_on_error": bool(step.get("continue_on_error", False)),
            }
            for step in steps
        ],
    }


def cmd_workflow_explain(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.path)
    config = load_workflow_file(path)
    values = workflow_vars(config, args.var)
    steps = rendered_workflow_steps(config, values)
    return envelope("zwill workflow explain", "ok", workflow_base_payload(path, config, values, steps))


def cmd_workflow_dry_run(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.path)
    config = load_workflow_file(path)
    values = workflow_vars(config, args.var)
    steps = rendered_workflow_steps(config, values)
    data = workflow_base_payload(path, config, values, steps)
    data["dry_run"] = True
    return envelope("zwill workflow dry-run", "ok", data)


def cmd_workflow_run(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.path)
    config = load_workflow_file(path)
    values = workflow_vars(config, args.var)
    steps = rendered_workflow_steps(config, values)
    artifacts_dir = Path(args.artifacts_dir) if args.artifacts_dir else default_workflow_artifacts_dir(config)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    previous_manifest = read_json(workflow_manifest_path(artifacts_dir), {"steps": []}) if args.resume else {"steps": []}
    previous_steps = list(previous_manifest.get("steps", []))
    run_started_at = utc_now()
    completed = []

    for step in steps:
        step_id = step["id"]
        stdout_path = artifacts_dir / f"{step['index'] + 1:02d}_{step_id}.stdout.txt"
        stderr_path = artifacts_dir / f"{step['index'] + 1:02d}_{step_id}.stderr.txt"
        if args.resume and any(row.get("id") == step_id and row.get("status") == "ok" for row in previous_steps):
            completed.append(
                {
                    "id": step_id,
                    "name": step.get("name"),
                    "run": step["run"],
                    "status": "skipped",
                    "returncode": 0,
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "started_at": None,
                    "finished_at": utc_now(),
                    "cwd": step.get("cwd"),
                }
            )
            continue

        cwd = Path(step.get("cwd") or ".")
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in (step.get("env") or {}).items()})
        started_at = utc_now()
        result = subprocess.run(
            step["run"],
            shell=True,
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
        )
        stdout_path.write_text(result.stdout)
        stderr_path.write_text(result.stderr)
        ok = result.returncode in step["ok_return_codes"]
        row = {
            "id": step_id,
            "name": step.get("name"),
            "run": step["run"],
            "status": "ok" if ok else "error",
            "returncode": result.returncode,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "started_at": started_at,
            "finished_at": utc_now(),
            "cwd": str(cwd),
        }
        completed.append(row)
        write_json(
            workflow_manifest_path(artifacts_dir),
            {
                "workflow_path": str(path),
                "name": config.get("name"),
                "description": config.get("description"),
                "artifacts_dir": str(artifacts_dir),
                "started_at": run_started_at,
                "updated_at": utc_now(),
                "status": "running" if ok else "error",
                "vars": values,
                "steps": previous_steps + completed,
            },
        )
        if not ok and not step.get("continue_on_error", False):
            raise ZwillError(
                "workflow_step_failed",
                f"Workflow step failed: {step_id}.",
                context={"step": row, "artifacts_dir": str(artifacts_dir)},
                hint=f"Inspect {stderr_path} and rerun with --resume --artifacts-dir {artifacts_dir}.",
            )

    final_steps = previous_steps + completed
    final_manifest = {
        "workflow_path": str(path),
        "name": config.get("name"),
        "description": config.get("description"),
        "artifacts_dir": str(artifacts_dir),
        "started_at": run_started_at,
        "finished_at": utc_now(),
        "status": "ok",
        "vars": values,
        "steps": final_steps,
    }
    write_json(workflow_manifest_path(artifacts_dir), final_manifest)
    return envelope(
        "zwill workflow run",
        "ok",
        {
            "workflow_path": str(path),
            "artifacts_dir": str(artifacts_dir),
            "manifest_path": str(workflow_manifest_path(artifacts_dir)),
            "step_count": len(steps),
            "steps": final_steps[-len(steps):],
        },
    )



def default_pew_source_dir() -> Path:
    return Path(
        "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/"
        "papers/microdata_twins/data/computed_objects/normalized"
    )


def cmd_workflow_pew_demo(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    survey_name = "pew_w154_diff1"
    source_dir = Path(args.source_dir or default_pew_source_dir())
    workdir = Path(args.workdir or repo_root / "examples" / survey_name / "workdir")
    import_dir = workdir / "imports"

    if args.fresh:
        shutil.rmtree(workdir / ".zwill", ignore_errors=True)
        shutil.rmtree(import_dir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    prepare_script = repo_root / "examples" / survey_name / "prepare_imports.py"
    subprocess.run(
        [sys.executable, str(prepare_script), "--source-dir", str(source_dir), "--out-dir", str(import_dir)],
        check=True,
        text=True,
        capture_output=True,
    )

    old_cwd = Path.cwd()
    paths: dict[str, str] = {"workdir": str(workdir), "imports": str(import_dir)}
    try:
        os.chdir(workdir)
        cmd_init(argparse.Namespace())
        cmd_survey_create(argparse.Namespace(name=survey_name))
        cmd_context_set(
            argparse.Namespace(
                survey=survey_name,
                path=str(repo_root / "examples" / survey_name / "context.md"),
                text=None,
            )
        )
        cmd_raw_add(
            argparse.Namespace(
                survey=survey_name,
                id="w154_diff1_metadata",
                path=str(source_dir / "W154_DIFF1_metadata.json"),
                kind="metadata",
                title="Pew W154 DIFF1 Normalized Metadata",
            )
        )
        cmd_raw_add(
            argparse.Namespace(
                survey=survey_name,
                id="w154_diff1_respondents",
                path=str(source_dir / "W154_DIFF1_respondents.csv"),
                kind="respondent_data",
                title="Pew W154 DIFF1 Normalized Respondents",
            )
        )
        cmd_question_import(argparse.Namespace(survey=survey_name, path=str(import_dir / "questions.jsonl")))
        cmd_respondent_import(argparse.Namespace(survey=survey_name, path=str(import_dir / "respondents.jsonl")))
        cmd_answer_import(argparse.Namespace(survey=survey_name, path=str(import_dir / "answers.jsonl")))
        commit_result = cmd_commit(argparse.Namespace(survey=survey_name))

        if not args.no_edsl:
            survey_export_path = workdir / f"{survey_name}.edsl.json"
            survey_export_path.write_text(json.dumps(build_edsl_survey_dict(survey_name), indent=2) + "\n")
            paths["edsl_survey"] = str(survey_export_path)

            probability_job_args = argparse.Namespace(
                survey=survey_name,
                question=args.question,
                questions=args.questions,
                exclude_question=args.exclude_question,
                limit=None,
                model=args.model,
                models=args.models,
                service_name=args.service_name,
                model_param=args.model_param,
                job_question_name=args.job_question_name,
            )
            probability_job = build_edsl_probability_job_dict(survey_name, probability_job_args)
            probability_job_path = workdir / f"{survey_name}_probability_job.edsl.json"
            probability_job_path.write_text(json.dumps(probability_job, indent=2) + "\n")
            paths["probability_job"] = str(probability_job_path)
            paths["probability_job_id"] = probability_job.get("zwill", {}).get("probability_job_id")

        if args.results_path:
            import_result = cmd_probability_results_import(
                argparse.Namespace(
                    survey=survey_name,
                    path=args.results_path,
                    job_id=args.job_id,
                    replace=True,
                )
            )
            job_id = import_result["data"]["job_id"]
            paths["imported_results_job_id"] = job_id
            for report_format, suffix in [("json", "json"), ("csv", "csv"), ("html", "html")]:
                report_path = workdir / f"{survey_name}_probability_report.{suffix}"
                cmd_probability_results_report(
                    argparse.Namespace(
                        survey=survey_name,
                        job_id=job_id,
                        model=None,
                        format=report_format,
                        path=str(report_path),
                    )
                )
                paths[f"{report_format}_report"] = str(report_path)
    finally:
        os.chdir(old_cwd)

    return envelope(
        "zwill workflow pew-demo",
        "ok",
        {
            "survey": survey_name,
            "source_dir": str(source_dir),
            "paths": paths,
            "commit": commit_result["data"],
        },
        next_steps=[
            f"cd {workdir}",
            f"zwill table --survey {survey_name} --limit 12",
        ],
    )

