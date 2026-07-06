from __future__ import annotations

from .cli import *  # noqa: F403


def build_one_shot_analysis_report_context(
    args: argparse.Namespace,
    payload: dict[str, Any],
) -> dict[str, Any]:
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    per_question: dict[str, dict[str, Any]] = {}
    for row in rows:
        question = str(row.get("question") or "")
        current = per_question.get(question)
        candidate = {
            "question": question,
            "question_text": row.get("question_text"),
            "option_count": len(row.get("actual") or {}),
            "model": row.get("model_label") or model_label(row.get("service"), row.get("model")),
            "brier": row.get("brier"),
            "uniform_brier": row.get("uniform_brier"),
            "brier_improvement": row.get("brier_improvement"),
            "brier_percent_improvement": row.get("brier_percent_improvement"),
            "mae": row.get("mae"),
            "kl_divergence": row.get("kl_divergence"),
            "actual_top_option": max((row.get("actual") or {}).items(), key=lambda item: item[1])[0] if row.get("actual") else None,
            "predicted_top_option": max((row.get("predicted") or {}).items(), key=lambda item: item[1])[0] if row.get("predicted") else None,
        }
        if current is None or float(candidate.get("brier") or float("inf")) < float(current.get("brier") or float("inf")):
            per_question[question] = candidate

    best_questions = sorted(per_question.values(), key=lambda row: float(row.get("brier_improvement") or 0.0), reverse=True)[:12]
    weakest_questions = sorted(per_question.values(), key=lambda row: float(row.get("brier_improvement") or 0.0))[:12]
    rows_beat_uniform = [row for row in rows if float(row.get("brier_improvement") or 0.0) > 0.0]
    best_model, best_values = min(
        summary.items(),
        key=lambda item: float(item[1].get("mean_brier") or float("inf")),
        default=(None, {}),
    )
    return {
        "report_kind": "frontier_generated_one_shot_marginal_analysis",
        "survey": args.survey,
        "source_filters": {
            "job_id": getattr(args, "job_id", None),
            "probability_model": getattr(args, "probability_model", None),
        },
        "filters": {
            "job_id": getattr(args, "job_id", None),
            "model": getattr(args, "probability_model", None),
            "questions": sorted(per_question),
        },
        "analysis_target": {
            "page": "one-shot-marginals",
            "purpose": "Interpret no-persona aggregate marginal predictions as the deployable baseline for later digital twin validation.",
            "raw_prediction_rows_in_context": False,
        },
        "headline_metrics": {
            "prediction_rows": len(rows),
            "questions_evaluated": len(per_question),
            "models_evaluated": len(summary),
            "rows_beating_uniform_share": len(rows_beat_uniform) / len(rows) if rows else None,
            "best_model_by_mean_brier": best_model,
            "best_model_metrics": best_values,
        },
        "model_summary": summary,
        "best_question_fits": best_questions,
        "weakest_question_fits": weakest_questions,
        "per_question_best_model_summary": sorted(per_question.values(), key=lambda row: row["question"]),
    }


def build_one_shot_analysis_report_prompt(report_context: dict[str, Any]) -> str:
    return f"""You are writing the Analysis section for a one-shot marginal prediction report.

The report compares frontier-model, no-persona predictions of survey response marginals against committed empirical survey marginals and a uniform-over-options baseline.

Write Markdown only. Do not include a top-level title. Use plain language for an executive or research lead.

Your analysis should:
- Explain what was done. Define one-shot as asking the model once per survey question for the aggregate population response distribution, without respondent personas, prior answers, or digital twin context.
- Interpret the performance metrics versus the uniform baseline.
- Say what this implies for later digital twin work: this is the deployable aggregate baseline that persona-based twins should beat if the persona machinery is adding value.
- Identify where the model is strongest and weakest using the supplied question summaries.
- Be honest about limitations: this validates aggregate marginal prediction, not respondent-level matching.
- Do not claim individual predictive power.
- Do not say the text was computed deterministically or written by a template.

Recorded context and summary statistics:

{json.dumps(report_context, indent=2)}
"""


def build_edsl_one_shot_analysis_report_job_dict(
    args: argparse.Namespace,
    report_context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    prompt = build_one_shot_analysis_report_prompt(report_context)
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = load_edsl_job_classes()
    question_name = "one_shot_analysis_markdown"
    question = QuestionFreeText(question_name=question_name, question_text=prompt)
    model_params = parse_model_params(args)
    model_specs = parse_model_specs(args)
    job = Jobs(
        survey=Survey(questions=[question]),
        scenarios=ScenarioList([Scenario({})]),
        models=ModelList(
            [
                Model(
                    model_name=model_name,
                    service_name=service_name,
                    **model_kwargs_for(model_name, service_name, model_params),
                )
                for model_name, service_name in model_specs
            ]
        ),
    )
    job_dict = job.to_dict()
    report_id = practitioner_report_id_from_job(job_dict)
    job_dict["zwill"] = {
        **job_dict.get("zwill", {}),
        "practitioner_report_id": report_id,
        "practitioner_report_question_name": question_name,
        "report_kind": "one_shot_marginal_analysis",
    }
    generation = {
        "mode": "job_exported",
        "report_id": report_id,
        "model": model_label(model_specs[0][1], model_specs[0][0]) if model_specs else None,
        "models": [model_label(service_name, model_name) for model_name, service_name in model_specs],
        "report_kind": "one_shot_marginal_analysis",
    }
    context = {
        "report_id": report_id,
        "one_shot_analysis_context": report_context,
        "generation": generation,
    }
    return job_dict, context, prompt



def build_executive_summary_report_context(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    questions = questions_by_name(sdir)
    heldout_names = sorted({str(row.get("heldout_question")) for row in rows if row.get("heldout_question")})
    probability_rows = read_jsonl(probability_predictions_path(sdir))
    truth_path = sdir / "committed" / "truth_marginals.json"
    one_shot_payload = build_probability_report(probability_rows, read_json(truth_path, {})) if probability_rows and truth_path.exists() else {}
    one_shot_rows = [
        row
        for row in one_shot_payload.get("rows", [])
        if not heldout_names or str(row.get("question")) in set(heldout_names)
    ]
    twin_payload = build_twin_report(rows)
    attach_twin_set_descriptions(sdir, twin_payload, rows)
    run_manifests = read_twin_run_manifest(sdir)
    job_ids = sorted({str(row.get("job_id")) for row in rows if row.get("job_id")})
    diagnostics = twin_payload.get("diagnostics", {})
    compact_failures = []
    for row in (diagnostics.get("overconfident_misses", []) + diagnostics.get("worst_misses", []))[:12]:
        compact_failures.append(
            {
                "job_id": row.get("job_id"),
                "respondent_id": row.get("respondent_id"),
                "heldout_question": row.get("heldout_question"),
                "actual_answer": row.get("actual_answer"),
                "predicted_option": top_prediction(row.get("probabilities", {}))[0] if row.get("probabilities") else row.get("predicted_option"),
                "probability_actual": row.get("probability_actual"),
                "negative_log_likelihood": row.get("negative_log_likelihood"),
                "top1_correct": row.get("top1_correct"),
                "model": row.get("model_label") or model_label(row.get("service"), row.get("model")),
            }
        )
    question_summary = []
    for question, by_model in sorted(twin_payload.get("summary_by_question", {}).items()):
        question_summary.append(
            {
                "question": question,
                "question_text": questions.get(question, {}).get("question_text"),
                "models": by_model,
            }
        )
    marginal_comparisons = [
        {
            key: row.get(key)
            for key in [
                "heldout_question",
                "model_label",
                "rows",
                "l1",
                "js_divergence",
                "top_option_agrees",
                "predicted_top_option",
                "target_top_option",
                "largest_deltas",
            ]
        }
        for row in diagnostics.get("marginal_comparisons", [])[:40]
    ]
    source_filters = {
        "survey": args.survey,
        "job_id": getattr(args, "job_id", None),
        "jobs": getattr(args, "jobs", None),
        "prediction_model": getattr(args, "prediction_model", None),
        "question": getattr(args, "question", None),
        "questions": getattr(args, "questions", None),
    }
    compact_run_manifests = []
    for run in run_manifests:
        if run.get("job_id") not in set(job_ids):
            continue
        compact_run_manifests.append(
            {
                key: run.get(key)
                for key in [
                    "job_id",
                    "status",
                    "created_at",
                    "heldout_questions",
                    "models",
                    "scenario_count",
                    "prompt_variant",
                    "context_question_count",
                    "sample_respondents",
                    "seed",
                    "complete_cases",
                    "balance_actual",
                    "stratify_actual",
                    "include_agent_material",
                    "skipped_missing_heldout_count",
                    "issue_count",
                    "extracted_count",
                ]
            }
        )
    executive_diagnostics = {
        key: result.get(key)
        for key in [
            "rows",
            "questions",
            "metrics",
            "lift",
            "empirical_lift",
            "individual_signal",
            "pairwise_ordering",
            "spearman_rank_order",
            "artifacts",
        ]
    }
    return {
        "report_kind": "frontier_generated_executive_twin_validation",
        "survey": args.survey,
        "survey_summary": survey_summary(args.survey),
        "survey_context": context_path(sdir).read_text().strip() if context_path(sdir).exists() else "",
        "source_filters": source_filters,
        "filters": {
            "job_ids": job_ids,
            "model": getattr(args, "prediction_model", None),
            "questions": heldout_names,
        },
        "heldout_questions": [
            {
                "question_name": name,
                "question_text": questions.get(name, {}).get("question_text"),
                "question_options": questions.get(name, {}).get("question_options", []),
            }
            for name in heldout_names
        ],
        "executive_diagnostics": executive_diagnostics,
        "twin_validation": {
            "summary": twin_payload.get("summary", {}),
            "summary_by_question": question_summary,
            "baseline_comparison": diagnostics.get("baseline_comparison", {}),
            "marginal_comparisons": marginal_comparisons,
            "calibration": diagnostics.get("calibration", {}),
            "failure_examples": compact_failures,
            "row_count": len(rows),
        },
        "twin_specific_diagnostics": {
            "joint_structure": diagnostics.get("joint_structure", {}),
            "subgroup_marginals": diagnostics.get("subgroup_marginals", {}),
            "conditional_consistency": diagnostics.get("conditional_consistency", {}),
            "note": "These diagnostics test capabilities that one-shot aggregate marginals cannot provide: crosstab recovery, subgroup slicing, and conditional coherence across respondent-level answers.",
        },
        "one_shot_no_persona_baseline": {
            "available": bool(one_shot_rows),
            "summary": one_shot_payload.get("summary", {}),
            "heldout_question_rows": one_shot_rows,
            "note": "This is the deployable no-persona / one-shot marginal baseline for aggregate distributions. It is not a substitute for testing twin-specific claims such as joint structure, subgroup slicing, counterfactual consistency, or reusable individual state.",
        },
        "run_manifests": compact_run_manifests,
        "context_policy_warning": (
            "Audit whether held-out prompts included strong correlates of the target. If strong correlates were present and the permutation test is null, treat that as a prompt/model conditioning problem to debug before scaling."
        ),
        "ranking_sample_warning": (
            "Treat pairwise ordering and Spearman estimates as preliminary when they are based on fewer than ten held-out questions or only a few option pairs."
        ),
        "context_size_policy": {
            "raw_prediction_rows_included": False,
            "full_diagnostics_included": False,
            "failure_examples_cap": len(compact_failures),
            "marginal_comparisons_cap": len(marginal_comparisons),
        },
    }


def build_executive_summary_report_prompt(report_context: dict[str, Any]) -> str:
    return f"""You are writing the executive interpretation for a survey digital twin validation report.

Use the recorded Expected Parrot diagnostics below. Do not invent data. The report body must be evidence-aware, but the executive summary must be written for non-technical decision makers. Avoid leading with terms such as "permutation test," "marginals," "NLL," "Brier," or "calibration." Use those terms only in the evidence section, and translate them into plain business meaning when they are necessary. Do not make claims contradicted by the diagnostics or baselines.

Write Markdown only. Do not include a top-level title. Use these sections:

## Executive Summary
Give the decision-facing bottom line in plain language. State what the twins are useful for now, what they are not ready for, and the most important caveat. Do not lead with statistical test names or metric acronyms. If respondent-specific matching was not demonstrated, say that in ordinary language, for example: "The twins captured broad response patterns, but did not reliably identify which specific respondent would choose which answer."

## Reasonable Uses
Give concrete examples of reasonable uses supported by the evidence. Examples might include using twins to explore likely reactions to draft survey questions, compare broad message directions, prioritize themes for additional research, or generate hypotheses for follow-up. Tailor the examples to the survey context and the observed validation strength.

## Uses To Avoid
Give concrete examples of uses that are not supported by the evidence. Examples might include targeting individual respondents, replacing a real survey for exact population estimates, making high-stakes allocation decisions, or claiming precise subgroup effects when the validation did not establish that level of accuracy. Tailor the examples to the survey context and failure modes.

## What The Validation Shows
Interpret the main evidence in accessible language: sample size, number of held-out questions, whether twins beat random guessing, whether they added respondent-specific information, whether they preserved option ordering or directional ranking, and where errors were concentrated. Use technical labels such as permutation test, marginal baseline, pairwise ordering, Spearman/rank diagnostics, NLL, Brier, and calibration only when needed, and immediately explain what they mean for the decision.

## Baselines And What Personas Add
Compare twins to uniform and to any available no-persona / one-shot marginal baseline, but do not frame the one-shot marginal baseline as a full substitute for twins. It is a cheap aggregate-distribution benchmark only. Explain separately whether the validation found respondent-specific signal and whether the twin-specific diagnostics support joint structure, subgroup slicing, or conditional consistency.

## Twin-Specific Capabilities
Discuss crosstab/joint-structure recovery, subgroup marginal accuracy, and conditional consistency when those diagnostics are available. Explain why these capabilities matter: segmentation, driver analysis, arbitrary slicing after validation, persistent individual state, and simulated interventions. If any of these capabilities are not tested or are weakly tested, say so plainly.

## What This Validation Does Not Yet Test
Name the untested or only partially tested claims. Include counterfactual or intervention response, longitudinal or panel reuse, simulated interviews, and any joint/subgroup/conditional diagnostics that are missing or too sparse. Add a trust-matrix-style note that joint distributions / crosstabs are a distinct capability from aggregate marginals.

## Where To Trust It
Separate uses: exact estimates, ranking/prioritization, exploratory simulation, and respondent-level targeting. Be explicit about which are supported and which are not.

## Risks And Checks Before Scaling
Discuss small held-out-question count, confidence intervals or uncertainty, context-policy/leakage risks, and whether strong correlates were available in prompts. If strong correlates were available but the permutation result is null, identify that as a prompt/model conditioning issue to investigate.

## Recommendation
Give a concise operating recommendation and next validation step.

Critical interpretation rules:
- A within-question permutation p-value near 0.5 means the twins are not showing respondent-specific matching beyond aggregate/marginal structure.
- Good lift over uniform with a null permutation test supports aggregate opinion structure, not individual predictive power.
- Pairwise option-ordering accuracy and Spearman can support directional ranking, but label them preliminary when based on few held-out questions or few option pairs.
- Do not call the empirical marginal baseline deployable for genuinely new questions; it is an oracle diagnostic because it uses observed held-out answers.
- The deployable one-shot model marginal is an aggregate baseline, not a full replacement for respondent-level twins.
- Joint distributions, subgroup slices, conditional consistency, counterfactuals, and reusable individual state are twin-specific claims. Discuss them separately from aggregate marginal prediction.
- The Reasonable Uses and Uses To Avoid sections must contain specific examples, not generic categories.
- Do not use the internal tool name in the report prose.

Recorded report context:

{json.dumps(report_context, indent=2)}
"""


def build_edsl_executive_summary_report_job_dict(
    args: argparse.Namespace,
    report_context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    prompt = build_executive_summary_report_prompt(report_context)
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = load_edsl_job_classes()
    question_name = "executive_summary_markdown"
    question = QuestionFreeText(question_name=question_name, question_text=prompt)
    model_params = parse_model_params(args)
    model_specs = parse_model_specs(args)
    job = Jobs(
        survey=Survey(questions=[question]),
        scenarios=ScenarioList([Scenario({})]),
        models=ModelList(
            [
                Model(
                    model_name=model_name,
                    service_name=service_name,
                    **model_kwargs_for(model_name, service_name, model_params),
                )
                for model_name, service_name in model_specs
            ]
        ),
    )
    job_dict = job.to_dict()
    report_id = practitioner_report_id_from_job(job_dict)
    job_dict["zwill"] = {
        **job_dict.get("zwill", {}),
        "practitioner_report_id": report_id,
        "practitioner_report_question_name": question_name,
        "report_kind": "executive_twin_validation",
    }
    generation = {
        "mode": "job_exported",
        "report_id": report_id,
        "model": model_label(model_specs[0][1], model_specs[0][0]) if model_specs else None,
        "models": [model_label(service_name, model_name) for model_name, service_name in model_specs],
        "report_kind": "executive_twin_validation",
    }
    context = {
        "report_id": report_id,
        "benchmark_payload": report_context.get("executive_diagnostics", {}),
        "executive_report_context": report_context,
        "generation": generation,
    }
    return job_dict, context, prompt

