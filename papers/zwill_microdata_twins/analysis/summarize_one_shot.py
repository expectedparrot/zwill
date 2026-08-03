#!/usr/bin/env python3
"""Summarize battery-specific Zwill one-shot marginal reports."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parents[1]
WORK = HERE / "workdir"
MODEL = "openai:gpt-5.5"

DISPLAY_NAMES = {
    "W132_SPIRESSENT1": "Spirituality (W132)",
    "W141_PROVIDER": "Local-news providers (W141)",
    "W150_SMUSE": "Social-media use (W150)",
    "W152_HUMANVAI": "Human vs. AI (W152)",
    "W154_DIFF1": "Gender comparisons (W154)",
    "W157_SKILLIMP": "Worker skills (W157)",
    "W158_CCPOLICY": "Climate policy (W158)",
    "W163_SM9": "Views of social media (W163)",
    "ASIAN2022_23_ORIGINBATTERY": "Origin-group attitudes",
    "GALLUP_REMOTE_COVID_gallup_remote_covid": "Remote work (flagged)",
    "GALLUP_WELLBEING_gallup_wellbeing": "Wellbeing",
    "DATAVERSE_6RK7RD_BRAZIL_DIGITAL_CREDIT": "Brazil digital credit",
    "DATAVERSE_478TLG_KOREA_MOBILITY_FAIRNESS": "Korea mobility/fairness",
    "DATAVERSE_DNZT11_DNZT11_VIGNETTE_OUTCOMES": "Municipal vignettes",
    "DATAVERSE_IQ6EBZ_IQ6EBZ_2024_DEMOCRACY_NORMS": "Democratic norms",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def latex(text: str) -> str:
    return text.replace("&", "\\&").replace("%", "\\%").replace("_", "\\_")


def main() -> None:
    manifest = load(WORK / "study_manifest.json")
    question_rows: list[dict] = []
    option_rows: list[dict] = []
    battery_rows: list[dict] = []
    study = WORK / "study" / "by-question"
    study_questions = read_csv(study / "questions.csv")
    study_options = read_csv(study / "options.csv")

    for record in manifest["surveys"]:
        survey = record["survey"]
        label = DISPLAY_NAMES.get(record["source_id"], survey)
        local_questions = [row for row in study_questions if row["survey"] == survey]
        twin_question = {row["question"]: row for row in local_questions if row["model"] == MODEL}
        one_question = {row["question"]: row for row in local_questions if row["model"] == "baseline:one-shot"}
        local_options = [row for row in study_options if row["survey"] == survey]

        twin_options: dict[str, dict[str, dict]] = {}
        one_options: dict[str, dict[str, dict]] = {}
        for values in local_options:
            destination = twin_options if values["model_label"].endswith(f" / {MODEL}") else (
                one_options if values["model_label"].endswith(" / baseline:one-shot") else None
            )
            if destination is not None:
                destination.setdefault(values["heldout_question"], {})[values["option_label"]] = values

        local_rows = []
        for question in record["heldout_questions"]:
            aligned = twin_options[question]
            one_aligned = one_options[question]
            sample_actual = {option: float(values["target_probability"])
                             for option, values in aligned.items()}
            twin_predicted = {option: float(values["predicted_probability"])
                              for option, values in aligned.items()}
            one_predicted = {option: float(one_aligned[option]["predicted_probability"]) for option in aligned}
            one_nll = float(one_question[question]["mean_nll"])
            twin_nll = float(twin_question[question]["mean_nll"])
            row = {
                "survey": survey,
                "battery": label,
                "question": question,
                "one_shot_mae": float(np.mean([
                    abs(one_predicted[option] - sample_actual[option]) for option in aligned
                ])),
                "twin_marginal_mae": float(np.mean([
                    abs(twin_predicted[option] - sample_actual[option]) for option in aligned
                ])),
                "one_shot_nll": one_nll,
                "twin_nll": twin_nll,
                "twin_minus_one_shot_nll": float(twin_question[question]["nll_delta_vs_one_shot"]),
                "one_shot_kl": sum(actual * math.log(max(actual, 1e-12) / max(one_predicted[option], 1e-12))
                                   for option, actual in sample_actual.items()),
                "nll_ci_lo": float(twin_question[question]["nll_delta_vs_one_shot_lo"]),
                "nll_ci_hi": float(twin_question[question]["nll_delta_vs_one_shot_hi"]),
            }
            question_rows.append(row)
            local_rows.append(row)
            for option in aligned:
                option_rows.append({
                    "survey": survey,
                    "question": question,
                    "option": option,
                    "actual": sample_actual[option],
                    "one_shot": one_predicted[option],
                    "twin": twin_predicted[option],
                })

        battery_rows.append({
            "survey": survey,
            "battery": label,
            "questions": len(local_rows),
            "one_shot_mae": float(np.mean([row["one_shot_mae"] for row in local_rows])),
            "twin_marginal_mae": float(np.mean([row["twin_marginal_mae"] for row in local_rows])),
            "one_shot_nll": float(np.mean([row["one_shot_nll"] for row in local_rows])),
            "twin_nll": float(np.mean([row["twin_nll"] for row in local_rows])),
            "twin_minus_one_shot_nll": float(np.mean([
                row["twin_minus_one_shot_nll"] for row in local_rows
            ])),
        })

    out = WORK / "cross_survey" / "one_shot"
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("questions.csv", question_rows), ("options.csv", option_rows),
                       ("batteries.csv", battery_rows)):
        with (out / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    costs = load(WORK / "study" / "one-shot-costs.json")
    aggregate = load(study / "aggregates.json")["models"][MODEL]
    one_shot_benchmark = aggregate["benchmarks"]["one_shot"]
    summary = {
        "batteries": len(battery_rows),
        "questions": len(question_rows),
        "calls": costs["call_count"],
        "cost_usd": costs["total_usd"],
        "mean_one_shot_mae": float(np.mean([row["one_shot_mae"] for row in question_rows])),
        "mean_twin_marginal_mae": float(np.mean([
            row["twin_marginal_mae"] for row in question_rows
        ])),
        "mean_nll_delta": one_shot_benchmark["mean"],
        "questions_twin_better_nll": one_shot_benchmark["questions_better"],
        "batteries_twin_better_nll": sum(
            row["twin_minus_one_shot_nll"] < 0 for row in battery_rows
        ),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    figure_dir = HERE / "output" / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    actual = np.array([row["actual"] for row in option_rows])
    fig, ax = plt.subplots(figsize=(6.2, 5.2))
    ax.scatter(actual, [row["one_shot"] for row in option_rows], s=17, alpha=.43,
               color="#d17a22", label="One-shot")
    ax.scatter(actual, [row["twin"] for row in option_rows], s=17, alpha=.43,
               color="#2878b5", label="Mean twin")
    ax.plot([0, 1], [0, 1], color="black", linestyle="--", linewidth=1)
    ax.set(xlim=(0, 1), ylim=(0, 1), xlabel="Observed option share",
           ylabel="Predicted option share",
           title="Aggregate marginals with and without respondent context")
    ax.legend(frameon=False)
    ax.grid(color="#e2e2e2", linewidth=.6)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(figure_dir / "one_shot_marginal_fit.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "one_shot_marginal_fit.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    ordered = sorted(battery_rows, key=lambda row: row["twin_minus_one_shot_nll"])
    fig, ax = plt.subplots(figsize=(7.4, 5.8))
    ax.scatter([row["twin_minus_one_shot_nll"] for row in ordered], range(len(ordered)),
               s=34, color="#2878b5")
    ax.axvline(0, color="black", linewidth=1, linestyle="--")
    ax.set_yticks(range(len(ordered)))
    ax.set_yticklabels([row["battery"] for row in ordered], fontsize=8.5)
    ax.set_xlabel(r"Twin $-$ one-shot NLL (lower favors respondent context)")
    ax.set_title("Same-model value of respondent-specific context", loc="left", fontsize=11)
    ax.grid(axis="x", color="#dddddd", linewidth=.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(figure_dir / "one_shot_nll_lift.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "one_shot_nll_lift.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    generated = HERE / "writeup" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    table = [
        f"{latex(row['battery'])} & {row['questions']} & {row['one_shot_mae']:.3f} & "
        f"{row['twin_marginal_mae']:.3f} & {row['twin_minus_one_shot_nll']:.3f} \\\\"
        for row in sorted(battery_rows, key=lambda row: row["battery"])
    ]
    (generated / "one_shot_table.tex").write_text(
        "\\begin{tabular}{lrrrr}\\toprule\n"
        "Battery & Questions & One-shot MAE & Twin MAE & $\\Delta$ NLL \\\\\n"
        "\\midrule\n" + "\n".join(table) + "\n\\bottomrule\\end{tabular}\n"
    )
    (generated / "one_shot_macros.tex").write_text(
        f"\\newcommand{{\\OneShotCalls}}{{{summary['calls']}}}\n"
        f"\\newcommand{{\\OneShotCost}}{{\\${summary['cost_usd']:.2f}}}\n"
        f"\\newcommand{{\\OneShotMAE}}{{{summary['mean_one_shot_mae']:.3f}}}\n"
        f"\\newcommand{{\\OneShotTwinMAE}}{{{summary['mean_twin_marginal_mae']:.3f}}}\n"
        f"\\newcommand{{\\OneShotNLLDelta}}{{{summary['mean_nll_delta']:.3f}}}\n"
        f"\\newcommand{{\\OneShotTwinWins}}{{{summary['questions_twin_better_nll']}}}\n"
        f"\\newcommand{{\\OneShotBatteryWins}}{{{summary['batteries_twin_better_nll']}}}\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
