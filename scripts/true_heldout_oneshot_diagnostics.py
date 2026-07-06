#!/usr/bin/env python3
"""Compare true held-out twin aggregate distributions to conditioned one-shot marginals."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from zwill.probability import normalized_probabilities, parse_probability_json


ROOT = Path(__file__).resolve().parents[1]
SURVEY = "survey_results_held_out_questions"
SDIR = ROOT / ".zwill/projects/excel_survey_analysis/surveys" / SURVEY
RUN_DIR = ROOT / "excel_true_heldout_answer_commonness"
DIAG_DIR = RUN_DIR / "diagnostics"
TWIN_LONG = RUN_DIR / "true_heldout_answer_commonness_predictions_long.csv"
MANIFEST = RUN_DIR / "manifest.json"
TEMPLATE_JOB = ROOT / "excel_all_mc_one_shot_with_other_actual_marginals_job.edsl.json"
JOB_PATH = DIAG_DIR / "true_heldout_conditioned_oneshot_job.edsl.json"
RESULTS_PATH = DIAG_DIR / "true_heldout_conditioned_oneshot_results.json.gz"
COMPARISON_CSV = DIAG_DIR / "true_heldout_twin_vs_conditioned_oneshot.csv"
SUMMARY_MD = DIAG_DIR / "true_heldout_twin_vs_conditioned_oneshot.md"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_result(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return json.load(f)
    return json.loads(path.read_text())


def format_options(options: list[str]) -> str:
    return "\n".join(f"{chr(97 + index)}: {option}" for index, option in enumerate(options))


def metric_l1(a: dict[str, float], b: dict[str, float], options: list[str]) -> float:
    return sum(abs(a.get(option, 0.0) - b.get(option, 0.0)) for option in options)


def metric_brier(a: dict[str, float], b: dict[str, float], options: list[str]) -> float:
    return sum((a.get(option, 0.0) - b.get(option, 0.0)) ** 2 for option in options)


def metric_kl(a: dict[str, float], b: dict[str, float], options: list[str]) -> float:
    eps = 1e-12
    return sum(
        a.get(option, 0.0) * math.log(max(a.get(option, 0.0), eps) / max(b.get(option, 0.0), eps))
        for option in options
        if a.get(option, 0.0) > 0
    )


def metric_js(a: dict[str, float], b: dict[str, float], options: list[str]) -> float:
    midpoint = {option: 0.5 * (a.get(option, 0.0) + b.get(option, 0.0)) for option in options}
    return 0.5 * metric_kl(a, midpoint, options) + 0.5 * metric_kl(b, midpoint, options)


def normalize(dist: dict[str, float], options: list[str]) -> dict[str, float]:
    total = sum(float(dist.get(option, 0.0)) for option in options)
    if total <= 0:
        return {option: 1.0 / len(options) for option in options}
    return {option: float(dist.get(option, 0.0)) / total for option in options}


def build_context() -> str:
    base = (SDIR / "context.md").read_text()
    questions = {row["question_name"]: row for row in read_jsonl(SDIR / "questions.jsonl")}
    truth = json.loads((SDIR / "committed/truth_marginals.json").read_text())["marginals"]
    lines = [
        base,
        "",
        "# Empirical Marginals For Calibration",
        "",
        "The following are the actual empirical answer distributions from this same survey for all available categorical questions. Use these distributions to infer likely distributions for the true held-out target questions, but do not assume any target-question answer distribution is directly observed.",
        "",
    ]
    for question_name in sorted(truth, key=lambda value: int(value[1:]) if value[1:].isdigit() else value):
        question = questions.get(question_name)
        if not question or question.get("question_type") != "multiple_choice":
            continue
        options = question.get("question_options", [])
        if not options:
            continue
        weighted = {
            option: float(truth.get(question_name, {}).get(option, {}).get("weighted_count", 0.0))
            for option in options
        }
        total = sum(weighted.values())
        if total <= 0:
            continue
        lines.append(f"### {question_name}: {question['question_text']}")
        lines.append("")
        lines.append("Actual empirical answer distribution:")
        for option in options:
            lines.append(f"- {option}: {weighted[option] / total:.1%}")
        lines.append("")
    return "\n".join(lines)


def build_job() -> None:
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    template = json.loads(TEMPLATE_JOB.read_text())
    manifest = json.loads(MANIFEST.read_text())
    survey_context = build_context()
    scenarios = []
    for question_name in manifest["questions"]:
        question = manifest["heldout_questions"][question_name]
        options = question["question_options"]
        scenarios.append(
            {
                "survey_name": SURVEY,
                "survey_context": survey_context,
                "source_question_name": question_name,
                "source_question_text": question["question_text"],
                "options_text": format_options(options),
                "option_keys": [chr(97 + index) for index in range(len(options))],
                "option_labels": options,
                "edsl_version": "1.0.8.dev1",
                "edsl_class_name": "Scenario",
            }
        )
    job = dict(template)
    job["scenarios"] = scenarios
    job["zwill"] = {
        "survey": SURVEY,
        "probability_job_id": "true_heldout_conditioned_oneshot_v1",
        "diagnostic": "true_heldout_twin_vs_conditioned_oneshot",
    }
    JOB_PATH.write_text(json.dumps(job, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"Wrote {JOB_PATH}")


def parse_oneshot() -> dict[str, dict[str, Any]]:
    results = read_result(RESULTS_PATH)
    parsed_rows = {}
    issues = []
    for index, row in enumerate(results.get("data", [])):
        scenario = row.get("scenario", {})
        question = scenario.get("source_question_name")
        options = [str(option) for option in scenario.get("option_labels", [])]
        answer = row.get("answer", {})
        raw = answer.get("response_probabilities") if isinstance(answer, dict) else None
        if raw is None and isinstance(answer, dict) and answer:
            raw = next(iter(answer.values()))
        payload, parse_error = parse_probability_json(raw)
        probabilities = payload.get("probabilities") if isinstance(payload, dict) else None
        notes = payload.get("notes") if isinstance(payload, dict) else None
        error = parse_error
        normalized = None
        probability_sum = None
        if isinstance(probabilities, list):
            try:
                values = [float(value) for value in probabilities]
                normalized, probability_sum, norm_error = normalized_probabilities(values, len(options))
                error = error or norm_error
            except (TypeError, ValueError):
                error = error or "invalid_probability_value"
        else:
            error = error or "missing_probabilities"
        if error:
            issues.append({"row": index, "question": question, "error": error})
            continue
        parsed_rows[question] = {
            "question": question,
            "question_text": scenario.get("source_question_text"),
            "options": options,
            "probabilities": {option: normalized[position] for position, option in enumerate(options)},
            "raw_probability_sum": probability_sum,
            "notes": notes,
        }
    if issues:
        raise SystemExit(f"One-shot result parse issues: {issues[:5]}")
    return parsed_rows


def aggregate_twins() -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, int] = defaultdict(int)
    question_text: dict[str, str] = {}
    option_order: dict[str, list[str]] = defaultdict(list)
    seen_predictions = set()
    with TWIN_LONG.open(newline="") as f:
        for row in csv.DictReader(f):
            question = row["heldout_question"]
            respondent = row["respondent_id"]
            option = row["option_label"]
            question_text[question] = row["heldout_question_text"]
            if option not in option_order[question]:
                option_order[question].append(option)
            totals[question][option] += float(row["probability"])
            key = (question, respondent)
            if key not in seen_predictions:
                seen_predictions.add(key)
                counts[question] += 1
    aggregates = {}
    for question, dist in totals.items():
        n = counts[question]
        options = option_order[question]
        aggregates[question] = {
            "question": question,
            "question_text": question_text[question],
            "n": n,
            "options": options,
            "probabilities": normalize({option: dist[option] / n for option in options}, options),
        }
    return aggregates


def compare() -> None:
    oneshot = parse_oneshot()
    twins = aggregate_twins()
    rows = []
    option_rows = []
    for question in sorted(twins, key=lambda value: int(value[1:]) if value[1:].isdigit() else value):
        twin = twins[question]
        shot = oneshot[question]
        options = twin["options"]
        twin_dist = normalize(twin["probabilities"], options)
        shot_dist = normalize(shot["probabilities"], options)
        row = {
            "question": question,
            "question_text": twin["question_text"],
            "respondent_n": twin["n"],
            "l1": metric_l1(twin_dist, shot_dist, options),
            "brier": metric_brier(twin_dist, shot_dist, options),
            "kl_twin_to_oneshot": metric_kl(twin_dist, shot_dist, options),
            "kl_oneshot_to_twin": metric_kl(shot_dist, twin_dist, options),
            "js_divergence": metric_js(twin_dist, shot_dist, options),
            "twin_top": max(twin_dist, key=twin_dist.get),
            "twin_top_probability": max(twin_dist.values()),
            "oneshot_top": max(shot_dist, key=shot_dist.get),
            "oneshot_top_probability": max(shot_dist.values()),
            "top_agree": max(twin_dist, key=twin_dist.get) == max(shot_dist, key=shot_dist.get),
            "oneshot_notes": shot.get("notes"),
        }
        rows.append(row)
        for index, option in enumerate(options, start=1):
            option_rows.append(
                {
                    "question": question,
                    "option_index": index,
                    "option_label": option,
                    "twin_probability": twin_dist[option],
                    "conditioned_oneshot_probability": shot_dist[option],
                    "difference_twin_minus_oneshot": twin_dist[option] - shot_dist[option],
                }
            )

    with COMPARISON_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    option_csv = DIAG_DIR / "true_heldout_twin_vs_conditioned_oneshot_options.csv"
    with option_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(option_rows[0]))
        writer.writeheader()
        writer.writerows(option_rows)

    sorted_rows = sorted(rows, key=lambda row: row["js_divergence"], reverse=True)
    md = [
        "# True held-out twin aggregate vs conditioned one-shot",
        "",
        "Diagnostic only. The individual twin prediction CSVs were not modified.",
        "",
        f"- Questions compared: {len(rows)}",
        f"- Mean L1 distance: {sum(row['l1'] for row in rows) / len(rows):.4f}",
        f"- Mean JS divergence: {sum(row['js_divergence'] for row in rows) / len(rows):.4f}",
        f"- Top option agreement: {sum(1 for row in rows if row['top_agree'])}/{len(rows)}",
        "",
        "## By Question",
        "",
        "| question | n | L1 | JS | twin top | one-shot top | top agree |",
        "|---|---:|---:|---:|---|---|---:|",
    ]
    for row in sorted_rows:
        md.append(
            f"| {row['question']} | {row['respondent_n']} | {row['l1']:.4f} | {row['js_divergence']:.4f} | "
            f"{row['twin_top']} ({row['twin_top_probability']:.1%}) | "
            f"{row['oneshot_top']} ({row['oneshot_top_probability']:.1%}) | "
            f"{'yes' if row['top_agree'] else 'no'} |"
        )
    SUMMARY_MD.write_text("\n".join(md) + "\n")
    print(json.dumps({
        "comparison_csv": str(COMPARISON_CSV.relative_to(ROOT)),
        "option_csv": str(option_csv.relative_to(ROOT)),
        "summary_md": str(SUMMARY_MD.relative_to(ROOT)),
        "questions": len(rows),
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build-job")
    sub.add_parser("compare")
    args = parser.parse_args()
    if args.command == "build-job":
        build_job()
    elif args.command == "compare":
        compare()


if __name__ == "__main__":
    main()
