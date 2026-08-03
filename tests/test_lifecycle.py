from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from zwill import cli
from zwill.cli import ZwillError
from zwill.lifecycle import (
    cmd_project_delete,
    cmd_survey_archive,
    cmd_survey_archived,
    cmd_survey_delete,
    cmd_survey_restore,
)


def _setup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cli.cmd_init(argparse.Namespace())
    cli.cmd_survey_create(argparse.Namespace(name="demo"))
    (cli.survey_dir("demo") / "payload.txt").write_text("evidence")


def test_survey_archive_dry_run_and_restore(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    preview = cmd_survey_archive(argparse.Namespace(survey="demo", dry_run=True))
    assert preview["data"]["inventory"]["file_count"] >= 1
    assert cli.survey_dir("demo").exists()

    archived = cmd_survey_archive(argparse.Namespace(survey="demo", dry_run=False))
    assert archived["data"]["archived"] is True
    assert not cli.survey_dir("demo").exists()
    assert (cli.active_project_dir() / "archived_surveys" / "demo").exists()
    assert json.loads(cli.project_surveys_path().read_text()) == []
    listed = cmd_survey_archived(argparse.Namespace())
    assert listed["data"]["count"] == 1
    assert listed["data"]["archived_surveys"][0]["survey"] == "demo"

    restored = cmd_survey_restore(argparse.Namespace(survey="demo"))
    assert restored["data"]["restored"] is True
    assert (cli.survey_dir("demo") / "payload.txt").read_text() == "evidence"
    assert [row["name"] for row in json.loads(cli.project_surveys_path().read_text())] == ["demo"]


def test_survey_delete_requires_exact_confirmation_and_writes_receipt(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    preview = cmd_survey_delete(argparse.Namespace(survey="demo", dry_run=True, confirm=None))
    assert preview["data"]["inventory"]["bytes"] > 0
    with pytest.raises(ZwillError) as exc:
        cmd_survey_delete(argparse.Namespace(survey="demo", dry_run=False, confirm="wrong"))
    assert exc.value.code == "confirmation_required"

    result = cmd_survey_delete(argparse.Namespace(survey="demo", dry_run=False, confirm="demo"))
    assert result["data"]["deleted"] is True
    assert not cli.survey_dir("demo").exists()
    assert Path(result["data"]["receipt"]).exists()


def test_project_delete_refuses_active_and_deletes_inactive_project(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    cli.cmd_project_create(argparse.Namespace(project_id="scratch", title="Scratch", use=False))
    active_preview = cmd_project_delete(argparse.Namespace(project_id="default", dry_run=True, confirm=None))
    assert active_preview["data"]["blocked_reasons"] == ["active_project"]
    with pytest.raises(ZwillError):
        cmd_project_delete(argparse.Namespace(project_id="default", dry_run=False, confirm="default"))

    result = cmd_project_delete(argparse.Namespace(project_id="scratch", dry_run=False, confirm="scratch"))
    assert result["data"]["deleted"] is True
    assert not cli.project_dir("scratch").exists()
    assert Path(result["data"]["receipt"]).exists()


def test_project_delete_refuses_only_remaining_project(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ZwillError) as exc:
        cmd_project_delete(argparse.Namespace(project_id="default", dry_run=False, confirm="default"))
    assert exc.value.code == "invalid_state"
