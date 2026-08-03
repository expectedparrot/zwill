#!/usr/bin/env python3
"""Build the offline, pre-inference state for the cross-survey twin paper."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONFIG = json.loads((HERE / "config" / "surveys.json").read_text())
SOURCE = (HERE / CONFIG["source_root"]).resolve()
WORK = HERE / "workdir"

def run(*args: str, cwd: Path = WORK) -> dict[str, Any]:
    proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    text = proc.stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return {}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def ingest(source_id: str) -> dict[str, Any]:
    """Read the canonical artifacts produced by `zwill study ingest`."""
    meta_path = SOURCE / f"{source_id}_metadata.json"
    respondents_path = SOURCE / f"{source_id}_respondents.csv"
    survey = slug(source_id)
    imports = WORK / "imports" / survey
    result = json.loads((imports / "manifest.json").read_text())
    questions = [json.loads(line) for line in (imports / "questions.jsonl").read_text().splitlines()]
    return {
        "source_id": source_id, "survey": survey, "metadata_path": meta_path,
        "respondents_path": respondents_path, "questions": questions,
        "n_respondents": result["respondent_count"], "n_answers": result["answer_count"], "imports": imports,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Replace existing generated state, including imported results and reports.",
    )
    args = parser.parse_args()
    if WORK.exists():
        if not args.fresh:
            raise SystemExit(
                f"{WORK} already exists. Refusing to replace completed state; "
                "pass --fresh only for an intentional clean rebuild."
            )
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    run("zwill", "init")
    run("zwill", "project", "create", "microdata_twins_v2", "--use")

    ingestion_path = WORK / "ingestion_manifest.json"
    run("zwill", "study", "init", "--path", str(ingestion_path))
    run(
        "zwill", "study", "set-matrix-defaults", "--manifest", str(ingestion_path),
        "--covariate-codebook", str(HERE / "config" / "covariate_codebook.json"),
        "--data-variable-template", "item_{item}",
        "--question-name-template", "{battery}_{item}",
        "--missing-code", "", "--missing-code", "NA", "--missing-code", "NaN",
        "--missing-code", "nan", "--missing-code", "Refused", "--missing-code", "Don't know",
    )
    for source_id in CONFIG["surveys"]:
        run(
            "zwill", "study", "add-matrix", "--manifest", str(ingestion_path),
            "--survey", slug(source_id), "--source-id", source_id,
            "--metadata", str(SOURCE / f"{source_id}_metadata.json"),
            "--respondents", str(SOURCE / f"{source_id}_respondents.csv"),
        )
    run("zwill", "study", "check", "--manifest", str(ingestion_path))
    run(
        "zwill", "study", "ingest", "--manifest", str(ingestion_path),
        "--out", str(WORK / "imports"), "--apply",
    )
    cohort: list[dict[str, Any]] = []
    run_lines = ["# Approved live-run commands", "", "Run each block from this directory after reviewing and approving its plan.", ""]

    for source_id in CONFIG["surveys"]:
        record = ingest(source_id)
        survey = record["survey"]
        run("zwill", "commit", "--survey", survey)
        run("zwill", "next", "--survey", survey)
        report = WORK / "reports" / survey / "survey-profile.html"
        report.parent.mkdir(parents=True, exist_ok=True)
        run("zwill", "survey", "report", "--survey", survey, "--format", "html", "--path", str(report))
        run("zwill", "next", "--survey", survey)

        run("zwill", "twin-approach", "add", "--survey", survey,
            "--approach-id", "battery_context", "--name", "Held-out battery context",
            "--description", "Predict held-out answers from other non-held-out battery answers and readable profile metadata.",
            "--complete-cases", "--context-question-count", str(CONFIG["context_question_count"]),
            "--model", CONFIG["models"][0])
        # A multi-target plan excludes every held-out item from every arm's context.
        # Always retain at least two battery items as genuine observed context.
        heldout_n = min(CONFIG["heldout_count"], max(1, len(record["questions"]) - 2))
        heldout = [q["question_name"] for q in record["questions"][:heldout_n]]
        plan_id = f"{survey}_validation_v1"
        plan_path = WORK / "plans" / f"{plan_id}.json"
        predictions = min(CONFIG["sample_respondents"], record["n_respondents"]) * len(heldout) * len(CONFIG["models"])
        cohort.append({
            "source_id": source_id, "survey": survey, "questions": len(record["questions"]),
            "respondents": record["n_respondents"], "heldout_questions": heldout,
            "plan_id": plan_id,
            "by_question_plan_ids": {question: f"{survey}_byq_{question}_v1" for question in heldout},
            "planned_predictions": predictions, "plan": str(plan_path.relative_to(HERE)),
            "leakage_exclusions": (
                {"gallup_remote_covid_q6rec": ["gallup_remote_covid_q18rec"]}
                if survey == "gallup_remote_covid_gallup_remote_covid" else {}
            ),
            "exclude_from_claims": survey == "gallup_remote_covid_gallup_remote_covid",
            "exclusion_reason": (
                "Potential q6rec/q18rec leakage flagged by the association audit."
                if survey == "gallup_remote_covid_gallup_remote_covid" else ""
            ),
        })
        run_lines += [
            f"## {survey}", "",
            f"zwill twin-experiment approve --input-path plans/{plan_id}.json --approved-by <reviewer>",
            f"zwill twin-experiment export-plan --input-path plans/{plan_id}.json --output-dir jobs/{plan_id}",
            f"# Run every jobs/*.ep package named in jobs/{plan_id}/manifest.json with ep run.",
            f"zwill twin-experiment import-plan-results --manifest jobs/{plan_id}/manifest.json --results-dir results/{plan_id}",
            f"zwill next --survey {survey}", "",
        ]

    manifest = {
        "schema_version": 1,
        "design": {
            **{key: CONFIG[key] for key in ("sample_respondents", "seed", "heldout_count", "context_question_count", "models")},
            "approach_id": "battery_context",
            "primary_metric": "nll_vs_empirical",
        },
        "prediction_count_formula": "respondents x heldout_questions x approaches x models",
        "total_planned_predictions": sum(row["planned_predictions"] for row in cohort),
        "surveys": cohort,
        "result_paths": ["results/**/*_results.ep"],
        "comparisons": {
            "frontier_models": {
                "mode": "by-question",
                "surveys": [
                    "w154_diff1", "w158_ccpolicy", "asian2022_23_originbattery",
                    "dataverse_6rk7rd_brazil_digital_credit",
                    "dataverse_iq6ebz_iq6ebz_2024_democracy_norms",
                ],
                "plan_id_template": "{survey}_model_{model_slug}_{question}_v2",
                "models": [
                    {"model": "anthropic:claude-sonnet-4-6", "approach_id": "battery_context_claude"},
                    {"model": "google:gemini-3.5-flash", "approach_id": "battery_context_gemini"},
                ],
            }
        },
        "excluded_surveys": CONFIG["excluded_surveys"],
        "remote_inference_run": False,
    }
    (WORK / "study_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    expanded = run(
        "zwill", "study", "expand", "--manifest", "study_manifest.json",
        "--mode", "simultaneous", "--out", str(WORK / "plans"),
    )["data"]
    by_plan = {row["plan_id"]: row for row in expanded["plans"]}
    for row in cohort:
        plan = by_plan[row["plan_id"]]
        validation = run("zwill", "twin-experiment", "validate", "--input-path", plan["path"])
        row["plan_validation_status"] = validation.get("status", "unknown")
        run("zwill", "next", "--survey", row["survey"])
    # Persist validation status without changing the design fields consumed by
    # study expand and downstream summarization.
    (WORK / "study_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (WORK / "RUN.md").write_text("\n".join(run_lines) + "\n")
    table_dir = HERE / "writeup" / "generated"
    table_dir.mkdir(parents=True, exist_ok=True)
    table_rows = []
    for row in cohort:
        source_label = row["source_id"].replace("_", "\\_")
        table_rows.append(
            f"{source_label} & {row['questions']} & {row['respondents']:,} & "
            f"{len(row['heldout_questions'])} & {row['planned_predictions']:,} \\\\"
        )
    rows = "\n".join(table_rows)
    (table_dir / "cohort.tex").write_text(
        "\\begin{tabular}{lrrrr}\\toprule\nBattery & Items & Respondents & Held out & Predictions \\\\\n"
        "\\midrule\n" + rows + "\n\\bottomrule\\end{tabular}\n"
    )
    print(json.dumps({"surveys": len(cohort), "planned_predictions": manifest["total_planned_predictions"],
                      "manifest": str(WORK / "study_manifest.json")}, indent=2))


if __name__ == "__main__":
    main()
