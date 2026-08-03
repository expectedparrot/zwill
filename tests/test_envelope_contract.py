"""The agent-facing output contract: one JSON envelope on stdout, always.

Runs commands through the real console entry point in a subprocess so stdout
purity is tested end to end (no stray prints around the envelope).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from zwill.cli import build_parser

REPO = Path(__file__).resolve().parents[1]
ENVELOPE_KEYS = {"schema_version", "command", "status", "argv", "data", "warnings", "errors", "next_steps"}


def run_zwill(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "zwill.cli", *args],
        cwd=cwd or REPO, text=True, capture_output=True,
    )


@pytest.mark.parametrize("argv", [
    ("version",),
    ("capabilities",),
    ("skills", "list"),
    ("skills", "path", "digital-twin-study-runner"),
    ("guide", "list"),
    ("guide", "show", "agent-workflow"),
])
def test_stdout_is_exactly_one_envelope(argv: tuple[str, ...], tmp_path: Path) -> None:
    completed = run_zwill(*argv, cwd=tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)  # fails if anything else is on stdout
    assert ENVELOPE_KEYS <= set(payload)
    assert payload["status"] == "ok"
    assert payload["argv"][0] == "zwill"


def test_errors_are_envelopes_with_nonzero_exit(tmp_path: Path) -> None:
    completed = run_zwill("report", "list", "--survey", "nope", cwd=tmp_path)  # no workspace here
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["status"] == "error"
    assert payload["errors"][0]["code"] == "not_initialized"


def test_human_flag_switches_presentation(tmp_path: Path) -> None:
    completed = run_zwill("skills", "path", "digital-twin-study-runner", "--human", cwd=tmp_path)
    assert completed.returncode == 0
    out = completed.stdout.strip()
    assert out.endswith("digital-twin-study-runner")
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)  # human view, not an envelope


def test_every_leaf_command_has_human_flag_and_func() -> None:
    parser = build_parser()
    problems: list[str] = []

    def walk(node: argparse.ArgumentParser, prefix: list[str]) -> None:
        subs = [a for a in node._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            flags = {flag for action in node._actions for flag in action.option_strings}
            if "--human" not in flags:
                problems.append(" ".join(prefix) + " (missing --human)")
            if node.get_default("func") is None:
                problems.append(" ".join(prefix) + " (missing func)")
            return
        for action in subs:
            for name, sub in action.choices.items():
                walk(sub, prefix + [name])

    walk(parser, [])
    assert problems == [], "\n".join(problems)
