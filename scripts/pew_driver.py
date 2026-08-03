#!/usr/bin/env python3
"""Run the tutorial's Pew W158 worked example end to end and capture evidence.

Every command, model response, baseline prediction, and reported metric comes
from this run. Publishing the tutorial therefore requires authenticated remote
inference; this driver deliberately has no synthetic-results fallback.

Usage (from the repo root, with dev dependencies + edsl installed):

    python scripts/pew_driver.py       # rerun the worked run, refresh captures
    python scripts/inject_captures.py  # rebuild docs/index.html from captures
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TUTORIAL = REPO / "build" / "tutorial"
WORK = TUTORIAL / "pew"
CAPTURES = TUTORIAL / "captures"


def zwill(*args: str, capture: str | None = None, cwd: Path | None = None,
          expect_fail: bool = False, human: bool = False) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    argv = [sys.executable, "-m", "zwill.cli", *args] + (["--human"] if human else [])
    completed = subprocess.run(argv, cwd=cwd or WORK, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if expect_fail:
        assert completed.returncode != 0, f"expected failure: {args}\n{completed.stdout}"
    else:
        assert completed.returncode == 0, f"failed: {args}\n{completed.stdout}\n{completed.stderr}"
    if human:
        if capture:
            (CAPTURES / f"{capture}.txt").write_text(
                "$ zwill " + shlex.join(args) + " --human\n" + completed.stdout
            )
        return {}
    payload = json.loads(completed.stdout)
    if capture:
        (CAPTURES / f"{capture}.json").write_text(json.dumps({
            "argv_display": "zwill " + shlex.join(args),
            "payload": payload,
        }, indent=2, ensure_ascii=False))
    return payload


def ep_run(jobs_path: Path, out_path: Path, capture: str) -> None:
    """Run a fresh remote job and retain its CLI output as run evidence."""
    jobs_arg = str(jobs_path.relative_to(WORK))
    output_arg = str(out_path.relative_to(WORK))
    argv = [
        "ep", "run", jobs_arg, "--fresh", "--output", output_arg,
        "--remote_inference_description", "Zwill Pew W158 published tutorial run",
        "--results_description", "Raw results for the published Zwill Pew W158 tutorial",
    ]
    completed = subprocess.run(
        argv,
        cwd=WORK,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.returncode == 0, (
        f"remote inference failed: {shlex.join(argv)}\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    assert out_path.exists(), f"ep run did not write {out_path}"
    (CAPTURES / f"{capture}.txt").write_text(
        "$ " + shlex.join(argv) + "\n" + completed.stdout + completed.stderr
    )


def main() -> None:
    if TUTORIAL.exists():
        shutil.rmtree(TUTORIAL)
    CAPTURES.mkdir(parents=True)
    WORK.mkdir(parents=True)

    # ── Fetch the bundled excerpt and set up the workspace ────────────────
    zwill("example", "fetch", "pew-w158-tutorial", capture="01-fetch")
    zwill("init", capture="02-init")
    zwill("survey", "create", "--name", "pew_w158", capture="03-survey-create")
    zwill("raw", "add", "--survey", "pew_w158", "--id", "pew_w158_excerpt",
          "--input-path", "pew-w158-tutorial/source.md",
          "--kind", "documentation",
          "--title", "Pew American Trends Panel Wave 158 excerpt",
          capture="04-raw-add")

    # ── The manual single-record demo (survey pew_w158_manual) ────────────
    zwill("survey", "create", "--name", "pew_w158_manual")
    zwill("question", "add", "--survey", "pew_w158_manual",
          "--question-name", "carbon_tax",
          "--question-type", "multiple_choice",
          "--question-text",
          "Do you favor or oppose taxing corporations based on the amount of carbon emissions they produce?",
          "--question-option", "Favor", "--question-option", "Oppose",
          "--role", "survey_item", "--source-raw", "pew_w158_excerpt",
          "--source-note", "Wave 158 CCPOLICY battery item b",
          capture="05-question-add")

    # ── Bulk import the excerpt ───────────────────────────────────────────
    zwill("question", "import", "--survey", "pew_w158",
          "--input-path", "pew-w158-tutorial/questions.jsonl", capture="06-question-import")
    zwill("respondent", "import", "--survey", "pew_w158",
          "--input-path", "pew-w158-tutorial/respondents.jsonl", capture="07-respondent-import")
    zwill("answer", "import", "--survey", "pew_w158",
          "--input-path", "pew-w158-tutorial/answers.jsonl", capture="08-answer-import")
    zwill("next", "--survey", "pew_w158", capture="09-next")
    zwill("status", capture="10-status")
    zwill("table", "--survey", "pew_w158", "--limit", "5",
          capture="11h-table", human=True)
    zwill("survey", "report", "--survey", "pew_w158", "--format", "html",
          "--path", "survey-profile.html", capture="12-survey-report")
    zwill("commit", "--survey", "pew_w158", capture="13-commit")
    zwill("next", "--survey", "pew_w158", capture="14-next-committed")

    # ── One-shot marginal: probability job, real run, import, report ──────
    zwill("edsl", "build", "--survey", "pew_w158", "--target", "probability-job",
          "--questions", "carbon_tax", "--model", "openai:gpt-5.5",
          "--path", "one_shot_jobs.ep", capture="15-prob-build")

    ep_run(
        WORK / "one_shot_jobs.ep",
        WORK / "one_shot_results.ep",
        "15a-prob-ep-run",
    )
    prob_import = zwill("prob-results", "import", "--survey", "pew_w158",
                        "--input-path", "one_shot_results.ep", capture="16-prob-import")
    prob_job_id = prob_import["data"]["job_id"]
    zwill("prob-results", "report", "--survey", "pew_w158",
          "--job-id", prob_job_id, "--format", "svg",
          "--path", "one-shot-marginals.svg", capture="17-prob-report")

    # ── Twin experiment plan: author, validate, approve, export ───────────
    plan = zwill("twin-experiment", "init-plan", "--survey", "pew_w158",
          "--plan-id", "carbon_tax_v1",
          "--heldout-questions", "carbon_tax",
          "--context-questions",
          "tree_planting,carbon_capture_credit,zero_carbon_power,methane_leaks,home_efficiency_credit",
          "--model", "openai:gpt-5.5", "--primary-metric", "nll",
          "--path", "carbon_tax_v1.json", capture="18-init-plan")
    plan_path = plan["data"]["path"]
    zwill("twin-experiment", "validate", "--input-path", plan_path,
          capture="19-plan-validate")
    zwill("twin-experiment", "approve", "--input-path", plan_path,
          "--approved-by", "tutorial_reader",
          "--note", "Approved one target, five context questions, all 100 eligible respondents, one model, and NLL as the primary metric.",
          capture="20-plan-approve")
    export = zwill("twin-experiment", "export-plan", "--input-path", plan_path,
                   "--output-dir", "carbon_tax_v1_jobs", capture="21-export-plan")
    twin_export = export["data"]["exports"][0]
    twin_job_id = twin_export["job_id"]
    twin_jobs_path = WORK / twin_export["job_path"]

    # ── Real respondent-level twin predictions ─────────────────────────────
    ep_run(
        twin_jobs_path,
        WORK / "carbon_tax_twin_results.ep",
        "21a-twin-ep-run",
    )
    zwill("twin-results", "import", "--survey", "pew_w158",
          "--input-path", "carbon_tax_twin_results.ep",
          "--jobs", twin_export["job_path"],
          "--job-id", twin_job_id, capture="22-twin-import")

    # ── Audit, score, and read the evidence ───────────────────────────────
    zwill("twin-results", "run-report", "--survey", "pew_w158",
          "--job-id", twin_job_id, "--format", "html", "--path", "twin-audit.html",
          capture="23-run-report")
    zwill("twin-study", "list", "--survey", "pew_w158", capture="24-study-list")
    zwill("twin-study", "list", "--survey", "pew_w158", capture="24h-study-list", human=True)
    zwill("twin-results", "report", "--survey", "pew_w158",
          "--job-id", twin_job_id, capture="25-twin-report")
    zwill("twin-results", "report", "--survey", "pew_w158",
          "--job-id", twin_job_id, "--format", "html", "--view", "full",
          "--path", "twin-results.html",
          "--calibration-svg-path", "twin-calibration.svg", capture="26-twin-report-html")
    zwill("twin-results", "report", "--survey", "pew_w158",
          "--job-id", twin_job_id, "--format", "csv",
          "--path", "twin-results.csv", capture="27-twin-report-csv")
    zwill("twin-results", "marginal-diagnostics", "--survey", "pew_w158",
          "--job-id", twin_job_id,
          "--target", "probability-job", "--target-job-id", prob_job_id,
          "--format", "svg", "--path", "twin-vs-one-shot-marginals.svg",
          capture="28-marginal-diagnostics")
    zwill("twin-validate", "--survey", "pew_w158",
          "--jobs", twin_job_id, "--out", "report_out/validation",
          "--embedder", "edsl", "--require-baseline",
          "--n-boot", "2000", "--seed", "42",
          capture="29-twin-validate")
    zwill("next", "--survey", "pew_w158", capture="30-next-final")
    zwill("report", "build", "--survey", "pew_w158", "--path", "report_out",
          "--job-id", twin_job_id,
          "--probability-job-id", prob_job_id, capture="31-report-build")
    zwill("report", "list", "--survey", "pew_w158", capture="32-report-list")

    # Retain the exact job packages, raw model results, and validation bundle
    # used by the published tutorial. These are the audit trail behind every
    # displayed result, not disposable build intermediates.
    evidence_dir = REPO / "docs" / "assets" / "pew-w158-real-run"
    if evidence_dir.exists():
        shutil.rmtree(evidence_dir)
    evidence_dir.mkdir(parents=True)
    shutil.copy2(WORK / "one_shot_jobs.ep", evidence_dir / "one_shot_jobs.ep")
    shutil.copy2(WORK / "one_shot_results.ep", evidence_dir / "one_shot_results.ep")
    shutil.copy2(twin_jobs_path, evidence_dir / twin_jobs_path.name)
    shutil.copy2(
        WORK / "carbon_tax_twin_results.ep",
        evidence_dir / "carbon_tax_twin_results.ep",
    )
    shutil.copytree(
        WORK / "zwill_work" / "report_out" / "validation",
        evidence_dir / "validation",
    )
    shutil.copy2(
        WORK / "zwill_work" / "one-shot-marginals.svg",
        REPO / "docs" / "assets" / "pew-w158-one-shot-marginal.svg",
    )
    shutil.copy2(
        evidence_dir / "validation" / "bootstrap-intervals.svg",
        REPO / "docs" / "assets" / "pew-w158-twin-vs-xgboost.svg",
    )

    prob_report = json.loads((CAPTURES / "17-prob-report.json").read_text())["payload"]["data"]
    twin_report = json.loads((CAPTURES / "25-twin-report.json").read_text())["payload"]["data"]
    validation = json.loads((evidence_dir / "validation" / "report_data.json").read_text())
    twin_metrics = twin_report["summary"]["openai:gpt-5.5"]
    twin_skill = validation["skill_scores"]["openai:gpt-5.5"]["macro"]
    baseline_skill = validation["skill_scores"]["baseline:conditional-embedding"]["macro"]
    deltas = validation["bootstrap"]["deltas_vs_baseline"]["models"]["openai:gpt-5.5"]["macro"]
    one_shot_cost = prob_import["data"]["cost"]["total_usd"]
    twin_import = json.loads((CAPTURES / "22-twin-import.json").read_text())["payload"]
    summary = {
        "run_date": date.today().isoformat(),
        "survey": "pew_w158",
        "heldout_question": "carbon_tax",
        "respondents": 100,
        "model": "openai:gpt-5.5",
        "artifact_root": "docs/assets/pew-w158-real-run",
        "one_shot": {
            "job_id": prob_job_id,
            "observed_weighted": prob_report["rows"][0]["actual"],
            "predicted": prob_report["rows"][0]["predicted"],
            "mae": prob_report["rows"][0]["mae"],
            "cost_usd": one_shot_cost,
        },
        "twin_run": {
            "job_id": twin_job_id,
            "completed_interviews": twin_import["data"]["row_count"],
            "extracted_predictions": twin_import["data"]["extracted_count"],
            "import_issues": twin_import["data"]["issue_count"],
            "cost_usd": twin_import["data"]["cost"]["total_usd"],
            "metrics": {
                "probability_actual": twin_metrics["mean_probability_actual"],
                "negative_log_likelihood": twin_skill["mean_nll"],
                "brier": twin_skill["mean_brier"],
                "top1_accuracy": twin_skill["accuracy"],
                "expected_calibration_error": twin_metrics["expected_calibration_error"],
            },
        },
        "conditional_baseline": {
            "job_id": validation["design"]["baseline_job_id"],
            "model_label": "baseline:conditional-embedding",
            "model_type": "xgboost",
            "embedding_model": "text-embedding-3-small",
            "prediction_rows": 100,
            "metrics": {
                "probability_actual": validation["bootstrap"]["models"]["baseline:conditional-embedding"]["macro"]["probability_actual"]["mean"],
                "negative_log_likelihood": baseline_skill["mean_nll"],
                "brier": baseline_skill["mean_brier"],
                "top1_accuracy": baseline_skill["accuracy"],
            },
        },
        "paired_twin_minus_xgboost": {
            "bootstrap_resamples": validation["bootstrap"]["n_boot"],
            "confidence_level": validation["bootstrap"]["ci"],
            **{
                metric: {
                    key: values[key]
                    for key in ("delta", "lo", "hi")
                }
                for metric, values in deltas.items()
            },
        },
        "leakage_audit": {
            "pair_count": validation["leakage"]["pair_count"],
            "flagged_count": validation["leakage"]["flagged_count"],
        },
    }
    summary_text = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    (REPO / "zwill" / "examples" / "pew-w158-tutorial" / "run_summary.json").write_text(summary_text)
    (REPO / "docs" / "assets" / "pew-w158-evidence-summary.json").write_text(summary_text)

    (TUTORIAL / "ids.json").write_text(json.dumps({
        "probability_job_id": prob_job_id,
        "twin_job_id": twin_job_id,
        "twin_job_path": twin_export["job_path"],
    }, indent=2))
    print("captures:", len(list(CAPTURES.iterdir())))


if __name__ == "__main__":
    main()
