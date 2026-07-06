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


def load_edsl_classes(*args, **kwargs):
    from .edsl_integration import load_edsl_classes as impl

    return impl(*args, **kwargs)

def load_edsl_job_classes(*args, **kwargs):
    from .edsl_integration import load_edsl_job_classes as impl

    return impl(*args, **kwargs)

def load_edsl_agent_study_classes(*args, **kwargs):
    from .edsl_integration import load_edsl_agent_study_classes as impl

    return impl(*args, **kwargs)

def load_edsl_runner_classes(*args, **kwargs):
    from .edsl_integration import load_edsl_runner_classes as impl

    return impl(*args, **kwargs)

def edsl_question_from_zwill(*args, **kwargs):
    from .edsl_integration import edsl_question_from_zwill as impl

    return impl(*args, **kwargs)

def build_edsl_survey_dict(*args, **kwargs):
    from .edsl_integration import build_edsl_survey_dict as impl

    return impl(*args, **kwargs)

def selected_question_names(*args, **kwargs):
    from .edsl_integration import selected_question_names as impl

    return impl(*args, **kwargs)

def selected_agent_material_kinds(*args, **kwargs):
    from .edsl_integration import selected_agent_material_kinds as impl

    return impl(*args, **kwargs)

def selected_agent_material_tags(*args, **kwargs):
    from .edsl_integration import selected_agent_material_tags as impl

    return impl(*args, **kwargs)

def select_agent_material(*args, **kwargs):
    from .edsl_integration import select_agent_material as impl

    return impl(*args, **kwargs)

def format_agent_material(*args, **kwargs):
    from .edsl_integration import format_agent_material as impl

    return impl(*args, **kwargs)

def twin_material_paths(*args, **kwargs):
    from .edsl_integration import twin_material_paths as impl

    return impl(*args, **kwargs)

def normalize_twin_material_row(*args, **kwargs):
    from .edsl_integration import normalize_twin_material_row as impl

    return impl(*args, **kwargs)

def load_twin_material(*args, **kwargs):
    from .edsl_integration import load_twin_material as impl

    return impl(*args, **kwargs)

def matching_twin_material(*args, **kwargs):
    from .edsl_integration import matching_twin_material as impl

    return impl(*args, **kwargs)

def format_twin_material(*args, **kwargs):
    from .edsl_integration import format_twin_material as impl

    return impl(*args, **kwargs)

def build_agent_instruction(*args, **kwargs):
    from .edsl_integration import build_agent_instruction as impl

    return impl(*args, **kwargs)

def agent_list_traits_presentation_template(*args, **kwargs):
    from .edsl_integration import agent_list_traits_presentation_template as impl

    return impl(*args, **kwargs)

def option_key(*args, **kwargs):
    from .edsl_integration import option_key as impl

    return impl(*args, **kwargs)

def parse_model_specs(*args, **kwargs):
    from .edsl_integration import parse_model_specs as impl

    return impl(*args, **kwargs)

def parse_model_param_value(*args, **kwargs):
    from .edsl_integration import parse_model_param_value as impl

    return impl(*args, **kwargs)

def parse_model_params(*args, **kwargs):
    from .edsl_integration import parse_model_params as impl

    return impl(*args, **kwargs)

def model_kwargs_for(*args, **kwargs):
    from .edsl_integration import model_kwargs_for as impl

    return impl(*args, **kwargs)

def model_label(*args, **kwargs):
    from .edsl_integration import model_label as impl

    return impl(*args, **kwargs)

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


def build_edsl_agent_list_dict(*args, **kwargs):
    from .edsl_integration import build_edsl_agent_list_dict as impl

    return impl(*args, **kwargs)

def inspect_agent_list_dict(*args, **kwargs):
    from .edsl_integration import inspect_agent_list_dict as impl

    return impl(*args, **kwargs)

def cmd_agent_list_inspect(*args, **kwargs):
    from .edsl_integration import cmd_agent_list_inspect as impl

    return impl(*args, **kwargs)

def load_question_spec_from_args(*args, **kwargs):
    from .edsl_integration import load_question_spec_from_args as impl

    return impl(*args, **kwargs)

def agent_study_job_id_from_job(*args, **kwargs):
    from .edsl_integration import agent_study_job_id_from_job as impl

    return impl(*args, **kwargs)

def agent_study_job_id_from_results(*args, **kwargs):
    from .edsl_integration import agent_study_job_id_from_results as impl

    return impl(*args, **kwargs)

def build_edsl_agent_study_job_dict(*args, **kwargs):
    from .edsl_integration import build_edsl_agent_study_job_dict as impl

    return impl(*args, **kwargs)

def cmd_agent_study_export(*args, **kwargs):
    from .edsl_integration import cmd_agent_study_export as impl

    return impl(*args, **kwargs)

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


def probability_job_builder_deps(*args, **kwargs):
    from .edsl_integration import probability_job_builder_deps as impl

    return impl(*args, **kwargs)

def build_edsl_probability_job_dict(*args, **kwargs):
    from .edsl_integration import build_edsl_probability_job_dict as impl

    return impl(*args, **kwargs)

def respondent_selection(*args, **kwargs):
    from .edsl_integration import respondent_selection as impl

    return impl(*args, **kwargs)

def digital_twin_job_builder_deps(*args, **kwargs):
    from .edsl_integration import digital_twin_job_builder_deps as impl

    return impl(*args, **kwargs)

def build_edsl_digital_twin_job_dict(*args, **kwargs):
    from .edsl_integration import build_edsl_digital_twin_job_dict as impl

    return impl(*args, **kwargs)

def rank_utility_question_text(*args, **kwargs):
    from .edsl_integration import rank_utility_question_text as impl

    return impl(*args, **kwargs)

def build_edsl_rank_utility_twin_job_dict(*args, **kwargs):
    from .edsl_integration import build_edsl_rank_utility_twin_job_dict as impl

    return impl(*args, **kwargs)

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

def twin_import_metadata(*args, **kwargs):
    from .twin_studies import twin_import_metadata as impl

    return impl(*args, **kwargs)

def twin_run_manifest_path(*args, **kwargs):
    from .twin_studies import twin_run_manifest_path as impl

    return impl(*args, **kwargs)

def read_twin_run_manifest(*args, **kwargs):
    from .twin_studies import read_twin_run_manifest as impl

    return impl(*args, **kwargs)

def twin_set_description(*args, **kwargs):
    from .twin_studies import twin_set_description as impl

    return impl(*args, **kwargs)

def natural_question_sort_key(*args, **kwargs):
    from .twin_studies import natural_question_sort_key as impl

    return impl(*args, **kwargs)

def build_twin_run_report_payload(*args, **kwargs):
    from .twin_studies import build_twin_run_report_payload as impl

    return impl(*args, **kwargs)

def write_twin_run_manifest(*args, **kwargs):
    from .twin_studies import write_twin_run_manifest as impl

    return impl(*args, **kwargs)

def upsert_twin_run_manifest(*args, **kwargs):
    from .twin_studies import upsert_twin_run_manifest as impl

    return impl(*args, **kwargs)


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


def twin_experiments_path(*args, **kwargs):
    from .twin_experiments import twin_experiments_path as impl

    return impl(*args, **kwargs)

def read_twin_experiments(*args, **kwargs):
    from .twin_experiments import read_twin_experiments as impl

    return impl(*args, **kwargs)

def write_twin_experiments(*args, **kwargs):
    from .twin_experiments import write_twin_experiments as impl

    return impl(*args, **kwargs)

def update_twin_experiments(*args, **kwargs):
    from .twin_experiments import update_twin_experiments as impl

    return impl(*args, **kwargs)

def upsert_twin_experiment(*args, **kwargs):
    from .twin_experiments import upsert_twin_experiment as impl

    return impl(*args, **kwargs)

def twin_plan_note_from_experiments(*args, **kwargs):
    from .twin_experiments import twin_plan_note_from_experiments as impl

    return impl(*args, **kwargs)

def set_twin_plan_note(*args, **kwargs):
    from .twin_experiments import set_twin_plan_note as impl

    return impl(*args, **kwargs)

def twin_experiment_description(*args, **kwargs):
    from .twin_experiments import twin_experiment_description as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_note(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_note as impl

    return impl(*args, **kwargs)

def experiment_id_from_job_and_approach(*args, **kwargs):
    from .twin_experiments import experiment_id_from_job_and_approach as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_record(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_record as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_list(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_list as impl

    return impl(*args, **kwargs)

def merge_plan_dicts(*args, **kwargs):
    from .twin_experiments import merge_plan_dicts as impl

    return impl(*args, **kwargs)

def list_or_none(*args, **kwargs):
    from .twin_experiments import list_or_none as impl

    return impl(*args, **kwargs)

def resolve_plan_file_list(*args, **kwargs):
    from .twin_experiments import resolve_plan_file_list as impl

    return impl(*args, **kwargs)

def twin_export_namespace_from_plan(*args, **kwargs):
    from .twin_experiments import twin_export_namespace_from_plan as impl

    return impl(*args, **kwargs)

def normalize_plan_heldout_questions(*args, **kwargs):
    from .twin_experiments import normalize_plan_heldout_questions as impl

    return impl(*args, **kwargs)

def estimate_plan_prediction_count(*args, **kwargs):
    from .twin_experiments import estimate_plan_prediction_count as impl

    return impl(*args, **kwargs)

def edsl_job_prediction_count(*args, **kwargs):
    from .twin_experiments import edsl_job_prediction_count as impl

    return impl(*args, **kwargs)

def prediction_count_check(*args, **kwargs):
    from .twin_experiments import prediction_count_check as impl

    return impl(*args, **kwargs)

def plan_approval_record(*args, **kwargs):
    from .twin_experiments import plan_approval_record as impl

    return impl(*args, **kwargs)

def is_plan_approved(*args, **kwargs):
    from .twin_experiments import is_plan_approved as impl

    return impl(*args, **kwargs)

def approved_plan_metadata(*args, **kwargs):
    from .twin_experiments import approved_plan_metadata as impl

    return impl(*args, **kwargs)

def require_twin_plan_approval(*args, **kwargs):
    from .twin_experiments import require_twin_plan_approval as impl

    return impl(*args, **kwargs)

def plan_id_from_config(*args, **kwargs):
    from .twin_experiments import plan_id_from_config as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_init_plan(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_init_plan as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_approve(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_approve as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_export_plan(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_export_plan as impl

    return impl(*args, **kwargs)

def twin_plan_experiments(*args, **kwargs):
    from .twin_experiments import twin_plan_experiments as impl

    return impl(*args, **kwargs)

def infer_results_job_id(*args, **kwargs):
    from .twin_experiments import infer_results_job_id as impl

    return impl(*args, **kwargs)

def twin_plan_status_payload(*args, **kwargs):
    from .twin_experiments import twin_plan_status_payload as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_plan_status(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_plan_status as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_import_plan_results(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_import_plan_results as impl

    return impl(*args, **kwargs)

def copy_package_artifact(*args, **kwargs):
    from .twin_experiments import copy_package_artifact as impl

    return impl(*args, **kwargs)

def render_twin_experiment_package_runbook(*args, **kwargs):
    from .twin_experiments import render_twin_experiment_package_runbook as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_package(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_package as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_bundle(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_bundle as impl

    return impl(*args, **kwargs)

def resolve_manifest_artifact_path(*args, **kwargs):
    from .twin_experiments import resolve_manifest_artifact_path as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_bundle_show(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_bundle_show as impl

    return impl(*args, **kwargs)

def rel_link(*args, **kwargs):
    from .twin_experiments import rel_link as impl

    return impl(*args, **kwargs)

def resolve_bundle_manifest_for_dashboard(*args, **kwargs):
    from .twin_experiments import resolve_bundle_manifest_for_dashboard as impl

    return impl(*args, **kwargs)

def render_twin_experiment_dashboard_html(*args, **kwargs):
    from .twin_experiments import render_twin_experiment_dashboard_html as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_dashboard(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_dashboard as impl

    return impl(*args, **kwargs)

def twin_experiment_comparison_rows(*args, **kwargs):
    from .twin_experiments import twin_experiment_comparison_rows as impl

    return impl(*args, **kwargs)

def twin_experiment_response_changes(*args, **kwargs):
    from .twin_experiments import twin_experiment_response_changes as impl

    return impl(*args, **kwargs)

def selected_twin_experiments(*args, **kwargs):
    from .twin_experiments import selected_twin_experiments as impl

    return impl(*args, **kwargs)

def output_twin_experiment_comparison(*args, **kwargs):
    from .twin_experiments import output_twin_experiment_comparison as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_compare(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_compare as impl

    return impl(*args, **kwargs)

def twin_experiment_plot_id(*args, **kwargs):
    from .twin_experiments import twin_experiment_plot_id as impl

    return impl(*args, **kwargs)

def plot_category_style(*args, **kwargs):
    from .twin_experiments import plot_category_style as impl

    return impl(*args, **kwargs)

def render_paired_probability_scatter_svg(*args, **kwargs):
    from .twin_experiments import render_paired_probability_scatter_svg as impl

    return impl(*args, **kwargs)

def render_top_choice_change_svg(*args, **kwargs):
    from .twin_experiments import render_top_choice_change_svg as impl

    return impl(*args, **kwargs)

def write_twin_experiment_plots(*args, **kwargs):
    from .twin_experiments import write_twin_experiment_plots as impl

    return impl(*args, **kwargs)

def normalize_plot_manifest_paths(*args, **kwargs):
    from .twin_experiments import normalize_plot_manifest_paths as impl

    return impl(*args, **kwargs)

def load_plot_summaries(*args, **kwargs):
    from .twin_experiments import load_plot_summaries as impl

    return impl(*args, **kwargs)

def attach_plot_artifacts_to_payload(*args, **kwargs):
    from .twin_experiments import attach_plot_artifacts_to_payload as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_plots(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_plots as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_select(*args, **kwargs):
    from .twin_experiments import cmd_twin_experiment_select as impl

    return impl(*args, **kwargs)

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


def cmd_twin_study_run(*args, **kwargs):
    from .twin_studies import cmd_twin_study_run as impl

    return impl(*args, **kwargs)

def cmd_twin_study_export_holdout(*args, **kwargs):
    from .twin_studies import cmd_twin_study_export_holdout as impl

    return impl(*args, **kwargs)

def cmd_twin_study_import_results_dir(*args, **kwargs):
    from .twin_studies import cmd_twin_study_import_results_dir as impl

    return impl(*args, **kwargs)

def cmd_twin_study_list(*args, **kwargs):
    from .twin_studies import cmd_twin_study_list as impl

    return impl(*args, **kwargs)

def cmd_twin_study_show(*args, **kwargs):
    from .twin_studies import cmd_twin_study_show as impl

    return impl(*args, **kwargs)

def cmd_twin_study_compare(*args, **kwargs):
    from .twin_studies import cmd_twin_study_compare as impl

    return impl(*args, **kwargs)

def load_twin_benchmark_config(*args, **kwargs):
    from .practitioner_reports import load_twin_benchmark_config as impl

    return impl(*args, **kwargs)

def benchmark_name(*args, **kwargs):
    from .practitioner_reports import benchmark_name as impl

    return impl(*args, **kwargs)

def benchmark_output_dir(*args, **kwargs):
    from .practitioner_reports import benchmark_output_dir as impl

    return impl(*args, **kwargs)

def benchmark_manifest_path(*args, **kwargs):
    from .practitioner_reports import benchmark_manifest_path as impl

    return impl(*args, **kwargs)

def list_value(*args, **kwargs):
    from .practitioner_reports import list_value as impl

    return impl(*args, **kwargs)

def benchmark_study_namespace(*args, **kwargs):
    from .practitioner_reports import benchmark_study_namespace as impl

    return impl(*args, **kwargs)

def build_twin_benchmark_report(*args, **kwargs):
    from .practitioner_reports import build_twin_benchmark_report as impl

    return impl(*args, **kwargs)

def build_single_survey_practitioner_payload(*args, **kwargs):
    from .practitioner_reports import build_single_survey_practitioner_payload as impl

    return impl(*args, **kwargs)

def cmd_twin_benchmark_run(*args, **kwargs):
    from .practitioner_reports import cmd_twin_benchmark_run as impl

    return impl(*args, **kwargs)

def cmd_twin_benchmark_report(*args, **kwargs):
    from .practitioner_reports import cmd_twin_benchmark_report as impl

    return impl(*args, **kwargs)

def compact_prediction_row(*args, **kwargs):
    from .practitioner_reports import compact_prediction_row as impl

    return impl(*args, **kwargs)

def build_practitioner_report_context(*args, **kwargs):
    from .practitioner_reports import build_practitioner_report_context as impl

    return impl(*args, **kwargs)

def practitioner_report_skill_text(*args, **kwargs):
    from .practitioner_reports import practitioner_report_skill_text as impl

    return impl(*args, **kwargs)

def build_practitioner_report_prompt(*args, **kwargs):
    from .practitioner_reports import build_practitioner_report_prompt as impl

    return impl(*args, **kwargs)

def practitioner_report_id_from_job(*args, **kwargs):
    from .practitioner_reports import practitioner_report_id_from_job as impl

    return impl(*args, **kwargs)

def build_edsl_practitioner_report_job_dict(*args, **kwargs):
    from .practitioner_reports import build_edsl_practitioner_report_job_dict as impl

    return impl(*args, **kwargs)

def default_practitioner_report_paths(*args, **kwargs):
    from .practitioner_reports import default_practitioner_report_paths as impl

    return impl(*args, **kwargs)

def write_practitioner_report_export(*args, **kwargs):
    from .practitioner_reports import write_practitioner_report_export as impl

    return impl(*args, **kwargs)

def extract_free_text_answer(*args, **kwargs):
    from .practitioner_reports import extract_free_text_answer as impl

    return impl(*args, **kwargs)

def cmd_twin_benchmark_practitioner_report_export(*args, **kwargs):
    from .practitioner_reports import cmd_twin_benchmark_practitioner_report_export as impl

    return impl(*args, **kwargs)

def cmd_twin_study_practitioner_report_export(*args, **kwargs):
    from .practitioner_reports import cmd_twin_study_practitioner_report_export as impl

    return impl(*args, **kwargs)

def cmd_twin_benchmark_practitioner_report_import(*args, **kwargs):
    from .practitioner_reports import cmd_twin_benchmark_practitioner_report_import as impl

    return impl(*args, **kwargs)

def cmd_twin_study_practitioner_report_import(*args, **kwargs):
    from .practitioner_reports import cmd_twin_study_practitioner_report_import as impl

    return impl(*args, **kwargs)

def cmd_twin_benchmark_practitioner_report_render(*args, **kwargs):
    from .practitioner_reports import cmd_twin_benchmark_practitioner_report_render as impl

    return impl(*args, **kwargs)

def cmd_twin_study_practitioner_report_render(*args, **kwargs):
    from .practitioner_reports import cmd_twin_study_practitioner_report_render as impl

    return impl(*args, **kwargs)

def generate_practitioner_report_markdown(*args, **kwargs):
    from .practitioner_reports import generate_practitioner_report_markdown as impl

    return impl(*args, **kwargs)

def load_twin_benchmark_report_source(*args, **kwargs):
    from .practitioner_reports import load_twin_benchmark_report_source as impl

    return impl(*args, **kwargs)

def cmd_twin_benchmark_practitioner_report(*args, **kwargs):
    from .practitioner_reports import cmd_twin_benchmark_practitioner_report as impl

    return impl(*args, **kwargs)

def cmd_twin_study_practitioner_report(*args, **kwargs):
    from .practitioner_reports import cmd_twin_study_practitioner_report as impl

    return impl(*args, **kwargs)

def build_twin_experiment_report_context(*args, **kwargs):
    from .practitioner_reports import build_twin_experiment_report_context as impl

    return impl(*args, **kwargs)

def build_twin_experiment_report_prompt(*args, **kwargs):
    from .practitioner_reports import build_twin_experiment_report_prompt as impl

    return impl(*args, **kwargs)

def experiment_report_id_from_job(*args, **kwargs):
    from .practitioner_reports import experiment_report_id_from_job as impl

    return impl(*args, **kwargs)

def build_edsl_twin_experiment_report_job_dict(*args, **kwargs):
    from .practitioner_reports import build_edsl_twin_experiment_report_job_dict as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_report_export(*args, **kwargs):
    from .practitioner_reports import cmd_twin_experiment_report_export as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_report_import(*args, **kwargs):
    from .practitioner_reports import cmd_twin_experiment_report_import as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_report_render(*args, **kwargs):
    from .practitioner_reports import cmd_twin_experiment_report_render as impl

    return impl(*args, **kwargs)

def cmd_twin_experiment_report(*args, **kwargs):
    from .practitioner_reports import cmd_twin_experiment_report as impl

    return impl(*args, **kwargs)

def cmd_edsl_run(*args, **kwargs):
    from .edsl_integration import cmd_edsl_run as impl

    return impl(*args, **kwargs)

def cmd_edsl_export(*args, **kwargs):
    from .edsl_integration import cmd_edsl_export as impl

    return impl(*args, **kwargs)

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
