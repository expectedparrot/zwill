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
from .twin_report import build_twin_report, twin_top_prediction
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


def report_catalog_entry(*args, **kwargs):
    from .report_bundle import report_catalog_entry as impl

    return impl(*args, **kwargs)

def build_report_catalog(*args, **kwargs):
    from .report_bundle import build_report_catalog as impl

    return impl(*args, **kwargs)

def cmd_report_list(*args, **kwargs):
    from .report_bundle import cmd_report_list as impl

    return impl(*args, **kwargs)

def report_bundle_default_dir(*args, **kwargs):
    from .report_bundle import report_bundle_default_dir as impl

    return impl(*args, **kwargs)

def bundle_rel_link(*args, **kwargs):
    from .report_bundle import bundle_rel_link as impl

    return impl(*args, **kwargs)

def report_bundle_page(*args, **kwargs):
    from .report_bundle import report_bundle_page as impl

    return impl(*args, **kwargs)

def write_bundle_json(*args, **kwargs):
    from .report_bundle import write_bundle_json as impl

    return impl(*args, **kwargs)

def compact_twin_report_payload(*args, **kwargs):
    from .report_bundle import compact_twin_report_payload as impl

    return impl(*args, **kwargs)

def copy_bundle_file(*args, **kwargs):
    from .report_bundle import copy_bundle_file as impl

    return impl(*args, **kwargs)

def copy_generated_report_provenance(*args, **kwargs):
    from .report_bundle import copy_generated_report_provenance as impl

    return impl(*args, **kwargs)

def report_stage_status(*args, **kwargs):
    from .report_bundle import report_stage_status as impl

    return impl(*args, **kwargs)

def render_report_bundle_checklist(*args, **kwargs):
    from .report_bundle import render_report_bundle_checklist as impl

    return impl(*args, **kwargs)

def page_is_ready(*args, **kwargs):
    from .report_bundle import page_is_ready as impl

    return impl(*args, **kwargs)

def imported_generation_ready(*args, **kwargs):
    from .report_bundle import imported_generation_ready as impl

    return impl(*args, **kwargs)

def write_report_stage_artifacts(*args, **kwargs):
    from .report_bundle import write_report_stage_artifacts as impl

    return impl(*args, **kwargs)

def render_report_bundle_index(*args, **kwargs):
    from .report_bundle import render_report_bundle_index as impl

    return impl(*args, **kwargs)

def build_report_bundle(*args, **kwargs):
    from .report_bundle import build_report_bundle as impl

    return impl(*args, **kwargs)

def cmd_report_build(*args, **kwargs):
    from .report_bundle import cmd_report_build as impl

    return impl(*args, **kwargs)

def report_stage_envelope(*args, **kwargs):
    from .report_bundle import report_stage_envelope as impl

    return impl(*args, **kwargs)

def find_imported_executive_summary_report(*args, **kwargs):
    from .report_bundle import find_imported_executive_summary_report as impl

    return impl(*args, **kwargs)

def find_imported_one_shot_analysis_report(*args, **kwargs):
    from .report_bundle import find_imported_one_shot_analysis_report as impl

    return impl(*args, **kwargs)

def cmd_report_facts(*args, **kwargs):
    from .report_bundle import cmd_report_facts as impl

    return impl(*args, **kwargs)

def cmd_report_analyze(*args, **kwargs):
    from .report_bundle import cmd_report_analyze as impl

    return impl(*args, **kwargs)

def cmd_report_render(*args, **kwargs):
    from .report_bundle import cmd_report_render as impl

    return impl(*args, **kwargs)

def read_probability_imports(*args, **kwargs):
    from .report_bundle import read_probability_imports as impl

    return impl(*args, **kwargs)

def build_probability_coverage_payload(*args, **kwargs):
    from .report_bundle import build_probability_coverage_payload as impl

    return impl(*args, **kwargs)

def render_probability_coverage_html(*args, **kwargs):
    from .report_bundle import render_probability_coverage_html as impl

    return impl(*args, **kwargs)

def render_probability_coverage_section(*args, **kwargs):
    from .report_bundle import render_probability_coverage_section as impl

    return impl(*args, **kwargs)

def insert_before_main_close(*args, **kwargs):
    from .report_bundle import insert_before_main_close as impl

    return impl(*args, **kwargs)

def insert_after_main_open(*args, **kwargs):
    from .report_bundle import insert_after_main_open as impl

    return impl(*args, **kwargs)

def render_generated_executive_interpretation_section(*args, **kwargs):
    from .report_bundle import render_generated_executive_interpretation_section as impl

    return impl(*args, **kwargs)

def render_validation_diagnostics_html(*args, **kwargs):
    from .report_bundle import render_validation_diagnostics_html as impl

    return impl(*args, **kwargs)

def render_validation_diagnostics_section(*args, **kwargs):
    from .report_bundle import render_validation_diagnostics_section as impl

    return impl(*args, **kwargs)

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


def paired_twin_response_changes(*args, **kwargs):
    from .twin_microdata import paired_twin_response_changes as impl

    return impl(*args, **kwargs)

def paired_twin_response_pair_rows(*args, **kwargs):
    from .twin_microdata import paired_twin_response_pair_rows as impl

    return impl(*args, **kwargs)

def twin_job_template_and_scenarios(*args, **kwargs):
    from .twin_microdata import twin_job_template_and_scenarios as impl

    return impl(*args, **kwargs)

def format_probabilities_for_display(*args, **kwargs):
    from .twin_microdata import format_probabilities_for_display as impl

    return impl(*args, **kwargs)

def compact_observed_answers(*args, **kwargs):
    from .twin_microdata import compact_observed_answers as impl

    return impl(*args, **kwargs)

def paired_twin_microdata_rows(*args, **kwargs):
    from .twin_microdata import paired_twin_microdata_rows as impl

    return impl(*args, **kwargs)

def render_twin_microdata_table_html(*args, **kwargs):
    from .twin_microdata import render_twin_microdata_table_html as impl

    return impl(*args, **kwargs)

def experiment_microdata_id(*args, **kwargs):
    from .twin_microdata import experiment_microdata_id as impl

    return impl(*args, **kwargs)

def build_experiment_microdata_audit(*args, **kwargs):
    from .twin_microdata import build_experiment_microdata_audit as impl

    return impl(*args, **kwargs)

def build_experiment_microdata_matrix(*args, **kwargs):
    from .twin_microdata import build_experiment_microdata_matrix as impl

    return impl(*args, **kwargs)

def render_experiment_microdata_audit_html(*args, **kwargs):
    from .twin_microdata import render_experiment_microdata_audit_html as impl

    return impl(*args, **kwargs)

def render_experiment_microdata_matrix_html(*args, **kwargs):
    from .twin_microdata import render_experiment_microdata_matrix_html as impl

    return impl(*args, **kwargs)

def write_twin_experiment_microdata(*args, **kwargs):
    from .twin_microdata import write_twin_experiment_microdata as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_microdata(*args, **kwargs):
    from .twin_microdata import cmd_twin_experiment_microdata as impl

    return impl(*args, **kwargs)

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


def build_executive_summary_report_context(*args, **kwargs):
    from .generated_reports import build_executive_summary_report_context as impl

    return impl(*args, **kwargs)

def build_executive_summary_report_prompt(*args, **kwargs):
    from .generated_reports import build_executive_summary_report_prompt as impl

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
    from .cli_parser import build_parser as build_cli_parser

    return build_cli_parser()


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
