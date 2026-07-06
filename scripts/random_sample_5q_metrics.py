#!/usr/bin/env python3
"""Score the random-sample 5-question twin prompt variants."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "excel_random_sample_5q_variants"
PREDICTIONS = (
    ROOT
    / ".zwill/projects/excel_survey_analysis/surveys/"
    / "survey_results_held_out_questions/digital_twin_predictions.jsonl"
)
OUT_CSV = RUN_DIR / "random_sample_5q_metrics.csv"
OUT_MD = RUN_DIR / "random_sample_5q_summary.md"

VARIANTS = {
    "raw_ks": "random5q_raw_ks_chunk_",
    "answer_commonness_confidence": "random5q_answer_commonness_confidence_chunk_",
    "full_context_marginal": "random5q_full_context_marginal_chunk_",
}
ONE_SHOT_FILES = {
    "one_shot_unconditioned": ROOT / "excel_all_mc_one_shot_probability_report.json",
    "one_shot_conditioned": ROOT
    / "excel_all_mc_one_shot_with_other_actual_marginals_report.json",
}


def load_manifest() -> dict:
    return json.loads((RUN_DIR / "manifest.json").read_text())


def selected_keys(manifest: dict) -> set[tuple[str, str]]:
    keys = set()
    for question, respondents in manifest["selected_respondents"].items():
        for respondent in respondents:
            keys.add((question, respondent))
    return keys


def load_one_shot() -> dict[str, dict[str, dict]]:
    baselines = {}
    for name, path in ONE_SHOT_FILES.items():
        rows = json.loads(path.read_text())["rows"]
        baselines[name] = {row["question"]: row for row in rows}
    return baselines


def load_predictions(keys: set[tuple[str, str]]) -> tuple[dict, dict, dict]:
    by_variant = {name: {} for name in VARIANTS}
    actuals = {}
    question_meta = {}
    with PREDICTIONS.open() as f:
        for line in f:
            row = json.loads(line)
            key = (row.get("heldout_question"), row.get("respondent_id"))
            if key not in keys:
                continue
            if row.get("actual_answer") is not None:
                actuals.setdefault(key, row["actual_answer"])
            question_meta.setdefault(
                row.get("heldout_question"),
                {
                    "question_text": row.get("heldout_question_text", ""),
                    "options": row.get("option_labels") or list(row["probabilities"]),
                    "actual_full": row.get("empirical_marginal_probabilities") or {},
                },
            )
            job_id = row.get("job_id", "")
            for variant, prefix in VARIANTS.items():
                if job_id.startswith(prefix):
                    by_variant[variant][key] = row
                    break
    return by_variant, actuals, question_meta


def normalize(dist: dict, options: list[str]) -> dict[str, float]:
    values = {option: float(dist.get(option, 0.0)) for option in options}
    total = sum(values.values())
    if total > 0:
        return {option: value / total for option, value in values.items()}
    return {option: 1.0 / len(options) for option in options}


def brier_individual(dist: dict, actual: str, options: list[str]) -> float:
    return sum((dist[option] - (1.0 if option == actual else 0.0)) ** 2 for option in options)


def brier_marginal(predicted: dict, actual: dict, options: list[str]) -> float:
    return sum((predicted[option] - actual.get(option, 0.0)) ** 2 for option in options)


def l1_marginal(predicted: dict, actual: dict, options: list[str]) -> float:
    return sum(abs(predicted[option] - actual.get(option, 0.0)) for option in options)


def empirical_distribution(actuals: list[str], options: list[str]) -> dict[str, float]:
    counts = Counter(actuals)
    total = sum(counts.values())
    return {option: counts[option] / total if total else 0.0 for option in options}


def average_distribution(rows: list[dict], options: list[str]) -> dict[str, float]:
    totals = {option: 0.0 for option in options}
    for row in rows:
        dist = normalize(row["probabilities"], options)
        for option in options:
            totals[option] += dist[option]
    if not rows:
        return totals
    return {option: total / len(rows) for option, total in totals.items()}


def score_distribution_records(
    name: str,
    question: str,
    records: list[tuple[str, dict]],
    actuals: dict,
    meta: dict,
) -> dict:
    options = meta["options"]
    nll = []
    briers = []
    p_actuals = []
    top1 = []
    actual_values = []
    dists = []
    for respondent, dist in records:
        actual = actuals.get((question, respondent))
        if actual is None or actual not in options:
            continue
        norm = normalize(dist, options)
        prob = max(norm.get(actual, 0.0), 1e-12)
        nll.append(-math.log(prob))
        briers.append(brier_individual(norm, actual, options))
        p_actuals.append(prob)
        top = max(options, key=lambda option: norm[option])
        top1.append(1.0 if top == actual else 0.0)
        actual_values.append(actual)
        dists.append({"probabilities": norm})

    pred_marginal = average_distribution(dists, options)
    sample_actual = empirical_distribution(actual_values, options)
    full_actual = normalize(meta["actual_full"], options)
    return {
        "variant": name,
        "question": question,
        "question_text": meta["question_text"],
        "n": len(nll),
        "individual_nll": sum(nll) / len(nll) if nll else None,
        "individual_brier": sum(briers) / len(briers) if briers else None,
        "mean_p_actual": sum(p_actuals) / len(p_actuals) if p_actuals else None,
        "top1": sum(top1) / len(top1) if top1 else None,
        "sample_marginal_brier": brier_marginal(pred_marginal, sample_actual, options),
        "sample_marginal_l1": l1_marginal(pred_marginal, sample_actual, options),
        "full_marginal_brier": brier_marginal(pred_marginal, full_actual, options),
        "full_marginal_l1": l1_marginal(pred_marginal, full_actual, options),
    }


def fmt(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return f"{value:.4f}"


def main() -> None:
    manifest = load_manifest()
    keys = selected_keys(manifest)
    by_variant, actuals, question_meta = load_predictions(keys)
    one_shot = load_one_shot()
    rows = []

    for variant, predictions in by_variant.items():
        for question in manifest["questions"]:
            records = [
                (respondent, row["probabilities"])
                for (q, respondent), row in predictions.items()
                if q == question
            ]
            rows.append(
                score_distribution_records(
                    variant, question, records, actuals, question_meta[question]
                )
            )

    for baseline, baseline_rows in one_shot.items():
        for question in manifest["questions"]:
            row = baseline_rows[question]
            records = [
                (respondent, row["predicted"])
                for respondent in manifest["selected_respondents"][question]
            ]
            rows.append(
                score_distribution_records(
                    baseline, question, records, actuals, question_meta[question]
                )
            )

    fieldnames = [
        "variant",
        "question",
        "n",
        "individual_nll",
        "individual_brier",
        "mean_p_actual",
        "top1",
        "sample_marginal_brier",
        "sample_marginal_l1",
        "full_marginal_brier",
        "full_marginal_l1",
        "question_text",
    ]
    with OUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    aggregates = []
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["variant"]].append(row)
    for variant, variant_rows in grouped.items():
        total_n = sum(row["n"] for row in variant_rows)
        aggregates.append(
            {
                "variant": variant,
                "n": total_n,
                "individual_nll": sum(
                    row["individual_nll"] * row["n"] for row in variant_rows
                )
                / total_n,
                "individual_brier": sum(
                    row["individual_brier"] * row["n"] for row in variant_rows
                )
                / total_n,
                "mean_p_actual": sum(row["mean_p_actual"] * row["n"] for row in variant_rows)
                / total_n,
                "top1": sum(row["top1"] * row["n"] for row in variant_rows) / total_n,
                "sample_marginal_brier": sum(
                    row["sample_marginal_brier"] for row in variant_rows
                )
                / len(variant_rows),
                "full_marginal_brier": sum(
                    row["full_marginal_brier"] for row in variant_rows
                )
                / len(variant_rows),
            }
        )
    aggregates.sort(key=lambda row: row["individual_nll"])

    coverage_lines = []
    for variant in VARIANTS:
        have = len(by_variant[variant])
        missing = len(keys) - have
        coverage_lines.append(f"- `{variant}`: {have}/{len(keys)} imported; missing {missing}")

    md = [
        "# Random-sample 5-question variant comparison",
        "",
        "Same randomly selected respondents are used for every variant: 150 per question.",
        "",
        "## Coverage",
        "",
        *coverage_lines,
        "",
        "## Aggregate scores",
        "",
        "| variant | n | NLL | Brier | p(actual) | top1 | sample marginal Brier | full marginal Brier |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in aggregates:
        md.append(
            "| {variant} | {n} | {nll} | {brier} | {pactual} | {top1} | {sample_brier} | {full_brier} |".format(
                variant=row["variant"],
                n=row["n"],
                nll=fmt(row["individual_nll"]),
                brier=fmt(row["individual_brier"]),
                pactual=fmt(row["mean_p_actual"]),
                top1=fmt(row["top1"]),
                sample_brier=fmt(row["sample_marginal_brier"]),
                full_brier=fmt(row["full_marginal_brier"]),
            )
        )

    md.extend(
        [
            "",
            "## Best individual NLL by question",
            "",
            "| question | best variant | NLL | n |",
            "|---|---|---:|---:|",
        ]
    )
    for question in manifest["questions"]:
        qrows = [row for row in rows if row["question"] == question]
        best = min(qrows, key=lambda row: row["individual_nll"])
        md.append(
            f"| {question} | {best['variant']} | {fmt(best['individual_nll'])} | {best['n']} |"
        )

    md.extend(
        [
            "",
            "## Per-question scores",
            "",
            "| question | variant | n | NLL | Brier | p(actual) | top1 | sample marginal Brier | full marginal Brier |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    order = {variant: i for i, variant in enumerate(list(VARIANTS) + list(ONE_SHOT_FILES))}
    for row in sorted(rows, key=lambda r: (r["question"], order[r["variant"]])):
        md.append(
            "| {question} | {variant} | {n} | {nll} | {brier} | {pactual} | {top1} | {sample_brier} | {full_brier} |".format(
                question=row["question"],
                variant=row["variant"],
                n=row["n"],
                nll=fmt(row["individual_nll"]),
                brier=fmt(row["individual_brier"]),
                pactual=fmt(row["mean_p_actual"]),
                top1=fmt(row["top1"]),
                sample_brier=fmt(row["sample_marginal_brier"]),
                full_brier=fmt(row["full_marginal_brier"]),
            )
        )

    OUT_MD.write_text("\n".join(md) + "\n")
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
