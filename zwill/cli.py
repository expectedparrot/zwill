from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import gzip
import hashlib
import importlib.resources as resources
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .errors import ZwillError
from .executive_summary import build_executive_summary, remove_leading_executive_summary_heading
from .jsonlio import append_jsonl, read_jsonl, rewrite_jsonl
from .probability import (
    probability_job_id_from_job,
    probability_job_id_from_results,
    probability_jobs_dir,
    probability_predictions_path,
    true_probabilities_for,
)
from .probability_jobs import (
    ProbabilityJobBuilderDeps,
)
from .probability_jobs import (
    build_edsl_probability_job_dict as build_edsl_probability_job_dict_impl,
)
from .rank import (
    annotate_rank_items,
    build_rank_report,
    detect_rank_tasks,
    extract_rank_payload,
    potential_undetected_rank_batteries,
    rank_job_id_from_job,
    rank_job_id_from_results,
    rank_metrics,
    rank_twin_jobs_dir,
    rank_twin_predictions_path,
    selected_rank_tasks,
    synthetic_rank_questions,
)
from .reporting import (
    EP_REPORT_CSS,
    build_probability_report,
    copy_markdown_control,
    escape_script_text,
    fmt_probs,
    markdown_to_html,
    render_probability_report_html,
    render_twin_benchmark_report_html,
    render_twin_job_comparison_report_html,
    render_twin_practitioner_report_html,
    render_twin_report_html,
    render_twin_run_report_html,
    render_twin_summary_report_html,
    render_twin_supporting_artifacts_section,
    render_twin_value_diagnostics_section,
    report_display_title,
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
from .twin_jobs import (
    DigitalTwinJobBuilderDeps,
    answer_commonness_by_question,
    answer_commonness_text,
    balanced_by_actual,
    chunked_job_id,
    expand_question_text_fields,
    result_chunk_label,
    selected_heldout_question_names,
    stratified_by_actual,
)
from .twin_jobs import (
    build_edsl_digital_twin_job_dict as build_edsl_digital_twin_job_dict_impl,
)
from .twin_report import build_twin_report, twin_top_prediction
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


def cmd_guide_show(*args, **kwargs):
    from .guide_commands import cmd_guide_show as impl

    return impl(*args, **kwargs)

def cmd_guide_list(*args, **kwargs):
    from .guide_commands import cmd_guide_list as impl

    return impl(*args, **kwargs)

def cmd_next(*args, **kwargs):
    from .guide_commands import cmd_next as impl

    return impl(*args, **kwargs)

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


def load_workflow_file(*args, **kwargs):
    from .workflow_commands import load_workflow_file as impl

    return impl(*args, **kwargs)

def load_object_file(*args, **kwargs):
    from .workflow_commands import load_object_file as impl

    return impl(*args, **kwargs)

def workflow_vars(*args, **kwargs):
    from .workflow_commands import workflow_vars as impl

    return impl(*args, **kwargs)

def render_workflow_value(*args, **kwargs):
    from .workflow_commands import render_workflow_value as impl

    return impl(*args, **kwargs)

def workflow_step_id(*args, **kwargs):
    from .workflow_commands import workflow_step_id as impl

    return impl(*args, **kwargs)

def rendered_workflow_steps(*args, **kwargs):
    from .workflow_commands import rendered_workflow_steps as impl

    return impl(*args, **kwargs)

def default_workflow_artifacts_dir(*args, **kwargs):
    from .workflow_commands import default_workflow_artifacts_dir as impl

    return impl(*args, **kwargs)

def workflow_manifest_path(*args, **kwargs):
    from .workflow_commands import workflow_manifest_path as impl

    return impl(*args, **kwargs)

def workflow_base_payload(*args, **kwargs):
    from .workflow_commands import workflow_base_payload as impl

    return impl(*args, **kwargs)

def cmd_workflow_explain(*args, **kwargs):
    from .workflow_commands import cmd_workflow_explain as impl

    return impl(*args, **kwargs)

def cmd_workflow_dry_run(*args, **kwargs):
    from .workflow_commands import cmd_workflow_dry_run as impl

    return impl(*args, **kwargs)

def cmd_workflow_run(*args, **kwargs):
    from .workflow_commands import cmd_workflow_run as impl

    return impl(*args, **kwargs)

def find_local_env(start: Path | None = None) -> Path | None:
    start = start or Path.cwd()
    for directory in [start, *start.parents]:
        path = directory / ".env"
        if path.exists() and path.is_file():
            return path
    return None


# Credential env vars zwill / EDSL may rely on. `load_local_env` reports which of
# these are present (names only, never values) so callers can tell whether a key
# is available regardless of whether it came from the .env or the ambient
# environment. `loaded_keys` alone is misleading: it only lists keys the .env
# newly injected, so it reads empty when the keys are already exported.
CREDENTIAL_ENV_KEYS = (
    "EXPECTED_PARROT_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "TOGETHER_API_KEY",
    "GROQ_API_KEY",
    "AZURE_OPENAI_API_KEY",
)


def present_credential_env_keys() -> list[str]:
    return [key for key in CREDENTIAL_ENV_KEYS if os.environ.get(key)]


def load_local_env(path: Path | None = None) -> dict[str, Any]:
    path = path or find_local_env()
    if path is None:
        return {"path": None, "loaded_keys": [], "present_keys": present_credential_env_keys()}
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
    return {"path": str(path), "loaded_keys": loaded, "present_keys": present_credential_env_keys()}


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


DEFAULT_CHECKBOX_DELIMITER = "|"


def checkbox_selection_tokens(answer_value: Any, delimiter: str | None = None) -> list[str]:
    """Split a multi-select answer string into its individual selected labels."""
    delimiter = delimiter or DEFAULT_CHECKBOX_DELIMITER
    return [token.strip() for token in str(answer_value).split(delimiter) if token.strip()]


def answer_option_issue(
    question: dict[str, Any], question_name: Any, answer_value: Any, line: int | None = None
) -> dict[str, Any] | None:
    """Validate a non-missing answer against a question's option universe.

    For a `checkbox` (multi-select) question the answer is split on the question's
    `option_delimiter` (default `|`) and every selected token must be a known
    option. For other questions the answer must equal one option. Returns an issue
    dict when invalid, else None. Questions with no `question_options` are not
    validated.
    """
    valid_options = question.get("question_options") or []
    if not valid_options:
        return None
    if question.get("question_type") == "checkbox":
        delimiter = question.get("option_delimiter") or DEFAULT_CHECKBOX_DELIMITER
        tokens = checkbox_selection_tokens(answer_value, delimiter)
        invalid = [token for token in tokens if token not in valid_options]
        if tokens and not invalid:
            return None
        return {
            "code": "invalid_answer_option",
            "line": line,
            "question": question_name,
            "answer": answer_value,
            "invalid_selections": invalid,
            "valid_options": valid_options,
            "option_delimiter": delimiter,
        }
    if answer_value in valid_options:
        return None
    return {
        "code": "invalid_answer_option",
        "line": line,
        "question": question_name,
        "answer": answer_value,
        "valid_options": valid_options,
    }


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
        return answer_option_issue(questions[question_name], question_name, answer["answer"], line)
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
        next_steps=[
            "zwill guide   # end-to-end walkthrough",
            "zwill next    # what to run next at any point",
            "zwill survey create --name <survey>",
        ],
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
    ensure_project(args.project_id, title=args.title)
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
            # The file holds the raw report payload (survey/summary/questions/...);
            # stdout carries a parseable envelope pointing at it, so scripts get a
            # consistent {command,status,data,...} shape on stdout.
            Path(args.path).write_text(output + "\n")
            print_json(
                envelope(
                    "zwill survey report",
                    "ok",
                    {"survey": args.survey, "format": "json", "path": str(args.path), **payload["summary"]},
                )
            )
        else:
            print(output)
        return None
    if args.format == "html":
        output = render_survey_report_html(payload)
        if args.path:
            Path(args.path).write_text(output)
            # Mirror the json/csv branches: the file holds the rendered report and
            # stdout carries a parseable {command,status,data,...} envelope.
            print_json(
                envelope(
                    "zwill survey report",
                    "ok",
                    {"survey": args.survey, "format": "html", "path": str(args.path), **payload["summary"]},
                )
            )
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

def cmd_report_generate_interpretations(*args, **kwargs):
    from .report_bundle import cmd_report_generate_interpretations as impl

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

def cmd_raw_add(*args, **kwargs):
    from .survey_commands import cmd_raw_add as impl

    return impl(*args, **kwargs)

def cmd_raw_list(*args, **kwargs):
    from .survey_commands import cmd_raw_list as impl

    return impl(*args, **kwargs)

def markdown_from_args(*args, **kwargs):
    from .survey_commands import markdown_from_args as impl

    return impl(*args, **kwargs)

def cmd_context_add(*args, **kwargs):
    from .survey_commands import cmd_context_add as impl

    return impl(*args, **kwargs)

def cmd_context_set(*args, **kwargs):
    from .survey_commands import cmd_context_set as impl

    return impl(*args, **kwargs)

def cmd_context_show(*args, **kwargs):
    from .survey_commands import cmd_context_show as impl

    return impl(*args, **kwargs)

def cmd_question_add(*args, **kwargs):
    from .survey_commands import cmd_question_add as impl

    return impl(*args, **kwargs)

def cmd_question_import(*args, **kwargs):
    from .survey_commands import cmd_question_import as impl

    return impl(*args, **kwargs)

def cmd_respondent_add(*args, **kwargs):
    from .survey_commands import cmd_respondent_add as impl

    return impl(*args, **kwargs)

def cmd_respondent_import(*args, **kwargs):
    from .survey_commands import cmd_respondent_import as impl

    return impl(*args, **kwargs)

def material_markdown_from_args(*args, **kwargs):
    from .survey_commands import material_markdown_from_args as impl

    return impl(*args, **kwargs)

def normalize_tags(*args, **kwargs):
    from .survey_commands import normalize_tags as impl

    return impl(*args, **kwargs)

def next_agent_material_id(*args, **kwargs):
    from .survey_commands import next_agent_material_id as impl

    return impl(*args, **kwargs)

def validate_agent_material_row(*args, **kwargs):
    from .survey_commands import validate_agent_material_row as impl

    return impl(*args, **kwargs)

def cmd_agent_material_add(*args, **kwargs):
    from .survey_commands import cmd_agent_material_add as impl

    return impl(*args, **kwargs)

def cmd_agent_material_import(*args, **kwargs):
    from .survey_commands import cmd_agent_material_import as impl

    return impl(*args, **kwargs)

def cmd_agent_material_list(*args, **kwargs):
    from .survey_commands import cmd_agent_material_list as impl

    return impl(*args, **kwargs)

def cmd_agent_material_show(*args, **kwargs):
    from .survey_commands import cmd_agent_material_show as impl

    return impl(*args, **kwargs)

def cmd_answer_add(*args, **kwargs):
    from .survey_commands import cmd_answer_add as impl

    return impl(*args, **kwargs)

def cmd_answer_import(*args, **kwargs):
    from .survey_commands import cmd_answer_import as impl

    return impl(*args, **kwargs)

def cmd_status(*args, **kwargs):
    from .survey_commands import cmd_status as impl

    return impl(*args, **kwargs)

def compute_marginals(*args, **kwargs):
    from .survey_commands import compute_marginals as impl

    return impl(*args, **kwargs)

def cmd_commit(*args, **kwargs):
    from .survey_commands import cmd_commit as impl

    return impl(*args, **kwargs)

def cmd_quarantine_list(*args, **kwargs):
    from .survey_commands import cmd_quarantine_list as impl

    return impl(*args, **kwargs)

def cmd_quarantine_resolve(*args, **kwargs):
    from .survey_commands import cmd_quarantine_resolve as impl

    return impl(*args, **kwargs)

def cmd_table(*args, **kwargs):
    from .survey_commands import cmd_table as impl

    return impl(*args, **kwargs)

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

def cmd_probability_results_import(*args, **kwargs):
    from .probability_commands import cmd_probability_results_import as impl

    return impl(*args, **kwargs)

def cmd_probability_results_report(*args, **kwargs):
    from .probability_commands import cmd_probability_results_report as impl

    return impl(*args, **kwargs)

def filtered_probability_prediction_rows(*args, **kwargs):
    from .probability_commands import filtered_probability_prediction_rows as impl

    return impl(*args, **kwargs)

def build_one_shot_analysis_report_context(*args, **kwargs):
    from .probability_commands import build_one_shot_analysis_report_context as impl

    return impl(*args, **kwargs)

def build_one_shot_analysis_report_prompt(*args, **kwargs):
    from .probability_commands import build_one_shot_analysis_report_prompt as impl

    return impl(*args, **kwargs)

def build_edsl_one_shot_analysis_report_job_dict(*args, **kwargs):
    from .probability_commands import build_edsl_one_shot_analysis_report_job_dict as impl

    return impl(*args, **kwargs)

def cmd_probability_results_analysis_export(*args, **kwargs):
    from .probability_commands import cmd_probability_results_analysis_export as impl

    return impl(*args, **kwargs)

def cmd_probability_results_analysis_import(*args, **kwargs):
    from .probability_commands import cmd_probability_results_analysis_import as impl

    return impl(*args, **kwargs)

def cmd_probability_results_analysis_render(*args, **kwargs):
    from .probability_commands import cmd_probability_results_analysis_render as impl

    return impl(*args, **kwargs)

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

def agent_study_question_name(*args, **kwargs):
    from .agent_studies import agent_study_question_name as impl

    return impl(*args, **kwargs)

def read_agent_study_manifest(*args, **kwargs):
    from .agent_studies import read_agent_study_manifest as impl

    return impl(*args, **kwargs)

def write_agent_study_manifest(*args, **kwargs):
    from .agent_studies import write_agent_study_manifest as impl

    return impl(*args, **kwargs)

def upsert_agent_study_manifest(*args, **kwargs):
    from .agent_studies import upsert_agent_study_manifest as impl

    return impl(*args, **kwargs)

def agent_study_import_metadata(*args, **kwargs):
    from .agent_studies import agent_study_import_metadata as impl

    return impl(*args, **kwargs)

def cmd_agent_study_import(*args, **kwargs):
    from .agent_studies import cmd_agent_study_import as impl

    return impl(*args, **kwargs)

def build_agent_study_report(*args, **kwargs):
    from .agent_studies import build_agent_study_report as impl

    return impl(*args, **kwargs)

def render_agent_study_report_html(*args, **kwargs):
    from .agent_studies import render_agent_study_report_html as impl

    return impl(*args, **kwargs)

def html_escape(*args, **kwargs):
    from .agent_studies import html_escape as impl

    return impl(*args, **kwargs)

def cmd_agent_study_report(*args, **kwargs):
    from .agent_studies import cmd_agent_study_report as impl

    return impl(*args, **kwargs)

def cmd_agent_study_list(*args, **kwargs):
    from .agent_studies import cmd_agent_study_list as impl

    return impl(*args, **kwargs)

def cmd_agent_study_show(*args, **kwargs):
    from .agent_studies import cmd_agent_study_show as impl

    return impl(*args, **kwargs)

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

def cmd_twin_results_import(*args, **kwargs):
    from .result_commands import cmd_twin_results_import as impl

    return impl(*args, **kwargs)

def cmd_twin_results_retry_malformed(*args, **kwargs):
    from .result_commands import cmd_twin_results_retry_malformed as impl

    return impl(*args, **kwargs)

def cmd_rank_results_import(*args, **kwargs):
    from .result_commands import cmd_rank_results_import as impl

    return impl(*args, **kwargs)

def render_rank_report_html(*args, **kwargs):
    from .result_commands import render_rank_report_html as impl

    return impl(*args, **kwargs)

def fmt_optional(*args, **kwargs):
    from .result_commands import fmt_optional as impl

    return impl(*args, **kwargs)

def cmd_rank_results_report(*args, **kwargs):
    from .result_commands import cmd_rank_results_report as impl

    return impl(*args, **kwargs)

def selected_questions_arg(*args, **kwargs):
    from .twin_result_commands import selected_questions_arg as impl

    return impl(*args, **kwargs)

def default_calibrated_twin_job_id(*args, **kwargs):
    from .twin_result_commands import default_calibrated_twin_job_id as impl

    return impl(*args, **kwargs)

def probability_job_targets(*args, **kwargs):
    from .twin_result_commands import probability_job_targets as impl

    return impl(*args, **kwargs)

def empirical_marginal_targets(*args, **kwargs):
    from .twin_result_commands import empirical_marginal_targets as impl

    return impl(*args, **kwargs)

def cmd_twin_results_calibrate_marginal(*args, **kwargs):
    from .twin_result_commands import cmd_twin_results_calibrate_marginal as impl

    return impl(*args, **kwargs)

def cmd_twin_baseline_run(*args, **kwargs):
    from .twin_baseline_commands import cmd_twin_baseline_run as impl

    return impl(*args, **kwargs)

def cmd_twin_results_bootstrap(*args, **kwargs):
    from .twin_result_commands import cmd_twin_results_bootstrap as impl

    return impl(*args, **kwargs)

def cmd_twin_results_leakage_audit(*args, **kwargs):
    from .twin_result_commands import cmd_twin_results_leakage_audit as impl

    return impl(*args, **kwargs)

def cmd_twin_validate(*args, **kwargs):
    from .twin_validation_workflow import cmd_twin_validate as impl

    return impl(*args, **kwargs)

def filtered_twin_prediction_rows(*args, **kwargs):
    from .twin_result_commands import filtered_twin_prediction_rows as impl

    return impl(*args, **kwargs)

def cmd_twin_results_export(*args, **kwargs):
    from .twin_result_commands import cmd_twin_results_export as impl

    return impl(*args, **kwargs)

def cmd_twin_results_package(*args, **kwargs):
    from .twin_result_commands import cmd_twin_results_package as impl

    return impl(*args, **kwargs)

def cmd_twin_results_marginal_diagnostics(*args, **kwargs):
    from .twin_result_commands import cmd_twin_results_marginal_diagnostics as impl

    return impl(*args, **kwargs)

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


def twin_approaches_path(*args, **kwargs):
    from .twin_approaches import twin_approaches_path as impl

    return impl(*args, **kwargs)

def read_twin_approaches(*args, **kwargs):
    from .twin_approaches import read_twin_approaches as impl

    return impl(*args, **kwargs)

def write_twin_approaches(*args, **kwargs):
    from .twin_approaches import write_twin_approaches as impl

    return impl(*args, **kwargs)

def update_twin_approaches(*args, **kwargs):
    from .twin_approaches import update_twin_approaches as impl

    return impl(*args, **kwargs)

def twin_approach_id(*args, **kwargs):
    from .twin_approaches import twin_approach_id as impl

    return impl(*args, **kwargs)

def normalize_twin_approach_record(*args, **kwargs):
    from .twin_approaches import normalize_twin_approach_record as impl

    return impl(*args, **kwargs)

def twin_approach_from_args(*args, **kwargs):
    from .twin_approaches import twin_approach_from_args as impl

    return impl(*args, **kwargs)

def upsert_twin_approach(*args, **kwargs):
    from .twin_approaches import upsert_twin_approach as impl

    return impl(*args, **kwargs)

def markdown_from_note_args(*args, **kwargs):
    from .twin_approaches import markdown_from_note_args as impl

    return impl(*args, **kwargs)

def cmd_twin_approach_add(*args, **kwargs):
    from .twin_approaches import cmd_twin_approach_add as impl

    return impl(*args, **kwargs)

def cmd_twin_approach_note(*args, **kwargs):
    from .twin_approaches import cmd_twin_approach_note as impl

    return impl(*args, **kwargs)

def cmd_twin_approach_list(*args, **kwargs):
    from .twin_approaches import cmd_twin_approach_list as impl

    return impl(*args, **kwargs)

def cmd_twin_approach_show(*args, **kwargs):
    from .twin_approaches import cmd_twin_approach_show as impl

    return impl(*args, **kwargs)

def cmd_twin_approach_scaffold(*args, **kwargs):
    from .twin_approaches import cmd_twin_approach_scaffold as impl

    return impl(*args, **kwargs)

def find_twin_approach_record(*args, **kwargs):
    from .twin_approaches import find_twin_approach_record as impl

    return impl(*args, **kwargs)

def diff_values(*args, **kwargs):
    from .twin_approaches import diff_values as impl

    return impl(*args, **kwargs)

def twin_approach_diff_payload(*args, **kwargs):
    from .twin_approaches import twin_approach_diff_payload as impl

    return impl(*args, **kwargs)

def render_twin_approach_diff_html(*args, **kwargs):
    from .twin_approaches import render_twin_approach_diff_html as impl

    return impl(*args, **kwargs)

def cmd_twin_approach_diff(*args, **kwargs):
    from .twin_approaches import cmd_twin_approach_diff as impl

    return impl(*args, **kwargs)

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

def selected_twin_result_job_ids(*args, **kwargs):
    from .twin_validation_commands import selected_twin_result_job_ids as impl

    return impl(*args, **kwargs)

def attach_twin_set_descriptions(*args, **kwargs):
    from .twin_validation_commands import attach_twin_set_descriptions as impl

    return impl(*args, **kwargs)

def build_twin_job_comparison_report_payload(*args, **kwargs):
    from .twin_validation_commands import build_twin_job_comparison_report_payload as impl

    return impl(*args, **kwargs)

def cmd_twin_results_report(*args, **kwargs):
    from .twin_validation_commands import cmd_twin_results_report as impl

    return impl(*args, **kwargs)

def cmd_twin_results_executive_summary(*args, **kwargs):
    from .twin_validation_commands import cmd_twin_results_executive_summary as impl

    return impl(*args, **kwargs)

def build_executive_summary_report_context(*args, **kwargs):
    from .twin_validation_commands import build_executive_summary_report_context as impl

    return impl(*args, **kwargs)

def build_executive_summary_report_prompt(*args, **kwargs):
    from .twin_validation_commands import build_executive_summary_report_prompt as impl

    return impl(*args, **kwargs)

def build_executive_summary_report_section_prompts(*args, **kwargs):
    from .twin_validation_commands import build_executive_summary_report_section_prompts as impl

    return impl(*args, **kwargs)

def build_edsl_executive_summary_report_job_dict(*args, **kwargs):
    from .twin_validation_commands import build_edsl_executive_summary_report_job_dict as impl

    return impl(*args, **kwargs)

def cmd_twin_results_executive_summary_export(*args, **kwargs):
    from .twin_validation_commands import cmd_twin_results_executive_summary_export as impl

    return impl(*args, **kwargs)

def cmd_twin_results_executive_summary_import(*args, **kwargs):
    from .twin_validation_commands import cmd_twin_results_executive_summary_import as impl

    return impl(*args, **kwargs)

def cmd_twin_results_executive_summary_render(*args, **kwargs):
    from .twin_validation_commands import cmd_twin_results_executive_summary_render as impl

    return impl(*args, **kwargs)

def cmd_twin_results_compare_report(*args, **kwargs):
    from .twin_validation_commands import cmd_twin_results_compare_report as impl

    return impl(*args, **kwargs)

def cmd_twin_results_run_report(*args, **kwargs):
    from .twin_validation_commands import cmd_twin_results_run_report as impl

    return impl(*args, **kwargs)

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

def default_pew_source_dir(*args, **kwargs):
    from .workflow_commands import default_pew_source_dir as impl

    return impl(*args, **kwargs)

def cmd_workflow_pew_demo(*args, **kwargs):
    from .workflow_commands import cmd_workflow_pew_demo as impl

    return impl(*args, **kwargs)

def build_parser() -> argparse.ArgumentParser:
    from .cli_parser import build_parser as build_cli_parser

    return build_cli_parser()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # Load the nearest .env once, before any command runs, so every command
        # that makes model/embedding calls (edsl-run, twin-validate, the
        # conditional baseline, ...) sees the same keys. Individual commands may
        # still report their own `loaded_env`; load_local_env is idempotent
        # because it never overwrites a key already present in os.environ.
        env_path = Path(args.env_path) if getattr(args, "env_path", None) else None
        load_local_env(env_path)
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
