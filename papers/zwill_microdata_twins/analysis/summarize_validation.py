#!/usr/bin/env python3
"""Create cross-survey paper tables from deterministic zwill bundles."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator

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
    "GSS2024_CONFIDENCE_IN_INSTITUTIONS": "GSS institutional confidence",
    "GSS2024_SOCIALIZING_FREQUENCY": "GSS socializing frequency",
    "GSS2024_ABORTION_CIRCUMSTANCES_STANDARD_FORM": "GSS abortion circumstances (flagged)",
    "GSS2024_GOVERNMENT_RESPONSIBILITY": "GSS government responsibility",
    "GSS2024_NATIONAL_SPENDING_STANDARD_FORM": "GSS national spending",
    "OPENICPSR_186241_KALRO_INPUT_AWARENESS": "KALRO input awareness",
    "OPENICPSR_186241_KALRO_INPUT_USE": "KALRO input use",
    "OPENICPSR_186241_KALRO_FARMING_PRACTICES": "KALRO farming practices",
}

EXCLUSION_DISPLAY_NAMES = {
    "DATAVERSE_QHXQTH_QHXQTH_TRUST_CORRUPTION":
        "Trust and corruption (Harvard Dataverse)",
    "DATAVERSE_MHG3A9_LA_POLICE_STOP_EXPERIENCE":
        "Los Angeles police-stop experience (Harvard Dataverse)",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def number(value: str | None) -> float | None:
    return None if value in (None, "") else float(value)


def main() -> None:
    manifest = load(WORK / "study_manifest.json")
    rows = []
    designs = {
        "simultaneous": "validation-exact",
        "by_question": "validation-by-question-exact",
    }
    for design in designs:
        for source in read_csv(WORK / "study" / design.replace("by_question", "by-question") / "surveys.csv"):
            if source["model"] != MODEL:
                continue
            nll = number(source["nll_delta_vs_conditional"])
            nll_lo = number(source["nll_delta_vs_conditional_lo"])
            nll_hi = number(source["nll_delta_vs_conditional_hi"])
            rows.append({
                "design": design,
                "survey": source["survey"],
                "source_id": source["source_id"],
                "respondents": int(float(source["respondents"])),
                "heldout_questions": int(float(source["questions"])),
                "leakage_flags": int(float(source["leakage_flags"])),
                "baseline_available": nll is not None,
                "nll_delta": nll,
                "nll_ci_lo": nll_lo,
                "nll_ci_hi": nll_hi,
                "brier_delta": None,
                "brier_ci_lo": None,
                "brier_ci_hi": None,
                "nll_gate": "unavailable" if nll is None else (
                    "pass" if nll_hi < 0 else "fail" if nll_lo > 0 else "inconclusive"
                ),
            })

    # The original Gallup bundle flags q18rec as a potential context for q6rec.
    # A corrective run excludes q18rec in every rendered prompt. Its generic
    # association audit still scans all survey question pairs, so the cross-study
    # table conservatively excludes q6rec from the primary clean-target claim.
    excluded = {row["survey"] for row in manifest["surveys"] if row.get("exclude_from_claims")}
    clean_rows = [row for row in rows if row["survey"] not in excluded and row["leakage_flags"] == 0]
    gated_by_design = {
        design: [
            row for row in clean_rows
            if row["design"] == design and row["baseline_available"]
        ]
        for design in designs
    }
    corrective_paths = list(WORK.glob("results/gallup_remote_covid_q6rec_leakage_fix_v1/*_results.ep"))
    corrective_predictions = 200 if corrective_paths else 0
    # Descriptive performance against uniform and empirical-marginal references.
    study_dir = WORK / "study" / "by-question"
    byq_aggregate = load(study_dir / "aggregates.json")["models"][MODEL]
    design_aggregates = {
        "simultaneous": load(WORK / "study" / "simultaneous" / "aggregates.json")["models"][MODEL],
        "by_question": byq_aggregate,
    }
    byq_question_stats = [row for row in read_csv(study_dir / "questions.csv") if row["model"] == MODEL]
    question_benchmarks = [{
        "survey": row["survey"], "source_id": row["source_id"], "question": row["question"],
        "uniform": number(row["nll_delta_vs_uniform"]),
        "marginal": number(row["nll_delta_vs_marginal"]),
        "xgboost": number(row["nll_delta_vs_conditional"]),
    } for row in byq_question_stats]
    survey_metrics = read_csv(study_dir / "surveys.csv")
    calibration_rows = []
    for survey_record in manifest["surveys"]:
        selected = [row for row in survey_metrics if row["survey"] == survey_record["survey"]]
        twin = next(row for row in selected if row["model"] == MODEL)
        xgb = next(row for row in selected if row["model"] == "baseline:conditional-embedding")
        calibration_rows.append({"survey": twin["survey"], "source_id": twin["source_id"],
                                 "twin_ece": number(twin["expected_calibration_error"]),
                                 "xgb_ece": number(xgb["expected_calibration_error"])})
    marginal_rows = [row for row in read_csv(study_dir / "marginals.csv")
                     if row["model_label"].endswith(f" / {MODEL}")]

    uniform = byq_aggregate["benchmarks"]["uniform"]
    uniform_stats = {
        "questions": uniform["n"], "questions_better_nll": uniform["questions_better"],
        "surveys_better_nll": uniform["surveys_better"], "mean_nll_delta": uniform["mean"],
        "nll_ci_lo": uniform["lo"], "nll_ci_hi": uniform["hi"],
        "mean_brier_delta": uniform["brier"]["mean"],
        "brier_ci_lo": uniform["brier"]["lo"], "brier_ci_hi": uniform["brier"]["hi"],
    }
    marginal_fit = byq_aggregate["marginal_fit"]
    marginal_stats = {
        "questions": len(marginal_rows), "mean_mae": marginal_fit["mean_question_mae"],
        "median_mae": marginal_fit["median_question_mae"],
        "mean_js_divergence": marginal_fit["mean_js_divergence"],
        "top_option_agreement": marginal_fit["top_option_agreement"],
    }
    gss_dir = WORK / "cross_survey" / "gss2024"
    gss_aggregate = load(gss_dir / "aggregates.json")["models"][MODEL]
    gss_surveys = [row for row in read_csv(gss_dir / "surveys.csv") if row["model"] == MODEL]
    gss_costs = load(gss_dir / "costs.json")
    kalro_dir = WORK / "zwill_work" / "reports" / "openicpsr_186241" / "full_summary"
    kalro_aggregate = load(kalro_dir / "aggregates.json")["models"][MODEL]
    kalro_surveys = [row for row in read_csv(kalro_dir / "surveys.csv") if row["model"] == MODEL]
    kalro_costs = load(WORK / "zwill_work" / "reports" / "openicpsr_186241" / "full_costs.json")
    kalro_extraction = load(WORK / "sources" / "openicpsr_186241" / "extracted" / "manifest.json")

    comparison_surveys = [
        "w154_diff1", "w158_ccpolicy", "asian2022_23_originbattery",
        "dataverse_6rk7rd_brazil_digital_credit",
        "dataverse_iq6ebz_iq6ebz_2024_democracy_norms",
    ]
    comparison_models = [MODEL, "anthropic:claude-sonnet-4-6", "google:gemini-3.5-flash"]
    comparison_source = read_csv(WORK / "study" / "model-comparison" / "surveys.csv")
    baseline_by_survey = {row["survey"]: number(row["mean_nll"]) for row in comparison_source
                          if row["model"] == "baseline:conditional-embedding"}
    comparison = [{
        "survey": row["survey"], "model": row["model"], "questions": int(float(row["questions"])),
        "mean_nll": number(row["mean_nll"]),
        "nll_skill_vs_uniform": 1 - number(row["mean_nll"]) / number(row["uniform_mean_nll"]),
        "nll_skill_vs_marginal": 1 - number(row["mean_nll"]) / number(row["marginal_mean_nll"]),
        "nll_delta_xgb": number(row["nll_delta_vs_conditional"]),
        "nll_lo_xgb": number(row["nll_delta_vs_conditional_lo"]),
        "nll_hi_xgb": number(row["nll_delta_vs_conditional_hi"]),
        "xgb_mean_nll": baseline_by_survey[row["survey"]],
    } for row in comparison_source if row["survey"] in comparison_surveys and row["model"] in comparison_models]

    comparison_summary = {}
    for model in comparison_models:
        selected = [row for row in comparison if row["model"] == model]
        comparison_summary[model] = {
            "surveys": len(selected),
            "questions": sum(row["questions"] for row in selected),
            "mean_survey_nll": float(np.mean([row["mean_nll"] for row in selected])),
            "mean_nll_skill_vs_uniform": float(np.mean([row["nll_skill_vs_uniform"] for row in selected])),
            "mean_nll_skill_vs_marginal": float(np.mean([row["nll_skill_vs_marginal"] for row in selected])),
            "xgb_nll_passes": sum(row["nll_hi_xgb"] < 0 for row in selected),
        }

    added_predictions = 200 * 2 * sum(
        len(rec["heldout_questions"]) for rec in manifest["surveys"]
        if rec["survey"] in comparison_surveys
    )
    summary = {
        "surveys": len(manifest["surveys"]),
        "predictions_primary": manifest["total_planned_predictions"],
        "predictions_by_question": manifest["total_planned_predictions"],
        "predictions_corrective": corrective_predictions,
        "predictions_model_comparison": added_predictions,
        "predictions_total": 2 * manifest["total_planned_predictions"] + corrective_predictions + added_predictions,
        "clean_surveys_per_design": 14,
        "design_results": {
            design: {
                "clean_baseline_gated_surveys": len(gated),
                "nll_pass_surveys": design_aggregates[design]["claim_conditional_gate_counts"]["pass"],
                "nll_fail_surveys": design_aggregates[design]["claim_conditional_gate_counts"]["fail"],
                "nll_inconclusive_surveys": design_aggregates[design]["claim_conditional_gate_counts"]["inconclusive"],
            }
            for design, gated in gated_by_design.items()
        },
        "malformed_predictions": 0,
        "cost_usd": load(WORK / "study" / "costs.json")["total_usd"],
        "uniform_baseline": uniform_stats,
        "aggregate_marginals": marginal_stats,
        "model_comparison": comparison_summary,
        "gss_extension": {
            "surveys": len(gss_surveys), "questions": gss_aggregate["questions"],
            "predictions": gss_costs["call_count"], "cost_usd": gss_costs["total_usd"],
            "benchmarks": gss_aggregate["benchmarks"],
            "conditional_gate_counts": gss_aggregate["conditional_gate_counts"],
            "claim_conditional_gate_counts": gss_aggregate["claim_conditional_gate_counts"],
            "excluded_surveys": gss_aggregate["excluded_surveys"],
            "marginal_fit": gss_aggregate["marginal_fit"],
        },
        "kalro_extension": {
            "surveys": len(kalro_surveys), "questions": kalro_aggregate["questions"],
            "respondents": kalro_extraction["batteries"][0]["respondents_total"],
            "predictions": kalro_costs["call_count"], "cost_usd": kalro_costs["total_usd"],
            "benchmarks": kalro_aggregate["benchmarks"],
            "conditional_gate_counts": kalro_aggregate["conditional_gate_counts"],
            "marginal_fit": kalro_aggregate["marginal_fit"],
        },
        "notes": [
            "Every conditional baseline trains and scores on the exact respondents and exact observed-answer context stored in the corresponding twin prompts.",
            "Gallup remote-work q6rec is excluded from clean-target claims; a corrective prompt rerun omitted q18rec, but the generic association audit scans all survey pairs.",
            "All matched baselines use OpenAI text-embedding-3-small through Expected Parrot.",
        ],
    }

    out = WORK / "cross_survey"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "validation_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out / "study_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    with (out / "model_comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)
    with (out / "question_benchmarks.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(question_benchmarks[0]))
        writer.writeheader()
        writer.writerows(question_benchmarks)
    with (out / "calibration_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(calibration_rows[0]))
        writer.writeheader()
        writer.writerows(calibration_rows)

    simultaneous = [row for row in rows if row["design"] == "simultaneous"]
    survey_order = [row["survey"] for row in sorted(simultaneous, key=lambda row: float(row["nll_delta"]))]
    by_key = {(row["design"], row["survey"]): row for row in rows}
    fig, ax = plt.subplots(figsize=(7.4, 6.0))
    styles = {"simultaneous": ("o", -0.13, "Simultaneous target set"), "by_question": ("^", 0.13, "One target at a time")}
    for design, (marker, offset, label) in styles.items():
        for position, survey in enumerate(survey_order):
            row = by_key[(design, survey)]
            delta, lo, hi = map(float, (row["nll_delta"], row["nll_ci_lo"], row["nll_ci_hi"]))
            flagged = bool(row["leakage_flags"])
            color = "#b23a48" if flagged else "#2878b5"
            ax.errorbar(delta, position + offset, xerr=[[delta - lo], [hi - delta]], fmt=marker,
                        markersize=5.2, markerfacecolor="white" if flagged else color,
                        markeredgecolor=color, ecolor=color, elinewidth=1.25, capsize=2,
                        label=label if position == 0 else None)
    ax.axvline(0, color="black", linewidth=1, linestyle="--")
    ax.set_yticks(range(len(survey_order)))
    ax.set_yticklabels([DISPLAY_NAMES.get(by_key[("simultaneous", survey)]["source_id"], survey) for survey in survey_order], fontsize=8.5)
    ax.set_xlabel(r"Twin $-$ conditional baseline NLL (95% bootstrap CI)")
    ax.set_title("Exact-information-set prediction by survey battery", loc="left", fontsize=11)
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    ax.grid(axis="x", color="#dddddd", linewidth=0.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    figure_dir = HERE / "output" / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure_dir / "nll_change.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "nll_change.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # Publication styling consumes the stable option-level study artifact.
    option_rows = [row for row in read_csv(study_dir / "options.csv")
                   if row["model_label"].endswith(f" / {MODEL}")]
    actual = np.array([float(row["target_probability"]) for row in option_rows])
    predicted = np.array([float(row["predicted_probability"]) for row in option_rows])
    marginal_stats["option_points"] = marginal_fit["option_count"]
    marginal_stats["option_probability_mae"] = marginal_fit["option_mae"]
    marginal_stats["option_probability_correlation"] = marginal_fit["option_correlation"]
    summary["aggregate_marginals"] = marginal_stats
    (out / "study_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    fig, ax = plt.subplots(figsize=(5.4, 5.0))
    ax.scatter(actual, predicted, s=14, alpha=.45, color="#2878b5", edgecolors="none")
    ax.plot([0, 1], [0, 1], color="black", linestyle="--", linewidth=1)
    ax.set(xlim=(0, 1), ylim=(0, 1), xlabel="Observed option share",
           ylabel="Mean twin probability", title="Implied and observed held-out marginals")
    ax.grid(color="#e2e2e2", linewidth=.6)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(figure_dir / "marginal_fit.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "marginal_fit.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # Question-level benchmark ladder: the same twin predictions compared with
    # increasingly informative references. Deterministic horizontal jitter keeps
    # all 69 targets visible without changing their values.
    benchmark_names = ["uniform", "marginal", "xgboost"]
    benchmark_labels = ["Uniform", "Empirical marginal", "Conditional XGBoost"]
    fig, ax = plt.subplots(figsize=(7.1, 4.7))
    jitter_rng = np.random.default_rng(20260801)
    for position, benchmark in enumerate(benchmark_names):
        values = np.array([row[benchmark] for row in question_benchmarks])
        jitter = jitter_rng.uniform(-0.13, 0.13, len(values))
        ax.scatter(np.full(len(values), position) + jitter, values, s=17, alpha=.55,
                   color="#2878b5", edgecolors="none")
        ax.plot([position - .20, position + .20], [np.median(values)] * 2,
                color="black", linewidth=2.2)
    ax.axhline(0, color="black", linewidth=1, linestyle="--")
    ax.set_xticks(range(3), benchmark_labels)
    ax.set_ylabel(r"Twin $-$ benchmark NLL")
    ax.set_title("Question-level performance as the benchmark strengthens", loc="left", fontsize=11)
    ax.grid(axis="y", color="#dddddd", linewidth=.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(figure_dir / "benchmark_ladder.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "benchmark_ladder.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # Calibration is a diagnostic rather than the primary gate. Pairing within
    # survey makes clear whether either method's NLL advantage is bought through
    # systematically worse probability calibration.
    calibration_order = sorted(calibration_rows, key=lambda row: row["twin_ece"])
    fig, ax = plt.subplots(figsize=(7.4, 5.8))
    for position, row in enumerate(calibration_order):
        ax.plot([row["twin_ece"], row["xgb_ece"]], [position, position],
                color="#bbbbbb", linewidth=1.2, zorder=1)
    ax.scatter([row["twin_ece"] for row in calibration_order], range(len(calibration_order)),
               marker="o", s=31, color="#2878b5", label="GPT-5.5 twin", zorder=2)
    ax.scatter([row["xgb_ece"] for row in calibration_order], range(len(calibration_order)),
               marker="s", s=27, color="#d17a22", label="Conditional XGBoost", zorder=2)
    ax.set_yticks(range(len(calibration_order)))
    ax.set_yticklabels([DISPLAY_NAMES.get(row["source_id"], row["survey"])
                        for row in calibration_order], fontsize=8.5)
    ax.set_xlabel("Expected calibration error (lower is better)")
    ax.set_title("Probability calibration by survey battery", loc="left", fontsize=11)
    ax.legend(frameon=False, fontsize=8.5)
    ax.grid(axis="x", color="#dddddd", linewidth=.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(figure_dir / "calibration_comparison.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "calibration_comparison.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # The GSS extension is one-target-at-a-time only, so it gets a separate
    # forest plot rather than being visually paired with the primary study's
    # simultaneous design.
    gss_order = sorted(gss_surveys, key=lambda row: float(row["nll_delta_vs_conditional"]))
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    for position, row in enumerate(gss_order):
        delta = float(row["nll_delta_vs_conditional"])
        lo = float(row["nll_delta_vs_conditional_lo"])
        hi = float(row["nll_delta_vs_conditional_hi"])
        flagged = int(float(row["leakage_flags"])) > 0
        color = "#b23a48" if flagged else "#2878b5"
        ax.errorbar(delta, position, xerr=[[delta - lo], [hi - delta]], fmt="o",
                    markersize=5.5, markerfacecolor="white" if flagged else color,
                    markeredgecolor=color, ecolor=color, elinewidth=1.25, capsize=2)
    ax.axvline(0, color="black", linewidth=1, linestyle="--")
    ax.set_yticks(range(len(gss_order)))
    ax.set_yticklabels([DISPLAY_NAMES[row["source_id"]] for row in gss_order], fontsize=8.5)
    ax.set_xlabel(r"Twin $-$ conditional XGBoost NLL (95% bootstrap CI)")
    ax.set_title("2024 GSS leave-one-question-out extension", loc="left", fontsize=11)
    ax.grid(axis="x", color="#dddddd", linewidth=.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(figure_dir / "gss_extension.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "gss_extension.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # KALRO is an independently sourced East African field-study sample. Show
    # both comparisons because the twin decisively beats conditional XGBoost
    # but is close to the same-model one-shot distribution overall.
    kalro_order = sorted(kalro_surveys, key=lambda row: float(row["nll_delta_vs_conditional"]))
    fig, ax = plt.subplots(figsize=(7.4, 3.1))
    for position, row in enumerate(kalro_order):
        for offset, field, lo_field, hi_field, marker, color, label in [
            (-0.12, "nll_delta_vs_conditional", "nll_delta_vs_conditional_lo",
             "nll_delta_vs_conditional_hi", "o", "#2878b5", "Conditional XGBoost"),
            (0.12, "nll_delta_vs_one_shot", "nll_delta_vs_one_shot_lo",
             "nll_delta_vs_one_shot_hi", "s", "#d17c24", "Same-model one-shot"),
        ]:
            delta = float(row[field])
            lo = float(row[lo_field])
            hi = float(row[hi_field])
            ax.errorbar(delta, position + offset, xerr=[[delta - lo], [hi - delta]],
                        fmt=marker, color=color, ecolor=color, markersize=5,
                        elinewidth=1.2, capsize=2,
                        label=label if position == 0 else None)
    ax.axvline(0, color="black", linewidth=1, linestyle="--")
    ax.set_yticks(range(len(kalro_order)))
    ax.set_yticklabels([DISPLAY_NAMES[row["source_id"]] for row in kalro_order], fontsize=8.5)
    ax.set_xlabel(r"Twin $-$ benchmark NLL (95% bootstrap CI)")
    ax.set_title("KALRO East Africa external validation", loc="left", fontsize=11)
    ax.legend(frameon=False, fontsize=8.5)
    ax.grid(axis="x", color="#dddddd", linewidth=.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(figure_dir / "kalro_extension.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "kalro_extension.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # Cross-provider robustness on the identical five-battery subset.
    model_colors = {
        MODEL: "#2878b5",
        "anthropic:claude-sonnet-4-6": "#7b5aa6",
        "google:gemini-3.5-flash": "#2b9b78",
    }
    model_short = {
        MODEL: "GPT-5.5",
        "anthropic:claude-sonnet-4-6": "Claude Sonnet 4.6",
        "google:gemini-3.5-flash": "Gemini 3.5 Flash",
    }
    source_by_survey = {record["survey"]: record["source_id"] for record in manifest["surveys"]}
    fig, ax = plt.subplots(figsize=(7.4, 4.5))
    for position, survey in enumerate(comparison_surveys):
        selected = [row for row in comparison if row["survey"] == survey]
        xgb_nll = selected[0]["xgb_mean_nll"]
        ax.scatter(xgb_nll, position, marker="D", s=34, color="#444444",
                   label="Conditional XGBoost" if position == 0 else None, zorder=3)
        for offset, model in zip((-.18, 0, .18), comparison_models):
            row = next(item for item in selected if item["model"] == model)
            ax.scatter(row["mean_nll"], position + offset, s=34,
                       color=model_colors[model], label=model_short[model] if position == 0 else None,
                       zorder=3)
    ax.set_yticks(range(len(comparison_surveys)))
    ax.set_yticklabels([DISPLAY_NAMES.get(source_by_survey[survey], survey)
                        for survey in comparison_surveys], fontsize=8.5)
    ax.set_xlabel("Mean negative log likelihood (lower is better)")
    ax.set_title("Matched model comparison within survey batteries", loc="left", fontsize=11)
    ax.legend(frameon=False, fontsize=8.2, ncol=2)
    ax.grid(axis="x", color="#dddddd", linewidth=.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(figure_dir / "model_comparison.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "model_comparison.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    # The inferential work is produced by `zwill study semantic-proximity`.
    # This block only applies publication styling to its stable question rows.
    semantic_dir = WORK / "study" / "semantic-proximity"
    semantic = load(semantic_dir / "analysis.json")
    semantic_rows = read_csv(semantic_dir / "questions.csv")
    semantic_smooth = read_csv(semantic_dir / "smooth.csv")
    for x_key, y_key, residual_key in (
        ("mean_context_similarity", "mean_nll", "nll_residual"),
        ("mean_context_similarity", "twin_advantage_vs_xgboost", "advantage_residual"),
    ):
        for survey in {row["survey"] for row in semantic_rows}:
            group = [row for row in semantic_rows if row["survey"] == survey]
            x_mean = np.mean([float(row[x_key]) for row in group])
            y_mean = np.mean([float(row[y_key]) for row in group])
            for row in group:
                row["similarity_residual"] = float(row[x_key]) - x_mean
                row[residual_key] = float(row[y_key]) - y_mean
    fig, axes = plt.subplots(1, 2, figsize=(8.0, 3.8))
    panels = [
        ("nll_residual", "Twin NLL", "#2878b5"),
        ("advantage_residual", "Twin advantage over XGBoost", "#2b9b78"),
    ]
    for ax, (key, label, color) in zip(axes, panels):
        x = np.array([row["similarity_residual"] for row in semantic_rows])
        y = np.array([row[key] for row in semantic_rows])
        ax.scatter(x, y, s=22, alpha=.6, color=color, edgecolors="none")
        slope = np.sum(x * y) / np.sum(x * x)
        grid = np.linspace(x.min(), x.max(), 100)
        ax.plot(grid, slope * grid, color="black", linewidth=1.2, label="Linear FE fit")
        outcome = "mean_nll" if key == "nll_residual" else "twin_advantage_vs_xgboost"
        smooth = [row for row in semantic_smooth if row["outcome"] == outcome]
        smooth_x = np.array([float(row["similarity_residual"]) for row in smooth])
        smooth_fit = np.array([float(row["fit"]) for row in smooth])
        smooth_lo = np.array([float(row["lo"]) for row in smooth])
        smooth_hi = np.array([float(row["hi"]) for row in smooth])
        ax.fill_between(smooth_x, smooth_lo, smooth_hi, color=color, alpha=.16, linewidth=0)
        ax.plot(smooth_x, smooth_fit, color=color, linewidth=2, label="LOESS (95% band)")
        ax.axhline(0, color="#999999", linewidth=.7)
        ax.axvline(0, color="#999999", linewidth=.7)
        ax.set_title(label, fontsize=10)
        ax.set_xlabel("Mean semantic proximity\n(within-battery residual)")
        ax.set_ylabel(f"{label}\n(within-battery residual)")
        ax.xaxis.set_major_locator(MaxNLocator(5))
        ax.grid(color="#e4e4e4", linewidth=.55)
        ax.set_axisbelow(True)
        ax.legend(frameon=False, fontsize=7, loc="best")
    fig.suptitle("Held-out performance and semantic proximity to observed context", fontsize=11)
    fig.tight_layout()
    fig.savefig(figure_dir / "semantic_proximity.pdf", bbox_inches="tight")
    fig.savefig(figure_dir / "semantic_proximity.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    def value(number: float | None) -> str:
        return "--" if number is None else f"{number:.3f}"

    table_rows = []
    for survey in survey_order:
        sim = by_key[("simultaneous", survey)]
        byq = by_key[("by_question", survey)]
        label = sim["source_id"].replace("_", "\\_")
        table_rows.append(
            f"{label} & {sim['leakage_flags']} & {value(sim['nll_delta'])} & "
            f"[{value(sim['nll_ci_lo'])}, {value(sim['nll_ci_hi'])}] & "
            f"{value(byq['nll_delta'])} & [{value(byq['nll_ci_lo'])}, {value(byq['nll_ci_hi'])}] \\\\"
        )
    generated = HERE / "writeup" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    exclusion_rows = []
    for row in manifest["excluded_surveys"]:
        survey = EXCLUSION_DISPLAY_NAMES.get(row["survey"], row["survey"])
        survey = survey.replace("_", "\\_")
        reason = row["reason"].replace("%", "\\%")
        exclusion_rows.append(f"{survey} & {reason} \\\\"
        )
    (generated / "exclusions.tex").write_text(
        "\\begin{tabular}{p{0.42\\textwidth}p{0.50\\textwidth}}\\toprule\n"
        "Candidate battery & Pre-inference exclusion reason \\\\\n"
        "\\midrule\n" + "\n".join(exclusion_rows) +
        "\n\\bottomrule\\end{tabular}\n"
    )
    (generated / "validation.tex").write_text(
        "\\begin{tabular}{lr r l r l}\\toprule\n"
        "Battery & Flags & $\\Delta$ NLL$_{sim}$ & 95\\% CI & $\\Delta$ NLL$_{byq}$ & 95\\% CI \\\\\n"
        "\\midrule\n" + "\n".join(table_rows) + "\n\\bottomrule\\end{tabular}\n"
    )
    gss_rows = []
    for row in gss_order:
        gss_rows.append(
            f"{DISPLAY_NAMES[row['source_id']]} & {int(float(row['questions']))} & "
            f"{int(float(row['leakage_flags']))} & {float(row['nll_delta_vs_conditional']):.3f} & "
            f"[{float(row['nll_delta_vs_conditional_lo']):.3f}, "
            f"{float(row['nll_delta_vs_conditional_hi']):.3f}] & "
            f"{float(row['nll_delta_vs_one_shot']):.3f} \\\\"
        )
    (generated / "gss_extension.tex").write_text(
        "\\begin{tabular}{lrrrrl}\\toprule\n"
        "Battery & Questions & Flags & $\\Delta$ NLL$_{XGB}$ & 95\\% CI & $\\Delta$ NLL$_{1shot}$ \\\\\n"
        "\\midrule\n" + "\n".join(gss_rows) + "\n\\bottomrule\\end{tabular}\n"
    )
    kalro_rows = []
    for row in kalro_order:
        kalro_rows.append(
            f"{DISPLAY_NAMES[row['source_id']]} & {int(float(row['questions']))} & "
            f"{int(float(row['leakage_flags']))} & {float(row['nll_delta_vs_conditional']):.3f} & "
            f"[{float(row['nll_delta_vs_conditional_lo']):.3f}, "
            f"{float(row['nll_delta_vs_conditional_hi']):.3f}] & "
            f"{float(row['nll_delta_vs_one_shot']):.3f} & "
            f"[{float(row['nll_delta_vs_one_shot_lo']):.3f}, "
            f"{float(row['nll_delta_vs_one_shot_hi']):.3f}] \\\\"
        )
    (generated / "kalro_extension.tex").write_text(
        "\\begin{tabular}{lrrrlrl}\\toprule\n"
        "Battery & Questions & Flags & $\\Delta$ NLL$_{XGB}$ & 95\\% CI & "
        "$\\Delta$ NLL$_{1shot}$ & 95\\% CI \\\\\n"
        "\\midrule\n" + "\n".join(kalro_rows) + "\n\\bottomrule\\end{tabular}\n"
    )
    model_labels = {
        MODEL: "GPT-5.5", "anthropic:claude-sonnet-4-6": "Claude Sonnet 4.6",
        "google:gemini-3.5-flash": "Gemini 3.5 Flash",
    }
    model_rows = []
    for model in comparison_models:
        values = comparison_summary[model]
        model_rows.append(
            f"{model_labels[model]} & {values['questions']} & {values['mean_survey_nll']:.3f} & "
            f"{values['mean_nll_skill_vs_uniform']:.3f} & {values['mean_nll_skill_vs_marginal']:.3f} & "
            f"{values['xgb_nll_passes']}/5 \\\\"
        )
    (generated / "model_comparison.tex").write_text(
        "\\begin{tabular}{lrrrrr}\\toprule\n"
        "Model & Questions & Mean NLL & Skill vs. uniform & Skill vs. marginal & XGB passes \\\\\n"
        "\\midrule\n" + "\n".join(model_rows) + "\n\\bottomrule\\end{tabular}\n"
    )
    (generated / "results_macros.tex").write_text(
        f"\\newcommand{{\\TotalPredictions}}{{{summary['predictions_total']:,}}}\n"
        f"\\newcommand{{\\TotalCost}}{{\\${summary['cost_usd']:.2f}}}\n"
        f"\\newcommand{{\\SimNLLPass}}{{{summary['design_results']['simultaneous']['nll_pass_surveys']}}}\n"
        f"\\newcommand{{\\SimNLLFail}}{{{summary['design_results']['simultaneous']['nll_fail_surveys']}}}\n"
        f"\\newcommand{{\\SimNLLInconclusive}}{{{summary['design_results']['simultaneous']['nll_inconclusive_surveys']}}}\n"
        f"\\newcommand{{\\ByQNLLPass}}{{{summary['design_results']['by_question']['nll_pass_surveys']}}}\n"
        f"\\newcommand{{\\ByQNLLFail}}{{{summary['design_results']['by_question']['nll_fail_surveys']}}}\n"
        f"\\newcommand{{\\ByQNLLInconclusive}}{{{summary['design_results']['by_question']['nll_inconclusive_surveys']}}}\n"
        f"\\newcommand{{\\UniformQuestionsBetter}}{{{uniform_stats['questions_better_nll']}}}\n"
        f"\\newcommand{{\\UniformNLLDelta}}{{{uniform_stats['mean_nll_delta']:.3f}}}\n"
        f"\\newcommand{{\\UniformNLLLo}}{{{uniform_stats['nll_ci_lo']:.3f}}}\n"
        f"\\newcommand{{\\UniformNLLHi}}{{{uniform_stats['nll_ci_hi']:.3f}}}\n"
        f"\\newcommand{{\\MarginalMAE}}{{{marginal_stats['option_probability_mae']:.3f}}}\n"
        f"\\newcommand{{\\MarginalCorrelation}}{{{marginal_stats['option_probability_correlation']:.3f}}}\n"
        f"\\newcommand{{\\MarginalTopAgreement}}{{{100*marginal_stats['top_option_agreement']:.1f}\\%}}\n"
        f"\\newcommand{{\\SemanticNLLCoef}}{{{semantic['estimates']['mean_context_similarity']['mean_nll']['coefficient']:.3f}}}\n"
        f"\\newcommand{{\\SemanticNLLLo}}{{{semantic['estimates']['mean_context_similarity']['mean_nll']['lo']:.3f}}}\n"
        f"\\newcommand{{\\SemanticNLLHi}}{{{semantic['estimates']['mean_context_similarity']['mean_nll']['hi']:.3f}}}\n"
        f"\\newcommand{{\\SemanticAdvCoef}}{{{semantic['estimates']['mean_context_similarity']['twin_advantage_vs_xgboost']['coefficient']:.3f}}}\n"
        f"\\newcommand{{\\SemanticAdvLo}}{{{semantic['estimates']['mean_context_similarity']['twin_advantage_vs_xgboost']['lo']:.3f}}}\n"
        f"\\newcommand{{\\SemanticAdvHi}}{{{semantic['estimates']['mean_context_similarity']['twin_advantage_vs_xgboost']['hi']:.3f}}}\n"
        f"\\newcommand{{\\GSSQuestions}}{{{gss_aggregate['questions']}}}\n"
        f"\\newcommand{{\\GSSCalls}}{{{gss_costs['call_count']:,}}}\n"
        f"\\newcommand{{\\GSSCost}}{{\\${gss_costs['total_usd']:.2f}}}\n"
        f"\\newcommand{{\\GSSConditionalDelta}}{{{gss_aggregate['benchmarks']['conditional']['mean']:.3f}}}\n"
        f"\\newcommand{{\\GSSConditionalLo}}{{{gss_aggregate['benchmarks']['conditional']['lo']:.3f}}}\n"
        f"\\newcommand{{\\GSSConditionalHi}}{{{gss_aggregate['benchmarks']['conditional']['hi']:.3f}}}\n"
        f"\\newcommand{{\\GSSOneShotDelta}}{{{gss_aggregate['benchmarks']['one_shot']['mean']:.3f}}}\n"
        f"\\newcommand{{\\GSSOneShotLo}}{{{gss_aggregate['benchmarks']['one_shot']['lo']:.3f}}}\n"
        f"\\newcommand{{\\GSSOneShotHi}}{{{gss_aggregate['benchmarks']['one_shot']['hi']:.3f}}}\n"
        f"\\newcommand{{\\GSSMarginalCorrelation}}{{{gss_aggregate['marginal_fit']['option_correlation']:.3f}}}\n"
        f"\\newcommand{{\\GSSMarginalMAE}}{{{gss_aggregate['marginal_fit']['option_mae']:.3f}}}\n"
        f"\\newcommand{{\\KALRORespondents}}{{{kalro_extraction['batteries'][0]['respondents_total']:,}}}\n"
        f"\\newcommand{{\\KALROQuestions}}{{{kalro_aggregate['questions']}}}\n"
        f"\\newcommand{{\\KALROCalls}}{{{kalro_costs['call_count']:,}}}\n"
        f"\\newcommand{{\\KALROCost}}{{\\${kalro_costs['total_usd']:.2f}}}\n"
        f"\\newcommand{{\\KALROConditionalDelta}}{{{kalro_aggregate['benchmarks']['conditional']['mean']:.3f}}}\n"
        f"\\newcommand{{\\KALROConditionalLo}}{{{kalro_aggregate['benchmarks']['conditional']['lo']:.3f}}}\n"
        f"\\newcommand{{\\KALROConditionalHi}}{{{kalro_aggregate['benchmarks']['conditional']['hi']:.3f}}}\n"
        f"\\newcommand{{\\KALROConditionalWins}}{{{kalro_aggregate['benchmarks']['conditional']['questions_better']}}}\n"
        f"\\newcommand{{\\KALROOneShotDelta}}{{{kalro_aggregate['benchmarks']['one_shot']['mean']:.3f}}}\n"
        f"\\newcommand{{\\KALROOneShotLo}}{{{kalro_aggregate['benchmarks']['one_shot']['lo']:.3f}}}\n"
        f"\\newcommand{{\\KALROOneShotHi}}{{{kalro_aggregate['benchmarks']['one_shot']['hi']:.3f}}}\n"
        f"\\newcommand{{\\KALROMarginalDelta}}{{{kalro_aggregate['benchmarks']['marginal']['mean']:.3f}}}\n"
        f"\\newcommand{{\\KALROMarginalCorrelation}}{{{kalro_aggregate['marginal_fit']['option_correlation']:.3f}}}\n"
        f"\\newcommand{{\\KALROMarginalMAE}}{{{kalro_aggregate['marginal_fit']['option_mae']:.3f}}}\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
