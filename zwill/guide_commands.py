"""Self-documenting workflow surface: `zwill guide` and `zwill next`.

These make zwill teach its own end-to-end workflow through the CLI, so any agent
in any harness can go from raw survey data to a validated report with an
`index.html` without an external skill file:

- `zwill guide` prints the agent walkthrough; `zwill guide list` / `guide show`
  reach the other bundled guides (shipped as package data).
- `zwill next` inspects project/survey state and returns the exact command to run
  next, with a one-line reason. Run it after every stage to stay on rails.
"""

from __future__ import annotations

import argparse
import importlib.resources as resources

from .cli import *  # noqa: F403
from .twin_baseline import MODEL_LABEL as BASELINE_MODEL_LABEL

# name -> (title, one-line description). The default guide is listed first.
GUIDES: dict[str, tuple[str, str]] = {
    "agent-workflow": (
        "Agent workflow",
        "End-to-end walkthrough: raw survey data -> validated twin report with index.html.",
    ),
    "interpreting-results": (
        "Interpreting results",
        "How to read a twin-validate bundle and gate a positive claim.",
    ),
    "import-format": (
        "Import file formats",
        "Per-file JSONL schema for question / respondent / answer import, with examples.",
    ),
}
DEFAULT_GUIDE = "agent-workflow"


def guide_path(name: str) -> Path:
    if name not in GUIDES:
        raise ZwillError("not_found", f"Unknown zwill guide: {name}.", context={"known_guides": sorted(GUIDES)})
    return Path(str(resources.files("zwill") / "guides" / f"{name}.md"))


def cmd_guide_show(args: argparse.Namespace) -> dict[str, Any]:
    name = getattr(args, "name", None) or DEFAULT_GUIDE
    path = guide_path(name)
    if not path.exists():  # pragma: no cover - only if package data is missing
        raise ZwillError("not_found", f"Guide file is missing: {path}.")
    print(path.read_text())
    return envelope("zwill guide show", "ok", {"name": name, "path": str(path)})


def cmd_guide_list(args: argparse.Namespace) -> dict[str, Any]:
    rows = [
        {"name": name, "title": title, "description": description, "path": str(guide_path(name))}
        for name, (title, description) in GUIDES.items()
    ]
    if getattr(args, "format", "table") == "json":
        print_json(envelope("zwill guide list", "ok", {"guides": rows}))
    else:
        table = Table(title="zwill bundled guides")
        table.add_column("guide")
        table.add_column("description")
        for row in rows:
            table.add_row(row["name"], row["description"])
        Console().print(table)
        print("\nRead one with: zwill guide show <guide>   (or just `zwill guide` for the walkthrough)")
    return envelope("zwill guide list", "ok", {"guides": rows})


# ---------------------------------------------------------------------------
# `zwill next` — stage detection
# ---------------------------------------------------------------------------
def _has_rows(path: Path) -> bool:
    return path.exists() and any(line.strip() for line in path.read_text().splitlines())


def _report_stage_for_survey(survey: str) -> dict[str, Any] | None:
    manifests = sorted(Path.cwd().glob("*/stage-manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for manifest_path in manifests:
        manifest = read_json(manifest_path, {})
        if manifest.get("survey") != survey:
            continue
        output_dir = manifest.get("output_dir") or str(manifest_path.parent)
        for stage_id in ("final_report", "generated_analysis"):
            stage = (manifest.get("stages") or {}).get(stage_id) or {}
            next_step = str(stage.get("next_step") or "").strip()
            if stage.get("status") != "ready" and next_step:
                return {
                    "stage": stage_id,
                    "why": f"Report bundle '{output_dir}' is blocked: {', '.join(stage.get('missing') or ['required report artifact'])}.",
                    "next_command": next_step,
                    "report_bundle": output_dir,
                    "stage_manifest": str(manifest_path),
                }
        for page in manifest.get("pages") or []:
            next_step = str(page.get("next_step") or "").strip()
            if page.get("primary", True) and page.get("status") != "ready" and next_step:
                return {
                    "stage": str(page.get("page_id") or "report_page"),
                    "why": f"Report bundle '{output_dir}' has an unfinished primary page: {page.get('title') or page.get('page_id')}.",
                    "next_command": next_step,
                    "report_bundle": output_dir,
                    "stage_manifest": str(manifest_path),
                }
        final_stage = ((manifest.get("stages") or {}).get("final_report") or {})
        if final_stage.get("status") == "ready":
            return {
                "stage": "ready",
                "why": f"Report bundle '{output_dir}' has passed the final report gate.",
                "next_command": f"open {output_dir}/index.html",
                "report_bundle": output_dir,
                "stage_manifest": str(manifest_path),
            }
    return None


def _stage_for_survey(survey: str) -> dict[str, Any]:
    sdir = survey_dir(survey)
    if not _has_rows(sdir / "questions.jsonl") or not _has_rows(sdir / "answers.jsonl"):
        return {
            "stage": "import_data",
            "why": f"Survey '{survey}' has no imported questions/answers yet.",
            "next_command": (
                f"zwill question import --survey {survey} --path questions.jsonl  "
                f"# then respondent import + answer import"
            ),
        }
    if not (sdir / "committed" / "truth_marginals.json").exists():
        return {
            "stage": "commit",
            "why": f"Survey '{survey}' has data but is not committed (no truth marginals to score against).",
            "next_command": f"zwill commit --survey {survey}",
        }

    prediction_rows = read_jsonl(digital_twin_predictions_path(sdir))
    twin_jobs = sorted(
        {
            str(row.get("job_id"))
            for row in prediction_rows
            if row.get("job_id") and row.get("model_label") != BASELINE_MODEL_LABEL
        }
    )
    has_baseline = any(row.get("model_label") == BASELINE_MODEL_LABEL for row in prediction_rows)

    if not twin_jobs:
        return {
            "stage": "run_twins",
            "why": f"Survey '{survey}' is committed but has no imported twin jobs.",
            "next_command": (
                f"zwill edsl-export --survey {survey} --target twin-probability-job "
                f"--heldout-questions <q1,q2> --context-question-count 8 --complete-cases "
                f"--model openai:gpt-5.5 --path twin.edsl.json  "
                f"# then edsl-run + twin-results import"
            ),
        }
    if not has_baseline:
        return {
            "stage": "validate",
            "why": f"Survey '{survey}' has twin jobs but has not been validated (no conditional baseline yet).",
            "next_command": (
                f"zwill twin-validate --survey {survey} --jobs {','.join(twin_jobs)} --out validation_bundle"
            ),
        }
    report_stage = _report_stage_for_survey(survey)
    if report_stage:
        return report_stage
    return {
        "stage": "build_report",
        "why": f"Survey '{survey}' has been validated; build the report folder with an index.html.",
        "next_command": f"zwill report build --survey {survey} --path report_out",
    }


def cmd_next(args: argparse.Namespace) -> dict[str, Any]:
    # Stage 0: workspace not initialized.
    if not ROOT.exists() or not head_path().exists():
        return envelope(
            "zwill next",
            "ok",
            {
                "stage": "init",
                "why": "No zwill workspace here yet.",
                "next_command": "zwill init",
                "guide": "zwill guide",
            },
            next_steps=["zwill init", "zwill guide"],
        )

    surveys = read_json(project_surveys_path(), [])
    if not surveys:
        return envelope(
            "zwill next",
            "ok",
            {
                "stage": "create_survey",
                "why": "Workspace initialized but no survey exists in the active project.",
                "next_command": "zwill survey create --name <survey>",
                "guide": "zwill guide",
            },
            next_steps=["zwill survey create --name <survey>", "zwill guide"],
        )

    survey_names = [s["name"] for s in surveys]
    requested = getattr(args, "survey", None)
    if requested is None and len(survey_names) > 1:
        return envelope(
            "zwill next",
            "ok",
            {
                "stage": "choose_survey",
                "why": "Multiple surveys exist; pick one.",
                "surveys": survey_names,
                "next_command": f"zwill next --survey {survey_names[0]}",
            },
            next_steps=[f"zwill next --survey {name}" for name in survey_names],
        )
    survey = requested or survey_names[0]
    if survey not in survey_names:
        raise ZwillError("not_found", f"Unknown survey: {survey}.", context={"surveys": survey_names})

    stage = _stage_for_survey(survey)
    return envelope(
        "zwill next",
        "ok",
        {"survey": survey, **stage, "guide": "zwill guide"},
        next_steps=[stage["next_command"]],
    )
