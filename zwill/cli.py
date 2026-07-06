from __future__ import annotations

import argparse
import csv
import contextlib
import fcntl
import gzip
import hashlib
import importlib.resources as resources
import json
import os
import random
import re
import subprocess
import shutil
import sys
import tempfile
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .errors import ZwillError
from .executive_summary import build_executive_summary, remove_leading_executive_summary_heading
from .probability import (
    probability_job_id_from_job,
    probability_job_id_from_results,
    probability_jobs_dir,
    probability_predictions_path,
    true_probabilities_for,
)
from .probability_jobs import (
    ProbabilityJobBuilderDeps,
    build_edsl_probability_job_dict as build_edsl_probability_job_dict_impl,
)
from .rank import (
    annotate_rank_items,
    build_rank_report,
    detect_rank_tasks,
    extract_rank_payload,
    rank_job_id_from_job,
    rank_job_id_from_results,
    rank_metrics,
    rank_twin_jobs_dir,
    rank_twin_predictions_path,
    selected_rank_tasks,
    synthetic_rank_questions,
)
from .twin_jobs import (
    DigitalTwinJobBuilderDeps,
    answer_commonness_by_question,
    answer_commonness_text,
    balanced_by_actual,
    build_edsl_digital_twin_job_dict as build_edsl_digital_twin_job_dict_impl,
    chunked_job_id,
    expand_question_text_fields,
    result_chunk_label,
    selected_heldout_question_names,
    stratified_by_actual,
)
from .twin_results import (
    aggregate_twin_marginals,
    distribution_distance_metrics,
    filter_prediction_rows,
    job_ids_from_manifest,
    top_prediction,
    twin_prediction_export_rows,
    write_csv_rows,
    zip_csv,
)
from .twin_diagnostics import (
    build_twin_conditional_consistency_diagnostics,
    build_twin_joint_structure_diagnostics,
    build_twin_subgroup_marginal_diagnostics,
)
from .reporting import (
    EP_REPORT_CSS,
    build_probability_report,
    copy_markdown_control,
    escape_script_text,
    fmt_probs,
    markdown_to_html,
    report_display_title,
    render_probability_report_html,
    render_twin_benchmark_report_html,
    render_twin_job_comparison_report_html,
    render_twin_practitioner_report_html,
    render_twin_report_html,
    render_twin_run_report_html,
    render_twin_summary_report_html,
    render_twin_supporting_artifacts_section,
    render_twin_value_diagnostics_section,
)
from .result_imports import (
    extract_probability_prediction_rows,
    extract_twin_prediction_rows,
)
from .survey_report import (
    build_survey_report_payload,
    render_survey_report_html,
    write_survey_report_csvs,
)
from .twin import (
    calibrate_probabilities_to_marginal,
    digital_twin_job_id_from_job,
    digital_twin_job_id_from_results,
    digital_twin_jobs_dir,
    digital_twin_predictions_path,
    one_hot_metrics,
    select_context_questions,
)


ROOT = Path(".zwill")
SCHEMA_VERSION = 1
DEFAULT_PROJECT_ID = "default"
DEFAULT_REPORT_PERMUTATIONS = 1000
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SKILL_NAMES = ["digital-twin-study-runner", "digital-twin-practitioner-report"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def envelope(
    command: str,
    status: str,
    data: dict[str, Any] | None = None,
    *,
    warnings: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
    next_steps: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "command": command,
        "status": status,
        "data": data or {},
        "warnings": warnings or [],
        "errors": errors or [],
        "next_steps": next_steps or [],
    }


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2))


def installed_skill_path(name: str) -> Path:
    if name not in SKILL_NAMES:
        raise ZwillError("not_found", f"Unknown zwill skill: {name}.", context={"known_skills": SKILL_NAMES})
    return Path(str(resources.files("zwill") / "skills" / name))


def cmd_skills_list(args: argparse.Namespace) -> dict[str, Any]:
    rows = []
    for name in SKILL_NAMES:
        path = installed_skill_path(name)
        rows.append(
            {
                "name": name,
                "path": str(path),
                "skill_md": str(path / "SKILL.md"),
            }
        )
    if args.format == "table":
        table = Table(title="zwill installed agent skills")
        table.add_column("skill")
        table.add_column("path")
        for row in rows:
            table.add_row(row["name"], row["path"])
        Console().print(table)
    elif args.format == "json":
        print_json(envelope("zwill skills list", "ok", {"skills": rows}))
    return envelope("zwill skills list", "ok", {"skills": rows})


def cmd_skills_path(args: argparse.Namespace) -> dict[str, Any]:
    path = installed_skill_path(args.name)
    if args.format == "json":
        print_json(envelope("zwill skills path", "ok", {"name": args.name, "path": str(path), "skill_md": str(path / "SKILL.md")}))
    else:
        print(path)
    return envelope("zwill skills path", "ok", {"name": args.name, "path": str(path), "skill_md": str(path / "SKILL.md")})


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def require_workspace() -> None:
    if not ROOT.exists():
        raise ZwillError("not_initialized", "No .zwill directory found.", hint="Run `zwill init`.")


def projects_dir() -> Path:
    return ROOT / "projects"


def head_path() -> Path:
    return ROOT / "HEAD"


def validate_project_id(project_id: str) -> str:
    if not PROJECT_ID_RE.match(project_id):
        raise ZwillError(
            "invalid_input",
            f"Invalid project id: {project_id}.",
            hint="Use letters, numbers, dots, underscores, or hyphens; start with a letter or number.",
        )
    return project_id


def project_dir(project_id: str) -> Path:
    return projects_dir() / validate_project_id(project_id)


def active_project_id() -> str:
    require_workspace()
    env_project = os.environ.get("ZWILL_PROJECT")
    if env_project:
        project_id = validate_project_id(env_project.strip())
    elif head_path().exists():
        project_id = validate_project_id(head_path().read_text().strip())
    else:
        raise ZwillError("not_initialized", "No active zwill project is set.", hint="Run `zwill init`.")
    if not project_dir(project_id).exists():
        raise ZwillError(
            "not_found",
            f"Active project does not exist: {project_id}.",
            hint="Run `zwill project list` or `zwill project create <project_id>`.",
        )
    return project_id


def active_project_dir() -> Path:
    return project_dir(active_project_id())


def project_surveys_path() -> Path:
    return active_project_dir() / "surveys.json"


def ensure_project(project_id: str, *, title: str | None = None) -> Path:
    project_id = validate_project_id(project_id)
    pdir = project_dir(project_id)
    pdir.mkdir(parents=True, exist_ok=True)
    metadata_path = pdir / "project.json"
    if not metadata_path.exists():
        write_json(
            metadata_path,
            {
                "project_id": project_id,
                "title": title or project_id,
                "schema_version": SCHEMA_VERSION,
                "created_at": utc_now(),
            },
        )
    if not (pdir / "surveys.json").exists():
        write_json(pdir / "surveys.json", [])
    return pdir


def require_project() -> Path:
    return active_project_dir()


def survey_dir(name: str) -> Path:
    return active_project_dir() / "surveys" / name


def require_survey(name: str) -> Path:
    require_project()
    path = survey_dir(name)
    if not path.exists():
        raise ZwillError("not_found", f"Survey does not exist: {name}.", hint="Run `zwill survey create --name <survey>`.")
    return path


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


@contextlib.contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def read_json_or_gzip(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return json.load(f)
    return json.loads(path.read_text())


def write_json_or_gzip(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        with gzip.open(path, "wt") as f:
            json.dump(value, f, indent=2)
            f.write("\n")
    else:
        path.write_text(json.dumps(value, indent=2) + "\n")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "workflow"


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


def find_local_env(start: Path | None = None) -> Path | None:
    start = start or Path.cwd()
    for directory in [start, *start.parents]:
        path = directory / ".env"
        if path.exists() and path.is_file():
            return path
    return None


def load_local_env(path: Path | None = None) -> dict[str, Any]:
    path = path or find_local_env()
    if path is None:
        return {"path": None, "loaded_keys": []}
    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "python-dotenv is required to load .env files.",
            hint="Install package dependencies, e.g. `pip install -e .`.",
        ) from exc
    loaded = []
    for key, value in dotenv_values(path).items():
        if not key or value is None or key in os.environ:
            continue
        os.environ[key] = str(value)
        loaded.append(key)
    return {"path": str(path), "loaded_keys": loaded}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(value, separators=(",", ":")) + "\n")


def rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def questions_by_name(sdir: Path) -> dict[str, dict[str, Any]]:
    return {q["question_name"]: q for q in read_jsonl(sdir / "questions.jsonl")}


def context_question_options(question: dict[str, Any]) -> list[str]:
    options = question.get("question_options") or []
    if options:
        return [str(option) for option in options]
    source = question.get("source") or {}
    known_options = source.get("known_options") or []
    if isinstance(known_options, list):
        return [str(option) for option in known_options if str(option).strip()]
    return []


def respondents_by_id(sdir: Path) -> dict[str, dict[str, Any]]:
    return {r["respondent_id"]: r for r in read_jsonl(sdir / "respondents.jsonl")}


def agent_material_path(sdir: Path) -> Path:
    return sdir / "agent_material.jsonl"


def agent_material_rows(sdir: Path) -> list[dict[str, Any]]:
    return read_jsonl(agent_material_path(sdir))


def raw_files_by_id(sdir: Path) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in read_json(sdir / "raw_files.json", [])}


def open_quarantine_issues(sdir: Path) -> list[dict[str, Any]]:
    return [issue for issue in read_jsonl(sdir / "quarantine.jsonl") if issue.get("status") == "open"]


def context_path(sdir: Path) -> Path:
    return sdir / "context.md"


def survey_context_text(sdir: Path) -> str:
    return context_path(sdir).read_text().strip() if context_path(sdir).exists() else ""


def practitioner_reports_dir() -> Path:
    return active_project_dir() / "practitioner_reports"


def agent_studies_dir() -> Path:
    return active_project_dir() / "agent_studies"


def agent_study_dir(job_id: str) -> Path:
    return agent_studies_dir() / job_id


def agent_study_answers_path() -> Path:
    return agent_studies_dir() / "answers.jsonl"


def agent_study_manifest_path() -> Path:
    return agent_studies_dir() / "manifest.json"


def practitioner_report_dir(report_id: str) -> Path:
    return practitioner_reports_dir() / report_id


def survey_summary(name: str) -> dict[str, Any]:
    sdir = require_survey(name)
    surveys = read_json(project_surveys_path(), [])
    survey = next((s for s in surveys if s["name"] == name), {"name": name, "status": "draft"})
    return {
        "name": name,
        "status": survey.get("status", "draft"),
        "raw_files": len(read_json(sdir / "raw_files.json", [])),
        "questions": len(read_jsonl(sdir / "questions.jsonl")),
        "respondents": len(read_jsonl(sdir / "respondents.jsonl")),
        "answers": len(read_jsonl(sdir / "answers.jsonl")),
        "agent_material": len(agent_material_rows(sdir)),
        "has_context": context_path(sdir).exists() and bool(context_path(sdir).read_text().strip()),
        "open_quarantine_issues": len(open_quarantine_issues(sdir)),
        "committed": (sdir / "committed").exists(),
    }


def parse_metadata(values: list[str] | None) -> dict[str, str]:
    metadata = {}
    for value in values or []:
        if "=" not in value:
            raise ZwillError("invalid_input", f"Invalid metadata value: {value}.", hint="Use key=value.")
        key, item = value.split("=", 1)
        metadata[key] = item
    return metadata


def parse_option_labels(values: list[str] | None) -> dict[str, str]:
    labels = {}
    for value in values or []:
        if "=" not in value:
            raise ZwillError("invalid_input", f"Invalid option label: {value}.", hint="Use option=label.")
        key, label = value.split("=", 1)
        labels[key] = label
    return labels


def ensure_implicit_respondent(sdir: Path, respondent_id: str) -> None:
    if respondent_id in respondents_by_id(sdir):
        return
    append_jsonl(sdir / "respondents.jsonl", {"respondent_id": respondent_id, "weight": 1.0, "metadata": {}})


def validate_answer(sdir: Path, answer: dict[str, Any], line: int | None = None) -> dict[str, Any] | None:
    questions = questions_by_name(sdir)
    question_name = answer.get("question")
    if question_name not in questions:
        return {
            "code": "unknown_question",
            "line": line,
            "question": question_name,
        }
    if "answer" in answer and answer.get("answer") is not None:
        valid_options = questions[question_name].get("question_options", [])
        if valid_options and answer["answer"] not in valid_options:
            return {
                "code": "invalid_answer_option",
                "line": line,
                "question": question_name,
                "answer": answer["answer"],
                "valid_options": valid_options,
            }
    elif not answer.get("missing_code"):
        return {
            "code": "invalid_input",
            "line": line,
            "question": question_name,
            "message": "answer or missing_code is required",
        }
    return None


def add_quarantine_issue(sdir: Path, issue: dict[str, Any]) -> dict[str, Any]:
    count = len(read_jsonl(sdir / "quarantine.jsonl")) + 1
    full_issue = {
        "issue_id": f"q{count:05d}",
        "status": "open",
        **{k: v for k, v in issue.items() if v is not None},
    }
    append_jsonl(sdir / "quarantine.jsonl", full_issue)
    return full_issue


def cmd_init(_: argparse.Namespace) -> dict[str, Any]:
    ROOT.mkdir(exist_ok=True)
    write_json(ROOT / "config.json", {"schema_version": SCHEMA_VERSION})
    pdir = ensure_project(DEFAULT_PROJECT_ID, title="Default")
    if not head_path().exists() or not head_path().read_text().strip():
        head_path().write_text(f"{DEFAULT_PROJECT_ID}\n")
    return envelope(
        "zwill init",
        "ok",
        {
            "path": ".zwill",
            "schema_version": SCHEMA_VERSION,
            "active_project": active_project_id(),
            "project_path": str(pdir),
        },
        next_steps=["zwill survey create --name <survey>"],
    )


def project_metadata(project_id: str) -> dict[str, Any]:
    pdir = project_dir(project_id)
    metadata = read_json(pdir / "project.json", {})
    surveys = read_json(pdir / "surveys.json", [])
    return {
        "project_id": project_id,
        "title": metadata.get("title", project_id),
        "schema_version": metadata.get("schema_version"),
        "created_at": metadata.get("created_at"),
        "path": str(pdir),
        "survey_count": len(surveys),
        "active": ROOT.exists() and head_path().exists() and head_path().read_text().strip() == project_id,
    }


def cmd_project_create(args: argparse.Namespace) -> dict[str, Any]:
    require_workspace()
    pdir = ensure_project(args.project_id, title=args.title)
    if args.use:
        head_path().write_text(f"{validate_project_id(args.project_id)}\n")
    return envelope(
        "zwill project create",
        "ok",
        {"project": project_metadata(args.project_id)},
        next_steps=[f"zwill project use {args.project_id}", "zwill survey create --name <survey>"] if not args.use else ["zwill survey create --name <survey>"],
    )


def cmd_project_use(args: argparse.Namespace) -> dict[str, Any]:
    require_workspace()
    project_id = validate_project_id(args.project_id)
    if not project_dir(project_id).exists():
        raise ZwillError("not_found", f"Project does not exist: {project_id}.", hint=f"Run `zwill project create {project_id}`.")
    head_path().write_text(f"{project_id}\n")
    return envelope("zwill project use", "ok", {"project": project_metadata(project_id)}, next_steps=["zwill status"])


def cmd_project_current(_: argparse.Namespace) -> dict[str, Any]:
    project_id = active_project_id()
    return envelope("zwill project current", "ok", {"project": project_metadata(project_id)})


def cmd_project_list(_: argparse.Namespace) -> dict[str, Any]:
    require_workspace()
    rows = []
    if projects_dir().exists():
        for pdir in sorted(projects_dir().iterdir()):
            if pdir.is_dir():
                rows.append(project_metadata(pdir.name))
    return envelope("zwill project list", "ok", {"projects": rows})


def cmd_project_show(args: argparse.Namespace) -> dict[str, Any]:
    project_id = validate_project_id(args.project_id or active_project_id())
    if not project_dir(project_id).exists():
        raise ZwillError("not_found", f"Project does not exist: {project_id}.")
    surveys = read_json(project_dir(project_id) / "surveys.json", [])
    data = project_metadata(project_id)
    data["surveys"] = surveys
    data["directories"] = {
        "surveys": str(project_dir(project_id) / "surveys"),
        "agent_studies": str(project_dir(project_id) / "agent_studies"),
        "practitioner_reports": str(project_dir(project_id) / "practitioner_reports"),
        "workflows": str(project_dir(project_id) / "workflows"),
    }
    return envelope("zwill project show", "ok", {"project": data})


def cmd_survey_create(args: argparse.Namespace) -> dict[str, Any]:
    require_project()
    sdir = survey_dir(args.name)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "raw").mkdir(exist_ok=True)
    for filename in [
        "questions.jsonl",
        "respondents.jsonl",
        "answers.jsonl",
        "agent_material.jsonl",
        "assertions.jsonl",
        "ingest_log.jsonl",
        "quarantine.jsonl",
    ]:
        (sdir / filename).touch(exist_ok=True)
    if not (sdir / "raw_files.json").exists():
        write_json(sdir / "raw_files.json", [])

    surveys = read_json(project_surveys_path(), [])
    existing = next((s for s in surveys if s["name"] == args.name), None)
    if existing is None:
        existing = {"name": args.name, "status": "draft", "created_at": utc_now()}
        surveys.append(existing)
        write_json(project_surveys_path(), surveys)
    return envelope(
        "zwill survey create",
        "ok",
        {"survey": existing},
        next_steps=[
            f"zwill raw add --survey {args.name} --id <id> --path <file-or-dir>",
            f"zwill question add --survey {args.name} ...",
        ],
    )


def cmd_survey_show(args: argparse.Namespace) -> dict[str, Any]:
    summary = survey_summary(args.name)
    summary.pop("committed", None)
    return envelope("zwill survey show", "ok", {"survey": summary})


def cmd_survey_report(args: argparse.Namespace) -> dict[str, Any] | None:
    sdir = require_survey(args.survey)
    payload = build_survey_report_payload(args.survey, sdir)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).write_text(output + "\n")
        else:
            print(output)
        return None
    if args.format == "html":
        output = render_survey_report_html(payload)
        if args.path:
            Path(args.path).write_text(output)
        else:
            print(output)
        return None
    if args.format == "csv":
        if not args.path:
            raise ZwillError("invalid_input", "--path is required for survey report --format csv.")
        paths = write_survey_report_csvs(payload, Path(args.path))
        return envelope("zwill survey report", "ok", {"survey": args.survey, **payload["summary"], **paths})
    table = Table(title=f"Survey Report: {args.survey}")
    for column in ["question", "type", "answers", "missing", "response", "options"]:
        table.add_column(column)
    for row in payload["questions"]:
        table.add_row(
            str(row["question_name"]),
            str(row.get("question_type")),
            str(row["answer_count"]),
            str(row["missing_count"]),
            f"{row['response_rate']:.1%}",
            str(row["option_count"]),
        )
    Console().print(table)
    return None


def report_catalog_entry(
    *,
    report_id: str,
    stage: str,
    name: str,
    purpose: str,
    ready: bool,
    inputs: str,
    available: str,
    command: str,
    path: str,
    notes: str = "",
    primary: bool = True,
) -> dict[str, Any]:
    return {
        "report_id": report_id,
        "stage": stage,
        "name": name,
        "purpose": purpose,
        "ready": bool(ready),
        "inputs": inputs,
        "available": available,
        "command": command,
        "suggested_path": path,
        "path_exists": Path(path).exists(),
        "notes": notes,
        "primary": primary,
        "role": "primary" if primary else "supporting",
    }


def build_report_catalog(survey: str) -> dict[str, Any]:
    sdir = require_survey(survey)
    questions = read_jsonl(sdir / "questions.jsonl")
    answers = read_jsonl(sdir / "answers.jsonl")
    respondents = read_jsonl(sdir / "respondents.jsonl")
    probability_rows = read_jsonl(probability_predictions_path(sdir))
    twin_rows = read_jsonl(digital_twin_predictions_path(sdir))
    twin_job_ids = sorted({str(row.get("job_id")) for row in twin_rows if row.get("job_id")})
    probability_job_ids = sorted({str(row.get("job_id")) for row in probability_rows if row.get("job_id")})
    twin_runs = read_twin_run_manifest(sdir)
    ordered_twin_job_ids = []
    for run in twin_runs:
        job_id = str(run.get("job_id"))
        if job_id in twin_job_ids and job_id not in ordered_twin_job_ids:
            ordered_twin_job_ids.append(job_id)
    for job_id in twin_job_ids:
        if job_id not in ordered_twin_job_ids:
            ordered_twin_job_ids.append(job_id)
    questions_by_twin_job: dict[str, set[str]] = defaultdict(set)
    rows_by_twin_job: Counter[str] = Counter()
    for row in twin_rows:
        job_id = str(row.get("job_id"))
        if not job_id:
            continue
        rows_by_twin_job[job_id] += 1
        if row.get("heldout_question"):
            questions_by_twin_job[job_id].add(str(row.get("heldout_question")))
    twin_experiments = read_twin_experiments(sdir)
    recorded_experiment_jobs = [str(row.get("job_id")) for row in twin_experiments if row.get("job_id")]
    practitioner_reports = list(practitioner_reports_dir().glob("*/report.html")) if practitioner_reports_dir().exists() else []
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", survey).strip("_") or "survey"
    bundle_command = f"zwill report build --survey {survey} --path {base}_report/"
    latest_twin_job = ordered_twin_job_ids[0] if ordered_twin_job_ids else "<job_id>"
    comparison_pair = ordered_twin_job_ids[:2]
    best_pair_score = (-1, -1, -1)
    for left_index, left in enumerate(ordered_twin_job_ids):
        for right_index, right in enumerate(ordered_twin_job_ids[left_index + 1 :], left_index + 1):
            overlap = len(questions_by_twin_job.get(left, set()) & questions_by_twin_job.get(right, set()))
            score = (overlap, min(rows_by_twin_job[left], rows_by_twin_job[right]), -(left_index + right_index))
            if score > best_pair_score:
                best_pair_score = score
                comparison_pair = [left, right]
    compare_jobs = ",".join(comparison_pair) if len(comparison_pair) >= 2 else "<job1>,<job2>"
    experiment_jobs = ",".join(recorded_experiment_jobs[:2]) if len(recorded_experiment_jobs) >= 2 else "<recorded_job1>,<recorded_job2>"
    executive_summary_path = Path("artifacts") / f"{base}_executive_summary.html"
    if not executive_summary_path.exists():
        executive_summary_path = Path(f"{base}_executive_summary.html")

    entries = [
        report_catalog_entry(
            report_id="survey-profile",
            stage="survey",
            name="Survey Profile Report",
            purpose="Question text, options, response distributions, and survey data-quality issues before any twin work.",
            ready=bool(questions),
            inputs="Survey questions and answers.",
            available=f"{len(questions)} questions, {len(answers)} answers, {len(respondents)} respondents",
            command=bundle_command,
            path=f"{base}_report/survey-profile.html",
        ),
        report_catalog_entry(
            report_id="probability-results",
            stage="one-shot",
            name="One-Shot Marginals Report",
            purpose="Frontier-model marginal predictions compared with committed empirical survey marginals. Export a frontier-model one-shot analysis job before treating the page as interpreted.",
            ready=bool(probability_rows),
            inputs="Imported probability-job results.",
            available=f"{len(probability_rows)} prediction rows across {len(probability_job_ids)} job ids",
            command=f"zwill prob-results analysis-export --survey {survey} --path {base}_report/one-shot-marginals.html",
            path=f"{base}_report/one-shot-marginals.html",
            notes="The report bundle renders diagnostics. The analysis section should be generated from compact one-shot summary statistics by a report-writing model.",
        ),
        report_catalog_entry(
            report_id="twin-run",
            stage="audit",
            name="Twin Run Report",
            purpose="Inspect one twin job's construction metadata, prompt template, rendered prompts, twin identity, and raw model response.",
            ready=bool(twin_job_ids),
            inputs="One imported digital twin result job.",
            available=f"{len(twin_job_ids)} twin job ids, {len(twin_runs)} run/import records",
            command=f"{bundle_command} --audit-job-id {latest_twin_job}",
            path=f"{base}_report/audit/twin-run-{latest_twin_job}.html",
            primary=False,
        ),
        report_catalog_entry(
            report_id="twin-validation",
            stage="validation",
            name="Twin Validation Report",
            purpose="Main twin validation page: held-out performance, generated interpretation, recommendation, deterministic diagnostics, lift distribution, individual predictive-power test, and rank-order evidence.",
            ready=bool(twin_rows),
            inputs="Imported digital twin predictions with observed held-out answers.",
            available=(str(executive_summary_path) if executive_summary_path.exists() else f"{len(twin_rows)} twin prediction rows available"),
            command=f"zwill twin-results executive-summary-export --survey {survey} --jobs {compare_jobs} --path {base}_report/executive-summary.html",
            path=f"{base}_report/twin-validation.html",
            notes="The report bundle folds deterministic diagnostics and generated executive interpretation into the main twin validation page.",
        ),
        report_catalog_entry(
            report_id="twin-job-comparison",
            stage="comparison",
            name="Twin Job Comparison Report",
            purpose="Side-by-side comparison of two or more imported twin jobs, including actual vs twin-implied marginals and option-level winners.",
            ready=len(twin_job_ids) >= 2,
            inputs="At least two imported digital twin result jobs.",
            available=f"{len(twin_job_ids)} twin job ids",
            command=f"{bundle_command} --jobs {compare_jobs}",
            path=f"{base}_report/twin-comparison.html",
            primary=False,
        ),
        report_catalog_entry(
            report_id="twin-experiment-microdata",
            stage="experiment",
            name="Twin Experiment Microdata Audit",
            purpose="Row-level audit of changes across recorded twin experiment approaches, with observed traits, material, prompts, and predictions.",
            ready=len(recorded_experiment_jobs) >= 2,
            inputs="At least two recorded twin experiments.",
            available=f"{len(twin_experiments)} recorded experiments",
            command=f"zwill twin-experiment microdata --survey {survey} --jobs {experiment_jobs} --path {base}_report/audit/twin-experiment-microdata.html",
            path=f"{base}_report/audit/twin-experiment-microdata.html",
            notes="Record approaches first with `zwill twin-experiment record` if this is not ready.",
            primary=False,
        ),
        report_catalog_entry(
            report_id="practitioner-narrative",
            stage="final",
            name="Practitioner Narrative Report",
            purpose="Model-authored final narrative report over a twin study or benchmark, using recorded artifacts as context.",
            ready=bool(twin_job_ids),
            inputs="One imported twin study job plus report-generation model access.",
            available=f"{len(practitioner_reports)} rendered practitioner report files currently under the project report store",
            command=f"zwill twin-study practitioner-report --survey {survey} --job-id {latest_twin_job} --path {base}_practitioner_report.html",
            path=f"{base}_practitioner_report.html",
        ),
    ]
    return {
        "survey": survey,
        "summary": {
            "questions": len(questions),
            "answers": len(answers),
            "respondents": len(respondents),
            "open_quarantine_issues": len(open_quarantine_issues(sdir)),
            "probability_prediction_rows": len(probability_rows),
            "probability_job_count": len(probability_job_ids),
            "twin_prediction_rows": len(twin_rows),
            "twin_job_count": len(twin_job_ids),
            "twin_experiment_count": len(twin_experiments),
            "ready_report_count": sum(1 for entry in entries if entry["ready"]),
        },
        "reports": entries,
    }


def cmd_report_list(args: argparse.Namespace) -> None:
    payload = build_report_catalog(args.survey)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    table = Table(title=f"{args.survey} report catalog")
    for column in ["stage", "report", "ready", "available", "suggested command"]:
        table.add_column(column)
    for entry in payload["reports"]:
        command = entry["command"]
        if len(command) > 96:
            command = command[:93] + "..."
        table.add_row(
            str(entry["stage"]),
            str(entry["name"]),
            "yes" if entry["ready"] else "no",
            str(entry["available"]),
            command,
        )
    Console().print(table)


def report_bundle_default_dir(survey: str) -> Path:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", survey).strip("_") or "survey"
    return Path(f"{base}_report")


def bundle_rel_link(path: str | Path, base: Path) -> str:
    return os.path.relpath(Path(path).resolve(), start=base.resolve()).replace(os.sep, "/")


def report_bundle_page(
    *,
    page_id: str,
    title: str,
    stage: str,
    status: str,
    description: str,
    path: Path | None = None,
    data_path: Path | None = None,
    inputs: str = "",
    next_step: str = "",
    notes: str = "",
    generated_files: list[Path] | None = None,
    primary: bool = True,
) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "title": title,
        "stage": stage,
        "status": status,
        "description": description,
        "path": str(path) if path else None,
        "data_path": str(data_path) if data_path else None,
        "inputs": inputs,
        "next_step": next_step,
        "notes": notes,
        "generated_files": [str(p) for p in (generated_files or [])],
        "primary": primary,
    }


def write_bundle_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def compact_twin_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact = dict(payload)
    rows = compact.pop("rows", [])
    compact["row_count"] = len(rows) if isinstance(rows, list) else 0
    compact["raw_prediction_rows_included"] = False
    return compact


def copy_bundle_file(source: Path, destination: Path) -> str | None:
    if not source.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(destination)


def copy_generated_report_provenance(generation: dict[str, Any] | None, output_dir: Path, *, prefix: str) -> list[Path]:
    if not generation or generation.get("mode") != "imported_results":
        return []
    report_id = str(generation.get("report_id") or "").strip()
    if not report_id:
        return []
    paths = default_practitioner_report_paths(report_id)
    destination_dir = output_dir / "analysis" / "generated-reports" / f"{prefix}-{report_id}"
    copied: list[Path] = []
    for name, source in [
        ("job.edsl.json", paths["job"]),
        ("prompt.md", paths["prompt"]),
        ("context.json", paths["context"]),
        ("import.json", paths["import"]),
        ("report.md", paths["markdown"]),
    ]:
        copied_path = copy_bundle_file(source, destination_dir / name)
        if copied_path:
            copied.append(Path(copied_path))
    return copied


def report_stage_status(*, ready: bool, label: str, files: list[str], missing: list[str] | None = None, next_step: str = "") -> dict[str, Any]:
    return {
        "status": "ready" if ready else "blocked",
        "label": label,
        "files": files,
        "missing": missing or [],
        "next_step": next_step,
    }


def render_report_bundle_checklist(stage_manifest: dict[str, Any]) -> str:
    survey = str(stage_manifest.get("survey") or "")
    lines = [
        f"# {survey} Report Bundle Checklist" if survey else "# Report Bundle Checklist",
        "",
        "This file is a read-only workflow view. `.zwill` remains the system of record for survey state, approvals, imports, generated report metadata, and result manifests.",
        "",
        "## Stages",
        "",
    ]
    for stage_id, stage in (stage_manifest.get("stages") or {}).items():
        status = str(stage.get("status") or "")
        label = str(stage.get("label") or stage_id)
        lines.append(f"- [{'x' if status == 'ready' else ' '}] {label} (`{stage_id}`): {status}")
        missing = stage.get("missing") or []
        if missing:
            lines.append(f"  - Missing: {', '.join(str(item) for item in missing)}")
        next_step = stage.get("next_step")
        if next_step:
            lines.append(f"  - Next: `{next_step}`")
    pages = stage_manifest.get("pages") or []
    if pages:
        lines.extend(["", "## Pages", ""])
        for page in pages:
            role = "primary" if page.get("primary", True) else "supporting"
            status = str(page.get("status") or "")
            title = str(page.get("title") or page.get("page_id") or "")
            path = page.get("path")
            lines.append(f"- [{'x' if status == 'ready' else ' '}] {title} ({role}): {status}")
            if path:
                lines.append(f"  - Path: `{path}`")
            if page.get("next_step"):
                lines.append(f"  - Next: `{page['next_step']}`")
    commands = stage_manifest.get("canonical_commands") or []
    if commands:
        lines.extend(["", "## Canonical Commands", ""])
        for command in commands:
            lines.append(f"- `{command}`")
    lines.append("")
    return "\n".join(lines)


def page_is_ready(manifest: dict[str, Any], page_id: str) -> bool:
    return any(page.get("page_id") == page_id and page.get("status") == "ready" for page in manifest.get("pages", []))


def imported_generation_ready(generation: dict[str, Any] | None) -> bool:
    return bool(generation) and generation.get("mode") == "imported_results"


def write_report_stage_artifacts(output_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    facts_dir = output_dir / "facts"
    analysis_dir = output_dir / "analysis"
    rendered_dir = output_dir / "report"
    data_dir = output_dir / "data"
    facts_files = [
        path
        for path in [
            copy_bundle_file(data_dir / "report-catalog.json", facts_dir / "report-catalog.json"),
            copy_bundle_file(data_dir / "survey-profile.json", facts_dir / "survey-profile.json"),
        ]
        if path
    ]
    analysis_files = []
    for name in [
        "one-shot-marginals.json",
        "one-shot-analysis.md",
        "one-shot-coverage.json",
        "twin-validation.json",
        "joint-structure.json",
        "subgroup-marginals.json",
        "conditional-consistency.json",
        "executive-summary.md",
        "validation-diagnostics.json",
        "twin-comparison.json",
    ]:
        copied = copy_bundle_file(data_dir / name, analysis_dir / name)
        if copied:
            analysis_files.append(copied)
    for path in sorted(output_dir.glob("executive-summary_*")):
        if path.is_file():
            copied = copy_bundle_file(path, analysis_dir / path.name)
            if copied:
                analysis_files.append(copied)
    report_files = []
    for path in sorted(output_dir.glob("*.html")):
        copied = copy_bundle_file(path, rendered_dir / path.name)
        if copied:
            report_files.append(copied)
    for page in manifest.get("pages", []):
        for raw_path in page.get("generated_files", []):
            source = Path(str(raw_path))
            if not source.is_absolute():
                source = output_dir / source
            try:
                relative = source.relative_to(output_dir)
            except ValueError:
                continue
            if not source.exists() or source.is_dir() or relative.parts[:1] == ("report",):
                continue
            if source.suffix.lower() == ".html":
                continue
            copied = copy_bundle_file(source, rendered_dir / relative)
            if copied:
                report_files.append(copied)
    for path in sorted((output_dir / "audit").glob("*.html")) if (output_dir / "audit").exists() else []:
        copied = copy_bundle_file(path, rendered_dir / "audit" / path.name)
        if copied:
            report_files.append(copied)
    survey = str(manifest.get("survey", ""))
    one_shot_page_ready = page_is_ready(manifest, "one-shot-marginals")
    twin_pages_ready = page_is_ready(manifest, "twin-validation")
    one_shot_generation = manifest.get("one_shot_analysis_generation") or {}
    executive_generation = (manifest.get("executive_summary") or {}).get("generation") or {}
    one_shot_generated_ready = imported_generation_ready(one_shot_generation)
    executive_generated_ready = imported_generation_ready(executive_generation)
    one_shot_generated_file = analysis_dir / "one-shot-analysis.md"
    executive_generated_file = analysis_dir / "executive-summary.md"
    one_shot_generated_next = (
        f"zwill prob-results analysis-export --survey {survey} --path {output_dir / 'one-shot-marginals.html'}"
        if survey
        else "Run `zwill prob-results analysis-export`, then run/import/render the report-writing job."
    )
    executive_generated_next = (
        f"zwill twin-results executive-summary-export --survey {survey} --path {output_dir / 'executive-summary.html'}"
        if survey
        else "Run `zwill twin-results executive-summary-export`, then run/import/render the report-writing job."
    )
    required_generated = []
    if one_shot_page_ready:
        required_generated.append(
            {
                "id": "one-shot-analysis",
                "label": "Generated One-Shot Analysis",
                "ready": one_shot_generated_ready,
                "file": str(one_shot_generated_file),
                "missing": "frontier-model one-shot marginal analysis Markdown",
                "next_step": "" if one_shot_generated_ready else one_shot_generated_next,
            }
        )
    if twin_pages_ready:
        required_generated.append(
            {
                "id": "executive-twin-validation",
                "label": "Generated Executive Twin Validation",
                "ready": executive_generated_ready,
                "file": str(executive_generated_file),
                "missing": "frontier-model executive twin validation Markdown",
                "next_step": "" if executive_generated_ready else executive_generated_next,
            }
        )
    missing_generated = [item["missing"] for item in required_generated if not item["ready"]]
    generated_files = [
        item["file"]
        for item in required_generated
        if item["ready"] and Path(item["file"]).exists()
    ]
    generated_analysis_ready = not missing_generated
    first_generated_next = next((item["next_step"] for item in required_generated if not item["ready"] and item.get("next_step")), "")
    stages = {
        "facts": report_stage_status(
            ready=bool(facts_files),
            label="Facts",
            files=facts_files,
            missing=[] if facts_files else ["survey-profile facts"],
            next_step="Review facts/survey-profile.json and survey-profile.html.",
        ),
        "analysis": report_stage_status(
            ready=bool(analysis_files),
            label="Deterministic Analysis",
            files=analysis_files,
            missing=[] if analysis_files else ["deterministic diagnostics"],
            next_step="Import one-shot or twin results, then rerun `zwill report analyze`.",
        ),
        "generated_analysis": report_stage_status(
            ready=generated_analysis_ready,
            label="Generated Interpretations",
            files=generated_files if generated_analysis_ready else generated_files,
            missing=missing_generated,
            next_step="" if generated_analysis_ready else first_generated_next,
        ),
        "report_preview": report_stage_status(
            ready=bool(report_files),
            label="Report Preview",
            files=report_files,
            missing=[] if report_files else ["rendered report HTML"],
            next_step="Open report/index.html or index.html.",
        ),
        "final_report": report_stage_status(
            ready=bool(report_files) and generated_analysis_ready,
            label="Final Report",
            files=report_files if generated_analysis_ready else [],
            missing=missing_generated,
            next_step="Ready." if generated_analysis_ready else first_generated_next,
        ),
    }
    page_views = [
        {
            "page_id": page.get("page_id"),
            "title": page.get("title"),
            "stage": page.get("stage"),
            "status": page.get("status"),
            "primary": page.get("primary", True),
            "path": page.get("path"),
            "data_path": page.get("data_path"),
            "next_step": page.get("next_step"),
        }
        for page in manifest.get("pages", [])
    ]
    canonical_commands = []
    if survey:
        canonical_commands.extend(
            [
                f"zwill report list --survey {survey}",
                f"zwill report build --survey {survey} --path {output_dir}",
                f"zwill report render --survey {survey} --path {output_dir} --final",
            ]
        )
    stage_manifest = {
        "survey": manifest.get("survey"),
        "generated_at": manifest.get("generated_at"),
        "output_dir": str(output_dir),
        "facts_dir": str(facts_dir),
        "analysis_dir": str(analysis_dir),
        "report_dir": str(rendered_dir),
        "report_catalog_path": str(data_dir / "report-catalog.json"),
        "report_manifest_path": str(output_dir / "report-manifest.json"),
        "checklist_path": str(output_dir / "CHECKLIST.md"),
        "stages": stages,
        "pages": page_views,
        "canonical_commands": canonical_commands,
        "required_generated_interpretations": required_generated,
    }
    checklist_markdown = render_report_bundle_checklist(stage_manifest)
    (output_dir / "CHECKLIST.md").write_text(checklist_markdown)
    (rendered_dir / "CHECKLIST.md").parent.mkdir(parents=True, exist_ok=True)
    (rendered_dir / "CHECKLIST.md").write_text(checklist_markdown)
    write_bundle_json(output_dir / "stage-manifest.json", stage_manifest)
    write_bundle_json(facts_dir / "facts-manifest.json", stage_manifest)
    write_bundle_json(analysis_dir / "analysis-manifest.json", stage_manifest)
    return stage_manifest


def render_report_bundle_index(payload: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        import html

        return html.escape(str(value), quote=True)

    output_dir = Path(payload["output_dir"])
    generated_at = esc(payload.get("generated_at", ""))
    survey_id = str(payload.get("survey", ""))
    survey_title, _raw_title = report_display_title(survey_id)
    survey = esc(survey_title)
    summary = payload.get("summary", {})
    executive = payload.get("executive_summary") or {}
    executive_block = ""
    if executive:
        metrics = executive.get("metrics") or {}
        lift = executive.get("lift") or {}
        individual = executive.get("individual_signal") or {}
        pairwise = executive.get("pairwise_ordering") or {}
        executive_page = next((page for page in payload.get("pages", []) if page.get("page_id") == "executive-summary"), {})
        executive_href = bundle_rel_link(executive_page.get("path"), output_dir) if executive_page.get("path") else ""
        validation_page = next((page for page in payload.get("pages", []) if page.get("page_id") == "twin-validation"), {})
        validation_href = bundle_rel_link(validation_page.get("path"), output_dir) if validation_page.get("path") else ""
        executive_block = f"""
    <section class="decision-summary">
      <div class="stage">Executive Summary</div>
      <h2>Digital Twin Validation Readout</h2>
      <p>This bundle has held-out validation results. Use twins for exploratory and directional work when the validation target family matches the intended use; use fresh validation for exact estimates or respondent-level action.</p>
      <table>
        <tbody>
          <tr><th>Validation rows</th><td class="num">{esc(int(metrics.get("row_count", 0)))}</td><th>Held-out questions</th><td class="num">{esc(int(metrics.get("question_count", 0)))}</td></tr>
          <tr><th>Mean p(actual)</th><td class="num">{float(metrics.get("mean_probability_actual", 0.0)):.1%}</td><th>Uniform p(actual)</th><td class="num">{float(metrics.get("mean_uniform_probability_actual", 0.0)):.1%}</td></tr>
          <tr><th>Rows above uniform</th><td class="num">{float(lift.get("share_above_1", 0.0)):.0%}</td><th>Mean lift vs uniform</th><td class="num">{float(lift.get("mean_lift", 0.0)):.2f}x</td></tr>
          <tr><th>Individual-signal p-value</th><td class="num">{float(individual.get("p_value_mean_p_actual", 0.0)):.5f}</td><th>Option-pair ordering accuracy</th><td class="num">{float((pairwise.get("summary") or {}).get("pairwise_order_accuracy", 0.0)):.0%}</td></tr>
        </tbody>
      </table>
      <p>{f'<a class="button" href="{esc(executive_href)}">Open full executive summary</a>' if executive_href else ''} {f'<a class="button secondary" href="{esc(validation_href)}">Open technical validation</a>' if validation_href else ''}</p>
    </section>"""
    primary_pages = [page for page in payload.get("pages", []) if page.get("primary", True)]
    secondary_pages = [page for page in payload.get("pages", []) if not page.get("primary", True)]
    items = []
    for index, page in enumerate(primary_pages, 1):
        status = str(page.get("status", "not_ready"))
        status_label = status.replace("_", " ").title()
        path = page.get("path")
        href = bundle_rel_link(path, output_dir) if path else ""
        title = esc(page.get("title", page.get("page_id", "")))
        description = esc(page.get("description", ""))
        stage = esc(page.get("stage", ""))
        inputs = esc(page.get("inputs", ""))
        next_step = esc(page.get("next_step", ""))
        notes = esc(page.get("notes", ""))
        link = f'<a class="button" href="{esc(href)}">Open</a>' if path else ""
        items.append(
            f"""
      <li class="step {esc(status)}">
        <div class="step-number">{index}</div>
        <div class="step-body">
          <div class="step-head">
          <div>
            <div class="stage">{stage}</div>
            <h2>{title}</h2>
          </div>
          <span class="status">{esc(status_label)}</span>
        </div>
        <p>{description}</p>
        <dl>
          <dt>Inputs</dt><dd>{inputs or "Available survey state"}</dd>
          <dt>Next</dt><dd>{next_step or ("Open the page." if path else "No action needed.")}</dd>
        </dl>
        {f'<p class="notes">{notes}</p>' if notes else ''}
        {link}
        </div>
      </li>"""
        )
    secondary_items = []
    for page in secondary_pages:
        status = str(page.get("status", "not_ready"))
        path = page.get("path")
        href = bundle_rel_link(path, output_dir) if path else ""
        secondary_items.append(
            f"""
      <li class="{esc(status)}">
        <div>
          <b>{esc(page.get("title", page.get("page_id", "")))}</b>
          <span>{esc(page.get("description", ""))}</span>
        </div>
        {f'<a class="button secondary" href="{esc(href)}">Open</a>' if path else f'<span class="status">{esc(status.replace("_", " ").title())}</span>'}
      </li>"""
        )
    secondary_block = ""
    if secondary_items:
        secondary_block = f"""
    <section class="panel secondary-reports">
      <div class="stage">Supporting Artifacts</div>
      <h2>Linked From The Main Reports</h2>
      <p>These are generated for auditability and comparisons, but they are not separate top-level reports.</p>
      <ul>{''.join(secondary_items)}</ul>
    </section>"""
    stale_items = [f"<li>{esc(bundle_rel_link(path, output_dir))}</li>" for path in payload.get("stale_files", [])]
    stale_block = ""
    if stale_items:
        stale_block = f"""
      <section class="panel">
        <h2>Stale Generated Files</h2>
        <p>These files were generated by an earlier bundle build but were not regenerated in this run. They were left in place.</p>
        <ul>{''.join(stale_items)}</ul>
      </section>"""
    data = escape_script_text(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{survey} Report Bundle</title>
  <style>
    {EP_REPORT_CSS}
    header {{ margin-bottom: 1.5rem; }}
    main {{ padding-bottom: 2rem; }}
    .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:18px; }}
    .stat {{ border:1px solid var(--ep-border); border-radius:8px; padding:12px; background:var(--ep-light-gray); }}
    .stat strong {{ display:block; font-size:22px; }}
    .workflow {{ list-style:none; padding:0; margin:1.5rem 0; display:grid; gap:12px; }}
    .step,.panel,.decision-summary {{ background:#fff; border:1px solid var(--ep-border); border-radius:8px; padding:16px; }}
    .decision-summary {{ border-left:4px solid var(--ep-green); margin-bottom:1.5rem; }}
    .decision-summary h2 {{ margin-top:0; }}
    .step {{ display:grid; grid-template-columns:34px 1fr; gap:12px; }}
    .step-number {{ width:28px; height:28px; border-radius:999px; background:var(--ep-green); display:flex; align-items:center; justify-content:center; font-weight:700; color:#fff; }}
    .step-head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }}
    .step h2 {{ margin:0; border-bottom:0; padding-bottom:0; color:var(--ep-dark); font-size:1.35rem; }}
    .stage {{ color:var(--ep-gray); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }}
    .status {{ border:1px solid var(--ep-border); border-radius:999px; padding:3px 8px; font-size:12px; white-space:nowrap; }}
    .ready .status {{ color:#12643b; border-color:#9ac6ad; background:#edf8f1; }}
    .not_ready .status {{ color:#8a5a00; border-color:#dbc17f; background:#fff8e6; }}
    .stale .status {{ color:#7a322b; border-color:#dda39e; background:#fff0ef; }}
    dl {{ display:grid; grid-template-columns:58px 1fr; gap:4px 10px; margin:12px 0; }}
    dt {{ color:var(--ep-gray); }}
    dd {{ margin:0; }}
    .button {{ display:inline-block; border:1px solid var(--ep-green); color:var(--ep-green); background:#fff; border-radius:6px; padding:6px 10px; text-decoration:none; }}
    .button.secondary {{ border-color:var(--ep-border); color:var(--ep-dark); }}
    .notes {{ color:var(--ep-gray); font-size:13px; }}
    .secondary-reports ul {{ list-style:none; margin:12px 0 0; display:grid; gap:10px; }}
    .secondary-reports li {{ border:1px solid var(--ep-border); border-radius:8px; padding:12px; display:flex; justify-content:space-between; gap:14px; align-items:center; }}
    .secondary-reports span {{ display:block; color:var(--ep-gray); }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    @media (max-width: 760px) {{ .step-head {{ display:block; }} .status {{ display:inline-block; margin-top:8px; }} }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <header>
    <h1>{survey} Report Bundle</h1>
    <div class="subtle">Survey id: <code>{esc(survey_id)}</code></div>
    <div class="subtle">Generated {generated_at}. Open this page first; it links to each report page that is ready.</div>
    <div class="stats">
      <div class="stat"><span>Questions</span><strong>{esc(summary.get("questions", 0))}</strong></div>
      <div class="stat"><span>Respondents</span><strong>{esc(summary.get("respondents", 0))}</strong></div>
      <div class="stat"><span>Open quarantine</span><strong>{esc(summary.get("open_quarantine_issues", 0))}</strong></div>
      <div class="stat"><span>One-shot rows</span><strong>{esc(summary.get("probability_prediction_rows", 0))}</strong></div>
      <div class="stat"><span>Twin rows</span><strong>{esc(summary.get("twin_prediction_rows", 0))}</strong></div>
    </div>
  </header>
  <main>
    {executive_block}
    <ol class="workflow">
      {''.join(items)}
    </ol>
    {secondary_block}
    {stale_block}
  </main>
  <script type="application/json" id="report-bundle-data">{data}</script>
</body>
</html>
"""


def build_report_bundle(args: argparse.Namespace) -> dict[str, Any]:
    survey = args.survey
    sdir = require_survey(survey)
    output_dir = Path(args.path) if args.path else report_bundle_default_dir(survey)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    audit_dir = output_dir / "audit"
    manifest_path = output_dir / "report-manifest.json"
    previous_manifest = read_json(manifest_path, {})
    previous_files = set()
    for page in previous_manifest.get("pages", []):
        for raw_path in page.get("generated_files", []):
            previous_path = Path(str(raw_path))
            if not previous_path.is_absolute():
                previous_path = output_dir / previous_path
            previous_files.add(str(previous_path))

    catalog = build_report_catalog(survey)
    catalog_path = data_dir / "report-catalog.json"
    write_bundle_json(catalog_path, catalog)
    pages: list[dict[str, Any]] = []
    generated: set[str] = {str(catalog_path)}
    one_shot_analysis_generation: dict[str, Any] | None = None

    def add_page(page: dict[str, Any]) -> None:
        pages.append(page)
        for path in page.get("generated_files", []):
            generated.add(str(path))

    survey_payload = build_survey_report_payload(survey, sdir)
    survey_html_path = output_dir / "survey-profile.html"
    survey_data_path = data_dir / "survey-profile.json"
    survey_html = render_survey_report_html(survey_payload).replace("Survey Report", "Survey Profile")
    survey_html_path.write_text(survey_html)
    write_bundle_json(survey_data_path, survey_payload)
    add_page(
        report_bundle_page(
            page_id="survey-profile",
            title="Survey Profile",
            stage="survey",
            status="ready",
            description="Question text, response options, observed distributions, missingness, respondent counts, and data-quality issues.",
            path=survey_html_path,
            data_path=survey_data_path,
            inputs=f"{catalog['summary']['questions']} questions, {catalog['summary']['answers']} answers",
            next_step="Review this page before running model-based analyses.",
            generated_files=[survey_html_path, survey_data_path],
        )
    )

    truth_path = sdir / "committed" / "truth_marginals.json"
    probability_rows = read_jsonl(probability_predictions_path(sdir))
    if getattr(args, "probability_job_id", None):
        probability_rows = [row for row in probability_rows if row.get("job_id") == args.probability_job_id]
    if getattr(args, "probability_model", None):
        probability_rows = [
            row
            for row in probability_rows
            if row.get("model") == args.probability_model or row.get("model_label") == args.probability_model
        ]
    if probability_rows and truth_path.exists():
        probability_payload = build_probability_report(probability_rows, read_json(truth_path, {}))
        probability_html_path = output_dir / "one-shot-marginals.html"
        probability_data_path = data_dir / "one-shot-marginals.json"
        probability_markdown_path = data_dir / "one-shot-analysis.md"
        generated_one_shot = find_imported_one_shot_analysis_report(
            survey=survey,
            job_id=getattr(args, "probability_job_id", None),
            model=getattr(args, "probability_model", None),
            questions=sorted({str(row.get("question")) for row in probability_payload["rows"] if row.get("question")}),
        )
        if generated_one_shot:
            one_shot_analysis_generation = generated_one_shot.get("generation") or {}
            probability_markdown_path.write_text(generated_one_shot["markdown"].strip() + "\n")
        coverage_payload = build_probability_coverage_payload(sdir, probability_rows)
        coverage_html_path = output_dir / "one-shot-coverage.html"
        coverage_data_path = data_dir / "one-shot-coverage.json"
        probability_html = render_probability_report_html(
            survey,
            probability_payload["rows"],
            probability_payload["summary"],
            generated_analysis_markdown=generated_one_shot.get("markdown") if generated_one_shot else None,
            generation=generated_one_shot.get("generation") if generated_one_shot else None,
        )
        probability_html = insert_before_main_close(probability_html, render_probability_coverage_section(coverage_payload))
        probability_html_path.write_text(probability_html)
        write_bundle_json(probability_data_path, probability_payload)
        coverage_html_path.write_text(render_probability_coverage_html(coverage_payload))
        write_bundle_json(coverage_data_path, coverage_payload)
        generated_files = [probability_html_path, probability_data_path, coverage_html_path, coverage_data_path]
        if generated_one_shot:
            generated_files.append(probability_markdown_path)
            generated_files.extend(
                copy_generated_report_provenance(
                    generated_one_shot.get("generation"),
                    output_dir,
                    prefix="one-shot",
                )
            )
        add_page(
            report_bundle_page(
                page_id="one-shot-marginals",
                title="One-Shot Marginals",
                stage="one-shot",
                status="ready",
                description="Frontier-model aggregate marginal predictions compared with committed empirical survey marginals.",
                path=probability_html_path,
                data_path=probability_data_path,
                inputs=f"{len(probability_rows)} imported probability prediction rows",
                next_step=(
                    "Use this as an aggregate baseline before individual twin validation."
                    if generated_one_shot
                    else f"Run `zwill prob-results analysis-export --survey {survey} --path {probability_html_path}`, then run/import/render the report-writing job."
                ),
                notes="Generated one-shot analysis imported." if generated_one_shot else "Generated one-shot analysis has not been imported yet.",
                generated_files=generated_files,
            )
        )
    else:
        missing = "Imported probability-job results"
        if probability_rows and not truth_path.exists():
            missing = "Committed truth marginals"
        add_page(
            report_bundle_page(
                page_id="one-shot-marginals",
                title="One-Shot Marginals",
                stage="one-shot",
                status="not_ready",
                description="Frontier-model aggregate marginal predictions compared with empirical survey marginals.",
                inputs=missing,
                next_step=f"Run/import one-shot probability results, then rerun `zwill report build --survey {survey} --path {output_dir}`.",
            )
        )

    twin_rows = read_jsonl(digital_twin_predictions_path(sdir))
    selected_job_ids = selected_twin_result_job_ids(args)
    if selected_job_ids:
        selected_job_set = set(selected_job_ids)
        twin_rows = [row for row in twin_rows if row.get("job_id") in selected_job_set]
    if getattr(args, "model", None):
        twin_rows = [row for row in twin_rows if row.get("model") == args.model or row.get("model_label") == args.model]
    twin_job_ids = sorted({str(row.get("job_id")) for row in twin_rows if row.get("job_id")})
    if twin_rows:
        executive_result = {}
        twin_payload = build_twin_report(twin_rows)
        attach_twin_set_descriptions(sdir, twin_payload, twin_rows)
        twin_payload["health"] = {"job_ids": twin_job_ids}
        twin_html_path = output_dir / "twin-validation.html"
        twin_data_path = data_dir / "twin-validation.json"

        executive_path = output_dir / "executive-summary.html"
        executive_markdown_path = data_dir / "executive-summary.md"
        heldout_questions_for_report = sorted({str(row.get("heldout_question")) for row in twin_rows if row.get("heldout_question")})
        generated_executive = find_imported_executive_summary_report(
            survey=survey,
            job_ids=twin_job_ids,
            model=getattr(args, "model", None),
            questions=heldout_questions_for_report,
        )
        executive_result = build_executive_summary(
            twin_rows,
            survey=survey,
            path=executive_path,
            markdown_path=executive_markdown_path,
            simulations=getattr(args, "permutations", DEFAULT_REPORT_PERMUTATIONS),
            seed=getattr(args, "seed", 20260701),
            generated_markdown=generated_executive.get("markdown") if generated_executive else None,
            generation=generated_executive.get("generation") if generated_executive else None,
        )
        executive_generated = [Path(path) for path in executive_result.get("artifacts", {}).values()]
        executive_generated.extend([executive_path, executive_markdown_path])
        executive_generated.extend(
            copy_generated_report_provenance(
                generated_executive.get("generation") if generated_executive else None,
                output_dir,
                prefix="twin-executive",
            )
        )
        diagnostics_html_path = output_dir / "validation-diagnostics.html"
        diagnostics_data_path = data_dir / "validation-diagnostics.json"
        diagnostics_payload = {"survey": survey, "artifacts": executive_result.get("artifacts", {})}
        diagnostics_html_path.write_text(
            render_validation_diagnostics_html(
                survey=survey,
                artifacts=executive_result.get("artifacts", {}),
                output_dir=output_dir,
            )
        )
        write_bundle_json(diagnostics_data_path, diagnostics_payload)
        joint_structure_path = data_dir / "joint-structure.json"
        subgroup_marginals_path = data_dir / "subgroup-marginals.json"
        conditional_consistency_path = data_dir / "conditional-consistency.json"
        write_bundle_json(joint_structure_path, twin_payload.get("diagnostics", {}).get("joint_structure", {}))
        write_bundle_json(subgroup_marginals_path, twin_payload.get("diagnostics", {}).get("subgroup_marginals", {}))
        write_bundle_json(conditional_consistency_path, twin_payload.get("diagnostics", {}).get("conditional_consistency", {}))
        twin_html = render_twin_summary_report_html(
            survey,
            twin_payload["rows"],
            twin_payload["summary"],
            twin_payload.get("diagnostics"),
            twin_payload.get("health"),
        )
        twin_html = insert_after_main_open(twin_html, render_generated_executive_interpretation_section(generated_executive))
        twin_html = insert_before_main_close(
            twin_html,
            render_validation_diagnostics_section(
                survey=survey,
                artifacts=executive_result.get("artifacts", {}),
                output_dir=output_dir,
            ),
        )
        twin_html = insert_before_main_close(
            twin_html,
            render_twin_value_diagnostics_section(twin_payload.get("diagnostics", {})),
        )
        twin_html_path.write_text(twin_html)
        write_bundle_json(twin_data_path, compact_twin_report_payload(twin_payload))
        twin_generated_files = [
            twin_html_path,
            twin_data_path,
            diagnostics_html_path,
            diagnostics_data_path,
            joint_structure_path,
            subgroup_marginals_path,
            conditional_consistency_path,
            *executive_generated,
        ]
        add_page(
            report_bundle_page(
                page_id="twin-validation",
                title="Twin Validation",
                stage="validation",
                status="ready",
                description="Held-out twin performance, generated interpretation, uniform baseline comparisons, calibration diagnostics, marginal fit, and supporting validation diagnostics.",
                path=twin_html_path,
                data_path=twin_data_path,
                inputs=f"{len(twin_rows)} twin prediction rows across {len(twin_job_ids)} job ids",
                next_step=(
                    "Use this as the main technical validation and decision-readout page."
                    if generated_executive
                    else f"Run `zwill twin-results executive-summary-export --survey {survey} --jobs {','.join(twin_job_ids)} --path {executive_path}`, then run/import/render the report-writing job."
                ),
                notes="Generated twin validation interpretation imported." if generated_executive else "Generated twin validation interpretation has not been imported yet.",
                generated_files=twin_generated_files,
            )
        )

        audit_job_id = getattr(args, "audit_job_id", None) or (selected_job_ids[0] if selected_job_ids else twin_job_ids[0])
        try:
            audit_payload = build_twin_run_report_payload(sdir, survey, audit_job_id, example_limit=getattr(args, "example_limit", 6))
            audit_html_path = audit_dir / f"twin-run-{audit_job_id}.html"
            audit_data_path = data_dir / f"twin-run-{audit_job_id}.json"
            audit_html_path.parent.mkdir(parents=True, exist_ok=True)
            audit_html_path.write_text(render_twin_run_report_html(audit_payload))
            write_bundle_json(audit_data_path, audit_payload)
            add_page(
                report_bundle_page(
                    page_id="twin-run-audit",
                    title="Twin Run Audit",
                    stage="audit",
                    status="ready",
                    description="Construction metadata, held-out questions, prompt templates, rendered prompt examples, twin identity, and raw model responses.",
                    path=audit_html_path,
                    data_path=audit_data_path,
                    inputs=f"Imported twin job {audit_job_id}",
                    next_step="Use this to inspect leakage and prompt construction details.",
                    generated_files=[audit_html_path, audit_data_path],
                    primary=False,
                )
            )
        except ZwillError as exc:
            add_page(
                report_bundle_page(
                    page_id="twin-run-audit",
                    title="Twin Run Audit",
                    stage="audit",
                    status="not_ready",
                    description="Construction metadata, prompt templates, rendered prompt examples, and raw model responses.",
                    inputs=f"Twin run metadata for {audit_job_id}",
                    next_step="Import or rerun the twin job with stored raw Results, then rebuild the report bundle.",
                    notes=exc.message,
                    primary=False,
                )
            )

        if len(twin_job_ids) >= 2:
            comparison_payload = build_twin_job_comparison_report_payload(sdir, survey, twin_job_ids, model=getattr(args, "model", None))
            comparison_html_path = output_dir / "twin-comparison.html"
            comparison_data_path = data_dir / "twin-comparison.json"
            comparison_html_path.write_text(render_twin_job_comparison_report_html(comparison_payload))
            write_bundle_json(comparison_data_path, comparison_payload)
            add_page(
                report_bundle_page(
                    page_id="twin-comparison",
                    title="Twin Comparison",
                    stage="comparison",
                    status="ready",
                    description="Side-by-side comparison of twin jobs, including actual versus twin-implied marginals and option-level winners.",
                    path=comparison_html_path,
                    data_path=comparison_data_path,
                    inputs=f"{len(twin_job_ids)} imported twin job ids",
                    next_step="Use this when choosing between construction approaches.",
                    generated_files=[comparison_html_path, comparison_data_path],
                    primary=False,
                )
            )
        else:
            add_page(
                report_bundle_page(
                    page_id="twin-comparison",
                    title="Twin Comparison",
                    stage="comparison",
                    status="not_ready",
                    description="Side-by-side comparison of two or more twin jobs.",
                    inputs="At least two imported twin jobs",
                    next_step="Import another twin job or record another approach, then rebuild the report bundle.",
                    primary=False,
                )
            )
        supporting_section = render_twin_supporting_artifacts_section(pages, output_dir)
        if supporting_section and twin_html_path.exists():
            twin_html_path.write_text(insert_before_main_close(twin_html_path.read_text(), supporting_section))
    else:
        for page_id, title, stage, description in [
            ("twin-validation", "Twin Validation", "validation", "Held-out twin performance, calibration diagnostics, and marginal fit."),
            ("twin-run-audit", "Twin Run Audit", "audit", "Prompt, construction, and raw-response audit for one twin job."),
            ("twin-comparison", "Twin Comparison", "comparison", "Side-by-side comparison of two or more twin jobs."),
        ]:
            add_page(
                report_bundle_page(
                    page_id=page_id,
                    title=title,
                    stage=stage,
                    status="not_ready",
                    description=description,
                    inputs="Imported digital twin predictions",
                    next_step=f"Run/import twin results, then rerun `zwill report build --survey {survey} --path {output_dir}`.",
                    primary=page_id == "twin-validation",
                )
            )

    manifest_generated = set(generated)
    stale_files = sorted(path for path in previous_files - manifest_generated if Path(path).exists())
    bundle_summary = {
        **catalog["summary"],
        "ready_report_count": sum(1 for page in pages if page.get("status") == "ready"),
        "top_level_page_count": sum(1 for page in pages if page.get("primary", True)),
        "secondary_page_count": sum(1 for page in pages if not page.get("primary", True)),
    }
    manifest = {
        "survey": survey,
        "generated_at": utc_now(),
        "output_dir": str(output_dir),
        "index_path": str(output_dir / "index.html"),
        "summary": bundle_summary,
        "pages": pages,
        "one_shot_analysis_generation": one_shot_analysis_generation,
        "executive_summary": executive_result if twin_rows else None,
        "stale_files": stale_files,
        "generated_files": sorted(manifest_generated | {str(output_dir / "index.html"), str(manifest_path)}),
    }
    stage_manifest = write_report_stage_artifacts(output_dir, manifest)
    manifest["stages"] = stage_manifest
    (output_dir / "index.html").write_text(render_report_bundle_index(manifest))
    write_bundle_json(manifest_path, manifest)
    copied_index = copy_bundle_file(output_dir / "index.html", output_dir / "report" / "index.html")
    if copied_index:
        if copied_index not in stage_manifest["stages"]["report_preview"]["files"]:
            stage_manifest["stages"]["report_preview"]["files"].append(copied_index)
        if stage_manifest["stages"]["final_report"]["status"] == "ready" and copied_index not in stage_manifest["stages"]["final_report"]["files"]:
            stage_manifest["stages"]["final_report"]["files"].append(copied_index)
        manifest["stages"] = stage_manifest
        write_bundle_json(output_dir / "stage-manifest.json", stage_manifest)
        write_bundle_json(output_dir / "facts" / "facts-manifest.json", stage_manifest)
        write_bundle_json(output_dir / "analysis" / "analysis-manifest.json", stage_manifest)
        write_bundle_json(manifest_path, manifest)
    return manifest


def cmd_report_build(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_report_bundle(args)
    ready_count = sum(1 for page in manifest["pages"] if page["status"] == "ready")
    return envelope(
        "zwill report build",
        "ok",
        {
            "survey": args.survey,
            "path": manifest["output_dir"],
            "index_path": manifest["index_path"],
            "ready_pages": ready_count,
            "page_count": len(manifest["pages"]),
            "stale_file_count": len(manifest.get("stale_files", [])),
        },
        next_steps=[f"open {manifest['index_path']}"],
    )


def report_stage_envelope(command: str, manifest: dict[str, Any], stage_name: str) -> dict[str, Any]:
    stages = manifest.get("stages", {}).get("stages", {})
    stage = stages.get(stage_name, {})
    return envelope(
        command,
        "ok",
        {
            "survey": manifest.get("survey"),
            "path": manifest["output_dir"],
            "stage": stage_name,
            "stage_status": stage.get("status"),
            "stage_detail": stage,
            "stage_manifest": str(Path(manifest["output_dir"]) / "stage-manifest.json"),
        },
        next_steps=[stage.get("next_step") or f"open {manifest['index_path']}"],
    )


def find_imported_executive_summary_report(
    *,
    survey: str,
    job_ids: list[str],
    model: str | None,
    questions: list[str],
) -> dict[str, Any] | None:
    reports_root = practitioner_reports_dir()
    if not reports_root.exists():
        return None
    expected_jobs = set(job_ids)
    expected_questions = set(questions)
    candidates: list[dict[str, Any]] = []
    for context_path_candidate in sorted(reports_root.glob("*/context.json")):
        report_dir = context_path_candidate.parent
        markdown_path = report_dir / "report.md"
        import_path = report_dir / "import.json"
        if not markdown_path.exists() or not import_path.exists():
            continue
        context = read_json(context_path_candidate, {})
        report_context = context.get("executive_report_context", {})
        if report_context.get("report_kind") != "frontier_generated_executive_twin_validation":
            continue
        if report_context.get("survey") != survey:
            continue
        filters = report_context.get("filters", {})
        context_jobs = set(str(job) for job in filters.get("job_ids", []) if job)
        context_questions = set(str(question) for question in filters.get("questions", []) if question)
        context_model = filters.get("model")
        if expected_jobs and context_jobs != expected_jobs:
            continue
        if expected_questions and context_questions != expected_questions:
            continue
        if (model or None) != (context_model or None):
            continue
        imported = read_json(import_path, {})
        generation = {
            **context.get("generation", {}),
            "mode": "imported_results",
            "report_id": context.get("report_id") or report_dir.name,
            "context_path": str(context_path_candidate),
            "markdown_path": str(markdown_path),
            "import_path": str(import_path),
            "imported_at": imported.get("imported_at"),
        }
        candidates.append(
            {
                "report_id": generation["report_id"],
                "markdown": markdown_path.read_text(),
                "generation": generation,
                "imported_at": imported.get("imported_at") or "",
            }
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.get("imported_at") or "", item.get("report_id") or ""))[-1]


def find_imported_one_shot_analysis_report(
    *,
    survey: str,
    job_id: str | None,
    model: str | None,
    questions: list[str],
) -> dict[str, Any] | None:
    reports_root = practitioner_reports_dir()
    if not reports_root.exists():
        return None
    expected_questions = set(questions)
    candidates: list[dict[str, Any]] = []
    for context_path_candidate in sorted(reports_root.glob("*/context.json")):
        report_dir = context_path_candidate.parent
        markdown_path = report_dir / "report.md"
        import_path = report_dir / "import.json"
        if not markdown_path.exists() or not import_path.exists():
            continue
        context = read_json(context_path_candidate, {})
        report_context = context.get("one_shot_analysis_context", {})
        if report_context.get("report_kind") != "frontier_generated_one_shot_marginal_analysis":
            continue
        if report_context.get("survey") != survey:
            continue
        filters = report_context.get("filters", {})
        context_questions = set(str(question) for question in filters.get("questions", []) if question)
        if expected_questions and context_questions != expected_questions:
            continue
        if job_id is not None and (job_id or None) != (filters.get("job_id") or None):
            continue
        if model is not None and (model or None) != (filters.get("model") or None):
            continue
        imported = read_json(import_path, {})
        generation = {
            **context.get("generation", {}),
            "mode": "imported_results",
            "report_id": context.get("report_id") or report_dir.name,
            "context_path": str(context_path_candidate),
            "markdown_path": str(markdown_path),
            "import_path": str(import_path),
            "imported_at": imported.get("imported_at"),
        }
        candidates.append(
            {
                "report_id": generation["report_id"],
                "markdown": markdown_path.read_text(),
                "generation": generation,
                "imported_at": imported.get("imported_at") or "",
            }
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.get("imported_at") or "", item.get("report_id") or ""))[-1]


def cmd_report_facts(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_report_bundle(args)
    return report_stage_envelope("zwill report facts", manifest, "facts")


def cmd_report_analyze(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_report_bundle(args)
    return report_stage_envelope("zwill report analyze", manifest, "analysis")


def cmd_report_render(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_report_bundle(args)
    final_stage = manifest.get("stages", {}).get("stages", {}).get("final_report", {})
    if args.final and final_stage.get("status") != "ready":
        raise ZwillError(
            "blocked",
            "Final report is blocked because required generated interpretations are missing.",
            hint=final_stage.get("next_step") or "Run the relevant export/run/import/render generated-interpretation flow.",
            context={"stage_manifest": str(Path(manifest["output_dir"]) / "stage-manifest.json"), "missing": final_stage.get("missing", [])},
        )
    return report_stage_envelope("zwill report render", manifest, "final_report" if args.final else "report_preview")


def read_probability_imports(sdir: Path) -> list[dict[str, Any]]:
    imports = []
    jobs_dir = probability_jobs_dir(sdir)
    if not jobs_dir.exists():
        return imports
    for import_path in sorted(jobs_dir.glob("*/import.json")):
        metadata = read_json(import_path, {})
        if metadata:
            imports.append(metadata)
    return imports


def build_probability_coverage_payload(sdir: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    imports = read_probability_imports(sdir)
    imported_by_job_model: Counter[tuple[str, str]] = Counter()
    for row in rows:
        imported_by_job_model[(str(row.get("job_id")), model_label(row.get("service"), row.get("model")))] += 1
    issue_model_rows: list[tuple[str, str]] = []
    requested_by_job_model: Counter[tuple[str, str]] = Counter()
    job_rows = []
    for metadata in imports:
        job_id = str(metadata.get("job_id"))
        for issue in metadata.get("issues", []):
            issue_model_rows.append((job_id, str(issue.get("model") or "")))
        stored_path = metadata.get("stored_path")
        if stored_path and Path(stored_path).exists():
            try:
                results = read_json_or_gzip(Path(stored_path))
            except Exception:
                results = {}
            for result_row in results.get("data", []) if isinstance(results, dict) else []:
                model = result_row.get("model", {})
                requested_by_job_model[(job_id, model_label(model.get("inference_service"), model.get("model")))] += 1
        job_rows.append(
            {
                "job_id": job_id,
                "source_path": metadata.get("source_path"),
                "requested_rows": metadata.get("row_count", 0),
                "imported_rows": metadata.get("extracted_count", 0),
                "issue_count": metadata.get("issue_count", 0),
                "imported_at": metadata.get("imported_at"),
            }
        )
    labels_by_job: dict[str, set[str]] = defaultdict(set)
    for job_id, label in set(requested_by_job_model) | set(imported_by_job_model):
        labels_by_job[job_id].add(label)
    issue_by_job_model: Counter[tuple[str, str]] = Counter()
    for job_id, raw_label in issue_model_rows:
        label = raw_label
        if raw_label not in labels_by_job.get(job_id, set()):
            suffix_matches = sorted(label for label in labels_by_job.get(job_id, set()) if label.split(":", 1)[-1] == raw_label)
            if len(suffix_matches) == 1:
                label = suffix_matches[0]
        issue_by_job_model[(job_id, label)] += 1
    model_rows = []
    keys = sorted(set(requested_by_job_model) | set(imported_by_job_model) | set(issue_by_job_model))
    for job_id, model in keys:
        imported = imported_by_job_model.get((job_id, model), 0)
        issues = issue_by_job_model.get((job_id, model), 0)
        model_rows.append(
            {
                "job_id": job_id,
                "model": model,
                "requested_rows": requested_by_job_model.get((job_id, model), 0),
                "imported_rows": imported,
                "issue_count": issues,
                "missing_or_malformed_rows": max(0, requested_by_job_model.get((job_id, model), 0) - imported),
            }
        )
    return {"jobs": job_rows, "models": model_rows}


def render_probability_coverage_html(payload: dict[str, Any]) -> str:
    section = render_probability_coverage_section(payload)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>One-Shot Import Coverage</title>
  <style>
    {EP_REPORT_CSS}
    section {{ border:1px solid var(--ep-border); border-radius:8px; padding:18px; margin-bottom:16px; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <main>
    <h1>One-Shot Import Coverage</h1>
    <p>Requested rows come from the stored raw EDSL Results object when available. Imported rows are rows that passed probability parsing and normalization.</p>
    {section}
  </main>
</body>
</html>"""


def render_probability_coverage_section(payload: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        import html

        return html.escape(str(value), quote=True)

    job_rows = "".join(
        "<tr>"
        f"<td><code>{esc(row['job_id'])}</code></td>"
        f"<td class=\"num\">{esc(row['requested_rows'])}</td>"
        f"<td class=\"num\">{esc(row['imported_rows'])}</td>"
        f"<td class=\"num\">{esc(row['issue_count'])}</td>"
        f"<td>{esc(row.get('imported_at') or '')}</td>"
        "</tr>"
        for row in payload.get("jobs", [])
    )
    model_rows = "".join(
        "<tr>"
        f"<td><code>{esc(row['job_id'])}</code></td>"
        f"<td>{esc(row['model'])}</td>"
        f"<td class=\"num\">{esc(row['requested_rows'])}</td>"
        f"<td class=\"num\">{esc(row['imported_rows'])}</td>"
        f"<td class=\"num\">{esc(row['missing_or_malformed_rows'])}</td>"
        f"<td class=\"num\">{esc(row['issue_count'])}</td>"
        "</tr>"
        for row in payload.get("models", [])
    )
    if not job_rows:
        job_rows = "<tr><td colspan=\"5\">No imported one-shot jobs found.</td></tr>"
    if not model_rows:
        model_rows = "<tr><td colspan=\"6\">No model-level coverage rows found.</td></tr>"
    return f"""
    <section class="analysis-card">
      <h2>Coverage and Import Quality</h2>
      <p>Requested rows come from the stored raw EDSL Results object when available. Imported rows are rows that passed probability parsing and normalization.</p>
      <h3>Jobs</h3>
      <table><thead><tr><th>Job</th><th class="num">Requested rows</th><th class="num">Imported rows</th><th class="num">Issues</th><th>Imported at</th></tr></thead><tbody>{job_rows}</tbody></table>
      <h3>Models</h3>
      <table><thead><tr><th>Job</th><th>Model</th><th class="num">Requested rows</th><th class="num">Imported rows</th><th class="num">Missing/malformed</th><th class="num">Issues</th></tr></thead><tbody>{model_rows}</tbody></table>
    </section>"""


def insert_before_main_close(html_text: str, section: str) -> str:
    if not section:
        return html_text
    if "</main>" in html_text:
        return html_text.replace("</main>", f"{section}\n  </main>", 1)
    return html_text + section


def insert_after_main_open(html_text: str, section: str) -> str:
    if not section:
        return html_text
    if "<main>" in html_text:
        return html_text.replace("<main>", f"<main>\n{section}", 1)
    return section + html_text


def render_generated_executive_interpretation_section(generated: dict[str, Any] | None) -> str:
    if not generated:
        return """
    <section class="summary-card">
      <h2>Interpretation and Recommendation</h2>
      <p>No generated twin-validation interpretation has been imported yet. Run the executive summary export, then run/import/render the report-writing job before circulating this report.</p>
    </section>"""
    body = markdown_to_html(remove_leading_executive_summary_heading(generated.get("markdown", "")))
    generation = generated.get("generation") or {}
    model = generation.get("model") or generation.get("model_label") or "unknown model"
    return f"""
    <section class="summary-card generated-analysis">
      <h2>Interpretation and Recommendation</h2>
      {body}
      <p class="subtle">Generated by {model} from compact twin-validation artifacts.</p>
    </section>"""


def render_validation_diagnostics_html(*, survey: str, artifacts: dict[str, str], output_dir: Path) -> str:
    display_title, _raw_title = report_display_title(str(survey))
    section = render_validation_diagnostics_section(survey=survey, artifacts=artifacts, output_dir=output_dir)
    import html

    esc = lambda value: html.escape(str(value), quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(display_title)} Validation Diagnostics</title>
  <style>
    {EP_REPORT_CSS}
    section {{ border:1px solid var(--ep-border); border-radius:8px; padding:18px; margin-bottom:16px; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <main>
    <h1>Validation Diagnostics</h1>
    <p class="subtle">{esc(display_title)} · Survey id: <code>{esc(survey)}</code></p>
    <p>Supporting plot and data artifacts generated for the executive summary and twin validation readout.</p>
    {section}
  </main>
</body>
</html>"""


def render_validation_diagnostics_section(*, survey: str, artifacts: dict[str, str], output_dir: Path) -> str:
    def esc(value: Any) -> str:
        import html

        return html.escape(str(value), quote=True)

    labels = {
        "lift_histogram": "Lift vs uniform histogram",
        "empirical_lift_histogram": "Lift vs empirical marginal oracle histogram",
        "pairwise_order_accuracy": "Pairwise option-order accuracy",
        "spearman_rank_order": "Spearman rank-order JSON",
        "spearman_rank_order_csv": "Spearman rank-order CSV",
        "individual_predictive_power": "Overall and per-question permutation JSON",
        "individual_predictive_power_by_question": "Per-question permutation CSV",
        "pairwise_order_accuracy_csv": "Pairwise option-order CSV",
    }
    rows = []
    image_blocks = []
    for key, path in artifacts.items():
        href = bundle_rel_link(path, output_dir)
        label = labels.get(key, key.replace("_", " ").title())
        rows.append(f"<tr><td>{esc(label)}</td><td><a href=\"{esc(href)}\">{esc(href)}</a></td></tr>")
        if str(path).endswith(".svg"):
            image_blocks.append(f"<section><h3>{esc(label)}</h3><img src=\"{esc(href)}\" alt=\"{esc(label)}\"></section>")
    if not rows:
        rows.append("<tr><td colspan=\"2\">No diagnostic artifacts were generated.</td></tr>")
    return f"""
    <section class="summary-card">
      <h2 id="definitions">Supporting Diagnostics</h2>
      <p>Links and previews for lift histograms, rank-order diagnostics, pairwise option ordering, and permutation tests.</p>
      <table><thead><tr><th>Artifact</th><th>File</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
      {''.join(image_blocks)}
    </section>"""


def cmd_raw_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists() or not source.is_file():
        raise ZwillError("invalid_input", f"Raw path is not a file: {args.path}.")
    stored_dir = sdir / "raw" / args.id
    stored_dir.mkdir(parents=True, exist_ok=True)
    stored = stored_dir / source.name
    shutil.copy2(source, stored)
    raw_file = {
        "id": args.id,
        "kind": args.kind,
        "title": args.title,
        "source_path": args.path,
        "source_hash": sha256(source),
        "stored_path": str(stored),
        "stored_hash": sha256(stored),
        "added_at": utc_now(),
    }
    raw_files = [r for r in read_json(sdir / "raw_files.json", []) if r["id"] != args.id]
    raw_files.append(raw_file)
    write_json(sdir / "raw_files.json", raw_files)
    return envelope("zwill raw add", "ok", {"raw_file": raw_file})


def cmd_raw_list(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    raw_files = [
        {k: r[k] for k in ["id", "kind", "title", "stored_path", "stored_hash"] if k in r}
        for r in read_json(sdir / "raw_files.json", [])
    ]
    return envelope("zwill raw list", "ok", {"raw_files": raw_files, "raw_file_count": len(raw_files)})


def markdown_from_args(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.path:
        path = Path(args.path)
        if not path.exists() or not path.is_file():
            raise ZwillError("invalid_input", f"Context path is not a file: {args.path}.")
        return path.read_text(), args.path
    return args.text, None


def cmd_context_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    markdown, source_path = markdown_from_args(args)
    path = context_path(sdir)
    existing = path.read_text() if path.exists() else ""
    if existing.strip():
        content = existing.rstrip() + "\n\n" + markdown.strip() + "\n"
    else:
        content = markdown.strip() + "\n"
    path.write_text(content)
    data = {
        "survey": args.survey,
        "path": str(path),
        "source_path": source_path,
        "chars": len(markdown),
        "total_chars": len(content),
        "updated_at": utc_now(),
    }
    return envelope("zwill context add", "ok", {"context": data})


def cmd_context_set(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    markdown, source_path = markdown_from_args(args)
    path = context_path(sdir)
    content = markdown.strip() + "\n"
    path.write_text(content)
    data = {
        "survey": args.survey,
        "path": str(path),
        "source_path": source_path,
        "chars": len(markdown),
        "total_chars": len(content),
        "updated_at": utc_now(),
    }
    return envelope("zwill context set", "ok", {"context": data})


def cmd_context_show(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    path = context_path(sdir)
    markdown = path.read_text() if path.exists() else ""
    return envelope(
        "zwill context show",
        "ok",
        {
            "context": {
                "survey": args.survey,
                "path": str(path),
                "markdown": markdown,
                "chars": len(markdown),
            }
        },
    )


def cmd_question_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    if not args.question_name:
        raise ZwillError("invalid_input", "question_name is required.", hint="Pass --question-name.")
    question = {
        "question_name": args.question_name,
        "question_type": args.question_type,
        "question_text": args.question_text,
        "question_options": args.question_option or [],
        "role": args.role,
        "registered_at": utc_now(),
    }
    option_labels = parse_option_labels(args.option_label)
    if option_labels:
        question["option_labels"] = option_labels
    if args.source_raw or args.source_note:
        question["source"] = {"raw_id": args.source_raw, "note": args.source_note}
    rows = [q for q in read_jsonl(sdir / "questions.jsonl") if q["question_name"] != args.question_name]
    rows.append(question)
    annotated, _rank_tasks = annotate_rank_items(rows)
    rewrite_jsonl(sdir / "questions.jsonl", annotated)
    return envelope("zwill question add", "ok", {"question": question}, next_steps=[f"zwill answer import --survey {args.survey} --path answers.jsonl"])


def cmd_question_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    existing = questions_by_name(sdir)
    imported = []
    for row in read_jsonl(Path(args.path)):
        existing[row["question_name"]] = row
        imported.append(row["question_name"])
    annotated, rank_tasks = annotate_rank_items(list(existing.values()))
    rewrite_jsonl(sdir / "questions.jsonl", annotated)
    return envelope(
        "zwill question import",
        "ok",
        {
            "imported_count": len(imported),
            "skipped_count": 0,
            "question_names": imported,
            "rank_task_count": len(rank_tasks),
            "rank_tasks": rank_tasks,
        },
    )


def cmd_respondent_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    respondent = {
        "respondent_id": args.respondent_id,
        "weight": args.weight,
        "metadata": parse_metadata(args.metadata),
    }
    if args.source_raw or args.source_note:
        respondent["source"] = {"raw_id": args.source_raw, "note": args.source_note}
    rows = [r for r in read_jsonl(sdir / "respondents.jsonl") if r["respondent_id"] != args.respondent_id]
    rows.append(respondent)
    rewrite_jsonl(sdir / "respondents.jsonl", rows)
    return envelope("zwill respondent add", "ok", {"respondent": respondent})


def cmd_respondent_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    existing = respondents_by_id(sdir)
    imported = 0
    for row in read_jsonl(Path(args.path)):
        existing[row["respondent_id"]] = row
        imported += 1
    rewrite_jsonl(sdir / "respondents.jsonl", list(existing.values()))
    return envelope("zwill respondent import", "ok", {"imported_count": imported, "respondent_count": len(existing)})


def material_markdown_from_args(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.path:
        path = Path(args.path)
        if not path.exists() or not path.is_file():
            raise ZwillError("invalid_input", f"Agent material path is not a file: {args.path}.")
        return path.read_text().strip(), args.path
    return args.text.strip(), None


def normalize_tags(values: list[str] | str | None) -> list[str]:
    if values is None:
        return []
    raw_values = values if isinstance(values, list) else [values]
    tags: list[str] = []
    for value in raw_values:
        tags.extend(tag.strip() for tag in value.split(",") if tag.strip())
    return tags


def next_agent_material_id(sdir: Path, respondent_id: str, kind: str) -> str:
    existing = agent_material_rows(sdir)
    prefix = f"{kind}_{respondent_id}_"
    count = sum(1 for row in existing if str(row.get("material_id", "")).startswith(prefix))
    return f"{prefix}{count + 1:03d}"


def validate_agent_material_row(sdir: Path, row: dict[str, Any], line: int | None = None) -> dict[str, Any] | None:
    respondent_id = row.get("respondent_id")
    if not respondent_id:
        return {"code": "invalid_input", "line": line, "message": "respondent_id is required"}
    if respondent_id not in respondents_by_id(sdir):
        return {"code": "unknown_respondent", "line": line, "respondent_id": respondent_id}
    if not row.get("kind"):
        return {"code": "invalid_input", "line": line, "respondent_id": respondent_id, "message": "kind is required"}
    if not row.get("body_markdown"):
        return {"code": "invalid_input", "line": line, "respondent_id": respondent_id, "message": "body_markdown is required"}
    return None


def cmd_agent_material_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    markdown, source_path = material_markdown_from_args(args)
    material = {
        "material_id": args.material_id or next_agent_material_id(sdir, args.respondent_id, args.kind),
        "respondent_id": args.respondent_id,
        "kind": args.kind,
        "title": args.title,
        "body_markdown": markdown,
        "tags": normalize_tags(args.tag),
        "include_by_default": args.include_by_default,
        "source": {
            "path": source_path,
            "raw_id": args.source_raw,
            "note": args.source_note,
        },
        "created_at": utc_now(),
    }
    material["source"] = {key: value for key, value in material["source"].items() if value}
    issue = validate_agent_material_row(sdir, material)
    if issue:
        raise ZwillError(issue["code"], "Agent material failed validation.", context=issue)
    rows = [row for row in agent_material_rows(sdir) if row.get("material_id") != material["material_id"]]
    rows.append(material)
    rewrite_jsonl(agent_material_path(sdir), rows)
    return envelope("zwill agent-material add", "ok", {"material": material})


def cmd_agent_material_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    existing = {row["material_id"]: row for row in agent_material_rows(sdir) if row.get("material_id")}
    imported = 0
    quarantined = 0
    examples = []
    for line, row in enumerate(read_jsonl(Path(args.path)), 1):
        material = dict(row)
        material.setdefault("material_id", next_agent_material_id(sdir, material.get("respondent_id", "unknown"), material.get("kind", "material")))
        material.setdefault("title", material.get("kind", "Agent material"))
        material.setdefault("tags", [])
        material["tags"] = normalize_tags(material.get("tags"))
        material.setdefault("include_by_default", False)
        material.setdefault("created_at", utc_now())
        issue = validate_agent_material_row(sdir, material, line)
        if issue:
            full_issue = add_quarantine_issue(sdir, {"type": "agent_material", **issue})
            quarantined += 1
            if len(examples) < 3:
                examples.append(full_issue)
            continue
        existing[material["material_id"]] = material
        imported += 1
    rewrite_jsonl(agent_material_path(sdir), list(existing.values()))
    data = {
        "imported_count": imported,
        "material_count": len(existing),
        "quarantined_count": quarantined,
    }
    if examples:
        data["quarantine_examples"] = examples
    warnings = [{"code": "partial_import", "message": f"{quarantined} agent material row was quarantined."}] if quarantined else []
    return envelope("zwill agent-material import", "ok", data, warnings=warnings)


def cmd_agent_material_list(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    rows = select_agent_material(sdir, [args.respondent_id] if args.respondent_id else None, args)
    compact = [
        {
            "material_id": row.get("material_id"),
            "respondent_id": row.get("respondent_id"),
            "kind": row.get("kind"),
            "title": row.get("title"),
            "tags": row.get("tags", []),
            "chars": len(row.get("body_markdown", "")),
        }
        for row in rows
    ]
    return envelope("zwill agent-material list", "ok", {"materials": compact, "material_count": len(compact)})


def cmd_agent_material_show(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    for row in agent_material_rows(sdir):
        if row.get("material_id") == args.material_id:
            return envelope("zwill agent-material show", "ok", {"material": row})
    raise ZwillError("not_found", f"Agent material does not exist: {args.material_id}.")


def cmd_answer_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    answer = {"respondent_id": args.respondent_id, "question": args.question}
    if args.missing_code is not None:
        answer["answer"] = None
        answer["missing_code"] = args.missing_code
    else:
        answer["answer"] = args.answer
    issue = validate_answer(sdir, answer)
    if issue:
        add_quarantine_issue(sdir, issue)
        raise ZwillError(issue["code"], "Answer failed validation.", context=issue, next_steps=[f"zwill quarantine list --survey {args.survey}"])
    ensure_implicit_respondent(sdir, args.respondent_id)
    answer["recorded_at"] = utc_now()
    append_jsonl(sdir / "answers.jsonl", answer)
    return envelope("zwill answer add", "ok", {"answer": answer})


def cmd_answer_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    questions = questions_by_name(sdir)
    respondent_ids = set(respondents_by_id(sdir))
    imported = 0
    quarantined = 0
    quarantine_examples = []
    valid_rows = []
    implicit_respondents = []
    for line, row in enumerate(read_jsonl(Path(args.path)), 1):
        question_name = row.get("question")
        issue = None
        if question_name not in questions:
            issue = {"code": "unknown_question", "line": line, "question": question_name}
        elif "answer" in row and row.get("answer") is not None:
            valid_options = questions[question_name].get("question_options", [])
            if valid_options and row["answer"] not in valid_options:
                issue = {
                    "code": "invalid_answer_option",
                    "line": line,
                    "question": question_name,
                    "answer": row["answer"],
                    "valid_options": valid_options,
                }
        elif not row.get("missing_code"):
            issue = {
                "code": "invalid_input",
                "line": line,
                "question": question_name,
                "message": "answer or missing_code is required",
            }
        if issue:
            full_issue = add_quarantine_issue(sdir, issue)
            quarantined += 1
            if len(quarantine_examples) < 3:
                quarantine_examples.append(full_issue)
            continue
        respondent_id = row["respondent_id"]
        if respondent_id not in respondent_ids:
            respondent_ids.add(respondent_id)
            implicit_respondents.append({"respondent_id": respondent_id, "weight": 1.0, "metadata": {}})
        valid_rows.append(row)
        imported += 1
    if implicit_respondents:
        with (sdir / "respondents.jsonl").open("a") as f:
            for respondent in implicit_respondents:
                f.write(json.dumps(respondent, separators=(",", ":")) + "\n")
    if valid_rows:
        with (sdir / "answers.jsonl").open("a") as f:
            for row in valid_rows:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
    warnings = []
    if quarantined:
        warnings.append({"code": "partial_import", "message": f"{quarantined} answer row was quarantined."})
    data = {
        "imported_count": imported,
        "answer_count": len(read_jsonl(sdir / "answers.jsonl")),
        "respondent_count": len(read_jsonl(sdir / "respondents.jsonl")),
        "question_count": len(read_jsonl(sdir / "questions.jsonl")),
        "quarantined_count": quarantined,
    }
    if quarantine_examples:
        data["quarantine_examples"] = quarantine_examples
    return envelope("zwill answer import", "ok", data, warnings=warnings, next_steps=[f"zwill commit --survey {args.survey}"] if not quarantine_examples else [f"zwill quarantine list --survey {args.survey}"])


def cmd_status(_: argparse.Namespace) -> dict[str, Any]:
    project_id = active_project_id()
    surveys = read_json(project_surveys_path(), [])
    summaries = [survey_summary(s["name"]) for s in surveys]
    return envelope(
        "zwill status",
        "ok",
        {"project": project_id, "surveys": summaries},
        next_steps=["zwill commit --survey <survey>"] if summaries else [],
    )


def compute_marginals(sdir: Path) -> dict[str, Any]:
    questions = read_jsonl(sdir / "questions.jsonl")
    respondents = respondents_by_id(sdir)
    answers = read_jsonl(sdir / "answers.jsonl")
    marginals: dict[str, dict[str, dict[str, float | int]]] = {}
    counts: dict[str, Counter] = defaultdict(Counter)
    weighted_counts: dict[str, Counter] = defaultdict(Counter)
    for answer in answers:
        value = answer.get("answer")
        key = "__missing__" if value is None else value
        weight = float(respondents.get(answer["respondent_id"], {}).get("weight", 1.0))
        counts[answer["question"]][key] += 1
        weighted_counts[answer["question"]][key] += weight
    for question in questions:
        qname = question["question_name"]
        keys = list(question.get("question_options", []))
        if counts[qname].get("__missing__"):
            keys.append("__missing__")
        marginals[qname] = {}
        for key in keys:
            marginals[qname][key] = {
                "count": counts[qname].get(key, 0),
                "weighted_count": round(weighted_counts[qname].get(key, 0.0), 10),
            }
    return marginals


def cmd_commit(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    issues = open_quarantine_issues(sdir)
    if issues:
        raise ZwillError(
            "gate_blocked",
            "Survey has open quarantine issues.",
            context={"open_quarantine_issues": len(issues)},
            hint=f"Run `zwill quarantine list --survey {args.survey}`.",
            next_steps=[f"zwill quarantine list --survey {args.survey}"],
        )
    committed = sdir / "committed"
    committed.mkdir(exist_ok=True)
    respondents = read_jsonl(sdir / "respondents.jsonl")
    questions = read_jsonl(sdir / "questions.jsonl")
    answers = read_jsonl(sdir / "answers.jsonl")
    write_json(committed / "respondents.json", respondents)
    write_json(committed / "truth_marginals.json", {"survey": args.survey, "marginals": compute_marginals(sdir)})
    committed_paths = {
        "respondents": str(committed / "respondents.json"),
        "truth_marginals": str(committed / "truth_marginals.json"),
    }
    if context_path(sdir).exists():
        shutil.copy2(context_path(sdir), committed / "context.md")
        committed_paths["context"] = str(committed / "context.md")
    return envelope(
        "zwill commit",
        "ok",
        {
            "survey": args.survey,
            "respondent_count": len(respondents),
            "question_count": len(questions),
            "answer_count": len(answers),
            "truth_marginal_count": len(questions),
            "committed_paths": committed_paths,
        },
    )


def cmd_quarantine_list(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    issues = read_jsonl(sdir / "quarantine.jsonl")
    return envelope("zwill quarantine list", "ok", {"issues": issues, "issue_count": len(issues)})


def cmd_quarantine_resolve(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    rows = read_jsonl(sdir / "quarantine.jsonl")
    for row in rows:
        if row["issue_id"] == args.issue_id:
            row["status"] = "resolved"
            row["resolution"] = {"action": args.action, "note": args.note}
            rewrite_jsonl(sdir / "quarantine.jsonl", rows)
            return envelope("zwill quarantine resolve", "ok", {"issue": row}, next_steps=[f"zwill commit --survey {args.survey}"])
    raise ZwillError("not_found", f"Quarantine issue does not exist: {args.issue_id}.")


def cmd_table(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    questions = read_jsonl(sdir / "questions.jsonl")
    answers = read_jsonl(sdir / "answers.jsonl")
    question_names = [q["question_name"] for q in questions]
    respondent_ids = sorted({a["respondent_id"] for a in answers})
    if args.limit is not None:
        respondent_ids = respondent_ids[: args.limit]
    values = {rid: {q: "" for q in question_names} for rid in respondent_ids}
    for answer in answers:
        if answer["respondent_id"] not in values:
            continue
        value = answer.get("answer")
        if value is None:
            value = f"missing:{answer.get('missing_code', 'unknown')}"
        values[answer["respondent_id"]][answer["question"]] = str(value)

    table = Table(title=f"{args.survey} answers")
    table.add_column("respondent_id", style="bold")
    for question_name in question_names:
        table.add_column(question_name)
    for respondent_id in respondent_ids:
        table.add_row(respondent_id, *(values[respondent_id][name] for name in question_names))
    Console().print(table)


def load_edsl_classes() -> tuple[Any, Any, Any, Any]:
    os.environ.setdefault("EDSL_LOG_DIR", str((ROOT / "edsl_logs").resolve()))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from edsl import Agent, AgentList, Question, Survey
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "Could not import EDSL.",
            hint="Install EDSL or make sure ~/tools/ep/edsl is available in this Python environment.",
        ) from exc
    return Agent, AgentList, Question, Survey


def load_edsl_job_classes() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    os.environ.setdefault("EDSL_LOG_DIR", str((ROOT / "edsl_logs").resolve()))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from edsl import Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "Could not import EDSL job dependencies.",
            hint="Install EDSL or make sure ~/tools/ep/edsl is available in this Python environment.",
        ) from exc
    return Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey


def load_edsl_agent_study_classes() -> tuple[Any, Any, Any, Any, Any, Any]:
    os.environ.setdefault("EDSL_LOG_DIR", str((ROOT / "edsl_logs").resolve()))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from edsl import AgentList, Jobs, Model, ModelList, Question, Survey
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "Could not import EDSL agent-study dependencies.",
            hint="Install EDSL or make sure ~/tools/ep/edsl is available in this Python environment.",
        ) from exc
    return AgentList, Jobs, Model, ModelList, Question, Survey


def load_edsl_runner_classes() -> tuple[Any, Any]:
    os.environ.setdefault("EDSL_LOG_DIR", str((ROOT / "edsl_logs").resolve()))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from edsl import Jobs
            from edsl.jobs.data_structures import RunParameters
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "Could not import EDSL runner dependencies.",
            hint="Install EDSL or make sure ~/tools/ep/edsl is available in this Python environment.",
        ) from exc
    return Jobs, RunParameters


def edsl_question_from_zwill(question: dict[str, Any], Question: Any) -> Any:
    kwargs = {
        "question_name": question["question_name"],
        "question_text": question["question_text"],
    }
    if question.get("question_options"):
        kwargs["question_options"] = question["question_options"]
    try:
        return Question(question["question_type"], **kwargs)
    except Exception as exc:
        if question.get("question_type") in {"rank", "ranking"}:
            try:
                from edsl import QuestionRank

                return QuestionRank(**kwargs)
            except Exception:
                pass
        raise ZwillError(
            "edsl_export_failed",
            f"Could not convert question {question['question_name']} to EDSL.",
            context={
                "question_name": question.get("question_name"),
                "question_type": question.get("question_type"),
                "error": str(exc),
            },
        ) from exc


def build_edsl_survey_dict(survey_name: str) -> dict[str, Any]:
    sdir = require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    _, _, Question, Survey = load_edsl_classes()
    survey = Survey()
    rank_tasks = detect_rank_tasks(questions)
    if not rank_tasks:
        rank_task_map: dict[str, dict[str, Any]] = {}
        for question in questions:
            if question.get("question_type") != "rank_item" or not question.get("rank_task_id"):
                continue
            task_id = str(question["rank_task_id"])
            rank_task_map.setdefault(
                task_id,
                {
                    "rank_task_id": task_id,
                    "rank_task_text": question.get("rank_task_text") or question.get("question_text") or task_id,
                    "rank_direction": question.get("rank_direction") or "1_is_best",
                    "source_question_names": [],
                    "items": [],
                },
            )
            rank_task_map[task_id]["source_question_names"].append(question["question_name"])
            rank_task_map[task_id]["items"].append(
                {
                    "item_id": question["question_name"],
                    "label": question.get("rank_item_label") or question.get("question_text") or question["question_name"],
                }
            )
        rank_tasks = list(rank_task_map.values())
    rank_item_names = {name for task in rank_tasks for name in task.get("source_question_names", [])}
    for question in synthetic_rank_questions(rank_tasks):
        survey.add_question(edsl_question_from_zwill(question, Question))
    for question in questions:
        if question.get("question_name") in rank_item_names or question.get("question_type") == "rank_item":
            continue
        survey.add_question(edsl_question_from_zwill(question, Question))
    return survey.to_dict()


def selected_question_names(args: argparse.Namespace, questions: list[dict[str, Any]]) -> list[str]:
    available = [question["question_name"] for question in questions]
    selected: list[str]
    if args.question or args.questions:
        selected = []
        for question_name in args.question or []:
            selected.append(question_name)
        if args.questions:
            selected.extend(name.strip() for name in args.questions.split(",") if name.strip())
    else:
        selected = available[:]

    excluded = set(args.exclude_question or [])
    selected = [name for name in selected if name not in excluded]
    unknown = [name for name in selected if name not in available]
    if unknown:
        raise ZwillError(
            "invalid_input",
            "Unknown question selected for EDSL export.",
            context={"unknown_questions": unknown, "available_questions": available},
            hint="Use question names from `zwill survey show` or the survey questions.jsonl file.",
        )
    return selected


def selected_agent_material_kinds(args: argparse.Namespace) -> set[str]:
    return set(normalize_tags(getattr(args, "agent_material_kind", None)))


def selected_agent_material_tags(args: argparse.Namespace) -> set[str]:
    return set(normalize_tags(getattr(args, "agent_material_tag", None)))


def select_agent_material(
    sdir: Path,
    respondent_ids: list[str] | None,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    respondent_set = set(respondent_ids) if respondent_ids is not None else None
    kinds = selected_agent_material_kinds(args)
    tags = selected_agent_material_tags(args)
    selected = []
    for row in agent_material_rows(sdir):
        if respondent_set is not None and row.get("respondent_id") not in respondent_set:
            continue
        if kinds and row.get("kind") not in kinds:
            continue
        row_tags = set(row.get("tags", []))
        if tags and not row_tags.intersection(tags):
            continue
        selected.append(row)
    return selected


def format_agent_material(materials: list[dict[str, Any]], max_chars: int | None = None) -> str:
    if not materials:
        return "No non-survey agent material provided."
    blocks = []
    for material in materials:
        heading = material.get("title") or material.get("kind") or "Agent material"
        kind = material.get("kind")
        body = material.get("body_markdown", "").strip()
        if kind:
            blocks.append(f"### {heading} ({kind})\n{body}")
        else:
            blocks.append(f"### {heading}\n{body}")
    text = "\n\n".join(blocks).strip()
    if max_chars is not None and max_chars >= 0 and len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n\n[Truncated to max agent material characters.]"
    return text


def twin_material_paths(args: argparse.Namespace) -> list[str]:
    values = getattr(args, "twin_material", None) or []
    return [str(value) for value in values]


def normalize_twin_material_row(row: dict[str, Any], source_path: str, index: int) -> dict[str, Any]:
    material_id = row.get("material_id") or row.get("id") or f"{Path(source_path).stem}_{index:04d}"
    question = row.get("question") or row.get("heldout_question") or row.get("heldout_question_name") or row.get("source_question_name")
    body = row.get("body_markdown") or row.get("markdown") or row.get("text") or row.get("body") or ""
    probabilities = row.get("probabilities")
    if not body and isinstance(probabilities, dict):
        body = "Probabilities:\n" + "\n".join(f"- {option}: {probability}" for option, probability in probabilities.items())
    return {
        "material_id": str(material_id),
        "title": str(row.get("title") or row.get("kind") or material_id),
        "kind": str(row.get("kind") or "supplemental"),
        "body_markdown": str(body).strip(),
        "survey": row.get("survey"),
        "question": question,
        "respondent_id": row.get("respondent_id"),
        "source_path": source_path,
        "metadata": row.get("metadata", {}),
    }


def load_twin_material(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_path in twin_material_paths(args):
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            raise ZwillError("not_found", f"Twin material path does not exist: {raw_path}.")
        if path.suffix == ".jsonl":
            for index, row in enumerate(read_jsonl(path), start=1):
                if not isinstance(row, dict):
                    raise ZwillError("invalid_input", f"Twin material JSONL rows must be objects: {raw_path}.")
                rows.append(normalize_twin_material_row(row, raw_path, index))
        elif path.suffix == ".json":
            data = read_json(path, None)
            records = data if isinstance(data, list) else data.get("materials", [data]) if isinstance(data, dict) else None
            if not isinstance(records, list):
                raise ZwillError("invalid_input", f"Twin material JSON must be an object, list, or object with materials: {raw_path}.")
            for index, row in enumerate(records, start=1):
                if not isinstance(row, dict):
                    raise ZwillError("invalid_input", f"Twin material JSON records must be objects: {raw_path}.")
                rows.append(normalize_twin_material_row(row, raw_path, index))
        else:
            rows.append(
                normalize_twin_material_row(
                    {
                        "material_id": path.stem,
                        "title": path.stem.replace("_", " "),
                        "kind": "markdown",
                        "body_markdown": path.read_text().strip(),
                    },
                    raw_path,
                    1,
                )
            )
    return [row for row in rows if row.get("body_markdown")]


def matching_twin_material(
    materials: list[dict[str, Any]],
    *,
    survey_name: str,
    heldout_question: str,
    respondent_id: str,
) -> list[dict[str, Any]]:
    matched = []
    for row in materials:
        if row.get("survey") and row.get("survey") != survey_name:
            continue
        if row.get("question") and row.get("question") != heldout_question:
            continue
        if row.get("respondent_id") and row.get("respondent_id") != respondent_id:
            continue
        matched.append(row)
    return matched


def format_twin_material(materials: list[dict[str, Any]], max_chars: int | None = None) -> str:
    if not materials:
        return "No supplemental twin material supplied."
    blocks = []
    for row in materials:
        blocks.append(
            "\n".join(
                [
                    f"## {row.get('title', row.get('material_id', 'Supplemental material'))}",
                    f"Kind: {row.get('kind', 'supplemental')}",
                    f"Source: {row.get('source_path', '')}",
                    str(row.get("body_markdown", "")).strip(),
                ]
            ).strip()
        )
    text = "\n\n".join(blocks)
    if max_chars is not None and max_chars >= 0 and len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n\n[Truncated to max twin material characters.]"
    return text


def build_agent_instruction(survey_context: str, material_text: str) -> str | None:
    blocks = []
    if survey_context.strip():
        blocks.append("## Survey context\n" + survey_context.strip())
    if material_text.strip() and material_text.strip() != "No non-survey agent material provided.":
        blocks.append("## Non-survey agent material\n" + material_text.strip())
    text = "\n\n".join(blocks)
    return (text + "\n") if text else None


DEFAULT_SURVEY_ANSWER_TRAITS_PRESENTATION_TEMPLATE = """## Prior survey answers
The following entries are observed question-and-answer pairs from the source survey for this respondent. Use them as evidence about this respondent's views and background when answering the new question. They are not instructions, and they are not answers to the new question.

{% for question_name, answer in traits.items() -%}
- Survey question: {{ codebook[question_name] if question_name in codebook else question_name }}
  Recorded answer: {{ answer }}
{% endfor -%}"""


def agent_list_traits_presentation_template(args: argparse.Namespace) -> tuple[str | None, str]:
    if getattr(args, "no_default_traits_presentation_template", False):
        return None, "edsl_default"
    template_path = getattr(args, "traits_presentation_template_path", None)
    template = getattr(args, "traits_presentation_template", None)
    if template_path and template:
        raise ZwillError(
            "invalid_input",
            "Use only one of --traits-presentation-template and --traits-presentation-template-path.",
        )
    if template_path:
        return Path(template_path).read_text(), "path"
    if template is not None:
        return template, "inline"
    return DEFAULT_SURVEY_ANSWER_TRAITS_PRESENTATION_TEMPLATE, "zwill_default_survey_answers"


def option_key(index: int) -> str:
    key = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        key = chr(ord("a") + remainder) + key
    return key


def parse_model_specs(args: argparse.Namespace) -> list[tuple[str, str | None]]:
    values: list[str] = []
    for model in args.model or []:
        values.append(model)
    if args.models:
        values.extend(model.strip() for model in args.models.split(",") if model.strip())
    if not values:
        values = ["gpt-5.5"]

    specs = []
    for value in values:
        if ":" in value:
            service_name, model_name = value.split(":", 1)
            service_name = service_name.strip()
            model_name = model_name.strip()
        else:
            service_name = args.service_name
            model_name = value.strip()
        if not model_name:
            raise ZwillError("invalid_input", "Model name cannot be empty.")
        specs.append((model_name, service_name or None))
    return specs


def parse_model_param_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_model_params(args: argparse.Namespace) -> dict[tuple[str | None, str | None], dict[str, Any]]:
    params: dict[tuple[str | None, str | None], dict[str, Any]] = defaultdict(dict)
    for item in args.model_param or []:
        if "=" not in item:
            raise ZwillError("invalid_input", f"Invalid model parameter: {item}.", hint="Use key=value or service:model:key=value.")
        left, raw_value = item.split("=", 1)
        parts = left.split(":")
        if len(parts) == 1:
            service_name = None
            model_name = None
            key = parts[0]
        elif len(parts) == 3:
            service_name, model_name, key = parts
            service_name = service_name or None
            model_name = model_name or None
        else:
            raise ZwillError(
                "invalid_input",
                f"Invalid model parameter target: {left}.",
                hint="Use key=value for all models or service:model:key=value for one model.",
            )
        if not key:
            raise ZwillError("invalid_input", f"Invalid model parameter: {item}.", hint="Parameter key cannot be empty.")
        params[(service_name, model_name)][key] = parse_model_param_value(raw_value)
    return params


def model_kwargs_for(
    model_name: str,
    service_name: str | None,
    model_params: dict[tuple[str | None, str | None], dict[str, Any]],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    kwargs.update(model_params.get((None, None), {}))
    kwargs.update(model_params.get((None, model_name), {}))
    kwargs.update(model_params.get((service_name, model_name), {}))
    return kwargs


def model_label(service_name: str | None, model_name: str | None) -> str:
    if service_name and model_name:
        return f"{service_name}:{model_name}"
    return str(model_name or "")


def cmd_probability_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.path).open("w", newline="") as output_file:
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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output)
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


def build_one_shot_analysis_report_context(
    args: argparse.Namespace,
    payload: dict[str, Any],
) -> dict[str, Any]:
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    per_question: dict[str, dict[str, Any]] = {}
    for row in rows:
        question = str(row.get("question") or "")
        current = per_question.get(question)
        candidate = {
            "question": question,
            "question_text": row.get("question_text"),
            "option_count": len(row.get("actual") or {}),
            "model": row.get("model_label") or model_label(row.get("service"), row.get("model")),
            "brier": row.get("brier"),
            "uniform_brier": row.get("uniform_brier"),
            "brier_improvement": row.get("brier_improvement"),
            "brier_percent_improvement": row.get("brier_percent_improvement"),
            "mae": row.get("mae"),
            "kl_divergence": row.get("kl_divergence"),
            "actual_top_option": max((row.get("actual") or {}).items(), key=lambda item: item[1])[0] if row.get("actual") else None,
            "predicted_top_option": max((row.get("predicted") or {}).items(), key=lambda item: item[1])[0] if row.get("predicted") else None,
        }
        if current is None or float(candidate.get("brier") or float("inf")) < float(current.get("brier") or float("inf")):
            per_question[question] = candidate

    best_questions = sorted(per_question.values(), key=lambda row: float(row.get("brier_improvement") or 0.0), reverse=True)[:12]
    weakest_questions = sorted(per_question.values(), key=lambda row: float(row.get("brier_improvement") or 0.0))[:12]
    rows_beat_uniform = [row for row in rows if float(row.get("brier_improvement") or 0.0) > 0.0]
    best_model, best_values = min(
        summary.items(),
        key=lambda item: float(item[1].get("mean_brier") or float("inf")),
        default=(None, {}),
    )
    return {
        "report_kind": "frontier_generated_one_shot_marginal_analysis",
        "survey": args.survey,
        "source_filters": {
            "job_id": getattr(args, "job_id", None),
            "probability_model": getattr(args, "probability_model", None),
        },
        "filters": {
            "job_id": getattr(args, "job_id", None),
            "model": getattr(args, "probability_model", None),
            "questions": sorted(per_question),
        },
        "analysis_target": {
            "page": "one-shot-marginals",
            "purpose": "Interpret no-persona aggregate marginal predictions as the deployable baseline for later digital twin validation.",
            "raw_prediction_rows_in_context": False,
        },
        "headline_metrics": {
            "prediction_rows": len(rows),
            "questions_evaluated": len(per_question),
            "models_evaluated": len(summary),
            "rows_beating_uniform_share": len(rows_beat_uniform) / len(rows) if rows else None,
            "best_model_by_mean_brier": best_model,
            "best_model_metrics": best_values,
        },
        "model_summary": summary,
        "best_question_fits": best_questions,
        "weakest_question_fits": weakest_questions,
        "per_question_best_model_summary": sorted(per_question.values(), key=lambda row: row["question"]),
    }


def build_one_shot_analysis_report_prompt(report_context: dict[str, Any]) -> str:
    return f"""You are writing the Analysis section for a one-shot marginal prediction report.

The report compares frontier-model, no-persona predictions of survey response marginals against committed empirical survey marginals and a uniform-over-options baseline.

Write Markdown only. Do not include a top-level title. Use plain language for an executive or research lead.

Your analysis should:
- Explain what was done. Define one-shot as asking the model once per survey question for the aggregate population response distribution, without respondent personas, prior answers, or digital twin context.
- Interpret the performance metrics versus the uniform baseline.
- Say what this implies for later digital twin work: this is the deployable aggregate baseline that persona-based twins should beat if the persona machinery is adding value.
- Identify where the model is strongest and weakest using the supplied question summaries.
- Be honest about limitations: this validates aggregate marginal prediction, not respondent-level matching.
- Do not claim individual predictive power.
- Do not say the text was computed deterministically or written by a template.

Recorded context and summary statistics:

{json.dumps(report_context, indent=2)}
"""


def build_edsl_one_shot_analysis_report_job_dict(
    args: argparse.Namespace,
    report_context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    prompt = build_one_shot_analysis_report_prompt(report_context)
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = load_edsl_job_classes()
    question_name = "one_shot_analysis_markdown"
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
        "report_kind": "one_shot_marginal_analysis",
    }
    generation = {
        "mode": "job_exported",
        "report_id": report_id,
        "model": model_label(model_specs[0][1], model_specs[0][0]) if model_specs else None,
        "models": [model_label(service_name, model_name) for model_name, service_name in model_specs],
        "report_kind": "one_shot_marginal_analysis",
    }
    context = {
        "report_id": report_id,
        "one_shot_analysis_context": report_context,
        "generation": generation,
    }
    return job_dict, context, prompt


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
    job_dict, context, prompt = build_edsl_one_shot_analysis_report_job_dict(args, report_context)
    report_id = job_dict["zwill"]["practitioner_report_id"]
    path = Path(args.path or (Path("artifacts") / f"{args.survey}_one_shot_marginals.html"))
    data = write_practitioner_report_export(
        report_id,
        job_dict,
        context,
        prompt,
        job_path=Path(args.job_path) if args.job_path else None,
        prompt_path=Path(args.prompt_path) if args.prompt_path else None,
        context_path_arg=Path(args.context_path) if args.context_path else None,
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
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill prob-results analysis-import --report-id {report_id} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
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
            hint=f"Run `zwill prob-results analysis-import --report-id {args.report_id} --path <results.json.gz>`.",
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
    output_path = Path(args.path) if args.path else paths["html"]
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

    summary_table = Table(title="model summary")
    for column in ["model", "rows", "mean_brier", "uniform_brier", "brier_delta", "brier_%", "mean_kl", "uniform_kl", "kl_delta", "kl_%"]:
        summary_table.add_column(column)
    for model, values in summary.items():
        summary_table.add_row(
            model,
            str(values["rows"]),
            f"{values['mean_brier']:.4f}",
            f"{values['mean_uniform_brier']:.4f}",
            f"{values['mean_brier_improvement']:.4f}",
            f"{values['mean_brier_percent_improvement']:+.1f}%",
            f"{values['mean_kl_divergence']:.4f}",
            f"{values['mean_uniform_kl_divergence']:.4f}",
            f"{values['mean_kl_improvement']:.4f}",
            f"{values['mean_kl_percent_improvement']:+.1f}%",
        )
    Console().print(summary_table)


def build_edsl_agent_list_dict(survey_name: str, args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    selected = selected_question_names(args, questions)
    selected_set = set(selected)
    codebook = {
        question["question_name"]: question["question_text"]
        for question in questions
        if question["question_name"] in selected_set
    }

    respondent_ids = [row["respondent_id"] for row in read_jsonl(sdir / "respondents.jsonl")]
    if not respondent_ids:
        respondent_ids = sorted({row["respondent_id"] for row in read_jsonl(sdir / "answers.jsonl")})
    if args.limit is not None:
        respondent_ids = respondent_ids[: args.limit]

    traits_by_respondent = {
        respondent_id: {question_name: None for question_name in selected}
        for respondent_id in respondent_ids
    }
    for answer in read_jsonl(sdir / "answers.jsonl"):
        respondent_id = answer["respondent_id"]
        question_name = answer["question"]
        if respondent_id not in traits_by_respondent or question_name not in selected_set:
            continue
        traits_by_respondent[respondent_id][question_name] = answer.get("answer")

    include_material = getattr(args, "include_agent_material", False)
    include_survey_context = getattr(args, "include_survey_context", False)
    context_text = survey_context_text(sdir) if include_survey_context else ""
    max_chars = getattr(args, "max_agent_material_chars", None)
    traits_template, traits_template_source = agent_list_traits_presentation_template(args)
    instructions_by_respondent: dict[str, str | None] = {}
    for respondent_id in respondent_ids:
        material_text = ""
        if include_material:
            materials = select_agent_material(sdir, [respondent_id], args)
            material_text = format_agent_material(materials, max_chars)
        instructions_by_respondent[respondent_id] = build_agent_instruction(context_text, material_text)

    Agent, AgentList, _, _ = load_edsl_classes()
    agents = []
    for respondent_id in respondent_ids:
        kwargs = {
            "name": respondent_id,
            "traits": traits_by_respondent[respondent_id],
            "codebook": codebook,
        }
        if instructions_by_respondent[respondent_id]:
            kwargs["instruction"] = instructions_by_respondent[respondent_id]
        if traits_template is not None:
            kwargs["traits_presentation_template"] = traits_template
        agents.append(Agent(**kwargs))
    agent_list = AgentList(agents)
    data = agent_list.to_dict()

    # Keep the shared trait codebook at AgentList level. EDSL can rehydrate this
    # format and apply the codebook to agents during AgentList.from_dict.
    data["codebook"] = codebook
    if traits_template is not None:
        data["traits_presentation_template"] = traits_template
    for agent in data.get("agent_list", []):
        agent.pop("codebook", None)
        if traits_template is not None and agent.get("traits_presentation_template") == traits_template:
            agent.pop("traits_presentation_template", None)
    data["zwill"] = {
        "survey": survey_name,
        "selected_questions": selected,
        "include_survey_context": include_survey_context,
        "include_agent_material": include_material,
        "agent_material_kinds": sorted(selected_agent_material_kinds(args)),
        "agent_material_tags": sorted(selected_agent_material_tags(args)),
        "max_agent_material_chars": max_chars,
        "traits_presentation_template_source": traits_template_source,
        "agent_count": len(respondent_ids),
    }
    return data


def inspect_agent_list_dict(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("edsl_class_name") != "AgentList":
        raise ZwillError("invalid_input", "Expected an EDSL AgentList serialization.")
    agents = data.get("agent_list", [])
    if not isinstance(agents, list):
        raise ZwillError("invalid_input", "Expected an EDSL AgentList serialization with an agent_list array.")
    codebook = data.get("codebook", {})
    shared_traits_template = data.get("traits_presentation_template")
    trait_keys: Counter = Counter()
    agents_with_instruction = 0
    instruction_chars = []
    sample_agents = []
    for agent in agents:
        traits = agent.get("traits", {}) if isinstance(agent, dict) else {}
        for key in traits:
            trait_keys[key] += 1
        instruction = agent.get("instruction") if isinstance(agent, dict) else None
        if instruction:
            agents_with_instruction += 1
            instruction_chars.append(len(instruction))
        if len(sample_agents) < 3:
            sample_agents.append(
                {
                    "name": agent.get("name"),
                    "trait_count": len(traits),
                    "trait_keys": list(traits)[:10],
                    "has_instruction": bool(instruction),
                    "instruction_chars": len(instruction or ""),
                }
            )
    return {
        "agent_count": len(agents),
        "trait_keys": sorted(trait_keys),
        "trait_counts": dict(sorted(trait_keys.items())),
        "codebook_keys": sorted(codebook),
        "has_traits_presentation_template": bool(shared_traits_template),
        "traits_presentation_template_chars": len(shared_traits_template or ""),
        "traits_presentation_template_preview": (shared_traits_template or "")[:240],
        "agents_with_instruction": agents_with_instruction,
        "mean_instruction_chars": (
            sum(instruction_chars) / len(instruction_chars)
            if instruction_chars
            else 0.0
        ),
        "zwill": data.get("zwill", {}),
        "sample_agents": sample_agents,
    }


def cmd_agent_list_inspect(args: argparse.Namespace) -> dict[str, Any]:
    data = read_json_or_gzip(Path(args.path))
    summary = inspect_agent_list_dict(data)
    if args.format == "json":
        print_json(envelope("zwill agent-list inspect", "ok", summary))
    else:
        table = Table(title="EDSL AgentList")
        table.add_column("metric")
        table.add_column("value")
        table.add_row("agents", str(summary["agent_count"]))
        table.add_row("trait keys", ", ".join(summary["trait_keys"]) or "(none)")
        table.add_row("traits template", "yes" if summary["has_traits_presentation_template"] else "no")
        table.add_row("traits template chars", str(summary["traits_presentation_template_chars"]))
        table.add_row("agents with instruction", str(summary["agents_with_instruction"]))
        table.add_row("mean instruction chars", f"{summary['mean_instruction_chars']:.1f}")
        Console().print(table)
        if summary["sample_agents"]:
            sample_table = Table(title="sample agents")
            for column in ["name", "trait_count", "trait_keys", "has_instruction", "instruction_chars"]:
                sample_table.add_column(column)
            for row in summary["sample_agents"]:
                sample_table.add_row(
                    str(row["name"]),
                    str(row["trait_count"]),
                    ", ".join(row["trait_keys"]),
                    str(row["has_instruction"]),
                    str(row["instruction_chars"]),
                )
            Console().print(sample_table)
    return envelope("zwill agent-list inspect", "ok", summary)


def load_question_spec_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.question_path:
        data = read_json(Path(args.question_path), {})
        if data.get("edsl_class_name") == "QuestionBase":
            return {
                "question_name": data["question_name"],
                "question_type": data["question_type"],
                "question_text": data["question_text"],
                "question_options": data.get("question_options", []),
            }
        return data
    return {
        "question_name": args.question_name,
        "question_type": args.question_type,
        "question_text": args.question_text,
        "question_options": args.question_option or [],
    }


def agent_study_job_id_from_job(job: dict[str, Any]) -> str:
    payload = {
        "survey": job.get("survey", {}),
        "agents": job.get("agents", []),
        "models": job.get("models", []),
        "scenarios": job.get("scenarios", []),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def agent_study_job_id_from_results(results: dict[str, Any]) -> str:
    rows = []
    for row in results.get("data", []):
        question_to_attributes = row.get("question_to_attributes", {})
        rows.append(
            {
                "agent": row.get("agent", {}),
                "scenario": row.get("scenario", {}),
                "model": row.get("model", {}),
                "question_to_attributes": question_to_attributes,
                "answer_keys": sorted((row.get("answer") or {}).keys()),
            }
        )
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_edsl_agent_study_job_dict(args: argparse.Namespace) -> dict[str, Any]:
    agent_list_dict = read_json_or_gzip(Path(args.agent_list))
    agent_list_summary = inspect_agent_list_dict(agent_list_dict)
    question_spec = load_question_spec_from_args(args)
    required = ["question_name", "question_type", "question_text"]
    missing = [key for key in required if not question_spec.get(key)]
    if missing:
        raise ZwillError("invalid_input", "Agent-study question is missing required fields.", context={"missing": missing})

    AgentList, Jobs, Model, ModelList, Question, Survey = load_edsl_agent_study_classes()
    agent_list = AgentList.from_dict(agent_list_dict)
    question = edsl_question_from_zwill(question_spec, Question)
    model_params = parse_model_params(args)
    job = Jobs(
        survey=Survey(questions=[question]),
        agents=agent_list,
        models=ModelList(
            [
                Model(
                    model_name=model_name,
                    service_name=service_name,
                    **model_kwargs_for(model_name, service_name, model_params),
                )
                for model_name, service_name in parse_model_specs(args)
            ]
        ),
    )
    data = job.to_dict()
    data["zwill"] = {
        "agent_study_job_id": agent_study_job_id_from_job(data),
        "agent_list_path": args.agent_list,
        "question_name": question_spec["question_name"],
        "agent_count": agent_list_summary["agent_count"],
        "agent_list": agent_list_dict.get("zwill", {}),
    }
    return data


def cmd_agent_study_export(args: argparse.Namespace) -> None:
    export_dict = build_edsl_agent_study_job_dict(args)
    output = json.dumps(export_dict, indent=2)
    if args.path:
        path = Path(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n")
    print(output)


def agent_study_question_name(row: dict[str, Any]) -> str | None:
    answer = row.get("answer") or {}
    question_to_attributes = row.get("question_to_attributes") or {}
    for key in question_to_attributes:
        if key in answer:
            return key
    for key in answer:
        if not key.endswith("_comment"):
            return key
    return None


def read_agent_study_manifest() -> list[dict[str, Any]]:
    manifest = read_json(agent_study_manifest_path(), {"runs": []})
    runs = manifest.get("runs", [])
    return runs if isinstance(runs, list) else []


def write_agent_study_manifest(runs: list[dict[str, Any]]) -> None:
    write_json(agent_study_manifest_path(), {"runs": runs})


def upsert_agent_study_manifest(run: dict[str, Any]) -> None:
    runs = [item for item in read_agent_study_manifest() if item.get("job_id") != run.get("job_id")]
    runs.append(run)
    write_agent_study_manifest(sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True))


def agent_study_import_metadata(job_id: str) -> dict[str, Any]:
    return read_json(agent_study_dir(job_id) / "import.json", {})


def cmd_agent_study_import(args: argparse.Namespace) -> dict[str, Any]:
    require_project()
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    job_id = args.job_id or results.get("zwill", {}).get("agent_study_job_id") or agent_study_job_id_from_results(results)
    jdir = agent_study_dir(job_id)
    if jdir.exists() and not args.replace:
        raise ZwillError("already_exists", f"Agent study results already imported for job id {job_id}.", hint="Use --replace.")
    if jdir.exists():
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)

    existing = [row for row in read_jsonl(agent_study_answers_path()) if row.get("job_id") != job_id]
    extracted = []
    issues = []
    for index, row in enumerate(results.get("data", [])):
        question_name = agent_study_question_name(row)
        answer = row.get("answer") or {}
        if not question_name:
            issues.append({"row": index, "error": "missing_answer_question"})
            continue
        agent = row.get("agent", {})
        model = row.get("model", {})
        question_attributes = (row.get("question_to_attributes") or {}).get(question_name, {})
        raw_model_response = row.get("raw_model_response", {})
        prompt = row.get("prompt", {})
        system_prompt = (prompt.get(f"{question_name}_system_prompt") or {}).get("text")
        user_prompt = (prompt.get(f"{question_name}_user_prompt") or {}).get("text")
        extracted.append(
            {
                "job_id": job_id,
                "row": index,
                "agent_name": agent.get("name"),
                "traits": agent.get("traits", {}),
                "agent_instruction": agent.get("instruction"),
                "instruction_present": bool(agent.get("instruction")),
                "instruction_chars": len(agent.get("instruction") or ""),
                "question_name": question_name,
                "question_text": question_attributes.get("question_text"),
                "question_type": question_attributes.get("question_type"),
                "question_options": question_attributes.get("question_options", []),
                "answer": answer.get(question_name),
                "comment": answer.get(f"{question_name}_comment") or (row.get("comments_dict") or {}).get(f"{question_name}_comment"),
                "model": model.get("model"),
                "service": model.get("inference_service"),
                "model_label": model_label(model.get("inference_service"), model.get("model")),
                "model_parameters": model.get("parameters", {}),
                "scenario": row.get("scenario", {}),
                "prompt": prompt,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "raw_model_response": raw_model_response,
                "generated_tokens": row.get("generated_tokens", {}),
                "comments_dict": row.get("comments_dict", {}),
                "reasoning_summaries_dict": row.get("reasoning_summaries_dict", {}),
                "cache_used_dict": row.get("cache_used_dict", {}),
                "validated_dict": row.get("validated_dict", {}),
                "source_raw": str(stored_raw),
                "imported_at": utc_now(),
            }
        )

    rewrite_jsonl(agent_study_answers_path(), existing + extracted)
    metadata = {
        "job_id": job_id,
        "source_path": str(source),
        "source_hash": sha256(source),
        "stored_path": str(stored_raw),
        "stored_hash": sha256(stored_raw),
        "row_count": len(results.get("data", [])),
        "extracted_count": len(extracted),
        "issue_count": len(issues),
        "issues": issues,
        "question_names": sorted({row.get("question_name") for row in extracted if row.get("question_name")}),
        "models": sorted({row.get("model_label") for row in extracted if row.get("model_label")}),
        "imported_at": utc_now(),
    }
    write_json(jdir / "import.json", metadata)
    upsert_agent_study_manifest(
        {
            "job_id": job_id,
            "status": "imported",
            "created_at": metadata["imported_at"],
            "results_path": str(source),
            "stored_raw": str(stored_raw),
            "row_count": metadata["row_count"],
            "extracted_count": metadata["extracted_count"],
            "issue_count": metadata["issue_count"],
            "question_names": metadata["question_names"],
            "models": metadata["models"],
        }
    )
    return envelope(
        "zwill agent-study import",
        "ok",
        {
            "job_id": job_id,
            "stored_raw": str(stored_raw),
            "row_count": metadata["row_count"],
            "extracted_count": metadata["extracted_count"],
            "issue_count": metadata["issue_count"],
            "issues": issues,
        },
        next_steps=[f"zwill agent-study report --job-id {job_id}"],
    )


def build_agent_study_report(rows: list[dict[str, Any]], job_id: str | None = None) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    distributions: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_model[row.get("model_label", "")].append(row)
        by_question[row.get("question_name", "")].append(row)
        distributions[f"{row.get('question_name', '')}::{row.get('model_label', '')}"][str(row.get("answer"))] += 1
    return {
        "job_id": job_id,
        "rows": rows,
        "summary": {
            "row_count": len(rows),
            "agent_count": len({row.get("agent_name") for row in rows}),
            "question_count": len({row.get("question_name") for row in rows}),
            "model_count": len({row.get("model_label") for row in rows}),
            "by_model": {model: {"rows": len(model_rows)} for model, model_rows in sorted(by_model.items())},
            "by_question": {question: {"rows": len(question_rows)} for question, question_rows in sorted(by_question.items())},
            "answer_distributions": {key: dict(counter) for key, counter in sorted(distributions.items())},
        },
        "health": {"import": agent_study_import_metadata(job_id)} if job_id else {},
    }


def render_agent_study_report_html(payload: dict[str, Any]) -> str:
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    body_rows = "\n".join(
        "<tr>"
        f"<td>{html_escape(row.get('agent_name'))}</td>"
        f"<td>{html_escape(row.get('question_name'))}</td>"
        f"<td>{html_escape(row.get('answer'))}</td>"
        f"<td>{html_escape(row.get('model_label'))}</td>"
        f"<td>{html_escape(row.get('comment') or '')}</td>"
        "</tr>"
        for row in rows
    )
    data = escape_script_text(json.dumps(payload))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Agent Study Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d8dee8; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f6f8; }}
    .summary {{ display: flex; gap: 16px; margin: 20px 0; }}
    .summary div {{ border: 1px solid #d8dee8; padding: 12px; border-radius: 6px; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <h1>Agent Study Report</h1>
  <div class="summary">
    <div><strong>Rows</strong><br>{summary.get('row_count', 0)}</div>
    <div><strong>Agents</strong><br>{summary.get('agent_count', 0)}</div>
    <div><strong>Questions</strong><br>{summary.get('question_count', 0)}</div>
    <div><strong>Models</strong><br>{summary.get('model_count', 0)}</div>
  </div>
  <table>
    <thead><tr><th>Agent</th><th>Question</th><th>Answer</th><th>Model</th><th>Comment</th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
  <script type="application/json" id="agent-study-data">{data}</script>
</body>
</html>
"""


def html_escape(value: Any) -> str:
    import html

    return html.escape(str(value), quote=True)


def cmd_agent_study_report(args: argparse.Namespace) -> None:
    require_project()
    rows = read_jsonl(agent_study_answers_path())
    if args.job_id:
        rows = [row for row in rows if row.get("job_id") == args.job_id]
    if args.model:
        rows = [row for row in rows if row.get("model") == args.model or row.get("model_label") == args.model]
    if not rows:
        raise ZwillError("not_found", "No agent-study answers found for the requested filters.")
    payload = build_agent_study_report(rows, args.job_id)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    fieldnames = ["job_id", "agent_name", "question_name", "answer", "comment", "service", "model", "model_label", "instruction_present", "instruction_chars"]
    if args.format == "csv":
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
    if args.format == "html":
        output = render_agent_study_report_html(payload)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output)
        else:
            print(output)
        return
    table = Table(title="agent study answers")
    for column in ["agent", "question", "answer", "model"]:
        table.add_column(column)
    for row in rows:
        table.add_row(str(row.get("agent_name", "")), str(row.get("question_name", "")), str(row.get("answer", "")), str(row.get("model_label", "")))
    Console().print(table)


def cmd_agent_study_list(args: argparse.Namespace) -> None:
    require_project()
    runs = read_agent_study_manifest()
    if args.format == "json":
        print(json.dumps({"runs": runs}, indent=2))
        return
    table = Table(title="agent studies")
    for column in ["job_id", "status", "created_at", "rows", "issues", "questions", "models"]:
        table.add_column(column)
    for run in runs:
        table.add_row(
            str(run.get("job_id", "")),
            str(run.get("status", "")),
            str(run.get("created_at", "")),
            str(run.get("extracted_count", "")),
            str(run.get("issue_count", "")),
            ", ".join(run.get("question_names", [])),
            ", ".join(run.get("models", [])),
        )
    Console().print(table)


def cmd_agent_study_show(args: argparse.Namespace) -> dict[str, Any]:
    require_project()
    run = next((item for item in read_agent_study_manifest() if item.get("job_id") == args.job_id), None)
    if run is None:
        metadata = agent_study_import_metadata(args.job_id)
        if not metadata:
            raise ZwillError("not_found", f"No agent study found for job id {args.job_id}.")
        run = {"job_id": args.job_id, "status": "imported", "import": metadata}
    rows = [row for row in read_jsonl(agent_study_answers_path()) if row.get("job_id") == args.job_id]
    data = {"run": run, "import": agent_study_import_metadata(args.job_id), "row_count": len(rows)}
    if args.include_summary and rows:
        data["summary"] = build_agent_study_report(rows, args.job_id)["summary"]
    return envelope("zwill agent-study show", "ok", data)


def probability_job_builder_deps() -> ProbabilityJobBuilderDeps:
    return ProbabilityJobBuilderDeps(
        require_survey=require_survey,
        selected_question_names=selected_question_names,
        context_path=context_path,
        load_edsl_job_classes=load_edsl_job_classes,
        option_key=option_key,
        parse_model_params=parse_model_params,
        parse_model_specs=parse_model_specs,
        model_kwargs_for=model_kwargs_for,
    )


def build_edsl_probability_job_dict(survey_name: str, args: argparse.Namespace) -> dict[str, Any]:
    return build_edsl_probability_job_dict_impl(survey_name, args, probability_job_builder_deps())


def respondent_selection(args: argparse.Namespace, all_respondent_ids: list[str]) -> list[str]:
    selected: list[str]
    if getattr(args, "respondent", None) or getattr(args, "respondents", None):
        selected = []
        for respondent_id in args.respondent or []:
            selected.append(respondent_id)
        if args.respondents:
            selected.extend(value.strip() for value in args.respondents.split(",") if value.strip())
    else:
        selected = all_respondent_ids[:]
    unknown = [respondent_id for respondent_id in selected if respondent_id not in all_respondent_ids]
    if unknown:
        raise ZwillError(
            "invalid_input",
            "Unknown respondent selected for digital twin export.",
            context={"unknown_respondents": unknown},
        )
    if (
        getattr(args, "sample_respondents", None) is not None
        and not getattr(args, "balance_actual", False)
        and not getattr(args, "stratify_actual", False)
    ):
        if args.sample_respondents < 0:
            raise ZwillError("invalid_input", "--sample-respondents must be non-negative.")
        rng = random.Random(args.seed)
        selected = rng.sample(selected, min(args.sample_respondents, len(selected)))
    if args.limit_respondents is not None:
        selected = selected[: args.limit_respondents]
    return selected


def digital_twin_job_builder_deps() -> DigitalTwinJobBuilderDeps:
    return DigitalTwinJobBuilderDeps(
        require_survey=require_survey,
        selected_question_names=selected_question_names,
        respondent_selection=respondent_selection,
        context_question_options=context_question_options,
        context_path=context_path,
        load_edsl_job_classes=load_edsl_job_classes,
        load_twin_material=load_twin_material,
        selected_agent_material_kinds=selected_agent_material_kinds,
        selected_agent_material_tags=selected_agent_material_tags,
        select_agent_material=select_agent_material,
        format_agent_material=format_agent_material,
        matching_twin_material=matching_twin_material,
        format_twin_material=format_twin_material,
        twin_material_paths=twin_material_paths,
        option_key=option_key,
        parse_model_params=parse_model_params,
        parse_model_specs=parse_model_specs,
        model_kwargs_for=model_kwargs_for,
    )


def build_edsl_digital_twin_job_dict(survey_name: str, args: argparse.Namespace) -> dict[str, Any]:
    return build_edsl_digital_twin_job_dict_impl(survey_name, args, digital_twin_job_builder_deps())


def rank_utility_question_text() -> str:
    return """You are acting as a digital twin for one survey respondent.

Survey name:
{{ survey_name }}

Survey context:
{{ survey_context }}

Respondent id:
{{ respondent_id }}

Non-survey agent construction material:
{{ agent_material_text }}

Supplemental twin material:
{{ twin_material_text }}

Observed answers from this respondent:
{{ observed_answers_text }}

Held-out rank task:
{{ rank_task_text }}

Rank direction:
{{ rank_direction_text }}

Items to score:
{{ rank_items_text }}

Estimate this respondent's latent appeal or utility score for each item on a 0-100 scale.

Use the full scale:
- 0 means no appeal to this respondent.
- 50 means neutral/moderate appeal.
- 100 means strongest appeal among this kind of item.

Scores may be close or tied if the respondent would see items as similarly appealing.
The implied ranking is obtained by sorting items from highest score to lowest score.

Return only valid JSON. Do not include markdown fences, prose, or comments.

The JSON must have exactly this shape:
{
  "scores": {
    "item_id_1": 78,
    "item_id_2": 72
  },
  "confidence": 0.64,
  "notes": "Brief respondent-level explanation."
}"""


def build_edsl_rank_utility_twin_job_dict(survey_name: str, args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    question_by_name = {question["question_name"]: question for question in questions}
    tasks = detect_rank_tasks(questions)
    try:
        selected_tasks = selected_rank_tasks(args, tasks)
    except ValueError as exc:
        detail = str(exc)
        context = None
        if detail.startswith("{"):
            try:
                context = json.loads(detail)
            except json.JSONDecodeError:
                context = None
        raise ZwillError(
            "invalid_input",
            "Unknown or missing rank task selection.",
            context=context or {"error": detail, "available_rank_tasks": [task["rank_task_id"] for task in tasks]},
            hint="Use --rank-task-id, or pass one item-level source question with --heldout-question.",
        ) from exc
    if not selected_tasks:
        raise ZwillError("invalid_input", "No rank tasks selected.")

    context_args = type("ContextArgs", (), {})()
    context_args.question = args.context_question
    context_args.questions = args.context_questions
    context_args.exclude_question = args.exclude_context_question or []
    context_question_names = selected_question_names(context_args, questions)
    all_rank_item_names = {name for task in selected_tasks for name in task.get("source_question_names", [])}
    context_question_names = [name for name in context_question_names if name not in all_rank_item_names]

    all_respondent_ids = [row["respondent_id"] for row in read_jsonl(sdir / "respondents.jsonl")]
    if not all_respondent_ids:
        all_respondent_ids = sorted({row["respondent_id"] for row in read_jsonl(sdir / "answers.jsonl")})
    respondent_ids = respondent_selection(args, all_respondent_ids)
    answer_by_respondent: dict[str, dict[str, str]] = defaultdict(dict)
    for answer in read_jsonl(sdir / "answers.jsonl"):
        if answer.get("answer") is None:
            continue
        answer_by_respondent[answer["respondent_id"]][answer["question"]] = answer["answer"]
    counts_by_question = answer_commonness_by_question(answer_by_respondent)
    if args.complete_cases:
        required = set(context_question_names) | all_rank_item_names
        respondent_ids = [respondent_id for respondent_id in respondent_ids if required.issubset(answer_by_respondent.get(respondent_id, {}))]

    context_file = context_path(sdir)
    context_text = context_file.read_text().strip() if context_file.exists() else ""
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = load_edsl_job_classes()
    rank_question = QuestionFreeText(question_name=args.job_question_name, question_text=rank_utility_question_text())
    all_twin_material = load_twin_material(args)
    scenarios = []
    skipped_missing = []
    prompt_variant = getattr(args, "prompt_variant", "raw") or "raw"
    for task in selected_tasks:
        task_item_ids = [item["item_id"] for item in task["items"]]
        for respondent_id in respondent_ids:
            respondent_answers = answer_by_respondent.get(respondent_id, {})
            actual_ranks = {}
            missing_items = []
            for item_id in task_item_ids:
                raw_rank = respondent_answers.get(item_id)
                if raw_rank is None:
                    missing_items.append(item_id)
                    continue
                match = re.match(r"^(\d+)", str(raw_rank).strip())
                if not match:
                    missing_items.append(item_id)
                    continue
                actual_ranks[item_id] = int(match.group(1))
            if missing_items and not getattr(args, "allow_missing_actual", False):
                skipped_missing.append({"respondent_id": respondent_id, "rank_task_id": task["rank_task_id"], "missing_items": missing_items})
                continue
            target_context = [name for name in context_question_names if name not in task_item_ids]
            selected_context = select_context_questions(respondent_answers, target_context, "", args.context_question_count)
            observed_answers = [
                {
                    "question_name": question_name,
                    "question_text": expand_question_text_fields(question_by_name[question_name]["question_text"], respondent_answers, question_by_name),
                    "question_options": context_question_options(question_by_name[question_name]),
                    "answer": respondent_answers[question_name],
                }
                for question_name in selected_context
            ]
            observed_lines = []
            for observed in observed_answers:
                observed_lines.append(
                    "\n".join(
                        [
                            f"Question: {observed['question_name']}",
                            f"Text: {observed['question_text']}",
                            "Options: " + "; ".join(observed["question_options"]),
                            f"Respondent answered: {observed['answer']}",
                            *(
                                [answer_commonness_text(observed["question_name"], observed["answer"], counts_by_question)]
                                if prompt_variant == "answer-commonness-confidence"
                                else []
                            ),
                        ]
                    )
                )
            agent_material = select_agent_material(sdir, [respondent_id], args) if getattr(args, "include_agent_material", False) else []
            twin_material = matching_twin_material(all_twin_material, survey_name=survey_name, heldout_question=task["rank_task_id"], respondent_id=respondent_id)
            rank_items_text = "\n".join(f"{item['item_id']}: {item['label']}" for item in task["items"])
            scenarios.append(
                Scenario(
                    {
                        "survey_name": survey_name,
                        "survey_context": context_text,
                        "respondent_id": respondent_id,
                        "rank_task_id": task["rank_task_id"],
                        "rank_task_text": task["rank_task_text"],
                        "rank_direction": task["rank_direction"],
                        "rank_direction_text": "1 is the most preferred/appealing item; larger ranks are lower.",
                        "rank_items": task["items"],
                        "rank_items_text": rank_items_text,
                        "actual_ranks": actual_ranks,
                        "observed_answers": observed_answers,
                        "observed_answers_text": "\n\n".join(observed_lines) if observed_lines else "No observed answers provided.",
                        "agent_material": agent_material,
                        "agent_material_text": format_agent_material(agent_material, getattr(args, "max_agent_material_chars", None)),
                        "twin_material": twin_material,
                        "twin_material_text": format_twin_material(twin_material, getattr(args, "max_twin_material_chars", None)),
                        "leakage_exclusions": task_item_ids,
                    }
                )
            )
    if not scenarios:
        raise ZwillError(
            "invalid_input",
            "No rank utility twin scenarios could be built.",
            context={"skipped_missing": skipped_missing[:10], "skipped_count": len(skipped_missing)},
        )
    model_params = parse_model_params(args)
    job = Jobs(
        survey=Survey(questions=[rank_question]),
        scenarios=ScenarioList(scenarios),
        models=ModelList(
            [
                Model(model_name=model_name, service_name=service_name, **model_kwargs_for(model_name, service_name, model_params))
                for model_name, service_name in parse_model_specs(args)
            ]
        ),
    )
    data = job.to_dict()
    data["zwill"] = {
        "rank_utility_twin_job_id": rank_job_id_from_job(data),
        "rank_task_ids": [task["rank_task_id"] for task in selected_tasks],
        "rank_task_count": len(selected_tasks),
        "rank_item_count": sum(len(task["items"]) for task in selected_tasks),
        "context_question_count": args.context_question_count,
        "sample_respondents": args.sample_respondents,
        "seed": args.seed,
        "complete_cases": args.complete_cases,
        "include_agent_material": getattr(args, "include_agent_material", False),
        "twin_material_paths": twin_material_paths(args),
        "twin_material_count": len(all_twin_material),
        "allow_missing_actual": getattr(args, "allow_missing_actual", False),
        "prompt_variant": prompt_variant,
        "scenario_count": len(scenarios),
        "skipped_missing_rank_item_count": len(skipped_missing),
    }
    return data


def cmd_twin_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    if results.get("zwill", {}).get("rank_utility_twin_job_id"):
        return cmd_rank_results_import(args)

    job_id = args.job_id or results.get("zwill", {}).get("digital_twin_job_id") or digital_twin_job_id_from_results(results)
    jdir = digital_twin_jobs_dir(sdir) / job_id
    if jdir.exists() and not args.replace:
        raise ZwillError(
            "already_exists",
            f"Digital twin results already imported for job id {job_id}.",
            hint="Use --replace to overwrite this import.",
        )
    if jdir.exists():
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)
    truth_path = sdir / "committed" / "truth_marginals.json"
    truth = read_json(truth_path, {}) if truth_path.exists() else {}

    existing = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") != job_id]
    imported_at = utc_now()
    extracted, issues = extract_twin_prediction_rows(
        results,
        job_id=job_id,
        survey=args.survey,
        stored_raw=str(stored_raw),
        imported_at=imported_at,
        truth=truth,
        allow_missing_actual=getattr(args, "allow_missing_actual", False),
    )

    rewrite_jsonl(digital_twin_predictions_path(sdir), existing + extracted)
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
        "zwill twin-results import",
        "ok",
        {
            "job_id": job_id,
            "stored_raw": str(stored_raw),
            "row_count": len(results.get("data", [])),
            "extracted_count": len(extracted),
            "issue_count": len(issues),
            "issues": issues,
        },
        next_steps=[
            f"zwill twin-results export --survey {args.survey} --job-id {job_id} --path predictions.csv"
            if getattr(args, "allow_missing_actual", False)
            else f"zwill twin-results report --survey {args.survey} --job-id {job_id}"
        ],
    )


def cmd_rank_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    job_id = args.job_id or results.get("zwill", {}).get("rank_utility_twin_job_id") or rank_job_id_from_results(results)
    jdir = rank_twin_jobs_dir(sdir) / job_id
    if jdir.exists() and not args.replace:
        raise ZwillError("already_exists", f"Rank utility results already imported for job id {job_id}.", hint="Use --replace.")
    if jdir.exists():
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)
    imported_at = utc_now()
    extracted = []
    issues = []
    question_name = getattr(args, "job_question_name", None) or "rank_utility_scores"
    for index, row in enumerate(results.get("data", [])):
        scenario = row.get("scenario", {}) or {}
        model = row.get("model", {}) or {}
        scores, confidence, notes, error = extract_rank_payload(row, question_name=question_name)
        item_ids = [str(item.get("item_id")) for item in scenario.get("rank_items", []) if item.get("item_id")]
        actual_ranks = {str(key): int(value) for key, value in (scenario.get("actual_ranks") or {}).items() if value is not None}
        missing_scores = [item_id for item_id in item_ids if scores is not None and item_id not in scores]
        missing_actual = [item_id for item_id in item_ids if item_id not in actual_ranks]
        if missing_scores:
            error = error or "missing_item_scores"
        if missing_actual and not getattr(args, "allow_missing_actual", False):
            error = error or "missing_actual_ranks"
        if error:
            issues.append(
                {
                    "row": index,
                    "respondent_id": scenario.get("respondent_id"),
                    "rank_task_id": scenario.get("rank_task_id"),
                    "model": model_label(model.get("inference_service"), model.get("model")),
                    "error": error,
                    "missing_scores": missing_scores,
                    "missing_actual": missing_actual,
                }
            )
            continue
        scored_item_ids = [item_id for item_id in item_ids if scores and item_id in scores and item_id in actual_ranks]
        metrics = rank_metrics(actual_ranks, scores or {}, scored_item_ids) if scored_item_ids else {"predicted_ranks": {}}
        extracted.append(
            {
                "job_id": job_id,
                "row": index,
                "survey": args.survey,
                "respondent_id": scenario.get("respondent_id"),
                "rank_task_id": scenario.get("rank_task_id"),
                "rank_task_text": scenario.get("rank_task_text"),
                "rank_direction": scenario.get("rank_direction"),
                "items": scenario.get("rank_items", []),
                "item_count": len(item_ids),
                "actual_ranks": actual_ranks,
                "predicted_scores": scores or {},
                "predicted_ranks": metrics.get("predicted_ranks", {}),
                "model": model.get("model"),
                "service": model.get("inference_service"),
                "model_label": model_label(model.get("inference_service"), model.get("model")),
                "model_parameters": model.get("parameters", {}),
                "observed_answers": scenario.get("observed_answers", []),
                "twin_material": scenario.get("twin_material", []),
                "notes": notes,
                "confidence": confidence,
                "source_raw": str(stored_raw),
                "imported_at": imported_at,
                **{key: value for key, value in metrics.items() if key != "predicted_ranks"},
            }
        )
    existing = [row for row in read_jsonl(rank_twin_predictions_path(sdir)) if row.get("job_id") != job_id]
    rewrite_jsonl(rank_twin_predictions_path(sdir), existing + extracted)
    metadata = {
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
        "rank_task_ids": sorted({row.get("rank_task_id") for row in extracted if row.get("rank_task_id")}),
        "models": sorted({row.get("model_label") for row in extracted if row.get("model_label")}),
        "imported_at": imported_at,
    }
    write_json(jdir / "import.json", metadata)
    return envelope(
        "zwill twin-results import",
        "ok",
        {
            "job_id": job_id,
            "stored_raw": str(stored_raw),
            "row_count": metadata["row_count"],
            "extracted_count": metadata["extracted_count"],
            "issue_count": metadata["issue_count"],
            "issues": issues,
        },
        next_steps=[f"zwill twin-results rank-report --survey {args.survey} --job-id {job_id}"],
    )


def render_rank_report_html(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    model_rows = "".join(
        "<tr>"
        f"<td>{html_escape(model)}</td>"
        f"<td class=\"num\">{values.get('rows', 0)}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_spearman'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_pairwise_order_accuracy'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_top_3_overlap'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_absolute_rank_error'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('top_1_hit_rate'))}</td>"
        "</tr>"
        for model, values in (summary.get("by_model") or {}).items()
    )
    task_rows = "".join(
        "<tr>"
        f"<td>{html_escape(task)}</td>"
        f"<td class=\"num\">{values.get('rows', 0)}</td>"
        f"<td class=\"num\">{values.get('item_count') or ''}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_spearman'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_pairwise_order_accuracy'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_top_3_overlap'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_absolute_rank_error'))}</td>"
        "</tr>"
        for task, values in (summary.get("by_task") or {}).items()
    )
    item_rows = "".join(
        "<tr>"
        f"<td>{html_escape(row.get('rank_task_id'))}</td>"
        f"<td><b>{html_escape(row.get('item_id'))}</b><span>{html_escape(row.get('label'))}</span></td>"
        f"<td class=\"num\">{fmt_optional(row.get('mean_actual_rank'))}</td>"
        f"<td class=\"num\">{fmt_optional(row.get('mean_predicted_rank'))}</td>"
        f"<td class=\"num\">{fmt_optional(row.get('mean_predicted_score'))}</td>"
        f"<td class=\"num\">{fmt_optional(row.get('mean_rank_error'))}</td>"
        "</tr>"
        for row in payload.get("items", [])
    )
    data = escape_script_text(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rank Utility Twin Validation</title>
  <style>
    {EP_REPORT_CSS}
    body {{ max-width:1180px; }}
    section {{ border:1px solid var(--ep-border); border-radius:8px; padding:18px; margin-bottom:18px; background:#fff; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ border-bottom:1px solid #edf0f4; padding:8px; text-align:left; vertical-align:top; }}
    th {{ background:var(--ep-green); color:#fff; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
    td span {{ display:block; color:var(--ep-gray); font-size:12px; margin-top:3px; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <main>
    <h1>Rank Utility Twin Validation</h1>
    <p class="subtle">Joint rank-battery validation using latent utility scores, not independent categorical rank labels.</p>
    <section><h2>Summary</h2><table><tbody>
      <tr><th>Rows</th><td class="num">{summary.get('row_count', 0)}</td><th>Respondents</th><td class="num">{summary.get('respondent_count', 0)}</td></tr>
      <tr><th>Rank tasks</th><td class="num">{summary.get('task_count', 0)}</td><th>Models</th><td class="num">{summary.get('model_count', 0)}</td></tr>
    </tbody></table></section>
    <section><h2>Individual Rank Performance</h2><table><thead><tr><th>Model</th><th class="num">Rows</th><th class="num">Mean Spearman</th><th class="num">Pairwise order accuracy</th><th class="num">Top-3 overlap</th><th class="num">Rank MAE</th><th class="num">Top-1 hit rate</th></tr></thead><tbody>{model_rows}</tbody></table></section>
    <section><h2>Rank Battery Summary</h2><table><thead><tr><th>Rank task</th><th class="num">Rows</th><th class="num">Items</th><th class="num">Mean Spearman</th><th class="num">Pairwise order accuracy</th><th class="num">Top-3 overlap</th><th class="num">Rank MAE</th></tr></thead><tbody>{task_rows}</tbody></table></section>
    <section><h2>Item-Level Diagnostics</h2><table><thead><tr><th>Task</th><th>Item</th><th class="num">Actual avg rank</th><th class="num">Predicted avg rank</th><th class="num">Predicted avg score</th><th class="num">Rank error</th></tr></thead><tbody>{item_rows}</tbody></table></section>
  </main>
  <script type="application/json" id="rank-report-data">{data}</script>
</body>
</html>"""


def fmt_optional(value: Any, precision: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return str(value)


def cmd_rank_results_report(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    rows = read_jsonl(rank_twin_predictions_path(sdir))
    if args.job_id:
        rows = [row for row in rows if row.get("job_id") == args.job_id]
    if args.model:
        rows = [row for row in rows if row.get("model") == args.model or row.get("model_label") == args.model]
    if args.rank_task_id:
        selected = set(args.rank_task_id)
        rows = [row for row in rows if row.get("rank_task_id") in selected]
    if not rows:
        raise ZwillError("not_found", "No rank utility predictions found for the requested filters.")
    payload = build_rank_report(rows, args.job_id)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_rank_report_html(payload)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output)
        else:
            print(output)
        return
    if args.format == "csv":
        fieldnames = ["job_id", "respondent_id", "rank_task_id", "model_label", "spearman", "pairwise_order_accuracy", "top_3_overlap", "mean_absolute_rank_error", "top_1_hit"]
        writer_target = Path(args.path) if args.path else None
        if writer_target:
            writer_target.parent.mkdir(parents=True, exist_ok=True)
            with writer_target.open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows({key: row.get(key) for key in fieldnames} for row in rows)
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows({key: row.get(key) for key in fieldnames} for row in rows)
        return
    table = Table(title=f"{args.survey} rank utility validation")
    for column in ["model", "rows", "spearman", "pairwise", "top-3", "rank mae", "top-1"]:
        table.add_column(column)
    for model, values in payload["summary"]["by_model"].items():
        table.add_row(
            model,
            str(values.get("rows", 0)),
            fmt_optional(values.get("mean_spearman")),
            fmt_optional(values.get("mean_pairwise_order_accuracy")),
            fmt_optional(values.get("mean_top_3_overlap")),
            fmt_optional(values.get("mean_absolute_rank_error")),
            fmt_optional(values.get("top_1_hit_rate")),
        )
    Console().print(table)


def selected_questions_arg(args: argparse.Namespace) -> set[str]:
    questions = set()
    for value in getattr(args, "question", None) or []:
        questions.add(str(value))
    if getattr(args, "questions", None):
        questions.update(item.strip() for item in str(args.questions).split(",") if item.strip())
    return questions


def default_calibrated_twin_job_id(source_job_id: str, target_kind: str, target_id: str | None, model: str | None) -> str:
    payload = {
        "source_job_id": source_job_id,
        "target_kind": target_kind,
        "target_id": target_id,
        "model": model,
        "method": "kl_ipf",
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
    readable_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", target_id or target_kind).strip("_")[:32]
    return f"{source_job_id}_calibrated_{readable_target}_{digest}"


def probability_job_targets(sdir: Path, job_id: str, model: str | None = None) -> dict[str, dict[str, float]]:
    rows = [row for row in read_jsonl(probability_predictions_path(sdir)) if row.get("job_id") == job_id]
    if model:
        rows = [row for row in rows if row.get("model") == model or row.get("model_label") == model]
    if not rows:
        raise ZwillError("not_found", "No probability predictions found for target job.", context={"target_job_id": job_id})
    targets: dict[str, dict[str, float]] = {}
    duplicates = []
    for row in rows:
        question = row.get("question")
        if not question:
            continue
        if question in targets:
            duplicates.append(question)
            continue
        targets[str(question)] = {str(option): float(value) for option, value in row.get("probabilities", {}).items()}
    if duplicates:
        raise ZwillError(
            "invalid_input",
            "Target probability job has multiple rows for a question; specify --target-model or use a single-model target job.",
            context={"duplicates": sorted(set(duplicates))},
        )
    return targets


def empirical_marginal_targets(sdir: Path) -> dict[str, dict[str, float]]:
    truth_path = sdir / "committed" / "truth_marginals.json"
    truth = read_json(truth_path, {}) if truth_path.exists() else {}
    raw_marginals = truth.get("marginals", {}) if isinstance(truth, dict) else {}
    targets = {}
    for question, values in raw_marginals.items():
        total = sum(float(item.get("weighted_count", item.get("count", 0.0))) for item in values.values())
        if total <= 0:
            continue
        targets[question] = {
            str(option): float(item.get("weighted_count", item.get("count", 0.0))) / total
            for option, item in values.items()
        }
    return targets


def cmd_twin_results_calibrate_marginal(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    source_rows = [row for row in all_rows if row.get("job_id") == args.job_id]
    if args.model:
        source_rows = [
            row for row in source_rows
            if row.get("model") == args.model or row.get("model_label") == args.model
        ]
    selected_questions = selected_questions_arg(args)
    if selected_questions:
        source_rows = [row for row in source_rows if row.get("heldout_question") in selected_questions]
    if not source_rows:
        raise ZwillError("not_found", "No source digital twin predictions found for the requested filters.")

    target_kind = args.target
    target_id = None
    if target_kind == "probability-job":
        target_id = args.target_job_id or args.target_probability_job_id
        if not target_id:
            raise ZwillError("invalid_input", "--target-job-id is required when --target probability-job.")
        targets = probability_job_targets(sdir, target_id, args.target_model)
    elif target_kind == "empirical":
        targets = empirical_marginal_targets(sdir)
    else:
        raise ZwillError("invalid_input", f"Unsupported calibration target: {target_kind}.")

    output_job_id = args.output_job_id or default_calibrated_twin_job_id(args.job_id, target_kind, target_id, args.model)
    jdir = digital_twin_jobs_dir(sdir) / output_job_id
    if jdir.exists() and not args.replace:
        raise ZwillError(
            "already_exists",
            f"Calibrated digital twin result set already exists for job id {output_job_id}.",
            hint="Use --replace to overwrite this derived result set.",
        )
    if jdir.exists():
        shutil.rmtree(jdir)
    jdir.mkdir(parents=True, exist_ok=True)

    truth_path = sdir / "committed" / "truth_marginals.json"
    truth = read_json(truth_path, {}) if truth_path.exists() else {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        label = row.get("model_label") or model_label(row.get("service"), row.get("model"))
        grouped[(str(row.get("heldout_question")), label)].append(row)

    derived_rows = []
    issues = []
    diagnostics = []
    for (question, label), group_rows in sorted(grouped.items()):
        target = targets.get(question)
        if target is None:
            issues.append({"heldout_question": question, "model": label, "error": "missing_target_marginal"})
            continue
        try:
            adjusted_probabilities, calibration = calibrate_probabilities_to_marginal(
                group_rows,
                target,
                max_iter=args.max_iter,
                tolerance=args.tolerance,
            )
        except ValueError as exc:
            issues.append({"heldout_question": question, "model": label, "error": str(exc)})
            continue
        diagnostics.append({"heldout_question": question, "model": label, "rows": len(group_rows), **calibration})
        for source_row, probabilities_by_option in zip(group_rows, adjusted_probabilities):
            row = dict(source_row)
            row["job_id"] = output_job_id
            row["source_job_id"] = args.job_id
            row["source_row"] = source_row.get("row")
            row["probabilities"] = probabilities_by_option
            row["raw_probabilities"] = [probabilities_by_option[option] for option in row.get("option_labels", [])]
            row["raw_probability_sum"] = sum(row["raw_probabilities"])
            row["model_label"] = source_row.get("model_label") or label
            row["calibration"] = {
                "method": "kl_ipf",
                "source_job_id": args.job_id,
                "target": target_kind,
                "target_job_id": target_id,
                "target_model": args.target_model,
            }
            row["notes"] = f"KL/IPF calibrated from {args.job_id} to {target_kind}" + (f" {target_id}" if target_id else "")
            metrics = one_hot_metrics(row.get("option_labels", []), row.get("actual_answer"), probabilities_by_option)
            row.update(metrics)
            marginal_probabilities = true_probabilities_for(question, truth, row.get("option_labels", [])) if truth else {}
            marginal_metrics = one_hot_metrics(row.get("option_labels", []), row.get("actual_answer"), marginal_probabilities) if marginal_probabilities else {}
            row["empirical_marginal_probabilities"] = marginal_probabilities
            row["empirical_marginal_probability_actual"] = marginal_metrics.get("probability_actual")
            row["empirical_marginal_negative_log_likelihood"] = marginal_metrics.get("negative_log_likelihood")
            row["empirical_marginal_brier"] = marginal_metrics.get("brier")
            row["empirical_marginal_top1_correct"] = marginal_metrics.get("top1_correct")
            row["marginal_probabilities"] = marginal_probabilities
            row["marginal_probability_actual"] = marginal_metrics.get("probability_actual")
            row["marginal_negative_log_likelihood"] = marginal_metrics.get("negative_log_likelihood")
            row["marginal_brier"] = marginal_metrics.get("brier")
            row["marginal_top1_correct"] = marginal_metrics.get("top1_correct")
            row["imported_at"] = utc_now()
            derived_rows.append(row)

    if issues and not derived_rows:
        raise ZwillError("invalid_input", "No calibrated rows were produced.", context={"issues": issues})

    predictions_path = digital_twin_predictions_path(sdir)
    with file_lock(predictions_path):
        latest_rows = read_jsonl(predictions_path)
        remaining = [row for row in latest_rows if row.get("job_id") != output_job_id]
        rewrite_jsonl(predictions_path, remaining + derived_rows)
    metadata = {
        "job_id": output_job_id,
        "survey": args.survey,
        "source_job_id": args.job_id,
        "target": target_kind,
        "target_job_id": target_id,
        "target_model": args.target_model,
        "method": "kl_ipf",
        "row_count": len(source_rows),
        "extracted_count": len(derived_rows),
        "issue_count": len(issues),
        "issues": issues,
        "diagnostics": diagnostics,
        "imported_at": utc_now(),
    }
    write_json(jdir / "import.json", metadata)
    upsert_twin_run_manifest(
        sdir,
        {
            "job_id": output_job_id,
            "survey": args.survey,
            "status": "calibrated",
            "created_at": utc_now(),
            "source_job_id": args.job_id,
            "target": target_kind,
            "target_job_id": target_id,
            "row_count": len(source_rows),
            "extracted_count": len(derived_rows),
            "issue_count": len(issues),
            "models": sorted({row.get("model_label") or model_label(row.get("service"), row.get("model")) for row in derived_rows}),
            "heldout_questions": sorted({row.get("heldout_question") for row in derived_rows if row.get("heldout_question")}),
        },
    )
    return envelope(
        "zwill twin-results calibrate-marginal",
        "ok",
        {
            "job_id": output_job_id,
            "source_job_id": args.job_id,
            "target": target_kind,
            "target_job_id": target_id,
            "row_count": len(source_rows),
            "extracted_count": len(derived_rows),
            "issue_count": len(issues),
            "issues": issues,
            "diagnostics": diagnostics,
        },
        next_steps=[f"zwill twin-results report --survey {args.survey} --job-id {output_job_id}"],
    )


def filtered_twin_prediction_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    sdir = require_survey(args.survey)
    rows = read_jsonl(digital_twin_predictions_path(sdir))
    job_ids = set()
    if getattr(args, "manifest", None):
        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            raise ZwillError("not_found", f"Twin results manifest does not exist: {manifest_path}.")
        job_ids.update(job_ids_from_manifest(manifest_path))
    for value in getattr(args, "job_id", None) or []:
        job_ids.add(str(value))
    if getattr(args, "jobs", None):
        job_ids.update(item.strip() for item in str(args.jobs).split(",") if item.strip())
    selected_questions = selected_questions_arg(args)
    return filter_prediction_rows(
        rows,
        job_ids=job_ids or None,
        model=getattr(args, "model", None),
        questions=selected_questions or None,
    )


def cmd_twin_results_export(args: argparse.Namespace) -> None:
    rows = filtered_twin_prediction_rows(args)
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    output_rows = twin_prediction_export_rows(rows, args.format)
    write_csv_rows(Path(args.path) if args.path else None, output_rows)


def cmd_twin_results_package(args: argparse.Namespace) -> dict[str, Any]:
    rows = filtered_twin_prediction_rows(args)
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    csv_path = Path(args.path)
    output_rows = twin_prediction_export_rows(rows, args.format)
    write_csv_rows(csv_path, output_rows)
    zip_path = Path(args.zip_path) if args.zip_path else csv_path.with_suffix(".zip")
    zip_csv(csv_path, zip_path)
    return envelope(
        "zwill twin-results package",
        "ok",
        {
            "csv_path": str(csv_path),
            "zip_path": str(zip_path),
            "prediction_rows": len(rows),
            "csv_rows": len(output_rows),
            "csv_size_bytes": csv_path.stat().st_size,
            "zip_size_bytes": zip_path.stat().st_size,
        },
    )


def cmd_twin_results_marginal_diagnostics(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    source_rows = filtered_twin_prediction_rows(args)
    if not source_rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    if args.target == "probability-job":
        if not args.target_job_id:
            raise ZwillError("invalid_input", "--target-job-id is required when --target probability-job.")
        targets = probability_job_targets(sdir, args.target_job_id, args.target_model)
        target_label = args.target_job_id
    else:
        targets = empirical_marginal_targets(sdir)
        target_label = "empirical"
    aggregates = aggregate_twin_marginals(source_rows)
    summary_rows = []
    option_rows = []
    issues = []
    for (question, label), aggregate in sorted(aggregates.items()):
        target = targets.get(question)
        if target is None:
            issues.append({"heldout_question": question, "model": label, "error": "missing_target_marginal"})
            continue
        predicted = aggregate["probabilities"]
        metrics = distribution_distance_metrics(predicted, target)
        predicted_top, predicted_top_probability = top_prediction(predicted)
        target_top, target_top_probability = top_prediction(target)
        summary_rows.append(
            {
                "survey": args.survey,
                "job_id": ",".join(sorted({str(row.get("job_id")) for row in source_rows if row.get("job_id")})),
                "target": args.target,
                "target_job_id": target_label,
                "heldout_question": question,
                "heldout_question_text": aggregate.get("question_text"),
                "model_label": label,
                "respondent_count": aggregate["respondent_count"],
                "predicted_top_option": predicted_top,
                "predicted_top_probability": predicted_top_probability,
                "target_top_option": target_top,
                "target_top_probability": target_top_probability,
                "top_option_agrees": int(predicted_top == target_top),
                **metrics,
            }
        )
        for option in sorted(set(predicted) | set(target)):
            option_rows.append(
                {
                    "survey": args.survey,
                    "heldout_question": question,
                    "heldout_question_text": aggregate.get("question_text"),
                    "model_label": label,
                    "option_label": option,
                    "predicted_probability": predicted.get(option, 0.0),
                    "target_probability": target.get(option, 0.0),
                    "difference": predicted.get(option, 0.0) - target.get(option, 0.0),
                    "abs_difference": abs(predicted.get(option, 0.0) - target.get(option, 0.0)),
                }
            )
    payload = {"summary": summary_rows, "options": option_rows, "issues": issues}
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).write_text(output + "\n")
        else:
            print(output)
        return
    if args.format == "csv":
        path = Path(args.path) if args.path else None
        fieldnames = list(summary_rows[0].keys()) if summary_rows else [
            "survey",
            "job_id",
            "target",
            "target_job_id",
            "heldout_question",
            "model_label",
            "respondent_count",
            "l1",
            "mae",
            "brier",
            "js_divergence",
        ]
        if path:
            with path.open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(summary_rows)
            if args.option_path:
                option_fieldnames = list(option_rows[0].keys()) if option_rows else [
                    "survey",
                    "heldout_question",
                    "model_label",
                    "option_label",
                    "predicted_probability",
                    "target_probability",
                    "difference",
                    "abs_difference",
                ]
                with Path(args.option_path).open("w", newline="") as output_file:
                    writer = csv.DictWriter(output_file, fieldnames=option_fieldnames)
                    writer.writeheader()
                    writer.writerows(option_rows)
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)
        return
    table = Table(title="Twin Marginal Diagnostics")
    for column in ["question", "model", "n", "target", "L1", "Brier", "JS", "top"]:
        table.add_column(column)
    for row in summary_rows:
        table.add_row(
            str(row["heldout_question"]),
            str(row["model_label"]),
            str(row["respondent_count"]),
            str(row["target_job_id"]),
            f"{row['l1']:.4f}",
            f"{row['brier']:.4f}",
            f"{row['js_divergence']:.4f}",
            "yes" if row["top_option_agrees"] else "no",
        )
    Console().print(table)


def build_twin_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def percentile(values: list[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * q
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    def top_prediction(row: dict[str, Any]) -> tuple[str | None, float]:
        predicted = row.get("probabilities", {})
        if not predicted:
            return None, 0.0
        option, probability = max(predicted.items(), key=lambda item: (float(item[1]), str(item[0])))
        return str(option), float(probability)

    def top_probability_option(probabilities: dict[str, float]) -> tuple[str | None, float | None]:
        if not probabilities:
            return None, None
        option, probability = max(probabilities.items(), key=lambda item: float(item[1]))
        return str(option), float(probability)

    def summarize(model_rows: list[dict[str, Any]]) -> dict[str, Any]:
        marginal_rows = [row for row in model_rows if row.get("empirical_marginal_probability_actual", row.get("marginal_probability_actual")) is not None]
        nll_values = [float(row["negative_log_likelihood"]) for row in model_rows]
        top_confidences = [top_prediction(row)[1] for row in model_rows]
        values = {
            "rows": len(model_rows),
            "mean_probability_actual": sum(row["probability_actual"] for row in model_rows) / len(model_rows),
            "mean_uniform_probability_actual": sum(row["uniform_probability_actual"] for row in model_rows) / len(model_rows),
            "mean_negative_log_likelihood": sum(row["negative_log_likelihood"] for row in model_rows) / len(model_rows),
            "negative_log_likelihood_p50": percentile(nll_values, 0.50),
            "negative_log_likelihood_p90": percentile(nll_values, 0.90),
            "negative_log_likelihood_p95": percentile(nll_values, 0.95),
            "negative_log_likelihood_max": max(nll_values),
            "mean_top_confidence": sum(top_confidences) / len(top_confidences),
            "mean_uniform_negative_log_likelihood": sum(row["uniform_negative_log_likelihood"] for row in model_rows) / len(model_rows),
            "mean_brier": sum(row["brier"] for row in model_rows) / len(model_rows),
            "mean_uniform_brier": sum(row["uniform_brier"] for row in model_rows) / len(model_rows),
            "mean_brier_improvement": sum(row["brier_improvement"] for row in model_rows) / len(model_rows),
            "top1_accuracy": sum(row["top1_correct"] for row in model_rows) / len(model_rows),
        }
        if marginal_rows:
            mean_empirical_marginal_probability_actual = (
                sum(row.get("empirical_marginal_probability_actual", row.get("marginal_probability_actual")) for row in marginal_rows)
                / len(marginal_rows)
            )
            mean_empirical_marginal_negative_log_likelihood = (
                sum(
                    row.get(
                        "empirical_marginal_negative_log_likelihood",
                        row.get("marginal_negative_log_likelihood"),
                    )
                    for row in marginal_rows
                )
                / len(marginal_rows)
            )
            mean_empirical_marginal_brier = (
                sum(row.get("empirical_marginal_brier", row.get("marginal_brier")) for row in marginal_rows) / len(marginal_rows)
            )
            empirical_marginal_top1_accuracy = (
                sum(row.get("empirical_marginal_top1_correct", row.get("marginal_top1_correct")) for row in marginal_rows)
                / len(marginal_rows)
            )
            values.update(
                {
                    "mean_empirical_marginal_probability_actual": mean_empirical_marginal_probability_actual,
                    "mean_empirical_marginal_negative_log_likelihood": mean_empirical_marginal_negative_log_likelihood,
                    "mean_empirical_marginal_brier": mean_empirical_marginal_brier,
                    "empirical_marginal_top1_accuracy": empirical_marginal_top1_accuracy,
                    "mean_marginal_probability_actual": mean_empirical_marginal_probability_actual,
                    "mean_marginal_negative_log_likelihood": mean_empirical_marginal_negative_log_likelihood,
                    "mean_marginal_brier": mean_empirical_marginal_brier,
                    "marginal_top1_accuracy": empirical_marginal_top1_accuracy,
                }
            )
        return values

    def build_calibration(model_rows: list[dict[str, Any]], bins: int = 10) -> tuple[list[dict[str, Any]], float]:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in model_rows:
            confidence = top_prediction(row)[1]
            index = min(bins - 1, int(confidence * bins))
            grouped[index].append(row)
        calibration = []
        ece = 0.0
        for index in range(bins):
            bin_rows = grouped.get(index, [])
            low = index / bins
            high = (index + 1) / bins
            mean_confidence = (
                sum(top_prediction(row)[1] for row in bin_rows) / len(bin_rows)
                if bin_rows
                else None
            )
            accuracy = sum(row["top1_correct"] for row in bin_rows) / len(bin_rows) if bin_rows else None
            if bin_rows and mean_confidence is not None and accuracy is not None:
                ece += (len(bin_rows) / len(model_rows)) * abs(accuracy - mean_confidence)
            calibration.append(
                {
                    "bin": f"{low:.1f}-{high:.1f}",
                    "low": low,
                    "high": high,
                    "rows": len(bin_rows),
                    "mean_confidence": mean_confidence,
                    "accuracy": accuracy,
                }
            )
        return calibration, ece

    summary: dict[str, dict[str, Any]] = {}
    by_question: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_question_model: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    job_ids = {str(row.get("job_id")) for row in rows if row.get("job_id")}
    multiple_jobs = len(job_ids) > 1
    for row in rows:
        label = row.get("model_label") or model_label(row.get("service"), row.get("model"))
        row["model_label"] = label
        twin_set_label = f"{row.get('job_id')} / {label}" if multiple_jobs and row.get("job_id") else label
        row["twin_set_label"] = twin_set_label
        by_model[twin_set_label].append(row)
        by_question_model[(row["heldout_question"], twin_set_label)].append(row)
    for model, model_rows in by_model.items():
        summary[model] = summarize(model_rows)
    for (question, model), model_rows in by_question_model.items():
        by_question[question][model] = summarize(model_rows)
    calibration_by_model = {}
    ece_by_model = {}
    for model, model_rows in by_model.items():
        calibration, ece = build_calibration(model_rows)
        calibration_by_model[model] = calibration
        ece_by_model[model] = ece

    marginal_comparisons = []
    marginal_option_rows = []
    aggregate_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("heldout_question"):
            aggregate_groups[(str(row.get("heldout_question")), str(row.get("twin_set_label")))].append(row)
    for (question, label), group_rows in aggregate_groups.items():
        options = list(group_rows[0].get("option_labels", []))
        if not options:
            options = sorted({option for row in group_rows for option in row.get("probabilities", {})})
        totals = {str(option): 0.0 for option in options}
        for row in group_rows:
            for option in options:
                totals[str(option)] += float(row.get("probabilities", {}).get(str(option), 0.0))
        predicted = {option: totals[option] / len(group_rows) for option in totals} if group_rows else totals
        target = None
        for row in by_question_model.get((question, label), []):
            candidate = row.get("empirical_marginal_probabilities") or row.get("marginal_probabilities")
            if candidate:
                target = {str(option): float(value) for option, value in candidate.items()}
                break
        predicted_top, predicted_top_probability = top_probability_option(predicted)
        target_top = None
        target_top_probability = None
        metrics = {}
        if target:
            target_top, target_top_probability = top_probability_option(target)
            metrics = distribution_distance_metrics(predicted, target)
        comparison = {
            "heldout_question": question,
            "heldout_question_text": group_rows[0].get("heldout_question_text") if group_rows else None,
            "model_label": label,
            "job_id": group_rows[0].get("job_id") if group_rows else None,
            "respondent_count": len(group_rows),
            "predicted_top_option": predicted_top,
            "predicted_top_probability": predicted_top_probability,
            "target_top_option": target_top,
            "target_top_probability": target_top_probability,
            "top_option_agrees": int(predicted_top == target_top) if target_top is not None else None,
            **metrics,
        }
        marginal_comparisons.append(comparison)
        for option in options or sorted(set(predicted) | set(target or {})):
            option = str(option)
            predicted_probability = predicted.get(option, 0.0)
            target_probability = target.get(option, 0.0) if target else None
            marginal_option_rows.append(
                {
                    "heldout_question": question,
                    "heldout_question_text": group_rows[0].get("heldout_question_text") if group_rows else None,
                    "model_label": label,
                    "job_id": group_rows[0].get("job_id") if group_rows else None,
                    "option_label": option,
                    "predicted_probability": predicted_probability,
                    "target_probability": target_probability,
                    "difference": predicted_probability - target_probability if target_probability is not None else None,
                    "abs_difference": abs(predicted_probability - target_probability) if target_probability is not None else None,
                }
            )
    marginal_comparisons.sort(
        key=lambda item: (
            item.get("l1") is None,
            -(item.get("l1") or 0.0),
            str(item.get("heldout_question")),
            str(item.get("model_label")),
        )
    )

    diagnostics = {
        "calibration": calibration_by_model,
        "expected_calibration_error": ece_by_model,
        "summary_by_question": by_question,
        "marginal_comparisons": marginal_comparisons,
        "marginal_options": marginal_option_rows,
        "worst_misses": sorted(
            rows,
            key=lambda row: (
                row.get("top1_correct", 0),
                row.get("probability_actual", 0.0),
                -row.get("negative_log_likelihood", 0.0),
            ),
        )[:20],
        "baseline_comparison": {},
        "empirical_wins": [],
        "model_wins": [],
        "overconfident_misses": [],
        "confusion": {},
    }
    diagnostics["overconfident_misses"] = sorted(
        [row for row in rows if not row.get("top1_correct")],
        key=lambda row: (-top_prediction(row)[1], row.get("probability_actual", 0.0)),
    )[:20]
    confusion: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for row in rows:
        predicted_option, _ = top_prediction(row)
        key = f"{row.get('heldout_question')}::{row.get('twin_set_label', row.get('model_label'))}"
        confusion[key][str(row.get("actual_answer"))][str(predicted_option)] += 1
    diagnostics["confusion"] = {
        key: {
            actual: dict(predicted)
            for actual, predicted in actuals.items()
        }
        for key, actuals in confusion.items()
    }
    for model, values in summary.items():
        values["expected_calibration_error"] = ece_by_model.get(model)
        empirical_nll = values.get("mean_empirical_marginal_negative_log_likelihood", values.get("mean_marginal_negative_log_likelihood"))
        empirical_brier = values.get("mean_empirical_marginal_brier", values.get("mean_marginal_brier"))
        empirical_p = values.get("mean_empirical_marginal_probability_actual", values.get("mean_marginal_probability_actual"))
        diagnostics["baseline_comparison"][model] = {
            "p_actual_vs_uniform": values["mean_probability_actual"] - values["mean_uniform_probability_actual"],
            "nll_vs_uniform": values["mean_uniform_negative_log_likelihood"] - values["mean_negative_log_likelihood"],
            "brier_vs_uniform": values["mean_uniform_brier"] - values["mean_brier"],
            "p_actual_vs_empirical": values["mean_probability_actual"] - empirical_p if empirical_p is not None else None,
            "nll_vs_empirical": empirical_nll - values["mean_negative_log_likelihood"] if empirical_nll is not None else None,
            "brier_vs_empirical": empirical_brier - values["mean_brier"] if empirical_brier is not None else None,
        }
    for (question, model), model_rows in by_question_model.items():
        values = summarize(model_rows)
        empirical_nll = values.get("mean_empirical_marginal_negative_log_likelihood", values.get("mean_marginal_negative_log_likelihood"))
        if empirical_nll is None:
            continue
        item = {
            "heldout_question": question,
            "model": model,
            "rows": values["rows"],
            "model_nll": values["mean_negative_log_likelihood"],
            "empirical_nll": empirical_nll,
            "nll_vs_empirical": empirical_nll - values["mean_negative_log_likelihood"],
        }
        if item["nll_vs_empirical"] >= 0:
            diagnostics["model_wins"].append(item)
        else:
            diagnostics["empirical_wins"].append(item)
    diagnostics["model_wins"].sort(key=lambda item: item["nll_vs_empirical"], reverse=True)
    diagnostics["empirical_wins"].sort(key=lambda item: item["nll_vs_empirical"])
    diagnostics["joint_structure"] = build_twin_joint_structure_diagnostics(rows)
    diagnostics["subgroup_marginals"] = build_twin_subgroup_marginal_diagnostics(rows)
    diagnostics["conditional_consistency"] = build_twin_conditional_consistency_diagnostics(rows)
    return {"rows": rows, "summary": summary, "summary_by_question": by_question, "diagnostics": diagnostics}


def twin_top_prediction(row: dict[str, Any]) -> tuple[str | None, float]:
    predicted = row.get("probabilities", {})
    if not predicted:
        return None, 0.0
    option, probability = max(predicted.items(), key=lambda item: (float(item[1]), str(item[0])))
    return str(option), float(probability)


def paired_twin_response_changes(
    all_rows: list[dict[str, Any]],
    from_job_id: str,
    to_job_id: str,
    *,
    from_label: str | None = None,
    to_label: str | None = None,
    model: str | None = None,
    example_limit: int = 20,
) -> list[dict[str, Any]]:
    def label_for(row: dict[str, Any]) -> str:
        return str(row.get("model_label") or model_label(row.get("service"), row.get("model")))

    def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (str(row.get("respondent_id")), str(row.get("heldout_question")), label_for(row))

    from_rows = [
        row
        for row in all_rows
        if row.get("job_id") == from_job_id and (model is None or label_for(row) == model)
    ]
    to_rows = [
        row
        for row in all_rows
        if row.get("job_id") == to_job_id and (model is None or label_for(row) == model)
    ]
    from_by_key = {row_key(row): row for row in from_rows}
    to_by_key = {row_key(row): row for row in to_rows}
    by_model: dict[str, dict[str, Any]] = {}
    for respondent_id, heldout_question, model_name in sorted(set(from_by_key) & set(to_by_key)):
        before = from_by_key[(respondent_id, heldout_question, model_name)]
        after = to_by_key[(respondent_id, heldout_question, model_name)]
        before_top, before_confidence = twin_top_prediction(before)
        after_top, after_confidence = twin_top_prediction(after)
        before_correct = bool(before.get("top1_correct"))
        after_correct = bool(after.get("top1_correct"))
        changed = before_top != after_top
        probability_actual_delta = float(after.get("probability_actual", 0.0)) - float(before.get("probability_actual", 0.0))
        nll_delta = float(after.get("negative_log_likelihood", 0.0)) - float(before.get("negative_log_likelihood", 0.0))
        bucket = by_model.setdefault(
            model_name,
            {
                "from_job_id": from_job_id,
                "to_job_id": to_job_id,
                "from_label": from_label or from_job_id,
                "to_label": to_label or to_job_id,
                "model": model_name,
                "paired_rows": 0,
                "changed_top_choice": 0,
                "unchanged_top_choice": 0,
                "changed_top_choice_rate": 0.0,
                "corrections": 0,
                "regressions": 0,
                "changed_wrong_to_wrong": 0,
                "changed_correct_to_correct": 0,
                "unchanged_correct": 0,
                "unchanged_wrong": 0,
                "mean_probability_actual_delta": 0.0,
                "mean_nll_delta": 0.0,
                "examples": [],
            },
        )
        bucket["paired_rows"] += 1
        bucket["mean_probability_actual_delta"] += probability_actual_delta
        bucket["mean_nll_delta"] += nll_delta
        if changed:
            bucket["changed_top_choice"] += 1
            if not before_correct and after_correct:
                bucket["corrections"] += 1
            elif before_correct and not after_correct:
                bucket["regressions"] += 1
            elif not before_correct and not after_correct:
                bucket["changed_wrong_to_wrong"] += 1
            else:
                bucket["changed_correct_to_correct"] += 1
            if len(bucket["examples"]) < example_limit:
                bucket["examples"].append(
                    {
                        "respondent_id": respondent_id,
                        "heldout_question": heldout_question,
                        "actual_answer": before.get("actual_answer"),
                        "from_top_choice": before_top,
                        "to_top_choice": after_top,
                        "from_top_confidence": before_confidence,
                        "to_top_confidence": after_confidence,
                        "from_probability_actual": before.get("probability_actual"),
                        "to_probability_actual": after.get("probability_actual"),
                        "probability_actual_delta": probability_actual_delta,
                        "from_correct": before_correct,
                        "to_correct": after_correct,
                    }
                )
        else:
            bucket["unchanged_top_choice"] += 1
            if after_correct:
                bucket["unchanged_correct"] += 1
            else:
                bucket["unchanged_wrong"] += 1
    summaries = []
    for item in by_model.values():
        if item["paired_rows"]:
            item["changed_top_choice_rate"] = item["changed_top_choice"] / item["paired_rows"]
            item["mean_probability_actual_delta"] = item["mean_probability_actual_delta"] / item["paired_rows"]
            item["mean_nll_delta"] = item["mean_nll_delta"] / item["paired_rows"]
        summaries.append(item)
    return sorted(summaries, key=lambda item: (item["model"], item["from_job_id"], item["to_job_id"]))


def paired_twin_response_pair_rows(
    all_rows: list[dict[str, Any]],
    from_job_id: str,
    to_job_id: str,
    *,
    from_label: str | None = None,
    to_label: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    def label_for(row: dict[str, Any]) -> str:
        return str(row.get("model_label") or model_label(row.get("service"), row.get("model")))

    def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (str(row.get("respondent_id")), str(row.get("heldout_question")), label_for(row))

    from_by_key = {
        row_key(row): row
        for row in all_rows
        if row.get("job_id") == from_job_id and (model is None or label_for(row) == model)
    }
    to_by_key = {
        row_key(row): row
        for row in all_rows
        if row.get("job_id") == to_job_id and (model is None or label_for(row) == model)
    }
    pairs = []
    for respondent_id, heldout_question, model_name in sorted(set(from_by_key) & set(to_by_key)):
        before = from_by_key[(respondent_id, heldout_question, model_name)]
        after = to_by_key[(respondent_id, heldout_question, model_name)]
        before_top, before_confidence = twin_top_prediction(before)
        after_top, after_confidence = twin_top_prediction(after)
        before_correct = bool(before.get("top1_correct"))
        after_correct = bool(after.get("top1_correct"))
        changed = before_top != after_top
        if changed and not before_correct and after_correct:
            category = "correction"
        elif changed and before_correct and not after_correct:
            category = "regression"
        elif changed and before_correct and after_correct:
            category = "changed_correct_to_correct"
        elif changed:
            category = "changed_wrong_to_wrong"
        elif after_correct:
            category = "unchanged_correct"
        else:
            category = "unchanged_wrong"
        probability_actual_delta = float(after.get("probability_actual", 0.0)) - float(before.get("probability_actual", 0.0))
        nll_delta = float(after.get("negative_log_likelihood", 0.0)) - float(before.get("negative_log_likelihood", 0.0))
        pairs.append(
            {
                "from_job_id": from_job_id,
                "to_job_id": to_job_id,
                "from_label": from_label or from_job_id,
                "to_label": to_label or to_job_id,
                "respondent_id": respondent_id,
                "heldout_question": heldout_question,
                "model": model_name,
                "actual_answer": before.get("actual_answer"),
                "from_top_choice": before_top,
                "to_top_choice": after_top,
                "from_top_confidence": before_confidence,
                "to_top_confidence": after_confidence,
                "from_probability_actual": before.get("probability_actual"),
                "to_probability_actual": after.get("probability_actual"),
                "probability_actual_delta": probability_actual_delta,
                "from_nll": before.get("negative_log_likelihood"),
                "to_nll": after.get("negative_log_likelihood"),
                "nll_delta": nll_delta,
                "from_correct": before_correct,
                "to_correct": after_correct,
                "changed_top_choice": changed,
                "category": category,
            }
        )
    return pairs


def twin_job_template_and_scenarios(sdir: Path, job_id: str) -> tuple[str | None, dict[tuple[str, str], dict[str, Any]]]:
    run = next((item for item in read_twin_run_manifest(sdir) if item.get("job_id") == job_id), {})
    job_path_text = run.get("job_path")
    if not job_path_text:
        return None, {}
    job_path = Path(str(job_path_text))
    if not job_path.exists():
        return None, {}
    job_dict = read_json(job_path, {})
    questions = job_dict.get("survey", {}).get("questions", [])
    template = questions[0].get("question_text") if questions and isinstance(questions[0], dict) else None
    scenarios = {}
    for scenario in job_dict.get("scenarios", []):
        key = (str(scenario.get("respondent_id")), str(scenario.get("heldout_question_name")))
        scenarios[key] = scenario
    return template, scenarios


def format_probabilities_for_display(probabilities: dict[str, Any] | None) -> str:
    if not probabilities:
        return ""
    return "; ".join(f"{option}: {float(value):.3f}" for option, value in probabilities.items())


def compact_observed_answers(observed_answers: list[dict[str, Any]] | None) -> str:
    if not observed_answers:
        return "No observed answers recorded."
    parts = []
    for item in observed_answers:
        name = item.get("question_name", "")
        text = item.get("question_text", "")
        answer = item.get("answer", "")
        label = f"{name}: " if name else ""
        parts.append(f"{label}{text} -> {answer}")
    return "\n".join(parts)


def paired_twin_microdata_rows(
    sdir: Path,
    all_rows: list[dict[str, Any]],
    pair_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    job_ids = sorted({str(row.get("from_job_id")) for row in pair_rows} | {str(row.get("to_job_id")) for row in pair_rows})
    templates: dict[str, str | None] = {}
    scenarios_by_job: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for job_id in job_ids:
        template, scenarios = twin_job_template_and_scenarios(sdir, job_id)
        templates[job_id] = template
        scenarios_by_job[job_id] = scenarios

    def prediction_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
        label = str(row.get("model_label") or model_label(row.get("service"), row.get("model")))
        return (str(row.get("job_id")), str(row.get("respondent_id")), str(row.get("heldout_question")), label)

    predictions = {prediction_key(row): row for row in all_rows}
    rows = []
    for pair in pair_rows:
        from_job_id = str(pair.get("from_job_id"))
        to_job_id = str(pair.get("to_job_id"))
        respondent_id = str(pair.get("respondent_id"))
        heldout_question = str(pair.get("heldout_question"))
        model_name = str(pair.get("model"))
        before = predictions.get((from_job_id, respondent_id, heldout_question, model_name), {})
        after = predictions.get((to_job_id, respondent_id, heldout_question, model_name), {})
        before_scenario = scenarios_by_job.get(from_job_id, {}).get((respondent_id, heldout_question), {})
        after_scenario = scenarios_by_job.get(to_job_id, {}).get((respondent_id, heldout_question), {})
        scenario = after_scenario or before_scenario
        observed_answers = scenario.get("observed_answers") or after.get("observed_answers") or before.get("observed_answers") or []
        rows.append(
            {
                **pair,
                "heldout_question_text": scenario.get("heldout_question_text") or after.get("heldout_question_text") or before.get("heldout_question_text"),
                "heldout_options": scenario.get("heldout_options") or after.get("option_labels") or before.get("option_labels") or [],
                "observed_answers": observed_answers,
                "observed_answers_text": scenario.get("observed_answers_text") or compact_observed_answers(observed_answers),
                "agent_material_text": scenario.get("agent_material_text"),
                "from_template": templates.get(from_job_id),
                "to_template": templates.get(to_job_id),
                "from_twin_material_text": before_scenario.get("twin_material_text") or before.get("twin_material_text"),
                "to_twin_material_text": after_scenario.get("twin_material_text") or after.get("twin_material_text"),
                "from_probabilities": before.get("probabilities"),
                "to_probabilities": after.get("probabilities"),
                "from_probabilities_text": format_probabilities_for_display(before.get("probabilities")),
                "to_probabilities_text": format_probabilities_for_display(after.get("probabilities")),
                "from_notes": before.get("notes"),
                "to_notes": after.get("notes"),
            }
        )
    metadata = {
        "templates_by_job": templates,
        "row_count": len(rows),
    }
    return rows, metadata


def render_twin_microdata_table_html(rows: list[dict[str, Any]], *, title: str, include_title: bool = True) -> str:
    options = sorted({str(row.get("category")) for row in rows})
    model_options = sorted({str(row.get("model")) for row in rows})
    option_markup = "".join(f'<option value="{html_escape(value)}">{html_escape(value.replace("_", " "))}</option>' for value in options)
    model_markup = "".join(f'<option value="{html_escape(value)}">{html_escape(value)}</option>' for value in model_options)
    body_rows = []
    for row in rows:
        observed_summary = compact_observed_answers(row.get("observed_answers", []))
        template = row.get("to_template") or row.get("from_template") or ""
        material = row.get("to_twin_material_text") or row.get("from_twin_material_text") or ""
        search_blob = " ".join(
            str(value or "")
            for value in [
                row.get("respondent_id"),
                row.get("category"),
                row.get("actual_answer"),
                row.get("from_top_choice"),
                row.get("to_top_choice"),
                observed_summary,
                material,
                row.get("from_notes"),
                row.get("to_notes"),
            ]
        )
        body_rows.append(
            "<tr "
            f"data-category=\"{html_escape(row.get('category'))}\" "
            f"data-model=\"{html_escape(row.get('model'))}\" "
            f"data-search=\"{html_escape(search_blob)}\">"
            f"<td><code>{html_escape(row.get('respondent_id'))}</code><div class=\"muted\">{html_escape(row.get('heldout_question'))}</div></td>"
            f"<td><span class=\"pill {html_escape(row.get('category'))}\">{html_escape(str(row.get('category')).replace('_', ' '))}</span></td>"
            f"<td>{html_escape(row.get('actual_answer'))}</td>"
            f"<td><b>{html_escape(row.get('from_top_choice'))}</b><div class=\"muted\">p(actual) {float(row.get('from_probability_actual') or 0):.3f}</div><div class=\"mono small\">{html_escape(row.get('from_probabilities_text'))}</div></td>"
            f"<td><b>{html_escape(row.get('to_top_choice'))}</b><div class=\"muted\">p(actual) {float(row.get('to_probability_actual') or 0):.3f}</div><div class=\"mono small\">{html_escape(row.get('to_probabilities_text'))}</div></td>"
            f"<td class=\"num\">{float(row.get('probability_actual_delta') or 0):+.3f}</td>"
            f"<td><details><summary>Traits / prompt / response</summary>"
            f"<h4>Observed answer traits</h4><pre>{html_escape(observed_summary)}</pre>"
            f"<h4>Supplemental material</h4><pre>{html_escape(material or 'No supplemental material recorded.')}</pre>"
            f"<h4>Prompt template</h4><pre>{html_escape(template or 'No prompt template recorded.')}</pre>"
            f"<h4>Model notes</h4><pre>{html_escape('Before: ' + str(row.get('from_notes') or '') + chr(10) + 'After: ' + str(row.get('to_notes') or ''))}</pre>"
            "</details></td>"
            "</tr>"
        )
    data_json = escape_script_text(json.dumps(rows, separators=(",", ":")))
    title_markup = f"<h3>{html_escape(title)}</h3>" if include_title else ""
    return f"""<div class="microdata-widget">
  <style>
    .microdata-widget {{ font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#17202a; }}
    .microdata-widget .controls {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:10px 0 14px; }}
    .microdata-widget input,.microdata-widget select {{ border:1px solid #cfd7df; border-radius:6px; padding:7px 9px; font:inherit; background:#fff; }}
    .microdata-widget table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    .microdata-widget th,.microdata-widget td {{ border:1px solid #dfe3e6; padding:7px 8px; vertical-align:top; text-align:left; }}
    .microdata-widget th {{ background:#f0f3f4; }}
    .microdata-widget pre {{ white-space:pre-wrap; max-height:260px; overflow:auto; background:#f7f9fb; border:1px solid #dfe3e6; border-radius:6px; padding:8px; }}
    .microdata-widget .muted {{ color:#607080; font-size:12px; margin-top:3px; }}
    .microdata-widget .mono {{ font-family:SFMono-Regular,Consolas,Menlo,monospace; }}
    .microdata-widget .small {{ font-size:11.5px; }}
    .microdata-widget .num {{ text-align:right; font-family:SFMono-Regular,Consolas,Menlo,monospace; }}
    .microdata-widget .pill {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#eef2f6; font-size:12px; }}
    .microdata-widget .unchanged_correct {{ background:#e9f1fb; color:#244d78; }}
    .microdata-widget .unchanged_wrong {{ background:#eef0f3; color:#4b5563; }}
    .microdata-widget .correction {{ background:#e7f3eb; color:#1f6f43; }}
    .microdata-widget .regression {{ background:#f7e8e6; color:#9b2f24; }}
  </style>
  {title_markup}
  <div class="controls">
    <label>Category <select data-micro-filter="category"><option value="">All</option>{option_markup}</select></label>
    <label>Model <select data-micro-filter="model"><option value="">All</option>{model_markup}</select></label>
    <label>Search <input data-micro-filter="search" type="search" placeholder="respondent, answer, note"></label>
    <span class="muted" data-micro-count>{len(rows)} rows</span>
  </div>
  <div class="table-wrap"><table>
    <thead><tr><th>Respondent</th><th>Category</th><th>Actual</th><th>Before</th><th>After</th><th>Δ p(actual)</th><th>Inspect</th></tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table></div>
  <script type="application/json" class="microdata-json">{data_json}</script>
  <script>
    (function() {{
      const root = document.currentScript.closest(".microdata-widget");
      if (!root) return;
      const rows = Array.from(root.querySelectorAll("tbody tr"));
      const category = root.querySelector('[data-micro-filter="category"]');
      const model = root.querySelector('[data-micro-filter="model"]');
      const search = root.querySelector('[data-micro-filter="search"]');
      const count = root.querySelector("[data-micro-count]");
      function apply() {{
        const q = (search.value || "").toLowerCase();
        let visible = 0;
        for (const row of rows) {{
          const okCategory = !category.value || row.dataset.category === category.value;
          const okModel = !model.value || row.dataset.model === model.value;
          const okSearch = !q || (row.dataset.search || "").toLowerCase().includes(q);
          const show = okCategory && okModel && okSearch;
          row.style.display = show ? "" : "none";
          if (show) visible += 1;
        }}
        count.textContent = visible + " rows";
      }}
      category.addEventListener("change", apply);
      model.addEventListener("change", apply);
      search.addEventListener("input", apply);
    }})();
  </script>
</div>
"""


def experiment_microdata_id(args: argparse.Namespace) -> str:
    payload = {
        "survey": args.survey,
        "metric": args.metric,
        "model": args.model,
        "experiment_id": args.experiment_id,
        "job_id": args.job_id,
        "jobs": args.jobs,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_experiment_microdata_audit(
    sdir: Path,
    experiments: list[dict[str, Any]],
    metric: str,
    model: str | None = None,
) -> dict[str, Any]:
    comparison_rows, metric_info = twin_experiment_comparison_rows(sdir, experiments, metric, model)
    if not comparison_rows:
        raise ZwillError("not_found", "No scored experiment rows found for the requested filters.")
    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    selected_jobs = {str(row["job_id"]) for row in comparison_rows}
    experiment_by_job = {str(item.get("job_id")): item for item in experiments}
    display_experiments = []
    for row in comparison_rows:
        experiment = experiment_by_job.get(str(row["job_id"]), {})
        display_experiments.append(
            {
                "experiment_id": row.get("experiment_id"),
                "job_id": row.get("job_id"),
                "approach": row.get("approach"),
                "description": row.get("description", ""),
                "rank": row.get("rank"),
                "selected": row.get("selected"),
                "model": row.get("model"),
                "metric": metric,
                "metric_value": row.get("metric_value"),
                "primary_metric": experiment.get("primary_metric"),
            }
        )

    templates: dict[str, str | None] = {}
    scenarios_by_job: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for job_id in selected_jobs:
        template, scenarios = twin_job_template_and_scenarios(sdir, job_id)
        templates[job_id] = template
        scenarios_by_job[job_id] = scenarios

    groups_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    display_job_order = [str(row["job_id"]) for row in comparison_rows]
    for prediction in all_rows:
        job_id = str(prediction.get("job_id"))
        if job_id not in selected_jobs:
            continue
        label = str(prediction.get("model_label") or model_label(prediction.get("service"), prediction.get("model")))
        if model and label != model:
            continue
        respondent_id = str(prediction.get("respondent_id"))
        heldout_question = str(prediction.get("heldout_question"))
        group_key = (respondent_id, heldout_question, label)
        scenario = scenarios_by_job.get(job_id, {}).get((respondent_id, heldout_question), {})
        observed_answers = scenario.get("observed_answers") or prediction.get("observed_answers", [])
        group_id = hashlib.sha256(
            json.dumps(group_key, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:12]
        group = groups_by_key.setdefault(
            group_key,
            {
                "group_id": group_id,
                "respondent_id": respondent_id,
                "heldout_question": heldout_question,
                "heldout_question_text": scenario.get("heldout_question_text") or prediction.get("heldout_question_text"),
                "heldout_options": scenario.get("heldout_options") or prediction.get("option_labels", []),
                "model": label,
                "actual_answer": prediction.get("actual_answer"),
                "observed_answers": observed_answers,
                "observed_answers_text": scenario.get("observed_answers_text") or compact_observed_answers(observed_answers),
                "agent_material_text": scenario.get("agent_material_text"),
                "prediction_rows": {},
            },
        )
        top_choice, top_confidence = twin_top_prediction(prediction)
        experiment = experiment_by_job.get(job_id, {})
        group["prediction_rows"][job_id] = {
            "group_id": group_id,
            "respondent_id": respondent_id,
            "heldout_question": heldout_question,
            "heldout_question_text": group.get("heldout_question_text"),
            "heldout_options": group.get("heldout_options"),
            "model": label,
            "observed_answers": observed_answers,
            "observed_answers_text": scenario.get("observed_answers_text") or compact_observed_answers(observed_answers),
            "agent_material_text": scenario.get("agent_material_text"),
            "experiment_id": experiment.get("experiment_id"),
            "job_id": job_id,
            "approach": experiment.get("approach") or job_id,
            "description": experiment.get("description", ""),
            "top_choice": top_choice,
            "top_confidence": top_confidence,
            "probabilities": prediction.get("probabilities", {}),
            "probabilities_text": format_probabilities_for_display(prediction.get("probabilities")),
            "actual_answer": prediction.get("actual_answer"),
            "probability_actual": prediction.get("probability_actual"),
            "negative_log_likelihood": prediction.get("negative_log_likelihood"),
            "brier": prediction.get("brier"),
            "top1_correct": bool(prediction.get("top1_correct")),
            "notes": prediction.get("notes"),
            "twin_material_text": scenario.get("twin_material_text") or prediction.get("twin_material_text"),
            "agent_material_text": scenario.get("agent_material_text"),
            "prompt_template": templates.get(job_id),
            "source_row": prediction.get("row"),
        }

    groups = []
    prediction_rows = []
    for group in groups_by_key.values():
        rows_by_job = group.pop("prediction_rows")
        visible_rows = [rows_by_job[job_id] for job_id in display_job_order if job_id in rows_by_job]
        correct_values = [bool(item.get("top1_correct")) for item in visible_rows]
        p_values = [
            float(item.get("probability_actual"))
            for item in visible_rows
            if item.get("probability_actual") is not None
        ]
        nll_values = [
            (float(item.get("negative_log_likelihood")), item.get("experiment_id"))
            for item in visible_rows
            if item.get("negative_log_likelihood") is not None
        ]
        top_choices = {item.get("top_choice") for item in visible_rows if item.get("top_choice") is not None}
        if correct_values and all(correct_values):
            category = "all_correct"
        elif correct_values and not any(correct_values):
            category = "all_wrong"
        else:
            category = "mixed_correctness"
        if len(top_choices) > 1:
            category = "top_choice_changed"
        diagnostics = {
            "category": category,
            "top_choice_changed": len(top_choices) > 1,
            "any_correct": any(correct_values) if correct_values else None,
            "all_correct": all(correct_values) if correct_values else None,
            "p_actual_range": max(p_values) - min(p_values) if p_values else None,
            "best_experiment_by_nll": min(nll_values)[1] if nll_values else None,
            "worst_experiment_by_nll": max(nll_values)[1] if nll_values else None,
            "experiment_count": len(visible_rows),
        }
        group["diagnostics"] = diagnostics
        group["prediction_row_count"] = len(visible_rows)
        group["prediction_row_ids"] = [
            f"{item.get('group_id')}::{item.get('job_id')}" for item in visible_rows
        ]
        for item in visible_rows:
            item["row_id"] = f"{item.get('group_id')}::{item.get('job_id')}"
            item["group_diagnostics"] = diagnostics
            prediction_rows.append(item)
        groups.append(group)
    groups.sort(key=lambda row: (str(row.get("heldout_question")), str(row.get("model")), str(row.get("respondent_id"))))
    group_order = {group["group_id"]: index for index, group in enumerate(groups)}
    prediction_rows.sort(
        key=lambda row: (
            group_order.get(row.get("group_id"), 0),
            display_job_order.index(str(row.get("job_id"))) if str(row.get("job_id")) in display_job_order else 999999,
        )
    )
    return {
        "kind": "experiment_microdata_audit",
        "survey": sdir.name,
        "metric": {"name": metric, **metric_info},
        "experiments": display_experiments,
        "groups": groups,
        "prediction_rows": prediction_rows,
        "group_count": len(groups),
        "prediction_row_count": len(prediction_rows),
        "row_count": len(prediction_rows),
        "group_key": ["respondent_id", "heldout_question", "model"],
        "created_at": utc_now(),
    }


def build_experiment_microdata_matrix(
    sdir: Path,
    experiments: list[dict[str, Any]],
    metric: str,
    model: str | None = None,
) -> dict[str, Any]:
    return build_experiment_microdata_audit(sdir, experiments, metric, model)


def render_experiment_microdata_audit_html(payload: dict[str, Any], *, title: str) -> str:
    groups = payload.get("groups", [])
    prediction_rows = payload.get("prediction_rows", [])
    experiments = payload.get("experiments", [])
    questions = sorted({str(row.get("heldout_question")) for row in prediction_rows})
    models = sorted({str(row.get("model")) for row in prediction_rows})
    actuals = sorted({str(row.get("actual_answer")) for row in prediction_rows})
    categories = sorted({str(group.get("diagnostics", {}).get("category")) for group in groups})

    def options(values: list[str], labels: dict[str, str] | None = None) -> str:
        labels = labels or {}
        return "".join(f'<option value="{html_escape(value)}">{html_escape(labels.get(value, value))}</option>' for value in values)

    experiment_toggles = "".join(
        f'<label><input type="checkbox" data-exp-toggle="{html_escape(exp.get("job_id"))}" checked> {html_escape(exp.get("approach") or exp.get("job_id"))}</label>'
        for exp in experiments
    )
    body_rows = []
    rows_by_group: dict[str, list[dict[str, Any]]] = {}
    for row in prediction_rows:
        rows_by_group.setdefault(str(row.get("group_id")), []).append(row)
    for group in groups:
        group_id = str(group.get("group_id"))
        diag = group.get("diagnostics", {})
        observed_text = group.get("observed_answers_text") or compact_observed_answers(group.get("observed_answers", []))
        group_search = " ".join(
            str(value or "")
            for value in [
                group.get("respondent_id"),
                group.get("heldout_question"),
                group.get("heldout_question_text"),
                group.get("actual_answer"),
                observed_text,
                diag.get("category"),
            ]
        )
        body_rows.append(
            "<tr class=\"group-row\" "
            f"data-group-id=\"{html_escape(group_id)}\" "
            f"data-question=\"{html_escape(group.get('heldout_question'))}\" "
            f"data-model=\"{html_escape(group.get('model'))}\" "
            f"data-actual=\"{html_escape(group.get('actual_answer'))}\" "
            f"data-category=\"{html_escape(diag.get('category'))}\" "
            f"data-search=\"{html_escape(group_search)}\">"
            '<td colspan="10">'
            f"<div class=\"group-title\"><code>{html_escape(group.get('respondent_id'))}</code> "
            f"<span>{html_escape(group.get('heldout_question'))}</span> "
            f"<span class=\"muted\">{html_escape(group.get('model'))}</span></div>"
            f"<div class=\"muted\">{html_escape(group.get('heldout_question_text'))}</div>"
            f"<div><span class=\"pill {html_escape(diag.get('category'))}\">{html_escape(str(diag.get('category')).replace('_', ' '))}</span> "
            f"<span class=\"muted\">actual: {html_escape(group.get('actual_answer'))} | p(actual) range {float(diag.get('p_actual_range') or 0):.3f} | best NLL: {html_escape(diag.get('best_experiment_by_nll'))}</span></div>"
            '</td>'
            + "</tr>"
        )
        for row in rows_by_group.get(group_id, []):
            correct_class = "correct" if row.get("top1_correct") else "wrong"
            row_search = " ".join(
                str(value or "")
                for value in [
                    group_search,
                    row.get("approach"),
                    row.get("experiment_id"),
                    row.get("top_choice"),
                    row.get("probabilities_text"),
                    row.get("notes"),
                    row.get("twin_material_text"),
                ]
            )
            body_rows.append(
                "<tr class=\"prediction-row\" "
                f"data-group-id=\"{html_escape(group_id)}\" "
                f"data-job-id=\"{html_escape(row.get('job_id'))}\" "
                f"data-question=\"{html_escape(row.get('heldout_question'))}\" "
                f"data-model=\"{html_escape(row.get('model'))}\" "
                f"data-actual=\"{html_escape(row.get('actual_answer'))}\" "
                f"data-category=\"{html_escape(diag.get('category'))}\" "
                f"data-search=\"{html_escape(row_search)}\">"
                f"<td><code>{html_escape(row.get('respondent_id'))}</code></td>"
                f"<td><b>{html_escape(row.get('approach'))}</b><div class=\"muted\">{html_escape(row.get('experiment_id') or row.get('job_id'))}</div></td>"
                f"<td>{html_escape(row.get('actual_answer'))}</td>"
                f"<td class=\"{correct_class}\"><div class=\"choice\">{html_escape(row.get('top_choice'))}</div><div class=\"muted\">confidence {float(row.get('top_confidence') or 0):.3f}</div></td>"
                f"<td>{float(row.get('probability_actual') or 0):.3f}</td>"
                f"<td>{float(row.get('negative_log_likelihood') or 0):.3f}</td>"
                f"<td>{float(row.get('brier') or 0):.3f}</td>"
                f"<td>{'yes' if row.get('top1_correct') else 'no'}</td>"
                f"<td><div class=\"mono small\">{html_escape(row.get('probabilities_text'))}</div></td>"
                f"<td><button class=\"inspect-button\" type=\"button\" data-inspect-row=\"{html_escape(row.get('row_id'))}\">Inspect</button></td>"
                "</tr>"
            )
    data_json = escape_script_text(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_escape(title)}</title>
  <style>
    :root {{ --ink:#17202a; --muted:#607080; --line:#d8dee6; --bg:#f7f8fa; --panel:#fff; --good:#e7f3eb; --bad:#f7e8e6; --mixed:#fff4d8; --changed:#eaf0ff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ max-width:1500px; margin:0 auto; padding:28px 20px 56px; }}
    h1 {{ margin:0 0 8px; font-size:28px; line-height:1.15; }}
    .subtle,.muted {{ color:var(--muted); }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; margin:14px 0; }}
    .controls {{ display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; }}
    .experiments {{ display:flex; flex-wrap:wrap; gap:8px 14px; margin-top:12px; }}
    input,select {{ border:1px solid #cfd7df; border-radius:6px; padding:7px 9px; font:inherit; background:#fff; }}
    button {{ border:1px solid #c5ced8; border-radius:6px; padding:6px 10px; font:inherit; background:#fff; color:var(--ink); cursor:pointer; }}
    button:hover {{ background:#f2f5f8; }}
    .inspect-button {{ white-space:nowrap; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border:1px solid var(--line); padding:7px 8px; vertical-align:top; text-align:left; }}
    th {{ position:sticky; top:0; background:#f0f3f4; z-index:1; }}
    .table-wrap {{ overflow:auto; max-height:78vh; border:1px solid var(--line); border-radius:8px; background:#fff; }}
    pre {{ white-space:pre-wrap; max-height:260px; overflow:auto; background:#f7f9fb; border:1px solid var(--line); border-radius:6px; padding:8px; }}
    .mono {{ font-family:SFMono-Regular,Consolas,Menlo,monospace; }}
    .small {{ font-size:11.5px; }}
    .choice {{ font-weight:700; margin-bottom:3px; }}
    .group-row td {{ background:#f8fafc; border-top:3px solid #bac6d3; }}
    .group-title {{ display:flex; flex-wrap:wrap; gap:10px; align-items:baseline; font-weight:700; }}
    .prediction-row td:first-child {{ border-left:4px solid #d5dde7; }}
    td.correct {{ background:var(--good); }}
    td.wrong {{ background:var(--bad); }}
    td.missing {{ color:var(--muted); background:#f5f6f8; }}
    .pill {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#eef2f6; font-size:12px; }}
    .all_correct {{ background:var(--good); }}
    .all_wrong {{ background:var(--bad); }}
    .mixed_correctness {{ background:var(--mixed); }}
    .top_choice_changed {{ background:var(--changed); }}
    .modal-backdrop {{ position:fixed; inset:0; display:none; align-items:center; justify-content:center; padding:24px; background:rgba(15,23,42,.45); z-index:10; }}
    .modal-backdrop.open {{ display:flex; }}
    .modal {{ width:min(1120px,96vw); max-height:92vh; overflow:hidden; background:#fff; border:1px solid #cbd5df; border-radius:8px; box-shadow:0 22px 70px rgba(15,23,42,.25); display:flex; flex-direction:column; }}
    .modal-header {{ padding:14px 16px; border-bottom:1px solid var(--line); display:flex; gap:12px; justify-content:space-between; align-items:flex-start; }}
    .modal-title {{ font-size:18px; font-weight:700; }}
    .modal-body {{ padding:14px 16px; overflow:auto; }}
    .modal-actions {{ display:flex; gap:8px; align-items:center; }}
    .tabbar {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }}
    .tabbar button.active {{ background:#17202a; color:#fff; border-color:#17202a; }}
    .tab-panel {{ display:none; }}
    .tab-panel.active {{ display:block; }}
    .summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:10px; }}
    .summary-card {{ border:1px solid var(--line); border-radius:8px; padding:10px; background:#f8fafc; }}
    .summary-card .label {{ color:var(--muted); font-size:12px; }}
    .summary-card .value {{ font-weight:700; margin-top:2px; }}
  </style>
</head>
<body>
{copy_markdown_control()}
<main>
  <h1>{html_escape(title)}</h1>
  <p class="subtle">One audit row per experiment response, grouped by respondent, held-out question, and model. Use Inspect to open the exact observed traits, supplemental material, prompt template, model notes, and source row for that response.</p>
  <section class="panel">
    <div class="controls">
      <label>Question <select data-filter="question"><option value="">All</option>{options(questions)}</select></label>
      <label>Model <select data-filter="model"><option value="">All</option>{options(models)}</select></label>
      <label>Actual <select data-filter="actual"><option value="">All</option>{options(actuals)}</select></label>
      <label>Category <select data-filter="category"><option value="">All</option>{options(categories, {value: value.replace('_', ' ') for value in categories})}</select></label>
      <label>Search <input data-filter="search" type="search" placeholder="respondent, trait, note"></label>
      <span class="muted" data-count>{len(prediction_rows)} rows</span>
    </div>
    <div class="experiments">{experiment_toggles}</div>
  </section>
  <div class="table-wrap"><table>
    <thead><tr><th>Respondent</th><th>Experiment</th><th>Actual</th><th>Top choice</th><th>p(actual)</th><th>NLL</th><th>Brier</th><th>Correct</th><th>Probabilities</th><th>Inspect</th></tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table></div>
</main>
<div class="modal-backdrop" id="inspect-modal" role="dialog" aria-modal="true" aria-labelledby="inspect-modal-title">
  <div class="modal">
    <div class="modal-header">
      <div>
        <div class="modal-title" id="inspect-modal-title">Inspect prediction row</div>
        <div class="muted" id="inspect-modal-subtitle"></div>
      </div>
      <div class="modal-actions">
        <button type="button" data-modal-prev>Previous</button>
        <button type="button" data-modal-next>Next</button>
        <button type="button" data-modal-close>Close</button>
      </div>
    </div>
    <div class="modal-body">
      <div class="tabbar">
        <button type="button" data-tab="summary" class="active">Summary</button>
        <button type="button" data-tab="traits">Traits</button>
        <button type="button" data-tab="material">Material</button>
        <button type="button" data-tab="prompt">Prompt</button>
        <button type="button" data-tab="raw">Raw</button>
      </div>
      <section class="tab-panel active" data-tab-panel="summary"></section>
      <section class="tab-panel" data-tab-panel="traits"></section>
      <section class="tab-panel" data-tab-panel="material"></section>
      <section class="tab-panel" data-tab-panel="prompt"></section>
      <section class="tab-panel" data-tab-panel="raw"></section>
    </div>
  </div>
</div>
<script type="application/json" id="microdata-audit-data">{data_json}</script>
<script>
  const payload = JSON.parse(document.getElementById("microdata-audit-data").textContent);
  const rowById = new Map((payload.prediction_rows || []).map(row => [row.row_id, row]));
  const groupRows = Array.from(document.querySelectorAll("tr.group-row"));
  const predictionRows = Array.from(document.querySelectorAll("tr.prediction-row"));
  let visiblePredictionRows = [];
  let currentRowId = null;
  const filters = {{
    question: document.querySelector('[data-filter="question"]'),
    model: document.querySelector('[data-filter="model"]'),
    actual: document.querySelector('[data-filter="actual"]'),
    category: document.querySelector('[data-filter="category"]'),
    search: document.querySelector('[data-filter="search"]')
  }};
  const count = document.querySelector("[data-count]");
  const modal = document.getElementById("inspect-modal");
  const modalTitle = document.getElementById("inspect-modal-title");
  const modalSubtitle = document.getElementById("inspect-modal-subtitle");
  const panels = new Map(Array.from(document.querySelectorAll("[data-tab-panel]")).map(panel => [panel.dataset.tabPanel, panel]));
  function esc(value) {{
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }}
  function pretty(value) {{
    if (value === undefined || value === null || value === "") return "";
    if (typeof value === "string") return value;
    return JSON.stringify(value, null, 2);
  }}
  function pre(value) {{
    return `<pre>${{esc(pretty(value))}}</pre>`;
  }}
  function metric(value) {{
    return Number(value || 0).toFixed(3);
  }}
  function summaryCard(label, value) {{
    return `<div class="summary-card"><div class="label">${{esc(label)}}</div><div class="value">${{esc(value)}}</div></div>`;
  }}
  function renderModal(rowId) {{
    const row = rowById.get(rowId);
    if (!row) return;
    currentRowId = rowId;
    modalTitle.textContent = `${{row.respondent_id || ""}} | ${{row.approach || row.experiment_id || ""}}`;
    modalSubtitle.textContent = `${{row.heldout_question || ""}} | ${{row.model || ""}}`;
    panels.get("summary").innerHTML = `
      <div class="summary-grid">
        ${{summaryCard("Actual answer", row.actual_answer)}}
        ${{summaryCard("Top choice", row.top_choice)}}
        ${{summaryCard("Correct", row.top1_correct ? "yes" : "no")}}
        ${{summaryCard("p(actual)", metric(row.probability_actual))}}
        ${{summaryCard("NLL", metric(row.negative_log_likelihood))}}
        ${{summaryCard("Brier", metric(row.brier))}}
      </div>
      <h4>Held-out question</h4>
      ${{pre(row.heldout_question_text)}}
      <h4>Probabilities</h4>
      ${{pre(row.probabilities)}}
      <h4>Model notes</h4>
      ${{pre(row.notes || "No model notes recorded.")}}
    `;
    panels.get("traits").innerHTML = `<h4>Observed survey answers used for this twin</h4>${{pre(row.observed_answers_text || row.observed_answers)}}`;
    panels.get("material").innerHTML = `
      <h4>Supplemental twin material</h4>
      ${{pre(row.twin_material_text || "No supplemental twin material recorded.")}}
      <h4>Agent material</h4>
      ${{pre(row.agent_material_text || "No agent material recorded.")}}
    `;
    panels.get("prompt").innerHTML = `<h4>Prompt template</h4>${{pre(row.prompt_template || "No prompt template recorded.")}}`;
    panels.get("raw").innerHTML = `
      <h4>Prediction row</h4>
      ${{pre(row)}}
      <h4>Source row</h4>
      ${{pre(row.source_row)}}
    `;
    modal.classList.add("open");
  }}
  function visibleRowIds() {{
    return visiblePredictionRows.map(row => row.querySelector("[data-inspect-row]")?.dataset.inspectRow).filter(Boolean);
  }}
  function moveModal(delta) {{
    const ids = visibleRowIds();
    if (!ids.length || !currentRowId) return;
    const index = ids.indexOf(currentRowId);
    const nextIndex = index < 0 ? 0 : (index + delta + ids.length) % ids.length;
    renderModal(ids[nextIndex]);
  }}
  function closeModal() {{
    modal.classList.remove("open");
    currentRowId = null;
  }}
  function activeJobIds() {{
    return new Set(Array.from(document.querySelectorAll("[data-exp-toggle]")).filter(t => t.checked).map(t => t.dataset.expToggle));
  }}
  function applyFilters() {{
    const q = (filters.search.value || "").toLowerCase();
    const jobs = activeJobIds();
    let visible = 0;
    const visibleGroups = new Set();
    visiblePredictionRows = [];
    for (const row of predictionRows) {{
      const show = (!filters.question.value || row.dataset.question === filters.question.value)
        && (!filters.model.value || row.dataset.model === filters.model.value)
        && (!filters.actual.value || row.dataset.actual === filters.actual.value)
        && (!filters.category.value || row.dataset.category === filters.category.value)
        && jobs.has(row.dataset.jobId)
        && (!q || (row.dataset.search || "").toLowerCase().includes(q));
      row.style.display = show ? "" : "none";
      if (show) {{
        visible += 1;
        visibleGroups.add(row.dataset.groupId);
        visiblePredictionRows.push(row);
      }}
    }}
    for (const row of groupRows) {{
      const show = visibleGroups.has(row.dataset.groupId);
      row.style.display = show ? "" : "none";
    }}
    count.textContent = visible + " rows";
  }}
  for (const input of Object.values(filters)) input.addEventListener(input.type === "search" ? "input" : "change", applyFilters);
  for (const toggle of document.querySelectorAll("[data-exp-toggle]")) {{
    toggle.addEventListener("change", applyFilters);
  }}
  for (const button of document.querySelectorAll("[data-inspect-row]")) {{
    button.addEventListener("click", () => renderModal(button.dataset.inspectRow));
  }}
  for (const button of document.querySelectorAll("[data-tab]")) {{
    button.addEventListener("click", () => {{
      for (const other of document.querySelectorAll("[data-tab]")) other.classList.toggle("active", other === button);
      for (const panel of document.querySelectorAll("[data-tab-panel]")) panel.classList.toggle("active", panel.dataset.tabPanel === button.dataset.tab);
    }});
  }}
  document.querySelector("[data-modal-prev]").addEventListener("click", () => moveModal(-1));
  document.querySelector("[data-modal-next]").addEventListener("click", () => moveModal(1));
  document.querySelector("[data-modal-close]").addEventListener("click", closeModal);
  modal.addEventListener("click", event => {{
    if (event.target === modal) closeModal();
  }});
  document.addEventListener("keydown", event => {{
    if (!modal.classList.contains("open")) return;
    if (event.key === "Escape") closeModal();
    if (event.key === "ArrowLeft") moveModal(-1);
    if (event.key === "ArrowRight") moveModal(1);
  }});
  applyFilters();
</script>
</body>
</html>
"""


def render_experiment_microdata_matrix_html(payload: dict[str, Any], *, title: str) -> str:
    return render_experiment_microdata_audit_html(payload, title=title)


def write_twin_experiment_microdata(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    experiments = selected_twin_experiments(args, sdir)
    payload = build_experiment_microdata_audit(sdir, experiments, args.metric, args.model)
    microdata_id = args.microdata_id or experiment_microdata_id(args)
    if args.path:
        html_path = Path(args.path)
        output_dir = html_path.parent
    else:
        output_dir = digital_twin_jobs_dir(sdir) / "microdata" / microdata_id
        html_path = output_dir / "audit.html"
    json_path = Path(args.json_path) if args.json_path else html_path.with_suffix(".json")
    output_dir.mkdir(parents=True, exist_ok=True)
    title = args.title or f"{args.survey} Twin Experiment Microdata"
    html = render_experiment_microdata_audit_html(payload, title=title)
    html_path.write_text(html)
    write_json(json_path, payload)
    return {
        "microdata_id": microdata_id,
        "html_path": str(html_path),
        "json_path": str(json_path),
        "group_count": payload["group_count"],
        "prediction_row_count": payload["prediction_row_count"],
        "row_count": payload["row_count"],
        "experiment_count": len(payload["experiments"]),
    }


def cmd_twin_experiment_microdata(args: argparse.Namespace) -> dict[str, Any]:
    data = write_twin_experiment_microdata(args)
    return envelope(
        "zwill twin-experiment microdata",
        "ok",
        data,
        next_steps=[f"open {data['html_path']}"],
    )


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
        for import_path in jobs_dir.glob("*/import.json"):
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
    return sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True)


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


TWIN_EXPERIMENT_METRICS: dict[str, dict[str, str]] = {
    "nll": {
        "field": "mean_negative_log_likelihood",
        "direction": "lower",
        "label": "Negative log likelihood",
        "meaning": "Confidence-weighted loss; lower means the approach put more probability on the real answer.",
    },
    "brier": {
        "field": "mean_brier",
        "direction": "lower",
        "label": "Brier score",
        "meaning": "Squared-error loss against the one-hot actual answer; lower is better.",
    },
    "accuracy": {
        "field": "top1_accuracy",
        "direction": "higher",
        "label": "Top-1 accuracy",
        "meaning": "Share of cases where the highest-probability option matched the respondent's actual answer.",
    },
    "p_actual": {
        "field": "mean_probability_actual",
        "direction": "higher",
        "label": "Mean probability on actual answer",
        "meaning": "Average probability assigned to the respondent's actual held-out answer.",
    },
    "nll_vs_empirical": {
        "field": "nll_vs_empirical",
        "direction": "higher",
        "label": "NLL improvement vs empirical marginal",
        "meaning": "Positive means the approach beat guessing from the observed group distribution.",
    },
    "brier_vs_empirical": {
        "field": "brier_vs_empirical",
        "direction": "higher",
        "label": "Brier improvement vs empirical marginal",
        "meaning": "Positive means the approach beat guessing from the observed group distribution.",
    },
}


TWIN_APPROACH_CONSTRUCTION_KEYS = {
    "context_question",
    "context_questions",
    "exclude_context_question",
    "leakage_exclusion",
    "context_question_count",
    "include_agent_material",
    "agent_material_kind",
    "agent_material_tag",
    "max_agent_material_chars",
    "twin_material",
    "max_twin_material_chars",
    "sample_respondents",
    "seed",
    "complete_cases",
    "balance_actual",
    "stratify_actual",
    "limit_respondents",
    "respondent",
    "respondents",
    "model",
    "models",
    "service_name",
    "model_param",
    "job_question_name",
}


def twin_approaches_path(sdir: Path) -> Path:
    return digital_twin_jobs_dir(sdir) / "approaches.json"


def read_twin_approaches(sdir: Path) -> list[dict[str, Any]]:
    payload = read_json(twin_approaches_path(sdir), {"approaches": []})
    return payload.get("approaches", [])


def write_twin_approaches(sdir: Path, approaches: list[dict[str, Any]]) -> None:
    approaches = sorted(approaches, key=lambda item: item.get("updated_at", item.get("created_at", "")), reverse=True)
    write_json(twin_approaches_path(sdir), {"approaches": approaches})


def update_twin_approaches(sdir: Path, updater) -> list[dict[str, Any]]:
    path = twin_approaches_path(sdir)
    with file_lock(path):
        approaches = read_twin_approaches(sdir)
        updated = updater(approaches)
        write_twin_approaches(sdir, updated)
        return updated


def twin_approach_id(value: str) -> str:
    return slugify(value).lower()


def normalize_twin_approach_record(record: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
    name = str(record.get("name") or record.get("approach") or record.get("approach_id") or "")
    if not name:
        raise ZwillError("invalid_input", "Twin approach needs a name or approach_id.", context={"source": source})
    construction = dict(record.get("construction") or {})
    for key in TWIN_APPROACH_CONSTRUCTION_KEYS:
        if key in record:
            construction[key] = record[key]
    now = utc_now()
    return {
        "approach_id": twin_approach_id(str(record.get("approach_id") or name)),
        "name": name,
        "description": str(record.get("description") or "").strip(),
        "notes": str(record.get("notes") or record.get("note") or "").strip(),
        "tags": sorted(set(normalize_tags(record.get("tags") or record.get("tag")))),
        "construction": construction,
        "source": source,
        "created_at": str(record.get("created_at") or now),
        "updated_at": now,
    }


def twin_approach_from_args(args: argparse.Namespace) -> dict[str, Any]:
    construction: dict[str, Any] = {}
    for key in sorted(TWIN_APPROACH_CONSTRUCTION_KEYS):
        if not hasattr(args, key):
            continue
        value = getattr(args, key)
        if isinstance(value, bool) and value is False:
            continue
        if value is not None and value != []:
            construction[key] = value
    name = args.name or args.approach_id
    if not name:
        raise ZwillError("invalid_input", "Pass --approach-id or --name when adding an inline twin approach.")
    return normalize_twin_approach_record(
        {
            "approach_id": args.approach_id,
            "name": name,
            "description": twin_experiment_description(args),
            "tags": normalize_tags(args.tag),
            "construction": construction,
        }
    )


def upsert_twin_approach(sdir: Path, approach: dict[str, Any]) -> None:
    def updater(approaches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered = [item for item in approaches if item.get("approach_id") != approach.get("approach_id")]
        filtered.append(approach)
        return filtered

    update_twin_approaches(sdir, updater)


def markdown_from_note_args(args: argparse.Namespace) -> str | None:
    if getattr(args, "clear", False):
        return ""
    if getattr(args, "path", None):
        return Path(args.path).read_text().strip()
    if getattr(args, "text", None) is not None:
        return str(args.text).strip()
    return None


def cmd_twin_approach_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    if args.path:
        approach = normalize_twin_approach_record(load_object_file(Path(args.path), kind="Twin approach"), source=str(args.path))
    else:
        approach = twin_approach_from_args(args)
    upsert_twin_approach(sdir, approach)
    return envelope(
        "zwill twin-approach add",
        "ok",
        {"approach": approach},
        next_steps=[f"zwill twin-approach list --survey {args.survey}"],
    )


def cmd_twin_approach_note(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    approach = next((item for item in read_twin_approaches(sdir) if item.get("approach_id") == args.approach_id), None)
    if not approach:
        raise ZwillError("not_found", f"Twin approach not found: {args.approach_id}.")
    note = markdown_from_note_args(args)
    if note is None:
        return envelope("zwill twin-approach note", "ok", {"approach_id": args.approach_id, "notes": approach.get("notes", "")})

    def updater(approaches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matched = False
        for item in approaches:
            if item.get("approach_id") != args.approach_id:
                continue
            matched = True
            item["notes"] = note
            item["updated_at"] = utc_now()
        if not matched:
            raise ZwillError("not_found", f"Twin approach not found: {args.approach_id}.")
        return approaches

    update_twin_approaches(sdir, updater)
    return envelope(
        "zwill twin-approach note",
        "ok",
        {"approach_id": args.approach_id, "notes": note},
        next_steps=[f"zwill twin-approach show --survey {args.survey} --approach-id {args.approach_id}"],
    )


def cmd_twin_approach_list(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    approaches = read_twin_approaches(sdir)
    if args.format == "json":
        print(json.dumps({"survey": args.survey, "approaches": approaches}, indent=2))
        return
    table = Table(title=f"{args.survey} twin approaches")
    for column in ["approach_id", "name", "tags", "construction", "updated_at"]:
        table.add_column(column)
    for approach in approaches:
        construction = approach.get("construction", {})
        table.add_row(
            approach.get("approach_id", ""),
            approach.get("name", ""),
            ", ".join(approach.get("tags", [])),
            ", ".join(sorted(construction))[:90],
            approach.get("updated_at", ""),
        )
    Console().print(table)


def cmd_twin_approach_show(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    approach = next((item for item in read_twin_approaches(sdir) if item.get("approach_id") == args.approach_id), None)
    if not approach:
        raise ZwillError("not_found", f"Twin approach not found: {args.approach_id}.")
    return envelope("zwill twin-approach show", "ok", {"approach": approach})


def cmd_twin_approach_scaffold(args: argparse.Namespace) -> dict[str, Any]:
    require_survey(args.survey)
    construction: dict[str, Any] = {
        "context_question_count": args.context_question_count,
        "complete_cases": True,
        "model": list_or_none(args.model) or ["openai:gpt-5.5"],
    }
    if args.context_questions:
        construction["context_questions"] = args.context_questions
    if args.include_agent_material:
        construction["include_agent_material"] = True
    if args.twin_material:
        construction["twin_material"] = list_or_none(args.twin_material)
    approach = normalize_twin_approach_record(
        {
            "approach_id": args.approach_id,
            "name": args.name or args.approach_id.replace("_", " ").replace("-", " ").title(),
            "description": args.description or "Describe what information this construction approach gives each digital twin.",
            "tags": normalize_tags(args.tag),
            "construction": construction,
        }
    )
    path = Path(args.path or f"{approach['approach_id']}_approach.json")
    write_json(path, approach)
    return envelope(
        "zwill twin-approach scaffold",
        "ok",
        {"path": str(path), "approach": approach},
        next_steps=[f"zwill twin-approach add --survey {args.survey} --path {path}"],
    )


def find_twin_approach_record(sdir: Path, identifier: str) -> dict[str, Any] | None:
    normalized = twin_approach_id(identifier)
    for approach in read_twin_approaches(sdir):
        if approach.get("approach_id") == normalized or approach.get("name") == identifier:
            return approach
    for experiment in read_twin_experiments(sdir):
        if identifier in {
            str(experiment.get("approach_id")),
            str(experiment.get("experiment_id")),
            str(experiment.get("job_id")),
            str(experiment.get("approach")),
        }:
            return normalize_twin_approach_record(
                {
                    "approach_id": experiment.get("approach_id") or experiment.get("experiment_id") or experiment.get("job_id"),
                    "name": experiment.get("approach") or experiment.get("approach_id") or experiment.get("experiment_id"),
                    "description": experiment.get("description", ""),
                    "tags": experiment.get("tags", []),
                    "construction": experiment.get("plan", {}).get("construction", {}),
                    "created_at": experiment.get("created_at"),
                    "updated_at": experiment.get("created_at"),
                }
            )
    return None


def diff_values(left: Any, right: Any) -> str:
    if left == right:
        return "same"
    if left in (None, "", [], {}):
        return "added"
    if right in (None, "", [], {}):
        return "removed"
    return "changed"


def twin_approach_diff_payload(sdir: Path, left_id: str, right_id: str) -> dict[str, Any]:
    left = find_twin_approach_record(sdir, left_id)
    right = find_twin_approach_record(sdir, right_id)
    if not left:
        raise ZwillError("not_found", f"Twin approach not found: {left_id}.")
    if not right:
        raise ZwillError("not_found", f"Twin approach not found: {right_id}.")
    construction_fields = set(left.get("construction", {}).keys()) | set(right.get("construction", {}).keys())
    metadata_fields = (set(left.keys()) | set(right.keys())) - {"construction"}
    rows = []
    for field in sorted(metadata_fields):
        left_value = left.get(field)
        right_value = right.get(field)
        rows.append(
            {
                "field": field,
                "location": "metadata",
                "status": diff_values(left_value, right_value),
                "left": left_value,
                "right": right_value,
            }
        )
    for field in sorted(construction_fields):
        left_value = left.get("construction", {}).get(field)
        right_value = right.get("construction", {}).get(field)
        rows.append(
            {
                "field": field,
                "location": "construction",
                "status": diff_values(left_value, right_value),
                "left": left_value,
                "right": right_value,
            }
        )
    rows.sort(key=lambda row: (row["status"] == "same", row["location"], row["field"]))
    return {
        "survey": sdir.name,
        "left": {"id": left_id, "approach": left},
        "right": {"id": right_id, "approach": right},
        "differences": rows,
        "changed_count": sum(1 for row in rows if row["status"] != "same"),
    }


def render_twin_approach_diff_html(payload: dict[str, Any]) -> str:
    def pretty(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, sort_keys=True)
        return "" if value is None else str(value)

    rows = []
    for row in payload["differences"]:
        cls = row["status"]
        rows.append(
            "<tr>"
            f"<td><code>{html_escape(row['field'])}</code><div class=\"muted\">{html_escape(row['location'])}</div></td>"
            f"<td><span class=\"pill {cls}\">{html_escape(cls)}</span></td>"
            f"<td><pre>{html_escape(pretty(row['left']))}</pre></td>"
            f"<td><pre>{html_escape(pretty(row['right']))}</pre></td>"
            "</tr>"
        )
    left_name = payload["left"]["approach"].get("name")
    right_name = payload["right"]["approach"].get("name")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Twin approach diff</title>
  <style>
    body {{ font: 15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin: 32px; color:#17202a; background:#f7f8fa; }}
    main {{ max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin: 0 0 6px; font-size: 34px; }}
    .muted {{ color:#64748b; }}
    .card {{ background:#fff; border:1px solid #d8dee6; border-radius:8px; padding:20px; margin:18px 0; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; }}
    th,td {{ border:1px solid #dfe3e6; padding:10px; text-align:left; vertical-align:top; }}
    th {{ background:#f0f3f4; }}
    pre {{ white-space:pre-wrap; margin:0; font:12px/1.4 SFMono-Regular,Consolas,Menlo,monospace; max-height:220px; overflow:auto; }}
    .pill {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#eef2f6; font-size:12px; }}
    .changed,.added,.removed {{ background:#fff4cc; color:#6f4e00; }}
    .same {{ background:#edf2f7; color:#4a5568; }}
  </style>
</head>
<body>
{copy_markdown_control()}
<main>
  <h1>Twin approach diff</h1>
  <div class="muted">{html_escape(payload['survey'])}: {html_escape(left_name)} vs {html_escape(right_name)}</div>
  <section class="card"><b>{payload['changed_count']}</b> changed fields. Metadata and construction settings are compared separately.</section>
  <table>
    <thead><tr><th>Field</th><th>Status</th><th>{html_escape(left_name)}</th><th>{html_escape(right_name)}</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</main>
</body>
</html>
"""


def cmd_twin_approach_diff(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    payload = twin_approach_diff_payload(sdir, args.left, args.right)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_twin_approach_diff_html(payload)
        path = Path(args.path or f"{args.left}_vs_{args.right}_approach_diff.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
        print(str(path))
        return
    table = Table(title=f"{args.survey} twin approach diff")
    for column in ["field", "location", "status", args.left, args.right]:
        table.add_column(column)
    for row in payload["differences"]:
        if row["status"] == "same" and not args.show_same:
            continue
        table.add_row(
            str(row["field"]),
            str(row["location"]),
            str(row["status"]),
            json.dumps(row["left"], sort_keys=True) if isinstance(row["left"], (dict, list)) else str(row["left"]),
            json.dumps(row["right"], sort_keys=True) if isinstance(row["right"], (dict, list)) else str(row["right"]),
        )
    Console().print(table)


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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.path).open("w", newline="") as output_file:
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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output)
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
    path = Path(args.path or (Path("artifacts") / f"{args.survey}_executive_summary.html"))
    markdown_path = Path(args.markdown_path) if args.markdown_path else None
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


def build_executive_summary_report_context(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    questions = questions_by_name(sdir)
    heldout_names = sorted({str(row.get("heldout_question")) for row in rows if row.get("heldout_question")})
    probability_rows = read_jsonl(probability_predictions_path(sdir))
    truth_path = sdir / "committed" / "truth_marginals.json"
    one_shot_payload = build_probability_report(probability_rows, read_json(truth_path, {})) if probability_rows and truth_path.exists() else {}
    one_shot_rows = [
        row
        for row in one_shot_payload.get("rows", [])
        if not heldout_names or str(row.get("question")) in set(heldout_names)
    ]
    twin_payload = build_twin_report(rows)
    attach_twin_set_descriptions(sdir, twin_payload, rows)
    run_manifests = read_twin_run_manifest(sdir)
    job_ids = sorted({str(row.get("job_id")) for row in rows if row.get("job_id")})
    diagnostics = twin_payload.get("diagnostics", {})
    compact_failures = []
    for row in (diagnostics.get("overconfident_misses", []) + diagnostics.get("worst_misses", []))[:12]:
        compact_failures.append(
            {
                "job_id": row.get("job_id"),
                "respondent_id": row.get("respondent_id"),
                "heldout_question": row.get("heldout_question"),
                "actual_answer": row.get("actual_answer"),
                "predicted_option": top_prediction(row.get("probabilities", {}))[0] if row.get("probabilities") else row.get("predicted_option"),
                "probability_actual": row.get("probability_actual"),
                "negative_log_likelihood": row.get("negative_log_likelihood"),
                "top1_correct": row.get("top1_correct"),
                "model": row.get("model_label") or model_label(row.get("service"), row.get("model")),
            }
        )
    question_summary = []
    for question, by_model in sorted(twin_payload.get("summary_by_question", {}).items()):
        question_summary.append(
            {
                "question": question,
                "question_text": questions.get(question, {}).get("question_text"),
                "models": by_model,
            }
        )
    marginal_comparisons = [
        {
            key: row.get(key)
            for key in [
                "heldout_question",
                "model_label",
                "rows",
                "l1",
                "js_divergence",
                "top_option_agrees",
                "predicted_top_option",
                "target_top_option",
                "largest_deltas",
            ]
        }
        for row in diagnostics.get("marginal_comparisons", [])[:40]
    ]
    source_filters = {
        "survey": args.survey,
        "job_id": getattr(args, "job_id", None),
        "jobs": getattr(args, "jobs", None),
        "prediction_model": getattr(args, "prediction_model", None),
        "question": getattr(args, "question", None),
        "questions": getattr(args, "questions", None),
    }
    compact_run_manifests = []
    for run in run_manifests:
        if run.get("job_id") not in set(job_ids):
            continue
        compact_run_manifests.append(
            {
                key: run.get(key)
                for key in [
                    "job_id",
                    "status",
                    "created_at",
                    "heldout_questions",
                    "models",
                    "scenario_count",
                    "prompt_variant",
                    "context_question_count",
                    "sample_respondents",
                    "seed",
                    "complete_cases",
                    "balance_actual",
                    "stratify_actual",
                    "include_agent_material",
                    "skipped_missing_heldout_count",
                    "issue_count",
                    "extracted_count",
                ]
            }
        )
    executive_diagnostics = {
        key: result.get(key)
        for key in [
            "rows",
            "questions",
            "metrics",
            "lift",
            "empirical_lift",
            "individual_signal",
            "pairwise_ordering",
            "spearman_rank_order",
            "artifacts",
        ]
    }
    return {
        "report_kind": "frontier_generated_executive_twin_validation",
        "survey": args.survey,
        "survey_summary": survey_summary(args.survey),
        "survey_context": context_path(sdir).read_text().strip() if context_path(sdir).exists() else "",
        "source_filters": source_filters,
        "filters": {
            "job_ids": job_ids,
            "model": getattr(args, "prediction_model", None),
            "questions": heldout_names,
        },
        "heldout_questions": [
            {
                "question_name": name,
                "question_text": questions.get(name, {}).get("question_text"),
                "question_options": questions.get(name, {}).get("question_options", []),
            }
            for name in heldout_names
        ],
        "executive_diagnostics": executive_diagnostics,
        "twin_validation": {
            "summary": twin_payload.get("summary", {}),
            "summary_by_question": question_summary,
            "baseline_comparison": diagnostics.get("baseline_comparison", {}),
            "marginal_comparisons": marginal_comparisons,
            "calibration": diagnostics.get("calibration", {}),
            "failure_examples": compact_failures,
            "row_count": len(rows),
        },
        "twin_specific_diagnostics": {
            "joint_structure": diagnostics.get("joint_structure", {}),
            "subgroup_marginals": diagnostics.get("subgroup_marginals", {}),
            "conditional_consistency": diagnostics.get("conditional_consistency", {}),
            "note": "These diagnostics test capabilities that one-shot aggregate marginals cannot provide: crosstab recovery, subgroup slicing, and conditional coherence across respondent-level answers.",
        },
        "one_shot_no_persona_baseline": {
            "available": bool(one_shot_rows),
            "summary": one_shot_payload.get("summary", {}),
            "heldout_question_rows": one_shot_rows,
            "note": "This is the deployable no-persona / one-shot marginal baseline for aggregate distributions. It is not a substitute for testing twin-specific claims such as joint structure, subgroup slicing, counterfactual consistency, or reusable individual state.",
        },
        "run_manifests": compact_run_manifests,
        "context_policy_warning": (
            "Audit whether held-out prompts included strong correlates of the target. If strong correlates were present and the permutation test is null, treat that as a prompt/model conditioning problem to debug before scaling."
        ),
        "ranking_sample_warning": (
            "Treat pairwise ordering and Spearman estimates as preliminary when they are based on fewer than ten held-out questions or only a few option pairs."
        ),
        "context_size_policy": {
            "raw_prediction_rows_included": False,
            "full_diagnostics_included": False,
            "failure_examples_cap": len(compact_failures),
            "marginal_comparisons_cap": len(marginal_comparisons),
        },
    }


def build_executive_summary_report_prompt(report_context: dict[str, Any]) -> str:
    return f"""You are writing the executive interpretation for a survey digital twin validation report.

Use the recorded Expected Parrot diagnostics below. Do not invent data. The report body must be evidence-aware, but the executive summary must be written for non-technical decision makers. Avoid leading with terms such as "permutation test," "marginals," "NLL," "Brier," or "calibration." Use those terms only in the evidence section, and translate them into plain business meaning when they are necessary. Do not make claims contradicted by the diagnostics or baselines.

Write Markdown only. Do not include a top-level title. Use these sections:

## Executive Summary
Give the decision-facing bottom line in plain language. State what the twins are useful for now, what they are not ready for, and the most important caveat. Do not lead with statistical test names or metric acronyms. If respondent-specific matching was not demonstrated, say that in ordinary language, for example: "The twins captured broad response patterns, but did not reliably identify which specific respondent would choose which answer."

## Reasonable Uses
Give concrete examples of reasonable uses supported by the evidence. Examples might include using twins to explore likely reactions to draft survey questions, compare broad message directions, prioritize themes for additional research, or generate hypotheses for follow-up. Tailor the examples to the survey context and the observed validation strength.

## Uses To Avoid
Give concrete examples of uses that are not supported by the evidence. Examples might include targeting individual respondents, replacing a real survey for exact population estimates, making high-stakes allocation decisions, or claiming precise subgroup effects when the validation did not establish that level of accuracy. Tailor the examples to the survey context and failure modes.

## What The Validation Shows
Interpret the main evidence in accessible language: sample size, number of held-out questions, whether twins beat random guessing, whether they added respondent-specific information, whether they preserved option ordering or directional ranking, and where errors were concentrated. Use technical labels such as permutation test, marginal baseline, pairwise ordering, Spearman/rank diagnostics, NLL, Brier, and calibration only when needed, and immediately explain what they mean for the decision.

## Baselines And What Personas Add
Compare twins to uniform and to any available no-persona / one-shot marginal baseline, but do not frame the one-shot marginal baseline as a full substitute for twins. It is a cheap aggregate-distribution benchmark only. Explain separately whether the validation found respondent-specific signal and whether the twin-specific diagnostics support joint structure, subgroup slicing, or conditional consistency.

## Twin-Specific Capabilities
Discuss crosstab/joint-structure recovery, subgroup marginal accuracy, and conditional consistency when those diagnostics are available. Explain why these capabilities matter: segmentation, driver analysis, arbitrary slicing after validation, persistent individual state, and simulated interventions. If any of these capabilities are not tested or are weakly tested, say so plainly.

## What This Validation Does Not Yet Test
Name the untested or only partially tested claims. Include counterfactual or intervention response, longitudinal or panel reuse, simulated interviews, and any joint/subgroup/conditional diagnostics that are missing or too sparse. Add a trust-matrix-style note that joint distributions / crosstabs are a distinct capability from aggregate marginals.

## Where To Trust It
Separate uses: exact estimates, ranking/prioritization, exploratory simulation, and respondent-level targeting. Be explicit about which are supported and which are not.

## Risks And Checks Before Scaling
Discuss small held-out-question count, confidence intervals or uncertainty, context-policy/leakage risks, and whether strong correlates were available in prompts. If strong correlates were available but the permutation result is null, identify that as a prompt/model conditioning issue to investigate.

## Recommendation
Give a concise operating recommendation and next validation step.

Critical interpretation rules:
- A within-question permutation p-value near 0.5 means the twins are not showing respondent-specific matching beyond aggregate/marginal structure.
- Good lift over uniform with a null permutation test supports aggregate opinion structure, not individual predictive power.
- Pairwise option-ordering accuracy and Spearman can support directional ranking, but label them preliminary when based on few held-out questions or few option pairs.
- Do not call the empirical marginal baseline deployable for genuinely new questions; it is an oracle diagnostic because it uses observed held-out answers.
- The deployable one-shot model marginal is an aggregate baseline, not a full replacement for respondent-level twins.
- Joint distributions, subgroup slices, conditional consistency, counterfactuals, and reusable individual state are twin-specific claims. Discuss them separately from aggregate marginal prediction.
- The Reasonable Uses and Uses To Avoid sections must contain specific examples, not generic categories.
- Do not use the internal tool name in the report prose.

Recorded report context:

{json.dumps(report_context, indent=2)}
"""


def build_edsl_executive_summary_report_job_dict(
    args: argparse.Namespace,
    report_context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    prompt = build_executive_summary_report_prompt(report_context)
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = load_edsl_job_classes()
    question_name = "executive_summary_markdown"
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
        "report_kind": "executive_twin_validation",
    }
    generation = {
        "mode": "job_exported",
        "report_id": report_id,
        "model": model_label(model_specs[0][1], model_specs[0][0]) if model_specs else None,
        "models": [model_label(service_name, model_name) for model_name, service_name in model_specs],
        "report_kind": "executive_twin_validation",
    }
    context = {
        "report_id": report_id,
        "benchmark_payload": report_context.get("executive_diagnostics", {}),
        "executive_report_context": report_context,
        "generation": generation,
    }
    return job_dict, context, prompt


def cmd_twin_results_executive_summary_export(args: argparse.Namespace) -> dict[str, Any]:
    filter_args = argparse.Namespace(**vars(args))
    filter_args.model = getattr(args, "prediction_model", None)
    rows = filtered_twin_prediction_rows(filter_args)
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    path = Path(args.path or (Path("artifacts") / f"{args.survey}_executive_summary.html"))
    markdown_path = Path(args.markdown_path) if args.markdown_path else None
    result = build_executive_summary(
        rows,
        survey=args.survey,
        path=path,
        markdown_path=markdown_path,
        simulations=args.permutations,
        seed=args.seed,
    )
    report_context = build_executive_summary_report_context(args, rows, result)
    job_dict, context, prompt = build_edsl_executive_summary_report_job_dict(args, report_context)
    report_id = job_dict["zwill"]["practitioner_report_id"]
    context_bytes = len(json.dumps(context, separators=(",", ":")).encode("utf-8"))
    prompt_bytes = len(prompt.encode("utf-8"))
    data = write_practitioner_report_export(
        report_id,
        job_dict,
        context,
        prompt,
        job_path=Path(args.job_path) if args.job_path else None,
        prompt_path=Path(args.prompt_path) if args.prompt_path else None,
        context_path_arg=Path(args.context_path) if args.context_path else None,
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
            "raw_prediction_rows_in_prompt": False,
        },
        next_steps=[
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-results executive-summary-import --report-id {report_id} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
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
            hint=f"Run `zwill twin-results executive-summary-import --report-id {args.report_id} --path <results.json.gz>`.",
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
    output_path = Path(args.path) if args.path else paths["html"]
    markdown_path = Path(args.markdown_path) if args.markdown_path else output_path.with_suffix(".md")
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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_twin_job_comparison_report_html(payload)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output)
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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_twin_run_report_html(payload)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output)
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


def cmd_twin_study_run(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    approved_plan = require_twin_plan_approval(args, command="zwill twin-study run")

    job_dict = build_edsl_digital_twin_job_dict(args.survey, args)
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

    run_result = cmd_edsl_run(
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
    import_result = cmd_twin_results_import(
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
        cmd_twin_results_report(
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
    job_dict = build_edsl_digital_twin_job_dict(args.survey, args)
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
        result = cmd_twin_results_import(
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
    sdir = require_survey(args.survey)
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
    sdir = require_survey(args.survey)
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
    sdir = require_survey(args.survey)
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
            response_changes.extend(paired_twin_response_changes(all_rows, from_job_id, to_job_id))
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
    runs = []
    for study in config["studies"]:
        run_args = benchmark_study_namespace(config, study, output_dir, args.dry_run, args.replace)
        result = cmd_twin_study_run(run_args)
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
        {"benchmark": benchmark_name(config), "manifest_path": str(manifest_path), "runs": runs},
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
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "csv":
        fieldnames = list(payload["rows"][0]) if payload["rows"] else []
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.path).open("w", newline="") as output_file:
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
        Path(args.path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.path).write_text(output)
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


def extract_free_text_answer(results_dict: dict[str, Any], question_name: str) -> str:
    inspected: list[dict[str, Any]] = []
    for row in results_dict.get("data", []):
        answer = row.get("answer", {})
        if isinstance(answer, dict):
            value = answer.get(question_name)
            if value is None and answer:
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


def cmd_twin_benchmark_practitioner_report_export(args: argparse.Namespace) -> dict[str, Any]:
    config, studies = load_twin_benchmark_report_source(args)
    payload = build_twin_benchmark_report(config, studies)
    job_dict, context, prompt = build_edsl_practitioner_report_job_dict(args, payload, studies)
    report_id = job_dict["zwill"]["practitioner_report_id"]
    data = write_practitioner_report_export(
        report_id,
        job_dict,
        context,
        prompt,
        job_path=Path(args.job_path) if args.job_path else None,
        prompt_path=Path(args.prompt_path) if args.prompt_path else None,
        context_path_arg=Path(args.context_path) if args.context_path else None,
    )
    return envelope(
        "zwill twin-benchmark practitioner-report-export",
        "ok",
        data,
        next_steps=[
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-benchmark practitioner-report-import --report-id {report_id} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-benchmark practitioner-report-render --report-id {report_id}",
        ],
    )


def cmd_twin_study_practitioner_report_export(args: argparse.Namespace) -> dict[str, Any]:
    payload = build_single_survey_practitioner_payload(args.survey, args.job_id)
    studies = [{"survey": args.survey, "job_id": args.job_id}]
    job_dict, context, prompt = build_edsl_practitioner_report_job_dict(args, payload, studies)
    report_id = job_dict["zwill"]["practitioner_report_id"]
    data = write_practitioner_report_export(
        report_id,
        job_dict,
        context,
        prompt,
        job_path=Path(args.job_path) if args.job_path else None,
        prompt_path=Path(args.prompt_path) if args.prompt_path else None,
        context_path_arg=Path(args.context_path) if args.context_path else None,
    )
    return envelope(
        "zwill twin-study practitioner-report-export",
        "ok",
        data,
        next_steps=[
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-study practitioner-report-import --report-id {report_id} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-study practitioner-report-render --report-id {report_id}",
        ],
    )


def cmd_twin_benchmark_practitioner_report_import(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
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
            hint=f"Run `zwill twin-benchmark practitioner-report-import --report-id {report_id} --path <results.json.gz>`.",
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
    output_path = Path(args.path) if args.path else paths["html"]
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
    output_path = Path(args.path) if args.path else None
    prompt_path = Path(args.prompt_path) if args.prompt_path else (output_path.with_suffix(".prompt.md") if output_path else None)
    job_path = Path(args.job_path) if args.job_path else (output_path.with_suffix(".report_job.edsl.json") if output_path else None)
    job_dict, context, prompt = build_edsl_practitioner_report_job_dict(args, payload, studies)
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
    results_path = Path(args.results_path) if args.results_path else default_paths["dir"] / "results.json.gz"
    run_result = cmd_edsl_run(
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
    import_result = cmd_twin_benchmark_practitioner_report_import(
        argparse.Namespace(path=str(results_path), report_id=report_id, replace=True)
    )
    markdown = default_paths["markdown"].read_text().strip()
    markdown_path = Path(args.markdown_path) if args.markdown_path else (output_path.with_suffix(".md") if output_path else None)
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
    markdown, generation = generate_practitioner_report_markdown(args, payload, studies)
    output = render_twin_practitioner_report_html(payload, markdown, generation)
    if args.path:
        Path(args.path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.path).write_text(output)
    else:
        print(output)


def cmd_twin_study_practitioner_report(args: argparse.Namespace) -> None:
    payload = build_single_survey_practitioner_payload(args.survey, args.job_id)
    studies = [{"survey": args.survey, "job_id": args.job_id}]
    markdown, generation = generate_practitioner_report_markdown(args, payload, studies)
    output = render_twin_practitioner_report_html(payload, markdown, generation)
    if args.path:
        Path(args.path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.path).write_text(output)
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
        prompt_path=Path(args.prompt_path) if args.prompt_path else None,
        context_path_arg=Path(args.context_path) if args.context_path else None,
    )
    return envelope(
        "zwill twin-experiment report-export",
        "ok",
        data,
        next_steps=[
            f"zwill edsl-run --job {data['job_path']} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
            f"zwill twin-experiment report-import --report-id {report_id} --path {default_practitioner_report_paths(report_id)['dir'] / 'results.json.gz'}",
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
    output_path = Path(args.path) if args.path else None
    prompt_path = Path(args.prompt_path) if args.prompt_path else (output_path.with_suffix(".prompt.md") if output_path else None)
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
    results_path = Path(args.results_path) if args.results_path else default_paths["dir"] / "results.json.gz"
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
    cmd_twin_experiment_report_import(argparse.Namespace(path=str(results_path), report_id=report_id, replace=True))
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


def cmd_edsl_run(args: argparse.Namespace) -> dict[str, Any]:
    job_path = Path(args.job)
    if not job_path.exists():
        raise ZwillError("not_found", f"EDSL job file does not exist: {args.job}.")
    job_dict = read_json_or_gzip(job_path)
    if not isinstance(job_dict, dict) or job_dict.get("edsl_class_name") != "Jobs":
        raise ZwillError("invalid_input", "Expected an EDSL Jobs serialization.")

    env_path = Path(args.env_path) if getattr(args, "env_path", None) else None
    loaded_env = load_local_env(env_path)
    Jobs, RunParameters = load_edsl_runner_classes()
    job = Jobs.from_dict(job_dict)
    approved_validation_plan = (job_dict.get("zwill") or {}).get("approved_validation_plan")
    if isinstance(approved_validation_plan, dict):
        count_check = approved_validation_plan.get("export_count_check")
        if isinstance(count_check, dict) and count_check.get("requires_reapproval") and not getattr(args, "allow_count_delta", False):
            raise ZwillError(
                "approval_required",
                "Exported validation job prediction count differs from the approved plan.",
                context=count_check,
                hint="Review the exported count, re-approve the plan, or pass --allow-count-delta for an explicit debug run.",
            )
    run_parameters = {}
    if args.n is not None:
        run_parameters["n"] = args.n
    if args.progress_bar:
        run_parameters["progress_bar"] = True
    if args.fresh:
        run_parameters["fresh"] = True
    if args.stop_on_exception:
        run_parameters["stop_on_exception"] = True
    if args.check_api_keys:
        run_parameters["check_api_keys"] = True
    if args.verbose is not None:
        run_parameters["verbose"] = args.verbose
    if args.print_exceptions is not None:
        run_parameters["print_exceptions"] = args.print_exceptions
    if args.offload_execution:
        run_parameters["offload_execution"] = True
    if args.use_api_proxy:
        run_parameters["use_api_proxy"] = True
    for item in args.run_param or []:
        if "=" not in item:
            raise ZwillError("invalid_input", f"Invalid run parameter: {item}.", hint="Use key=value.")
        key, value = item.split("=", 1)
        if key not in RunParameters.__dataclass_fields__:
            raise ZwillError(
                "invalid_input",
                f"Unknown EDSL run parameter: {key}.",
                context={"available_parameters": sorted(RunParameters.__dataclass_fields__)},
            )
        run_parameters[key] = parse_model_param_value(value)

    output_path = Path(args.path)
    if args.dry_run:
        return envelope(
            "zwill edsl-run",
            "ok",
            {
                "job_path": str(job_path),
                "results_path": str(output_path),
                "dry_run": True,
                "scenario_count": len(job.scenarios),
                "model_count": len(job.models),
                "question_count": len(job.survey.questions),
                "probability_job_id": job_dict.get("zwill", {}).get("probability_job_id"),
                "digital_twin_job_id": job_dict.get("zwill", {}).get("digital_twin_job_id"),
                "rank_utility_twin_job_id": job_dict.get("zwill", {}).get("rank_utility_twin_job_id"),
                "agent_study_job_id": job_dict.get("zwill", {}).get("agent_study_job_id"),
                "practitioner_report_id": job_dict.get("zwill", {}).get("practitioner_report_id"),
                "run_parameters": run_parameters,
                "loaded_env": loaded_env,
            },
        )

    results = job.run(**run_parameters) if run_parameters else job.run()
    if results is None:
        raise ZwillError("edsl_run_failed", "EDSL job did not return a Results object.")
    results_dict = results.to_dict()
    if job_dict.get("zwill"):
        results_dict["zwill"] = job_dict["zwill"]
    write_json_or_gzip(output_path, results_dict)
    return envelope(
        "zwill edsl-run",
        "ok",
        {
            "job_path": str(job_path),
            "results_path": str(output_path),
            "result_count": len(results_dict.get("data", [])),
            "probability_job_id": results_dict.get("zwill", {}).get("probability_job_id"),
            "digital_twin_job_id": results_dict.get("zwill", {}).get("digital_twin_job_id"),
            "rank_utility_twin_job_id": results_dict.get("zwill", {}).get("rank_utility_twin_job_id"),
            "agent_study_job_id": results_dict.get("zwill", {}).get("agent_study_job_id"),
            "practitioner_report_id": results_dict.get("zwill", {}).get("practitioner_report_id"),
            "run_parameters": run_parameters,
            "loaded_env": loaded_env,
        },
        next_steps=[
            (
                f"zwill twin-benchmark practitioner-report-import --path {output_path}"
                if results_dict.get("zwill", {}).get("practitioner_report_id")
                else f"zwill twin-results import --survey <survey> --path {output_path}"
                if results_dict.get("zwill", {}).get("digital_twin_job_id")
                else f"zwill twin-results import --survey <survey> --path {output_path}"
                if results_dict.get("zwill", {}).get("rank_utility_twin_job_id")
                else f"zwill prob-results import --survey <survey> --path {output_path}"
                if results_dict.get("zwill", {}).get("probability_job_id")
                else f"zwill agent-study import --path {output_path}"
                if results_dict.get("zwill", {}).get("agent_study_job_id")
                else f"zwill prob-results import --survey <survey> --path {output_path}"
            )
        ],
    )


def cmd_edsl_export(args: argparse.Namespace) -> None:
    if args.target == "survey":
        export_dict = build_edsl_survey_dict(args.survey)
    elif args.target == "agent-list":
        export_dict = build_edsl_agent_list_dict(args.survey, args)
    elif args.target == "probability-job":
        export_dict = build_edsl_probability_job_dict(args.survey, args)
    elif args.target == "rank-utility-twin-job":
        approved_plan = require_twin_plan_approval(args, command="zwill edsl-export --target rank-utility-twin-job")
        export_dict = build_edsl_rank_utility_twin_job_dict(args.survey, args)
        if approved_plan:
            export_dict.setdefault("zwill", {})["approved_validation_plan"] = approved_plan
    else:
        approved_plan = require_twin_plan_approval(args, command="zwill edsl-export --target twin-probability-job")
        export_dict = build_edsl_digital_twin_job_dict(args.survey, args)
        if approved_plan:
            export_dict.setdefault("zwill", {})["approved_validation_plan"] = approved_plan
    output = json.dumps(export_dict, indent=2)
    if args.path:
        path = Path(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n")
    print(output)


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zwill")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("init")
    p.set_defaults(func=cmd_init)

    p = subparsers.add_parser("status")
    p.set_defaults(func=cmd_status)

    report = subparsers.add_parser("report").add_subparsers(dest="report_command", required=True)
    def add_report_build_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--survey", required=True)
        parser.add_argument("--path", help="Directory to write the report bundle. Defaults to <survey>_report/.")
        parser.add_argument("--job-id", action="append", help="Digital twin job id to include. Repeatable.")
        parser.add_argument("--jobs", help="Comma-separated digital twin job ids to include.")
        parser.add_argument("--model", help="Digital twin model or model label to include.")
        parser.add_argument("--audit-job-id", help="Twin job id to use for the run audit page. Defaults to the first selected twin job.")
        parser.add_argument("--example-limit", type=int, default=6, help="Maximum prompt examples to include in the audit page.")
        parser.add_argument("--probability-job-id", help="One-shot probability job id to include.")
        parser.add_argument("--probability-model", help="One-shot probability model or model label to include.")
        parser.add_argument(
            "--permutations",
            type=int,
            default=DEFAULT_REPORT_PERMUTATIONS,
            help=(
                "Permutation simulations for executive-summary chance tests. "
                f"Defaults to {DEFAULT_REPORT_PERMUTATIONS}; pass a larger value for slower, higher-resolution diagnostics."
            ),
        )
        parser.add_argument("--seed", type=int, default=20260701, help="Random seed for simulation diagnostics.")

    p = report.add_parser("list", help="List available reports for a survey and show readiness/suggested commands.")
    p.add_argument("--survey", required=True)
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.add_argument("--path", help="Write JSON catalog output to this path.")
    p.set_defaults(func=cmd_report_list, table_output=True)
    p = report.add_parser("build", help="Build an incremental HTML report folder with an index page and all ready report pages.")
    add_report_build_args(p)
    p.set_defaults(func=cmd_report_build)
    p = report.add_parser("facts", help="Build deterministic fact artifacts for the report bundle.")
    add_report_build_args(p)
    p.set_defaults(func=cmd_report_facts)
    p = report.add_parser("analyze", help="Build deterministic analysis artifacts for the report bundle.")
    add_report_build_args(p)
    p.set_defaults(func=cmd_report_analyze)
    p = report.add_parser("render", help="Render report HTML from available facts and analysis; use --final to enforce generated-analysis gating.")
    add_report_build_args(p)
    p.add_argument("--final", action="store_true", help="Fail unless generated executive analysis is available.")
    p.set_defaults(func=cmd_report_render)

    project = subparsers.add_parser("project").add_subparsers(dest="project_command", required=True)
    p = project.add_parser("create", help="Create a project under .zwill/projects/.")
    p.add_argument("project_id")
    p.add_argument("--title")
    p.add_argument("--use", action="store_true", help="Set the new project as the active project.")
    p.set_defaults(func=cmd_project_create)
    p = project.add_parser("use", help="Set the active project.")
    p.add_argument("project_id")
    p.set_defaults(func=cmd_project_use)
    p = project.add_parser("current", help="Show the active project.")
    p.set_defaults(func=cmd_project_current)
    p = project.add_parser("list", help="List projects.")
    p.set_defaults(func=cmd_project_list)
    p = project.add_parser("show", help="Show project details.")
    p.add_argument("project_id", nargs="?")
    p.set_defaults(func=cmd_project_show)

    p = subparsers.add_parser("commit")
    p.add_argument("--survey", required=True)
    p.set_defaults(func=cmd_commit)

    p = subparsers.add_parser("table")
    p.add_argument("--survey", required=True)
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_table, table_output=True)

    p = subparsers.add_parser("edsl-export")
    p.add_argument("--survey", required=True)
    p.add_argument("--path")
    p.add_argument("--target", choices=["survey", "agent-list", "probability-job", "twin-probability-job", "rank-utility-twin-job"], default="survey")
    p.add_argument("--question", action="append", help="Question name to include. Repeatable.")
    p.add_argument("--questions", help="Comma-separated question names to include.")
    p.add_argument("--exclude-question", action="append", help="Question name to exclude. Repeatable.")
    p.add_argument("--limit", type=int, help="Maximum number of agents to export.")
    p.add_argument(
        "--model",
        action="append",
        help="EDSL model for probability-job exports. Repeatable. Use service:model to set service per model.",
    )
    p.add_argument("--models", help="Comma-separated EDSL models for probability-job exports. Entries may be service:model.")
    p.add_argument("--service-name", help="EDSL service_name for unqualified probability-job models.")
    p.add_argument(
        "--model-param",
        action="append",
        help="Model parameter for probability-job exports. Use key=value for all models or service:model:key=value for one model. Repeatable.",
    )
    p.add_argument("--job-question-name", default="response_probabilities", help="Question name for probability-job exports.")
    p.add_argument("--heldout-question", action="append", help="Held-out question for twin-probability-job exports. Repeatable.")
    p.add_argument("--heldout-questions", help="Comma-separated held-out questions for twin-probability-job exports.")
    p.add_argument("--rank-task-id", action="append", help="Rank task id for rank-utility-twin-job exports. Repeatable.")
    p.add_argument("--rank-task-ids", help="Comma-separated rank task ids for rank-utility-twin-job exports.")
    p.add_argument("--approved-plan", help="Approved twin validation plan JSON required for twin-probability-job exports.")
    p.add_argument("--allow-unapproved", action="store_true", help="Explicitly allow an ad hoc twin export without an approved validation plan.")
    p.add_argument("--respondent", action="append", help="Respondent id to include in twin-probability-job exports. Repeatable.")
    p.add_argument("--respondents", help="Comma-separated respondent ids to include in twin-probability-job exports.")
    p.add_argument("--sample-respondents", type=int, help="Randomly sample this many respondents for twin-probability-job exports.")
    p.add_argument("--seed", type=int, help="Seed for --sample-respondents.")
    p.add_argument("--complete-cases", action="store_true", help="Only include respondents with answers for all selected context and held-out questions.")
    p.add_argument("--allow-missing-actual", action="store_true", help="Allow twin/rank exports whose validation targets are missing for some respondents.")
    p.add_argument("--balance-actual", action="store_true", help="Balance sampled respondents across actual held-out answer options.")
    p.add_argument("--stratify-actual", action="store_true", help="Sample respondents within actual held-out answer strata.")
    p.add_argument("--limit-respondents", type=int, help="Maximum number of respondents for twin-probability-job exports.")
    p.add_argument("--context-question", action="append", help="Question name to use as twin context. Repeatable.")
    p.add_argument("--context-questions", help="Comma-separated question names to use as twin context.")
    p.add_argument("--exclude-context-question", action="append", help="Question name to exclude from twin context. Repeatable.")
    p.add_argument("--leakage-exclusion", action="append", help="Target-specific context exclusion as heldout_question:context_question. Repeatable.")
    p.add_argument("--context-question-count", type=int, help="Maximum number of context questions per respondent.")
    p.add_argument("--include-survey-context", action="store_true", help="Include survey context markdown in exported EDSL Agent instructions.")
    p.add_argument("--include-agent-material", action="store_true", help="Include non-survey agent construction material in agent-list or twin job exports.")
    p.add_argument("--agent-material-kind", action="append", help="Only include agent material of this kind. Repeatable or comma-separated.")
    p.add_argument("--agent-material-tag", action="append", help="Only include agent material with this tag. Repeatable or comma-separated.")
    p.add_argument("--max-agent-material-chars", type=int, help="Maximum agent material characters per respondent.")
    p.add_argument("--twin-material", action="append", help="Supplemental twin material file. Repeatable. Supports Markdown, JSON, or JSONL.")
    p.add_argument("--max-twin-material-chars", type=int, help="Maximum supplemental twin material characters per scenario.")
    p.add_argument(
        "--prompt-variant",
        choices=["raw", "answer-commonness-confidence"],
        default="raw",
        help="Twin prompt variant for twin-probability-job exports.",
    )
    p.add_argument("--traits-presentation-template", help="Jinja template for presenting AgentList traits in EDSL prompts.")
    p.add_argument("--traits-presentation-template-path", help="Path to a Jinja template for presenting AgentList traits in EDSL prompts.")
    p.add_argument("--no-default-traits-presentation-template", action="store_true", help="Use EDSL's default Agent trait presentation instead of zwill's survey-answer template.")
    p.set_defaults(func=cmd_edsl_export, raw_output=True)

    agent_list = subparsers.add_parser("agent-list").add_subparsers(dest="agent_list_command", required=True)
    p = agent_list.add_parser("inspect", help="Inspect an exported EDSL AgentList JSON file.")
    p.add_argument("--path", required=True, help="Path to an EDSL AgentList JSON file.")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_agent_list_inspect, table_output=True)

    agent_study = subparsers.add_parser("agent-study").add_subparsers(dest="agent_study_command", required=True)
    p = agent_study.add_parser("export", help="Export an EDSL job that asks an exported AgentList a new question.")
    p.add_argument("--agent-list", required=True, help="Path to an exported EDSL AgentList JSON file.")
    p.add_argument("--path", help="Path to write the exported EDSL Jobs JSON.")
    question_group = p.add_mutually_exclusive_group(required=True)
    question_group.add_argument("--question-path", help="Path to a JSON question spec or EDSL Question serialization.")
    question_group.add_argument("--question-name", help="Question name for an inline question spec.")
    p.add_argument("--question-type", help="Question type for an inline question spec.")
    p.add_argument("--question-text", help="Question text for an inline question spec.")
    p.add_argument("--question-option", action="append", help="Question option for inline multiple-choice specs. Repeatable.")
    p.add_argument("--model", action="append", help="EDSL model. Repeatable. Use service:model to set service per model.")
    p.add_argument("--models", help="Comma-separated EDSL models. Entries may be service:model.")
    p.add_argument("--service-name", help="EDSL service_name for unqualified models.")
    p.add_argument("--model-param", action="append", help="Model parameter. Use key=value or service:model:key=value. Repeatable.")
    p.set_defaults(func=cmd_agent_study_export, raw_output=True)
    p = agent_study.add_parser("import", help="Import serialized EDSL Results from an agent-study job.")
    p.add_argument("--path", required=True)
    p.add_argument("--job-id", help="Override the agent-study job id.")
    p.add_argument("--replace", action="store_true", help="Replace an existing imported result set with the same job id.")
    p.set_defaults(func=cmd_agent_study_import)
    p = agent_study.add_parser("report", help="Report imported agent-study answers.")
    p.add_argument("--job-id")
    p.add_argument("--model")
    p.add_argument("--format", choices=["table", "json", "csv", "html"], default="table")
    p.add_argument("--path", help="Write json/csv/html report output to this path.")
    p.set_defaults(func=cmd_agent_study_report, table_output=True)
    p = agent_study.add_parser("list", help="List imported agent-study runs.")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_agent_study_list, table_output=True)
    p = agent_study.add_parser("show", help="Show metadata for one imported agent-study run.")
    p.add_argument("--job-id", required=True)
    p.add_argument("--include-summary", action="store_true")
    p.set_defaults(func=cmd_agent_study_show)

    p = subparsers.add_parser("edsl-run")
    p.add_argument("--job", required=True, help="Path to an exported EDSL Jobs JSON file.")
    p.add_argument("--path", required=True, help="Path to write the serialized EDSL Results object. Use .gz for gzip.")
    p.add_argument("--env-path", help="Explicit .env file to load before importing EDSL. Defaults to nearest .env above the current directory.")
    p.add_argument("--n", type=int)
    p.add_argument("--progress-bar", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--stop-on-exception", action="store_true")
    p.add_argument("--check-api-keys", action="store_true")
    p.add_argument("--verbose", action=argparse.BooleanOptionalAction)
    p.add_argument("--print-exceptions", action=argparse.BooleanOptionalAction)
    p.add_argument("--offload-execution", action="store_true", help="Run through EDSL offloaded execution.")
    p.add_argument("--use-api-proxy", action="store_true", help="Use EDSL API proxy.")
    p.add_argument("--allow-count-delta", action="store_true", help="Run an approved validation job even when export count differs from the approved plan.")
    p.add_argument("--run-param", action="append", help="Additional EDSL RunParameters key=value. Repeatable.")
    p.add_argument("--dry-run", action="store_true", help="Load and validate the job without running API calls.")
    p.set_defaults(func=cmd_edsl_run)

    workflow = subparsers.add_parser("workflow").add_subparsers(dest="workflow_command", required=True)
    p = workflow.add_parser("run", help="Run a declarative workflow file and capture step artifacts.")
    p.add_argument("path", help="Path to a workflow .json/.yaml/.yml file.")
    p.add_argument("--var", action="append", help="Override workflow variable. Use key=value. Repeatable.")
    p.add_argument("--artifacts-dir", help="Directory for stdout/stderr files and manifest.json.")
    p.add_argument("--resume", action="store_true", help="Skip steps that already succeeded in the existing manifest.")
    p.set_defaults(func=cmd_workflow_run)
    p = workflow.add_parser("dry-run", help="Render a workflow file without executing commands.")
    p.add_argument("path", help="Path to a workflow .json/.yaml/.yml file.")
    p.add_argument("--var", action="append", help="Override workflow variable. Use key=value. Repeatable.")
    p.set_defaults(func=cmd_workflow_dry_run)
    p = workflow.add_parser("explain", help="Show rendered workflow steps without executing commands.")
    p.add_argument("path", help="Path to a workflow .json/.yaml/.yml file.")
    p.add_argument("--var", action="append", help="Override workflow variable. Use key=value. Repeatable.")
    p.set_defaults(func=cmd_workflow_explain)
    p = workflow.add_parser("pew-demo", help="Build the persistent PEW W154 DIFF1 demo project.")
    p.add_argument("--source-dir", help="Directory containing W154_DIFF1_metadata.json and W154_DIFF1_respondents.csv.")
    p.add_argument("--workdir", help="Persistent workdir to create or update.")
    p.add_argument("--fresh", action=argparse.BooleanOptionalAction, default=True, help="Clear prior .zwill state and imports first.")
    p.add_argument("--no-edsl", action="store_true", help="Skip EDSL survey and probability job export.")
    p.add_argument("--results-path", help="Serialized EDSL Results JSON or JSON.GZ to import after building the survey.")
    p.add_argument("--job-id", help="Override probability job id when importing --results-path.")
    p.add_argument("--question", action="append", help="Question name to include in the probability job. Repeatable.")
    p.add_argument("--questions", help="Comma-separated question names to include in the probability job.")
    p.add_argument("--exclude-question", action="append", help="Question name to exclude from the probability job. Repeatable.")
    p.add_argument("--model", action="append", default=["openai:gpt-5.5", "google:gemini-2.5-pro"], help="EDSL model for probability-job export. Repeatable.")
    p.add_argument("--models", help="Comma-separated EDSL models for probability-job export. Entries may be service:model.")
    p.add_argument("--service-name", help="EDSL service_name for unqualified probability-job models.")
    p.add_argument(
        "--model-param",
        action="append",
        default=[
            "google:gemini-2.5-pro:max_tokens=8192",
            "google:gemini-2.5-pro:thinking_budget=4096",
            "google:gemini-2.5-pro:temperature=0",
        ],
        help="Model parameter for probability-job export. Repeatable.",
    )
    p.add_argument("--job-question-name", default="response_probabilities")
    p.set_defaults(func=cmd_workflow_pew_demo)

    prob_results = subparsers.add_parser("prob-results").add_subparsers(dest="prob_results_command", required=True)
    p = prob_results.add_parser("import")
    p.add_argument("--survey", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--job-id")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_probability_results_import)
    p = prob_results.add_parser("report")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id")
    p.add_argument("--model")
    p.add_argument("--format", choices=["table", "json", "html", "csv"], default="table")
    p.add_argument("--path", help="Write json/html/csv report output to this path.")
    p.set_defaults(func=cmd_probability_results_report, table_output=True)
    p = prob_results.add_parser("analysis-export", help="Export an EDSL job that writes the one-shot marginal analysis.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", help="Probability job id to include.")
    p.add_argument("--probability-model", help="Probability prediction model or model label to include.")
    p.add_argument("--path", help="Final HTML report path to suggest in next steps.")
    p.add_argument("--job-path", help="Write the EDSL report-generation job to this path.")
    p.add_argument("--prompt-path", help="Write the model report prompt to this path.")
    p.add_argument("--context-path", help="Write the assembled report context to this path.")
    p.add_argument("--model", "--report-model", action="append", dest="model", help="EDSL model for report generation. Repeatable.")
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.add_argument(
        "--model-param",
        action="append",
        default=["max_tokens=10000", "reasoning_effort=high"],
        help="Report-generation model parameter. Use key=value or service:model:key=value.",
    )
    p.set_defaults(func=cmd_probability_results_analysis_export)
    p = prob_results.add_parser("analysis-import", help="Import EDSL Results from a one-shot analysis report-generation job.")
    p.add_argument("--path", required=True, help="Serialized EDSL Results JSON or JSON.GZ.")
    p.add_argument("--report-id", help="Report id. Inferred from Results metadata when present.")
    p.add_argument("--replace", action="store_true", help="Replace an existing imported report result.")
    p.set_defaults(func=cmd_probability_results_analysis_import)
    p = prob_results.add_parser("analysis-render", help="Render imported generated one-shot analysis Markdown into the one-shot report HTML.")
    p.add_argument("--report-id", required=True)
    p.add_argument("--path", help="Write standalone HTML report output to this path. Defaults to .zwill/practitioner_reports/<id>/report.html.")
    p.set_defaults(func=cmd_probability_results_analysis_render)

    twin_results = subparsers.add_parser("twin-results").add_subparsers(dest="twin_results_command", required=True)
    p = twin_results.add_parser("import")
    p.add_argument("--survey", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--job-id")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--allow-missing-actual", action="store_true", help="Import true holdout predictions whose scenarios omit actual_answer.")
    p.set_defaults(func=cmd_twin_results_import)
    p = twin_results.add_parser("export", help="Export stored digital twin predictions to CSV.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", action="append", help="Job id to export. Repeatable.")
    p.add_argument("--jobs", help="Comma-separated job ids to export.")
    p.add_argument("--model", help="Model or model label to export.")
    p.add_argument("--question", action="append", help="Held-out question to export. Repeatable.")
    p.add_argument("--questions", help="Comma-separated held-out questions to export.")
    p.add_argument("--format", choices=["long", "wide"], default="long")
    p.add_argument("--path", help="Write CSV output to this path.")
    p.set_defaults(func=cmd_twin_results_export, table_output=True)
    p = twin_results.add_parser("package", help="Export stored digital twin predictions to CSV and zip it.")
    p.add_argument("--survey", required=True)
    p.add_argument("--manifest", help="Import/export manifest whose job ids should be packaged.")
    p.add_argument("--job-id", action="append", help="Job id to package. Repeatable.")
    p.add_argument("--jobs", help="Comma-separated job ids to package.")
    p.add_argument("--model", help="Model or model label to package.")
    p.add_argument("--question", action="append", help="Held-out question to package. Repeatable.")
    p.add_argument("--questions", help="Comma-separated held-out questions to package.")
    p.add_argument("--format", choices=["long", "wide"], default="long")
    p.add_argument("--path", required=True, help="Write CSV output to this path.")
    p.add_argument("--zip-path", help="Write zip output to this path. Defaults to --path with .zip suffix.")
    p.set_defaults(func=cmd_twin_results_package)
    p = twin_results.add_parser("report")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", action="append", help="Digital twin job id. Repeatable.")
    p.add_argument("--jobs", help="Comma-separated digital twin job ids.")
    p.add_argument("--model")
    p.add_argument("--format", choices=["table", "json", "csv", "html"], default="table")
    p.add_argument("--view", choices=["summary", "full"], default="full", help="HTML report view. `summary` writes a compact high-level diagnostics page; `full` includes row-level audit details.")
    p.add_argument("--path", help="Write json/html/csv report output to this path.")
    p.set_defaults(func=cmd_twin_results_report, table_output=True)
    p = twin_results.add_parser("rank-report", help="Report rank-utility digital twin validation results.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", help="Rank utility twin job id.")
    p.add_argument("--rank-task-id", action="append", help="Rank task id to include. Repeatable.")
    p.add_argument("--model", help="Model or model label to include.")
    p.add_argument("--format", choices=["table", "json", "csv", "html"], default="table")
    p.add_argument("--path", help="Write json/html/csv report output to this path.")
    p.set_defaults(func=cmd_rank_results_report, table_output=True)
    p = twin_results.add_parser("executive-summary", help="Generate an executive twin validation summary with plots and diagnostics.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", action="append", help="Digital twin job id. Repeatable.")
    p.add_argument("--jobs", help="Comma-separated digital twin job ids.")
    p.add_argument("--model", help="Model or model label to include.")
    p.add_argument("--question", action="append", help="Held-out question to include. Repeatable.")
    p.add_argument("--questions", help="Comma-separated held-out questions to include.")
    p.add_argument("--path", help="Write HTML report output to this path. Defaults to artifacts/<survey>_executive_summary.html.")
    p.add_argument("--markdown-path", help="Write Markdown companion output to this path. Defaults to --path with .md suffix.")
    p.add_argument("--permutations", type=int, default=20000, help="Permutation simulations for chance tests.")
    p.add_argument("--seed", type=int, default=20260701, help="Random seed for simulation diagnostics.")
    p.set_defaults(func=cmd_twin_results_executive_summary)
    p = twin_results.add_parser("executive-summary-export", help="Export an EDSL job that writes the executive twin validation interpretation.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", action="append", help="Digital twin job id. Repeatable.")
    p.add_argument("--jobs", help="Comma-separated digital twin job ids.")
    p.add_argument("--prediction-model", dest="prediction_model", help="Twin prediction model/model label to include.")
    p.add_argument("--question", action="append", help="Held-out question to include. Repeatable.")
    p.add_argument("--questions", help="Comma-separated held-out questions to include.")
    p.add_argument("--path", help="Final HTML report path to suggest in next steps. A deterministic diagnostics preview is written here during export.")
    p.add_argument("--markdown-path", help="Deterministic diagnostics Markdown path during export.")
    p.add_argument("--job-path", help="Write the EDSL report-generation job to this path.")
    p.add_argument("--prompt-path", help="Write the model report prompt to this path.")
    p.add_argument("--context-path", help="Write the assembled report context to this path.")
    p.add_argument("--permutations", type=int, default=20000, help="Permutation simulations for chance tests.")
    p.add_argument("--seed", type=int, default=20260701, help="Random seed for simulation diagnostics.")
    p.add_argument("--model", "--report-model", action="append", dest="model", help="EDSL model for report generation. Repeatable.")
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.add_argument(
        "--model-param",
        action="append",
        default=["max_tokens=12000", "reasoning_effort=high"],
        help="Report-generation model parameter. Use key=value or service:model:key=value.",
    )
    p.set_defaults(func=cmd_twin_results_executive_summary_export)
    p = twin_results.add_parser("executive-summary-import", help="Import EDSL Results from an executive summary report-generation job.")
    p.add_argument("--path", required=True, help="Serialized EDSL Results JSON or JSON.GZ.")
    p.add_argument("--report-id", help="Executive report id. Inferred from Results metadata when present.")
    p.add_argument("--replace", action="store_true", help="Replace an existing imported report result.")
    p.set_defaults(func=cmd_twin_results_executive_summary_import)
    p = twin_results.add_parser("executive-summary-render", help="Render imported generated executive summary Markdown as HTML.")
    p.add_argument("--report-id", required=True)
    p.add_argument("--path", help="Write standalone HTML report output to this path. Defaults to .zwill/practitioner_reports/<id>/report.html.")
    p.add_argument("--markdown-path", help="Write the generated Markdown companion to this path. Defaults to --path with .md suffix.")
    p.add_argument("--permutations", type=int, default=20000, help="Permutation simulations for regenerated diagnostic artifacts.")
    p.add_argument("--seed", type=int, default=20260701, help="Random seed for regenerated diagnostic artifacts.")
    p.set_defaults(func=cmd_twin_results_executive_summary_render)
    p = twin_results.add_parser("compare-report", help="Compare two or more imported digital twin jobs side by side.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", action="append", help="Digital twin job id. Repeatable.")
    p.add_argument("--jobs", help="Comma-separated digital twin job ids.")
    p.add_argument("--model", help="Restrict to one model or model label.")
    p.add_argument("--format", choices=["table", "json", "html"], default="html")
    p.add_argument("--path", help="Write json/html output to this path.")
    p.set_defaults(func=cmd_twin_results_compare_report, table_output=True)
    p = twin_results.add_parser("run-report", help="Audit a digital twin job's construction, import, and prompt examples.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", required=True, help="Digital twin job id.")
    p.add_argument("--format", choices=["table", "json", "html"], default="html")
    p.add_argument("--path", help="Write json/html output to this path.")
    p.add_argument("--example-limit", type=int, default=6, help="Maximum prompt examples to include.")
    p.set_defaults(func=cmd_twin_results_run_report, table_output=True)
    p = twin_results.add_parser("calibrate-marginal", help="KL/IPF calibrate twin probabilities to target question marginals.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", required=True, help="Source digital twin job id.")
    p.add_argument("--model", help="Source model or model label to calibrate.")
    p.add_argument("--question", action="append", help="Held-out question to calibrate. Repeatable.")
    p.add_argument("--questions", help="Comma-separated held-out questions to calibrate.")
    p.add_argument("--target", choices=["probability-job", "empirical"], default="probability-job")
    p.add_argument("--target-job-id", help="Target probability job id when --target probability-job.")
    p.add_argument("--target-probability-job-id", help=argparse.SUPPRESS)
    p.add_argument("--target-model", help="Target probability model or model label when the target job has multiple rows per question.")
    p.add_argument("--output-job-id", help="Job id for the derived calibrated twin rows.")
    p.add_argument("--max-iter", type=int, default=10000)
    p.add_argument("--tolerance", type=float, default=1e-12)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_twin_results_calibrate_marginal)
    p = twin_results.add_parser("marginal-diagnostics", help="Compare twin-implied aggregate marginals to one-shot or empirical marginals.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", action="append", help="Digital twin job id. Repeatable.")
    p.add_argument("--jobs", help="Comma-separated digital twin job ids.")
    p.add_argument("--model", help="Twin model or model label.")
    p.add_argument("--question", action="append", help="Held-out question to compare. Repeatable.")
    p.add_argument("--questions", help="Comma-separated held-out questions to compare.")
    p.add_argument("--target", choices=["probability-job", "empirical"], default="probability-job")
    p.add_argument("--target-job-id", help="Probability job id when --target probability-job.")
    p.add_argument("--target-model", help="Probability model or model label when the target job has multiple rows per question.")
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.add_argument("--path", help="Write summary json/csv output to this path.")
    p.add_argument("--option-path", help="Write option-level CSV output when --format csv.")
    p.set_defaults(func=cmd_twin_results_marginal_diagnostics, table_output=True)

    twin_study = subparsers.add_parser("twin-study").add_subparsers(dest="twin_study_command", required=True)
    p = twin_study.add_parser("run", help="Export, run, import, and report a digital twin probability study.")
    p.add_argument("--survey", required=True)
    p.add_argument("--output-dir", default=".", help="Directory for default job/results/report paths.")
    p.add_argument("--job-path", help="Path to write the exported EDSL Jobs JSON.")
    p.add_argument("--results-path", help="Path to write the serialized EDSL Results object. Use .gz for gzip.")
    p.add_argument("--report-html", help="Path to write the HTML report. Defaults to output-dir/<survey>_twin_<job_id>_report.html.")
    p.add_argument("--report-json", help="Optional path to write the JSON report.")
    p.add_argument("--report-csv", help="Optional path to write the CSV report.")
    p.add_argument("--replace", action="store_true", help="Replace an existing imported result set with the same job id.")
    p.add_argument("--dry-run", action="store_true", help="Export the job and report planned paths without running model calls.")
    p.add_argument("--approved-plan", help="Approved twin validation plan JSON required before exporting or running a twin study.")
    p.add_argument("--allow-unapproved", action="store_true", help="Explicitly allow an ad hoc/debug twin study without an approved validation plan.")
    p.add_argument("--question", action="append", help=argparse.SUPPRESS)
    p.add_argument("--questions", help=argparse.SUPPRESS)
    p.add_argument("--exclude-question", action="append", help=argparse.SUPPRESS)
    p.add_argument("--heldout-question", action="append", help="Held-out question. Repeatable.")
    p.add_argument("--heldout-questions", help="Comma-separated held-out questions.")
    p.add_argument("--respondent", action="append", help="Respondent id to include. Repeatable.")
    p.add_argument("--respondents", help="Comma-separated respondent ids to include.")
    p.add_argument("--sample-respondents", type=int, help="Randomly sample this many respondents.")
    p.add_argument("--seed", type=int, help="Seed for --sample-respondents.")
    p.add_argument("--complete-cases", action="store_true", help="Only include respondents with all selected context and held-out answers.")
    p.add_argument("--balance-actual", action="store_true", help="Balance sampled respondents across actual held-out answer options.")
    p.add_argument("--stratify-actual", action="store_true", help="Sample respondents within actual-answer strata.")
    p.add_argument("--limit-respondents", type=int, help="Maximum number of respondents.")
    p.add_argument("--context-question", action="append", help="Question name to use as context. Repeatable.")
    p.add_argument("--context-questions", help="Comma-separated context question names.")
    p.add_argument("--exclude-context-question", action="append", help="Question name to exclude from context. Repeatable.")
    p.add_argument("--leakage-exclusion", action="append", help="Target-specific context exclusion as heldout_question:context_question. Repeatable.")
    p.add_argument("--context-question-count", type=int, help="Maximum number of context questions per respondent.")
    p.add_argument("--include-agent-material", action="store_true", help="Include non-survey agent construction material in twin prompts.")
    p.add_argument("--agent-material-kind", action="append", help="Only include agent material of this kind. Repeatable or comma-separated.")
    p.add_argument("--agent-material-tag", action="append", help="Only include agent material with this tag. Repeatable or comma-separated.")
    p.add_argument("--max-agent-material-chars", type=int, help="Maximum agent material characters per respondent.")
    p.add_argument("--twin-material", action="append", help="Supplemental twin material file. Repeatable. Supports Markdown, JSON, or JSONL.")
    p.add_argument("--max-twin-material-chars", type=int, help="Maximum supplemental twin material characters per scenario.")
    p.add_argument(
        "--prompt-variant",
        choices=["raw", "answer-commonness-confidence"],
        default="raw",
        help="Twin prompt variant.",
    )
    p.add_argument("--model", action="append", help="EDSL model. Repeatable. Use service:model to set service per model.")
    p.add_argument("--models", help="Comma-separated EDSL models. Entries may be service:model.")
    p.add_argument("--service-name", help="EDSL service_name for unqualified models.")
    p.add_argument("--model-param", action="append", help="Model parameter. Use key=value or service:model:key=value. Repeatable.")
    p.add_argument("--job-question-name", default="response_probabilities")
    p.add_argument("--n", type=int)
    p.add_argument("--progress-bar", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--stop-on-exception", action="store_true")
    p.add_argument("--check-api-keys", action="store_true")
    p.add_argument("--verbose", action=argparse.BooleanOptionalAction)
    p.add_argument("--print-exceptions", action=argparse.BooleanOptionalAction)
    p.add_argument("--offload-execution", action="store_true")
    p.add_argument("--use-api-proxy", action="store_true")
    p.add_argument("--run-param", action="append", help="Additional EDSL RunParameters key=value. Repeatable.")
    p.set_defaults(func=cmd_twin_study_run)
    p = twin_study.add_parser("export-holdout", help="Export chunked EDSL jobs for true held-out question specs.")
    p.add_argument("--survey", required=True)
    p.add_argument("--output-dir", required=True, help="Directory to write chunked EDSL job files and manifest.")
    p.add_argument("--chunk-size", type=int, default=75)
    p.add_argument("--job-id-prefix", help="Prefix for chunk job ids. Defaults to <survey>_true_holdout.")
    p.add_argument("--approved-plan", help="Approved twin validation plan JSON required before exporting holdout jobs.")
    p.add_argument("--allow-unapproved", action="store_true", help="Explicitly allow an ad hoc/debug holdout export without an approved validation plan.")
    p.add_argument("--question-specs", help="JSON/JSONL file with held-out question specs.")
    p.add_argument("--question-specs-workbook", help="Workbook containing held-out question specs.")
    p.add_argument("--question-specs-sheet", default="Questions")
    p.add_argument("--question-specs-code-column", default="Question code")
    p.add_argument("--question-specs-text-column", default="Question text")
    p.add_argument("--question-specs-option-prefix", default="Answer option ")
    p.add_argument("--question-specs-labels-column", default="Answer value labels")
    p.add_argument("--heldout-question", action="append", help="Held-out question. Repeatable.")
    p.add_argument("--heldout-questions", help="Comma-separated held-out questions.")
    p.add_argument("--respondent", action="append", help="Respondent id to include. Repeatable.")
    p.add_argument("--respondents", help="Comma-separated respondent ids to include.")
    p.add_argument("--sample-respondents", type=int, help="Randomly sample this many respondents.")
    p.add_argument("--seed", type=int, help="Seed for --sample-respondents.")
    p.add_argument("--complete-cases", action="store_true", help="Only include respondents with all selected context answers.")
    p.add_argument("--balance-actual", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--stratify-actual", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--limit-respondents", type=int, help="Maximum number of respondents.")
    p.add_argument("--context-question", action="append", help="Question name to use as context. Repeatable.")
    p.add_argument("--context-questions", help="Comma-separated context question names.")
    p.add_argument("--exclude-context-question", action="append", help="Question name to exclude from context. Repeatable.")
    p.add_argument("--leakage-exclusion", action="append", help="Target-specific context exclusion as heldout_question:context_question. Repeatable.")
    p.add_argument("--context-question-count", type=int, help="Maximum number of context questions per respondent. Omit to use all available selected context answers.")
    p.add_argument("--include-agent-material", action="store_true", help="Include non-survey agent construction material in twin prompts.")
    p.add_argument("--agent-material-kind", action="append", help="Only include agent material of this kind. Repeatable or comma-separated.")
    p.add_argument("--agent-material-tag", action="append", help="Only include agent material with this tag. Repeatable or comma-separated.")
    p.add_argument("--max-agent-material-chars", type=int, help="Maximum agent material characters per respondent.")
    p.add_argument("--twin-material", action="append", help="Supplemental twin material file. Repeatable. Supports Markdown, JSON, or JSONL.")
    p.add_argument("--max-twin-material-chars", type=int, help="Maximum supplemental twin material characters per scenario.")
    p.add_argument("--prompt-variant", choices=["raw", "answer-commonness-confidence"], default="answer-commonness-confidence")
    p.add_argument("--model", action="append", help="EDSL model. Repeatable. Use service:model to set service per model.")
    p.add_argument("--models", help="Comma-separated EDSL models. Entries may be service:model.")
    p.add_argument("--service-name", help="EDSL service_name for unqualified models.")
    p.add_argument("--model-param", action="append", help="Model parameter. Use key=value or service:model:key=value. Repeatable.")
    p.add_argument("--job-question-name", default="response_probabilities")
    p.set_defaults(func=cmd_twin_study_export_holdout)
    p = twin_study.add_parser("import-results-dir", help="Import all EDSL twin result files in a directory with stable chunk job ids.")
    p.add_argument("--survey", required=True)
    p.add_argument("--results-dir", required=True)
    p.add_argument("--job-id-prefix", help="Prefix for imported job ids. Defaults to results directory name.")
    p.add_argument("--pattern", action="append", help="Glob pattern for result files. Repeatable.")
    p.add_argument("--allow-missing-actual", action="store_true", help="Import true holdout predictions whose scenarios omit actual_answer.")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_twin_study_import_results_dir)
    p = twin_study.add_parser("list", help="List recorded digital twin study runs.")
    p.add_argument("--survey", required=True)
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_twin_study_list, table_output=True)
    p = twin_study.add_parser("show", help="Show metadata for one digital twin study run.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--include-summary", action="store_true")
    p.set_defaults(func=cmd_twin_study_show)
    p = twin_study.add_parser("compare", help="Compare two or more digital twin study runs.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", action="append", help="Job id to compare. Repeatable.")
    p.add_argument("--jobs", help="Comma-separated job ids to compare.")
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.add_argument("--path", help="Write json/csv comparison output to this path.")
    p.set_defaults(func=cmd_twin_study_compare, table_output=True)
    p = twin_study.add_parser("practitioner-report", help="Generate a practitioner-focused HTML report for one survey twin study.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--path", help="Write standalone HTML report output to this path.")
    p.add_argument("--prompt-path", help="Write the model report prompt to this path.")
    p.add_argument("--job-path", help="Write the EDSL report-generation job to this path.")
    p.add_argument("--results-path", help="Write the serialized EDSL Results object to this path. Use .gz for gzip.")
    p.add_argument("--markdown-path", help="Write the generated Markdown report to this path.")
    p.add_argument("--model", action="append", help="EDSL model for report generation. Repeatable.")
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.add_argument(
        "--model-param",
        action="append",
        default=["max_tokens=24000", "reasoning_effort=high"],
        help="Model parameter. Use key=value or service:model:key=value. Repeatable. Defaults request a large report-generation budget.",
    )
    p.set_defaults(func=cmd_twin_study_practitioner_report, raw_output=True)
    p = twin_study.add_parser("practitioner-report-export", help="Export an EDSL job that writes a one-survey practitioner report.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--job-path", help="Write the EDSL report-generation job to this path.")
    p.add_argument("--prompt-path", help="Write the model report prompt to this path.")
    p.add_argument("--context-path", help="Write the assembled report context to this path.")
    p.add_argument("--model", action="append", help="EDSL model for report generation. Repeatable.")
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.add_argument(
        "--model-param",
        action="append",
        default=["max_tokens=24000", "reasoning_effort=high"],
        help="Model parameter. Use key=value or service:model:key=value. Repeatable. Defaults request a large report-generation budget.",
    )
    p.set_defaults(func=cmd_twin_study_practitioner_report_export)
    p = twin_study.add_parser("practitioner-report-import", help="Import EDSL Results from a one-survey practitioner report job.")
    p.add_argument("--path", required=True, help="Serialized EDSL Results JSON or JSON.GZ.")
    p.add_argument("--report-id", help="Practitioner report id. Inferred from Results metadata when present.")
    p.add_argument("--replace", action="store_true", help="Replace an existing imported report result.")
    p.set_defaults(func=cmd_twin_study_practitioner_report_import)
    p = twin_study.add_parser("practitioner-report-render", help="Render imported one-survey practitioner report Markdown as HTML.")
    p.add_argument("--report-id", required=True)
    p.add_argument("--path", help="Write standalone HTML report output to this path. Defaults to .zwill/practitioner_reports/<id>/report.html.")
    p.set_defaults(func=cmd_twin_study_practitioner_report_render, raw_output=True)

    twin_approach = subparsers.add_parser("twin-approach").add_subparsers(dest="twin_approach_command", required=True)
    p = twin_approach.add_parser("add", help="Add or update a reusable digital twin construction approach.")
    p.add_argument("--survey", required=True)
    p.add_argument("--path", help="JSON/YAML approach definition. If provided, inline flags are ignored.")
    p.add_argument("--approach-id")
    p.add_argument("--name")
    description_group = p.add_mutually_exclusive_group()
    description_group.add_argument("--description")
    description_group.add_argument("--description-path")
    p.add_argument("--tag", action="append")
    p.add_argument("--sample-respondents", type=int)
    p.add_argument("--seed", type=int)
    p.add_argument("--complete-cases", action="store_true")
    p.add_argument("--balance-actual", action="store_true")
    p.add_argument("--stratify-actual", action="store_true")
    p.add_argument("--limit-respondents", type=int)
    p.add_argument("--respondent", action="append")
    p.add_argument("--respondents")
    p.add_argument("--context-question", action="append")
    p.add_argument("--context-questions")
    p.add_argument("--exclude-context-question", action="append")
    p.add_argument("--leakage-exclusion", action="append")
    p.add_argument("--context-question-count", type=int)
    p.add_argument("--include-agent-material", action="store_true")
    p.add_argument("--agent-material-kind", action="append")
    p.add_argument("--agent-material-tag", action="append")
    p.add_argument("--max-agent-material-chars", type=int)
    p.add_argument("--twin-material", action="append")
    p.add_argument("--max-twin-material-chars", type=int)
    p.add_argument("--model", action="append")
    p.add_argument("--models")
    p.add_argument("--service-name")
    p.add_argument("--model-param", action="append")
    p.add_argument("--job-question-name")
    p.set_defaults(func=cmd_twin_approach_add)
    p = twin_approach.add_parser("list", help="List reusable twin construction approaches.")
    p.add_argument("--survey", required=True)
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_twin_approach_list, table_output=True)
    p = twin_approach.add_parser("show", help="Show a reusable twin construction approach.")
    p.add_argument("--survey", required=True)
    p.add_argument("--approach-id", required=True)
    p.set_defaults(func=cmd_twin_approach_show)
    p = twin_approach.add_parser("note", help="Show or set markdown notes for a reusable twin approach.")
    p.add_argument("--survey", required=True)
    p.add_argument("--approach-id", required=True)
    note_group = p.add_mutually_exclusive_group()
    note_group.add_argument("--text", help="Markdown note text.")
    note_group.add_argument("--path", help="Path to a Markdown note file.")
    note_group.add_argument("--clear", action="store_true", help="Clear the note.")
    p.set_defaults(func=cmd_twin_approach_note)
    p = twin_approach.add_parser("scaffold", help="Write a starter JSON approach definition.")
    p.add_argument("--survey", required=True)
    p.add_argument("--approach-id", required=True)
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--tag", action="append")
    p.add_argument("--context-questions")
    p.add_argument("--context-question-count", type=int, default=5)
    p.add_argument("--include-agent-material", action="store_true")
    p.add_argument("--twin-material", action="append")
    p.add_argument("--model", action="append")
    p.add_argument("--path")
    p.set_defaults(func=cmd_twin_approach_scaffold)
    p = twin_approach.add_parser("diff", help="Compare two reusable approaches or planned experiment approaches.")
    p.add_argument("--survey", required=True)
    p.add_argument("--left", required=True, help="Left approach id/name, experiment id, or job id.")
    p.add_argument("--right", required=True, help="Right approach id/name, experiment id, or job id.")
    p.add_argument("--format", choices=["table", "json", "html"], default="table")
    p.add_argument("--path", help="Write json/html output to this path.")
    p.add_argument("--show-same", action="store_true", help="Include unchanged fields in table output.")
    p.set_defaults(func=cmd_twin_approach_diff, table_output=True)

    twin_experiment = subparsers.add_parser("twin-experiment").add_subparsers(dest="twin_experiment_command", required=True)
    p = twin_experiment.add_parser("record", help="Record an approach for an existing digital twin job.")
    p.add_argument("--survey", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--experiment-id")
    p.add_argument("--approach", required=True, help="Short human-readable approach name.")
    description_group = p.add_mutually_exclusive_group()
    description_group.add_argument("--description", help="Markdown description of what this approach changed.")
    description_group.add_argument("--description-path", help="Path to a Markdown approach description.")
    p.add_argument("--tag", action="append", help="Experiment tag. Repeatable or comma-separated.")
    p.add_argument("--primary-metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.set_defaults(func=cmd_twin_experiment_record)
    p = twin_experiment.add_parser("list", help="List recorded twin-development experiments.")
    p.add_argument("--survey", required=True)
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_twin_experiment_list, table_output=True)
    p = twin_experiment.add_parser("init-plan", help="Write a starter JSON twin experiment plan.")
    p.add_argument("--survey", required=True)
    p.add_argument("--plan-id", required=True)
    p.add_argument("--path")
    p.add_argument("--heldout-question", action="append")
    p.add_argument("--heldout-questions")
    p.add_argument("--approach-id", action="append", help="Approach id to include as a plan arm. Repeatable.")
    p.add_argument("--sample-respondents", type=int)
    p.add_argument("--seed", type=int, default=123)
    p.add_argument("--context-question-count", type=int, default=5)
    p.add_argument("--model", action="append")
    p.add_argument("--primary-metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.set_defaults(func=cmd_twin_experiment_init_plan)
    p = twin_experiment.add_parser("approve", help="Mark a reviewed twin experiment plan as approved for export/run.")
    p.add_argument("--path", required=True, help="JSON/YAML experiment plan.")
    p.add_argument("--survey", help="Override or validate the survey in the plan.")
    p.add_argument("--approved-by", help="Name or identifier of the approving user. Defaults to user.")
    p.add_argument("--note", help="Approval note to preserve in plan provenance.")
    p.add_argument("--estimated-cost", help="Optional estimated cost note, e.g. '$42' or 'about $40'.")
    p.add_argument("--estimated-time", help="Optional estimated runtime note.")
    p.set_defaults(func=cmd_twin_experiment_approve)
    p = twin_experiment.add_parser("export-plan", help="Export EDSL jobs from a reusable twin experiment plan.")
    p.add_argument("--path", required=True, help="JSON/YAML experiment plan.")
    p.add_argument("--survey", help="Override survey in the plan.")
    p.add_argument("--output-dir", help="Directory to write job files and manifest.")
    p.add_argument("--plan-id", help="Override the plan id.")
    p.add_argument("--allow-unapproved", action="store_true", help="Explicitly export a draft/unapproved plan for debugging.")
    p.set_defaults(func=cmd_twin_experiment_export_plan)
    p = twin_experiment.add_parser("plan-status", help="Show exported/imported status for a twin experiment plan.")
    p.add_argument("--survey", required=True)
    p.add_argument("--plan-id", required=True)
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_twin_experiment_plan_status, table_output=True)
    p = twin_experiment.add_parser("note", help="Show or set markdown notes for a twin experiment plan.")
    p.add_argument("--survey", required=True)
    p.add_argument("--plan-id", required=True)
    note_group = p.add_mutually_exclusive_group()
    note_group.add_argument("--text", help="Markdown note text.")
    note_group.add_argument("--path", help="Path to a Markdown note file.")
    note_group.add_argument("--clear", action="store_true", help="Clear the note.")
    p.set_defaults(func=cmd_twin_experiment_note)
    p = twin_experiment.add_parser("import-plan-results", help="Import Results files that match jobs in a plan manifest.")
    p.add_argument("--manifest", required=True, help="Path to a plan export manifest.json.")
    p.add_argument("--results-dir", required=True, help="Directory containing serialized EDSL Results JSON/JSON.GZ files.")
    p.add_argument("--survey", help="Override survey from the manifest.")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_twin_experiment_import_plan_results)
    p = twin_experiment.add_parser("package", help="Create a portable run package from an exported twin experiment plan.")
    p.add_argument("--manifest", required=True, help="Path to a plan export manifest.json.")
    p.add_argument("--output-dir", help="Directory to write package files.")
    p.add_argument("--survey", help="Override survey from the manifest.")
    p.add_argument("--plan-id", help="Override plan id from the manifest.")
    p.add_argument("--env-path", help="Explicit .env path to include in RUN.md edsl-run commands.")
    p.set_defaults(func=cmd_twin_experiment_package)
    p = twin_experiment.add_parser("bundle", help="Create comparison, plots, microdata, and report-export artifacts for a plan.")
    p.add_argument("--survey", required=True)
    p.add_argument("--plan-id", required=True)
    p.add_argument("--metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.add_argument("--model", help="Restrict bundle artifacts to one model label, e.g. openai:gpt-5.5.")
    p.add_argument("--output-dir", help="Directory to write bundle artifacts.")
    p.add_argument("--report-export", action="store_true", help="Also export an EDSL report-writing job.")
    p.add_argument("--report-model", action="append", help="EDSL model to write the report. Repeatable. Use service:model to set service.")
    p.add_argument("--model-param", action="append", default=["max_tokens=12000", "reasoning_effort=high"])
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.set_defaults(func=cmd_twin_experiment_bundle)
    p = twin_experiment.add_parser("bundle-show", help="Show paths and selected result for a bundle manifest.")
    p.add_argument("--manifest", required=True)
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_twin_experiment_bundle_show, table_output=True)
    p = twin_experiment.add_parser("dashboard", help="Write a deterministic HTML dashboard for a twin experiment plan.")
    p.add_argument("--survey", required=True)
    p.add_argument("--plan-id", required=True)
    p.add_argument("--metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.add_argument("--model", help="Restrict dashboard comparisons to one model label, e.g. openai:gpt-5.5.")
    p.add_argument("--bundle-manifest", help="Optional bundle manifest to link artifacts from.")
    p.add_argument("--path", help="Path to write dashboard HTML. Defaults to the plan store.")
    p.add_argument("--json-path", help="Optional path to write dashboard JSON. Defaults next to HTML.")
    p.set_defaults(func=cmd_twin_experiment_dashboard)
    p = twin_experiment.add_parser("compare", help="Rank recorded twin experiments by a selected metric.")
    p.add_argument("--survey", required=True)
    p.add_argument("--experiment-id", action="append")
    p.add_argument("--job-id", action="append")
    p.add_argument("--jobs", help="Comma-separated job ids to compare.")
    p.add_argument("--model", help="Restrict comparison to one model label, e.g. openai:gpt-5.5.")
    p.add_argument("--metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.add_argument("--path", help="Write json/csv comparison output to this path.")
    p.set_defaults(func=cmd_twin_experiment_compare, table_output=True)
    p = twin_experiment.add_parser("plots", help="Generate deterministic plot artifacts for recorded twin experiment comparisons.")
    p.add_argument("--survey", required=True)
    p.add_argument("--experiment-id", action="append")
    p.add_argument("--job-id", action="append")
    p.add_argument("--jobs", help="Comma-separated job ids to compare.")
    p.add_argument("--model", help="Restrict plots to one model label, e.g. openai:gpt-5.5.")
    p.add_argument("--metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.add_argument("--path", help="Directory to write plot manifest, SVGs, and JSON data. Defaults to the survey plot store.")
    p.add_argument("--plot-id", help="Override deterministic plot id.")
    p.set_defaults(func=cmd_twin_experiment_plots)
    p = twin_experiment.add_parser("microdata", help="Generate a standalone HTML audit table of twin microdata across experiments.")
    p.add_argument("--survey", required=True)
    p.add_argument("--experiment-id", action="append")
    p.add_argument("--job-id", action="append")
    p.add_argument("--jobs", help="Comma-separated job ids to compare.")
    p.add_argument("--model", help="Restrict audit table to one model label, e.g. openai:gpt-5.5.")
    p.add_argument("--metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.add_argument("--path", help="Path to write standalone HTML. Defaults to the survey microdata store.")
    p.add_argument("--json-path", help="Optional path to write audit JSON. Defaults next to HTML.")
    p.add_argument("--microdata-id", help="Override deterministic microdata id.")
    p.add_argument("--title", help="HTML report title.")
    p.set_defaults(func=cmd_twin_experiment_microdata)
    p = twin_experiment.add_parser("select", help="Return the best recorded twin experiment for a selected metric.")
    p.add_argument("--survey", required=True)
    p.add_argument("--experiment-id", action="append")
    p.add_argument("--job-id", action="append")
    p.add_argument("--jobs", help="Comma-separated job ids to compare.")
    p.add_argument("--model", help="Restrict selection to one model label, e.g. openai:gpt-5.5.")
    p.add_argument("--metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.set_defaults(func=cmd_twin_experiment_select)
    p = twin_experiment.add_parser("report", help="Generate a model-authored HTML report comparing recorded twin experiments.")
    p.add_argument("--survey", required=True)
    p.add_argument("--experiment-id", action="append")
    p.add_argument("--job-id", action="append")
    p.add_argument("--jobs", help="Comma-separated job ids to compare.")
    p.add_argument("--model", help="Restrict comparison to one model label, e.g. openai:gpt-5.5.")
    p.add_argument("--metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.add_argument("--path", help="Write standalone HTML report output to this path.")
    p.add_argument("--prompt-path", help="Write the model report prompt to this path.")
    p.add_argument("--job-path", help="Write the EDSL report-generation job to this path.")
    p.add_argument("--results-path", help="Write the serialized EDSL Results object to this path. Use .gz for gzip.")
    p.add_argument("--include-plots", action="append", help="Plot manifest to include in report context and rendered HTML. Repeatable.")
    p.add_argument("--report-model", action="append", help="EDSL model to write the report. Repeatable. Use service:model to set service.")
    p.add_argument("--model-param", action="append", default=["max_tokens=12000", "reasoning_effort=high"])
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.set_defaults(func=cmd_twin_experiment_report, raw_output=True)
    p = twin_experiment.add_parser("report-export", help="Export an EDSL job that writes a twin-experiment comparison report.")
    p.add_argument("--survey", required=True)
    p.add_argument("--experiment-id", action="append")
    p.add_argument("--job-id", action="append")
    p.add_argument("--jobs", help="Comma-separated job ids to compare.")
    p.add_argument("--model", help="Restrict comparison to one model label, e.g. openai:gpt-5.5.")
    p.add_argument("--metric", choices=sorted(TWIN_EXPERIMENT_METRICS), default="nll")
    p.add_argument("--job-path", help="Write the EDSL report-generation job to this path.")
    p.add_argument("--prompt-path", help="Write the model report prompt to this path.")
    p.add_argument("--context-path", help="Write the assembled report context to this path.")
    p.add_argument("--include-plots", action="append", help="Plot manifest to include in report context and rendered HTML. Repeatable.")
    p.add_argument("--report-model", action="append", help="EDSL model to write the report. Repeatable. Use service:model to set service.")
    p.add_argument("--model-param", action="append", default=["max_tokens=12000", "reasoning_effort=high"])
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.set_defaults(func=cmd_twin_experiment_report_export)
    p = twin_experiment.add_parser("report-import", help="Import EDSL Results from a twin-experiment report job.")
    p.add_argument("--path", required=True, help="Serialized EDSL Results JSON or JSON.GZ.")
    p.add_argument("--report-id", help="Report id. Inferred from Results metadata when present.")
    p.add_argument("--replace", action="store_true", help="Replace an existing imported report result.")
    p.set_defaults(func=cmd_twin_experiment_report_import)
    p = twin_experiment.add_parser("report-render", help="Render imported twin-experiment report Markdown as HTML.")
    p.add_argument("--report-id", required=True)
    p.add_argument("--path", help="Write standalone HTML report output to this path.")
    p.set_defaults(func=cmd_twin_experiment_report_render, raw_output=True)

    twin_benchmark = subparsers.add_parser("twin-benchmark").add_subparsers(dest="twin_benchmark_command", required=True)
    p = twin_benchmark.add_parser("run", help="Run a config-driven cross-survey twin benchmark.")
    p.add_argument("--config", required=True, help="JSON benchmark config.")
    p.add_argument("--output-dir", help="Override benchmark output directory.")
    p.add_argument("--manifest", help="Path to write benchmark run manifest.")
    p.add_argument("--dry-run", action="store_true", help="Export jobs without running model calls.")
    p.add_argument("--replace", action="store_true", help="Replace imported twin results with matching job ids.")
    p.set_defaults(func=cmd_twin_benchmark_run)
    p = twin_benchmark.add_parser("report", help="Report a cross-survey twin benchmark.")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", help="JSON benchmark config with job_id on each study.")
    group.add_argument("--manifest", help="Benchmark manifest produced by twin-benchmark run.")
    p.add_argument("--format", choices=["json", "csv", "html"], default="html")
    p.add_argument("--path", help="Write report output to this path.")
    p.set_defaults(func=cmd_twin_benchmark_report, raw_output=True)
    p = twin_benchmark.add_parser("practitioner-report", help="Generate a practitioner-focused HTML report from a benchmark.")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", help="JSON benchmark config with job_id on each study.")
    group.add_argument("--manifest", help="Benchmark manifest produced by twin-benchmark run.")
    p.add_argument("--path", help="Write standalone HTML report output to this path.")
    p.add_argument("--prompt-path", help="Write the model report prompt to this path.")
    p.add_argument("--job-path", help="Write the EDSL report-generation job to this path.")
    p.add_argument("--results-path", help="Write the serialized EDSL Results object to this path. Use .gz for gzip.")
    p.add_argument("--markdown-path", help="Write the generated Markdown report to this path.")
    p.add_argument("--model", action="append", help="EDSL model for report generation. Repeatable.")
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.add_argument(
        "--model-param",
        action="append",
        default=["max_tokens=24000", "reasoning_effort=high"],
        help="Model parameter. Use key=value or service:model:key=value. Repeatable. Defaults request a large report-generation budget.",
    )
    p.set_defaults(func=cmd_twin_benchmark_practitioner_report, raw_output=True)
    p = twin_benchmark.add_parser("practitioner-report-export", help="Export an EDSL job that writes a practitioner report.")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", help="JSON benchmark config with job_id on each study.")
    group.add_argument("--manifest", help="Benchmark manifest produced by twin-benchmark run.")
    p.add_argument("--job-path", help="Write the EDSL report-generation job to this path.")
    p.add_argument("--prompt-path", help="Write the model report prompt to this path.")
    p.add_argument("--context-path", help="Write the assembled report context to this path.")
    p.add_argument("--model", action="append", help="EDSL model for report generation. Repeatable.")
    p.add_argument("--models", help="Comma-separated EDSL models for report generation. Entries may be service:model.")
    p.add_argument("--service-name", default="openai", help="EDSL service_name for unqualified report-generation models.")
    p.add_argument(
        "--model-param",
        action="append",
        default=["max_tokens=24000", "reasoning_effort=high"],
        help="Model parameter. Use key=value or service:model:key=value. Repeatable. Defaults request a large report-generation budget.",
    )
    p.set_defaults(func=cmd_twin_benchmark_practitioner_report_export)
    p = twin_benchmark.add_parser("practitioner-report-import", help="Import EDSL Results from a practitioner report job.")
    p.add_argument("--path", required=True, help="Serialized EDSL Results JSON or JSON.GZ.")
    p.add_argument("--report-id", help="Practitioner report id. Inferred from Results metadata when present.")
    p.add_argument("--replace", action="store_true", help="Replace an existing imported report result.")
    p.set_defaults(func=cmd_twin_benchmark_practitioner_report_import)
    p = twin_benchmark.add_parser("practitioner-report-render", help="Render imported practitioner report Markdown as HTML.")
    p.add_argument("--report-id", required=True)
    p.add_argument("--path", help="Write standalone HTML report output to this path. Defaults to .zwill/practitioner_reports/<id>/report.html.")
    p.set_defaults(func=cmd_twin_benchmark_practitioner_report_render, raw_output=True)

    skills = subparsers.add_parser("skills").add_subparsers(dest="skills_command", required=True)
    p = skills.add_parser("list", help="List zwill agent skills installed with the package.")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_skills_list, table_output=True)
    p = skills.add_parser("path", help="Print the installed path for one zwill agent skill.")
    p.add_argument("name", choices=SKILL_NAMES)
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=cmd_skills_path, raw_output=True)

    context = subparsers.add_parser("context").add_subparsers(dest="context_command", required=True)
    for command_name, func in [("add", cmd_context_add), ("set", cmd_context_set)]:
        p = context.add_parser(command_name)
        p.add_argument("--survey", required=True)
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument("--path")
        group.add_argument("--text")
        p.set_defaults(func=func)
    p = context.add_parser("show")
    p.add_argument("--survey", required=True)
    p.set_defaults(func=cmd_context_show)

    survey = subparsers.add_parser("survey").add_subparsers(dest="survey_command", required=True)
    p = survey.add_parser("create")
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_survey_create)
    p = survey.add_parser("show")
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_survey_show)
    p = survey.add_parser("report", help="Report survey questions, options, distributions, and data-quality issues.")
    p.add_argument("--survey", required=True)
    p.add_argument("--format", choices=["table", "json", "html", "csv"], default="table")
    p.add_argument("--path", help="Write json/html output, or CSV basename path for --format csv.")
    p.set_defaults(func=cmd_survey_report, table_output=True)

    raw = subparsers.add_parser("raw").add_subparsers(dest="raw_command", required=True)
    p = raw.add_parser("add")
    p.add_argument("--survey", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--title", required=True)
    p.set_defaults(func=cmd_raw_add)
    p = raw.add_parser("list")
    p.add_argument("--survey", required=True)
    p.set_defaults(func=cmd_raw_list)

    question = subparsers.add_parser("question").add_subparsers(dest="question_command", required=True)
    p = question.add_parser("add")
    p.add_argument("--survey", required=True)
    p.add_argument("--question-name", required=True)
    p.add_argument("--question-type", required=True)
    p.add_argument("--question-text", required=True)
    p.add_argument("--question-option", action="append")
    p.add_argument("--option-label", action="append")
    p.add_argument("--role", default="survey_item")
    p.add_argument("--source-raw")
    p.add_argument("--source-note")
    p.set_defaults(func=cmd_question_add)
    p = question.add_parser("import")
    p.add_argument("--survey", required=True)
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_question_import)

    respondent = subparsers.add_parser("respondent").add_subparsers(dest="respondent_command", required=True)
    p = respondent.add_parser("add")
    p.add_argument("--survey", required=True)
    p.add_argument("--respondent-id", required=True)
    p.add_argument("--weight", type=float, default=1.0)
    p.add_argument("--metadata", action="append")
    p.add_argument("--source-raw")
    p.add_argument("--source-note")
    p.set_defaults(func=cmd_respondent_add)
    p = respondent.add_parser("import")
    p.add_argument("--survey", required=True)
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_respondent_import)

    agent_material = subparsers.add_parser("agent-material").add_subparsers(dest="agent_material_command", required=True)
    p = agent_material.add_parser("add")
    p.add_argument("--survey", required=True)
    p.add_argument("--respondent-id", required=True)
    p.add_argument("--material-id")
    p.add_argument("--kind", required=True)
    p.add_argument("--title", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--path")
    group.add_argument("--text")
    p.add_argument("--tag", action="append", help="Tag for this material. Repeatable or comma-separated.")
    p.add_argument("--include-by-default", action="store_true")
    p.add_argument("--source-raw")
    p.add_argument("--source-note")
    p.set_defaults(func=cmd_agent_material_add)
    p = agent_material.add_parser("import")
    p.add_argument("--survey", required=True)
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_agent_material_import)
    p = agent_material.add_parser("list")
    p.add_argument("--survey", required=True)
    p.add_argument("--respondent-id")
    p.add_argument("--agent-material-kind", action="append", help="Only list material of this kind. Repeatable or comma-separated.")
    p.add_argument("--agent-material-tag", action="append", help="Only list material with this tag. Repeatable or comma-separated.")
    p.set_defaults(func=cmd_agent_material_list)
    p = agent_material.add_parser("show")
    p.add_argument("--survey", required=True)
    p.add_argument("--material-id", required=True)
    p.set_defaults(func=cmd_agent_material_show)

    answer = subparsers.add_parser("answer").add_subparsers(dest="answer_command", required=True)
    p = answer.add_parser("add")
    p.add_argument("--survey", required=True)
    p.add_argument("--respondent-id", required=True)
    p.add_argument("--question", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--answer")
    group.add_argument("--missing-code")
    p.set_defaults(func=cmd_answer_add)
    p = answer.add_parser("import")
    p.add_argument("--survey", required=True)
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_answer_import)

    quarantine = subparsers.add_parser("quarantine").add_subparsers(dest="quarantine_command", required=True)
    p = quarantine.add_parser("list")
    p.add_argument("--survey", required=True)
    p.set_defaults(func=cmd_quarantine_list)
    p = quarantine.add_parser("resolve")
    p.add_argument("--survey", required=True)
    p.add_argument("--issue-id", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--note", required=True)
    p.set_defaults(func=cmd_quarantine_resolve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
        if getattr(args, "table_output", False) or getattr(args, "raw_output", False):
            return 0
        print_json(result)
        return 0 if result["status"] == "ok" else 1
    except ZwillError as exc:
        command = "zwill " + " ".join(sys.argv[1:3])
        payload = envelope(
            command.strip(),
            "error",
            errors=[{"code": exc.code, "message": exc.message, "context": exc.context, "hint": exc.hint}],
            next_steps=exc.next_steps,
        )
        print_json(payload)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
