"""Docs and CLI must not drift: documented commands exist, and commands are documented.

This is the test that would have caught `twin-experiment report-export` being
documented in the README while the parser never registered it.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from zwill.cli import build_parser

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
DOCS_INDEX = REPO / "docs" / "index.html"
TUTORIAL_SOURCE = REPO / "docs" / "tutorial-source.html"
TUTORIAL_EVIDENCE = REPO / "docs" / "assets" / "pew-w158-real-run"


def registered_command_paths() -> set[str]:
    """Every invocable command path, e.g. {'init', 'report list', ...}."""
    parser = build_parser()
    paths: set[str] = set()

    def walk(node: argparse.ArgumentParser, prefix: list[str]) -> None:
        sub_actions = [
            action for action in node._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        if prefix and (not sub_actions or node.get_default("func")):
            paths.add(" ".join(prefix))
        for action in sub_actions:
            for name, sub in action.choices.items():
                walk(sub, prefix + [name])

    walk(parser, [])
    return paths


def documented_invocations(text: str) -> set[str]:
    """`zwill a [b]` command paths referenced in code fences and inline code.

    Only actual invocations count: a line in a fenced block starting with
    ``zwill`` (optionally after ``$``), or an inline code span that begins
    with ``zwill``. Prose mentions of the word never match.
    """
    invocations: list[str] = []
    for block in re.findall(r"```(?:bash|sh|console|text)?\n(.*?)```", text, re.DOTALL):
        for line in block.splitlines():
            stripped = line.strip().lstrip("$ ").strip()
            if stripped.startswith("zwill "):
                invocations.append(stripped)
    for span in re.findall(r"`([^`\n]+)`", text):
        if span.strip().startswith("zwill "):
            invocations.append(span.strip())
    found: set[str] = set()
    for invocation in invocations:
        match = re.match(r"zwill\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?", invocation)
        if match:
            first, second = match.group(1), match.group(2)
            found.add(first if second is None else f"{first} {second}")
    return found


def _html_invocations(text: str) -> set[str]:
    """Command paths inside the tutorial's <pre><code> blocks."""
    import re as _re

    blocks = _re.findall(r"<pre[^>]*><code[^>]*>(.*?)</code></pre>", text, _re.DOTALL)
    found: set[str] = set()
    for block in blocks:
        for line in block.splitlines():
            stripped = line.strip().lstrip("$ ").strip()
            match = _re.match(r"zwill\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?", stripped)
            if match:
                first, second = match.group(1), match.group(2)
                found.add(first if second is None else f"{first} {second}")
    return found


def test_every_documented_command_exists() -> None:
    registered = registered_command_paths()
    one_level = {path.split(" ")[0] for path in registered} | registered
    documented = documented_invocations(README.read_text())
    if DOCS_INDEX.exists():
        documented |= _html_invocations(DOCS_INDEX.read_text())
    problems = []
    for doc_path in sorted(documented):
        first = doc_path.split(" ")[0]
        if doc_path in one_level or first in one_level:
            # Two-word references where the second word is an argument (not a
            # subcommand) are fine as long as the group/command itself exists.
            if " " in doc_path and first in {p.split(" ")[0] for p in registered}:
                group_subs = {p.split(" ")[1] for p in registered if p.startswith(first + " ") and " " in p}
                if group_subs and doc_path.split(" ")[1] not in group_subs:
                    # group exists and takes subcommands, but this one is unknown
                    if doc_path.split(" ")[1] not in {"and", "or", "the"}:  # prose guards
                        problems.append(doc_path)
            continue
        problems.append(doc_path)
    assert problems == [], "README documents commands the CLI does not register:\n" + "\n".join(problems)


def test_every_registered_command_is_documented() -> None:
    documented = documented_invocations(README.read_text())
    documented_groups = {path.split(" ")[0] for path in documented}
    missing = []
    for path in sorted(registered_command_paths()):
        if path in documented:
            continue
        # A group documented via several subcommands counts for its siblings
        # only if this exact subcommand appears somewhere; otherwise flag it.
        if path.split(" ")[0] in documented_groups and " " not in path:
            continue
        missing.append(path)
    assert missing == [], (
        "Registered commands missing from the README:\n" + "\n".join(missing)
    )


def test_published_tutorial_uses_real_inference_artifacts() -> None:
    forbidden = (
        "fixture substitution",
        "deterministic stand-in",
        "--skip-baseline",
        "replaced here by labeled deterministic fixtures",
    )
    for path in (DOCS_INDEX, TUTORIAL_SOURCE):
        text = path.read_text().lower()
        assert not any(phrase in text for phrase in forbidden), path

    required = (
        "one_shot_jobs.ep",
        "one_shot_results.ep",
        "carbon_tax_twin_results.ep",
        "validation/report_data.json",
        "validation/bootstrap.json",
        "validation/leakage_audit.json",
        "validation/manifest.json",
    )
    assert all((TUTORIAL_EVIDENCE / name).is_file() for name in required)

    summary = json.loads(
        (REPO / "zwill" / "examples" / "pew-w158-tutorial" / "run_summary.json").read_text()
    )
    report = json.loads((TUTORIAL_EVIDENCE / "validation" / "report_data.json").read_text())
    twin = report["skill_scores"]["openai:gpt-5.5"]["macro"]
    baseline = report["skill_scores"]["baseline:conditional-embedding"]["macro"]
    assert summary["twin_run"]["metrics"]["negative_log_likelihood"] == twin["mean_nll"]
    assert summary["conditional_baseline"]["metrics"]["negative_log_likelihood"] == baseline["mean_nll"]
