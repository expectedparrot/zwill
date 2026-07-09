from __future__ import annotations

from pathlib import Path

import zwill.cli as cli
from zwill.cli import main, output_root, resolve_output_path


def test_relative_output_rebases_under_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ZWILL_OUT", raising=False)
    resolved = resolve_output_path("reports/x.html")
    assert resolved == Path("zwill_work") / "reports" / "x.html"
    # The parent is created so a caller can write immediately.
    assert resolved.parent.is_dir()


def test_absolute_output_passes_through_with_one_warning(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ZWILL_OUT", raising=False)
    cli._warned_output_escapes.clear()
    target = tmp_path / "elsewhere" / "r.html"
    assert resolve_output_path(target) == target
    assert resolve_output_path(target) == target  # second call: no duplicate warning
    err = capsys.readouterr().err
    assert err.count("outside the") == 1


def test_managed_zwill_paths_are_never_rebased(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ZWILL_OUT", raising=False)
    managed = Path(".zwill") / "projects" / "default" / "x.json"
    assert resolve_output_path(managed) == managed


def test_paths_already_under_root_are_not_double_nested(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ZWILL_OUT", raising=False)
    already = Path("zwill_work") / "demo_report"
    assert resolve_output_path(already) == already


def test_env_var_overrides_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZWILL_OUT", "custom_out")
    assert output_root() == Path("custom_out")
    assert resolve_output_path("r.html") == Path("custom_out") / "r.html"


def test_init_output_dir_is_persisted_and_used(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ZWILL_OUT", raising=False)
    assert main(["init", "--output-dir", "deliverables"]) == 0
    assert output_root() == Path("deliverables")
    # Re-init without the flag preserves the configured directory.
    assert main(["init"]) == 0
    assert output_root() == Path("deliverables")
