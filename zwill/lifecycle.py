"""Safe archive and deletion lifecycle commands for surveys and projects."""

from __future__ import annotations

import argparse
import shutil

from .cli import *  # noqa: F403


def _archived_surveys_dir(project: Path) -> Path:
    return project / "archived_surveys"


def _inventory(path: Path) -> dict[str, Any]:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    directories = sorted(item for item in path.rglob("*") if item.is_dir())
    return {
        "path": str(path),
        "file_count": len(files),
        "directory_count": len(directories),
        "bytes": sum(item.stat().st_size for item in files),
        "files": [str(item.relative_to(path)) for item in files],
    }


def _receipt(kind: str, identifier: str, payload: dict[str, Any]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ROOT / "deletion_receipts" / f"{stamp}-{kind}-{slugify(identifier)}.json"
    write_json(path, payload)
    return path


def _require_confirmation(actual: str | None, expected: str) -> None:
    if actual != expected:
        raise ZwillError(
            "confirmation_required",
            f"Permanent deletion requires --confirm {expected}.",
            context={"expected_confirmation": expected},
        )


def cmd_survey_archive(args: argparse.Namespace) -> dict[str, Any]:
    project_id = active_project_id()
    project = active_project_dir()
    source = require_survey(args.survey)
    destination = _archived_surveys_dir(project) / args.survey
    if destination.exists():
        raise ZwillError("already_exists", f"Archived survey already exists: {args.survey}.")
    surveys = read_json(project_surveys_path(), [])
    survey_record = next((row for row in surveys if row.get("name") == args.survey), None)
    data = {
        "project_id": project_id,
        "survey": args.survey,
        "source": str(source),
        "destination": str(destination),
        "inventory": _inventory(source),
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        return envelope("zwill survey archive", "ok", data, next_steps=[f"zwill survey archive --survey {args.survey}"])
    write_json(
        source / "archive.json",
        {
            "schema_version": 1,
            "project_id": project_id,
            "survey": args.survey,
            "archived_at": utc_now(),
            "survey_record": survey_record,
        },
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    write_json(project_surveys_path(), [row for row in surveys if row.get("name") != args.survey])
    data["archived"] = True
    return envelope(
        "zwill survey archive",
        "ok",
        data,
        next_steps=[f"zwill survey restore --survey {args.survey}"],
    )


def cmd_survey_archived(args: argparse.Namespace) -> dict[str, Any]:
    project_id = active_project_id()
    root = _archived_surveys_dir(active_project_dir())
    rows = []
    if root.is_dir():
        for path in sorted(item for item in root.iterdir() if item.is_dir()):
            marker = read_json(path / "archive.json", {})
            rows.append(
                {
                    "survey": path.name,
                    "archived_at": marker.get("archived_at"),
                    "inventory": _inventory(path),
                }
            )
    return envelope(
        "zwill survey archived",
        "ok",
        {"project_id": project_id, "archived_surveys": rows, "count": len(rows)},
    )


def cmd_survey_restore(args: argparse.Namespace) -> dict[str, Any]:
    project_id = active_project_id()
    project = active_project_dir()
    source = _archived_surveys_dir(project) / args.survey
    destination = survey_dir(args.survey)
    if not source.exists():
        raise ZwillError("not_found", f"Archived survey does not exist: {args.survey}.")
    if destination.exists():
        raise ZwillError("already_exists", f"Live survey already exists: {args.survey}.")
    marker = read_json(source / "archive.json", {})
    record = marker.get("survey_record") or {
        "name": args.survey,
        "status": "draft",
        "created_at": marker.get("archived_at", utc_now()),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    (destination / "archive.json").unlink(missing_ok=True)
    surveys = read_json(project_surveys_path(), [])
    if not any(row.get("name") == args.survey for row in surveys):
        surveys.append(record)
        write_json(project_surveys_path(), surveys)
    return envelope(
        "zwill survey restore",
        "ok",
        {"project_id": project_id, "survey": args.survey, "path": str(destination), "restored": True},
        next_steps=[f"zwill survey show --name {args.survey}"],
    )


def cmd_survey_delete(args: argparse.Namespace) -> dict[str, Any]:
    project_id = active_project_id()
    project = active_project_dir()
    live = survey_dir(args.survey)
    archived = _archived_surveys_dir(project) / args.survey
    if live.exists() and archived.exists():
        raise ZwillError("invalid_state", f"Survey exists in both live and archived state: {args.survey}.")
    target = live if live.exists() else archived
    if not target.exists():
        raise ZwillError("not_found", f"Survey does not exist: {args.survey}.")
    inventory = _inventory(target)
    data = {
        "project_id": project_id,
        "survey": args.survey,
        "state": "live" if target == live else "archived",
        "inventory": inventory,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        return envelope(
            "zwill survey delete",
            "ok",
            data,
            next_steps=[f"zwill survey delete --survey {args.survey} --confirm {args.survey}"],
        )
    _require_confirmation(args.confirm, args.survey)
    shutil.rmtree(target)
    surveys = read_json(project_surveys_path(), [])
    write_json(project_surveys_path(), [row for row in surveys if row.get("name") != args.survey])
    deleted_at = utc_now()
    receipt_payload = {
        "schema_version": 1,
        "operation": "survey_delete",
        "deleted_at": deleted_at,
        **data,
        "dry_run": False,
    }
    receipt = _receipt("survey", f"{project_id}-{args.survey}", receipt_payload)
    data.update({"deleted": True, "deleted_at": deleted_at, "receipt": str(receipt)})
    return envelope("zwill survey delete", "ok", data)


def cmd_project_delete(args: argparse.Namespace) -> dict[str, Any]:
    require_workspace()
    project_id = validate_project_id(args.project_id)
    target = project_dir(project_id)
    if not target.exists():
        raise ZwillError("not_found", f"Project does not exist: {project_id}.")
    projects = sorted(path.name for path in projects_dir().iterdir() if path.is_dir())
    active = project_id == active_project_id()
    sole = len(projects) <= 1
    data = {
        "project_id": project_id,
        "inventory": _inventory(target),
        "active": active,
        "sole_project": sole,
        "dry_run": bool(args.dry_run),
        "blocked": active or sole,
        "blocked_reasons": [
            reason
            for condition, reason in [
                (active, "active_project"),
                (sole, "sole_remaining_project"),
            ]
            if condition
        ],
    }
    if args.dry_run:
        next_steps = [] if data["blocked"] else [f"zwill project delete {project_id} --confirm {project_id}"]
        return envelope("zwill project delete", "ok", data, next_steps=next_steps)
    if active:
        raise ZwillError(
            "invalid_state",
            f"Cannot delete the active project: {project_id}.",
            hint="Switch projects first with `zwill project use`.",
        )
    if sole:
        raise ZwillError("invalid_state", "Cannot delete the only remaining project.")
    _require_confirmation(args.confirm, project_id)
    shutil.rmtree(target)
    deleted_at = utc_now()
    receipt_payload = {
        "schema_version": 1,
        "operation": "project_delete",
        "deleted_at": deleted_at,
        **data,
        "dry_run": False,
    }
    receipt = _receipt("project", project_id, receipt_payload)
    data.update({"deleted": True, "deleted_at": deleted_at, "receipt": str(receipt)})
    return envelope("zwill project delete", "ok", data)
