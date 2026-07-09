from __future__ import annotations

from .cli import *  # noqa: F403


def compact_twin_specific_diagnostics_for_report(
    diagnostics: dict[str, Any],
    *,
    row_limit: int = 24,
) -> dict[str, Any]:
    def compact_distribution(values: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
        if not isinstance(values, dict):
            return {}
        ranked = sorted(values.items(), key=lambda item: float(item[1] or 0.0), reverse=True)[:limit]
        return {str(key): value for key, value in ranked}

    def compact_rows(payload: dict[str, Any], keep_keys: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        rows = payload.get("rows") or []
        compact_rows = []
        for row in rows[:row_limit]:
            if not isinstance(row, dict):
                continue
            item = {key: row.get(key) for key in keep_keys if key in row}
            if "empirical" in row:
                item["empirical_top_options"] = compact_distribution(row.get("empirical") or {})
            if "twin_implied" in row:
                item["twin_implied_top_options"] = compact_distribution(row.get("twin_implied") or {})
            compact_rows.append(item)
        return {
            key: value
            for key, value in {
                "min_pair_rows": payload.get("min_pair_rows"),
                "min_cell_rows": payload.get("min_cell_rows"),
                "segment_questions_considered": payload.get("segment_questions_considered"),
                "pair_count": payload.get("pair_count"),
                "cell_count": payload.get("cell_count"),
                "rows": compact_rows,
                "included_row_count": len(compact_rows),
                "omitted_count": max(0, int(payload.get("omitted_count") or 0) + max(0, len(rows) - len(compact_rows))),
                "note": payload.get("note"),
            }.items()
            if value is not None
        }

    joint_keys = (
        "model_label",
        "left_question",
        "left_question_text",
        "right_question",
        "right_question_text",
        "respondents",
        "joint_l1",
        "empirical_cramers_v",
        "twin_cramers_v",
        "cramers_v_error",
        "warning",
    )
    subgroup_keys = (
        "model_label",
        "heldout_question",
        "heldout_question_text",
        "segment_question",
        "segment_question_text",
        "segment_value",
        "rows",
        "l1",
        "js",
        "warning",
    )
    conditional_keys = (
        "model_label",
        "condition_question",
        "condition_question_text",
        "condition_value",
        "target_question",
        "target_question_text",
        "rows",
        "l1",
        "js",
        "warning",
    )
    return {
        "joint_structure": compact_rows(diagnostics.get("joint_structure", {}), joint_keys),
        "subgroup_marginals": compact_rows(diagnostics.get("subgroup_marginals", {}), subgroup_keys),
        "conditional_consistency": compact_rows(diagnostics.get("conditional_consistency", {}), conditional_keys),
        "note": "These compact diagnostics test capabilities that one-shot aggregate marginals cannot provide: crosstab recovery, subgroup slicing, and conditional coherence across respondent-level answers. Full rows stay in deterministic JSON artifacts, not the report-writing prompt.",
    }


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
            # Fingerprint of the twin model set this narrative describes, so a
            # rebuild can detect when the prose predates an added/removed model.
            # Baseline rows are excluded so the fingerprint compares twin-to-twin.
            "model_labels": sorted(
                {
                    str(row.get("model_label"))
                    for row in rows
                    if row.get("model_label") and not str(row.get("model_label")).startswith("baseline:")
                }
            ),
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
        "twin_specific_diagnostics": compact_twin_specific_diagnostics_for_report(diagnostics),
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
            "twin_specific_rows_compacted": True,
            "twin_specific_row_limit_per_section": 24,
            "failure_examples_cap": len(compact_failures),
            "marginal_comparisons_cap": len(marginal_comparisons),
        },
    }


def build_executive_summary_report_prompt(report_context: dict[str, Any]) -> str:
    return f"""You are writing the executive interpretation for a survey digital twin validation report.

Use the recorded diagnostics below. Do not invent data. Write for three audiences by separating the report clearly:
- decision makers get a short, shareable executive version first;
- researchers/product users get practical allowed/not-allowed uses and next steps;
- technical auditors get metrics, baselines, and failure cases in appendices.

The report must be decision-first. Avoid leading with terms such as "permutation test," "marginals," "NLL," "Brier," or "calibration." Use technical terms only when they are needed, and translate them into plain business meaning. Do not make claims contradicted by the diagnostics or baselines.

Write Markdown only. Do not include a top-level title. Use these sections:

## Executive Summary
Write a short shareable version, suitable for pasting into an email or slide. Do not open with "Yes," or frame the first sentence as if answering a directly asked question. Start with a report-style sentence that names what the validation evaluated and gives the operating recommendation, for example: "This validation evaluated whether respondent-context digital twins can support exploratory research for this survey." It must cover these questions in the first 8-12 sentences:
- Can we use digital twins here?
- For what uses?
- For what not?
- Which model should we use?
- What validation should come next?

Use this decision framing unless contradicted by the supplied diagnostics: digital twins are useful for exploratory and directional research, especially with the best-calibrated model; they are not yet reliable enough for exact estimates, precise subgroup claims, high-stakes decisions, or individual-level targeting.

Do not make the default recommendation sound like "only a limited exploratory pilot" unless the diagnostics are weak. If the organization needs to make a decision now, frame twins as decision-support evidence with an explicit weight and guardrails: useful for direction, option ranking, objection discovery, and deciding where to investigate or act first; not sufficient as the sole basis for irreversible, high-stakes, or person-specific decisions. Distinguish "usable now with guardrails" from "validated as a replacement for fielded research."

Include a compact callout in prose:
**Bottom line:** proceed with a limited exploratory pilot if the diagnostics support it; do not treat the twins as a substitute for a fielded survey or as reliable predictions for specific people.

## What Digital Twins Are
Define digital twins in plain English. Explain that a digital twin is a model-conditioned representation of a respondent built from available context, then asked to predict held-out answers. Clarify that "digital twin," "persona," and "respondent-level simulation" refer to the same broad idea in this report. Define the tested twin condition with a stable label such as "Respondent-context twins"; do not use ambiguous labels like "baseline context model" without explaining them.

Also define the comparison groups once:
- Uniform random: a minimum sanity check.
- Empirical marginal / oracle: a diagnostic benchmark that uses the true held-out answer distribution and is not available for genuinely new questions.
- No-persona / one-shot baseline: a practical aggregate benchmark where the model predicts broad response distributions without respondent-level twins.

## What We Tested
Briefly describe the validation design:
- source survey topic and available population size;
- validation respondent count;
- number of held-out questions;
- models tested;
- method: hide selected answers, ask twins to predict them, compare predictions to actual responses.

Do not put dense metric tables here.

## How To Interpret This Validation
Separate three goals:
1. Individual prediction: can the twin predict what a specific respondent answered?
2. Aggregate distribution prediction: can the system estimate broad response patterns?
3. Directional ranking: can the system identify which answer choices, themes, or question areas are likely higher or lower?

Explain that individual prediction is the hardest bar; aggregate and ranking performance may be enough for exploratory research; the right baseline depends on the use case.

## Bottom-Line Findings
Use one concise table with columns: Finding, Result, Interpretation. Include only the highest-signal metrics:
- exact-answer prediction;
- probability assigned to actual answer;
- respondent-specific signal versus broad answer popularity;
- directional ranking / pairwise ordering;
- model reliability;
- Gemini or other model risk if applicable.

Translate each metric into a practical judgment. Do not list every available metric in the main body.

## What The Twins Are Useful For Now
Use a table with columns: Use, Why It Is Reasonable, Guardrail. Keep it crisp. Recommended uses should include:
- draft survey testing;
- question and answer-choice refinement;
- theme prioritization;
- message or concept comparison;
- hypothesis generation;
- early directional ranking.

Avoid long survey-specific bullet lists. Use this survey's topics only as brief examples.

## What The Twins Should Not Be Used For
Use a table with columns: Do Not Use For, Why Not, Safer Alternative. Group the warnings:
- no individual-level decisions or targeting;
- no final population estimates;
- no precise subgroup claims without subgroup validation;
- no high-stakes decisions;
- no uncalibrated or overconfident model use.

State plainly: the validation supports exploratory use; it does not support treating a digital twin as a reliable substitute for a specific person's answer.

## Model Comparison
Use a two-row table comparing the tested models. Explain which model should be used now and which should be excluded, recalibrated, or retested. Discuss calibration, overconfident misses, and practical reliability. If GPT-5.5 is best supported by the diagnostics, say that clearly.

## Where Performance Was Stronger And Weaker
Use a concise synthesis table with columns: Question Area, Signal, Recommended Treatment. Do not dump every per-question distribution. Mention specific areas only when supported by the context. Use treatments such as "directionally useful," "use with caveats," "validate with humans," and "do not use for subgroup precision."

## What Personas Add Beyond Simpler Baselines
This is a core section. Explain:
- Random guessing is only a sanity check.
- The empirical marginal baseline is an oracle diagnostic.
- The no-persona baseline is the practical comparison for broad aggregate estimates.
- Personas are valuable only if they add respondent-specific lift or support workflows that marginals cannot, such as slicing, crosstabs, persistent respondent state, or simulated follow-up.

Use this synthesis unless contradicted by the diagnostics: the strongest evidence for personas is not beating random; it is modest respondent-specific lift beyond broad answer popularity plus useful directional ordering.

## Twin-Specific Capabilities
Use a compact table with columns: Capability, Why It Matters, Evidence In This Run, Current Status. Discuss crosstab/joint-structure recovery, subgroup marginal accuracy, and conditional consistency when diagnostics are available. Explain why these capabilities matter: segmentation, driver analysis, arbitrary slicing after validation, persistent individual state, and simulated interventions. If any capability is untested, sparse, or weak, say so plainly.

## Next Steps
This must be the clearest operational section. Use a table with columns: Step, Purpose, Copy/Paste Prompt Or Command, Success Criterion.

Include concrete prompts or commands the user can run next. If exact commands are known from the context, include them. Otherwise provide command templates with placeholders. Include at least:
- expanded held-out validation with more questions/folds;
- uncertainty intervals or bootstrap intervals;
- direct no-persona baseline comparison for intended aggregate use;
- leakage and allowed-correlate audit;
- calibration check or model exclusion/recalibration for overconfident models;
- subgroup/crosstab validation review.

Prompts should be phrased so a researcher can paste them into a planning document or ask an analyst/model to run them. Example style:
`Prompt: Review the held-out validation plan and identify all variables that could leak the target answer or act as downstream consequences of the target. Return a leakage table with allowed, excluded, and ambiguous fields.`

## Risks And Required Checks Before Scaling
Use a checklist table with columns: Check, Why It Matters, Required Evidence. Cover the held-out-question count, uncertainty intervals, repeated folds, no-persona baseline comparison, prompt leakage, permitted correlates, subgroup coverage, malformed responses, and calibration controls. Keep this separate from the operating recommendation.

## Recommendation
Give exactly one concise operating recommendation, not multiple scattered recommendations. It should be 1-2 paragraphs. It must state:
- proceed / do not proceed / proceed only as a limited pilot;
- the preferred model;
- approved use cases;
- prohibited use cases;
- the next validation gate before broader use.

## Appendix A: Detailed Metrics
Move detailed metrics here: accuracy, p(actual), NLL, Brier, ECE/calibration, L1, JS, pairwise ordering, rank correlation, confidence gap, and lift versus baselines. Define each metric briefly.

## Appendix B: Question-Level Results
Summarize question-level results in a table. Do not include full raw distributions unless the context already provides a compact version. Use columns: Question Area, Stronger/Weaker Signal, Model Notes, Recommended Treatment.

## Appendix C: Failure Cases And Overconfident Misses
Use capped examples only. Explain that these are diagnostic failures, not anecdotes to overgeneralize from. Include model attribution.

## Appendix D: Supporting Artifacts
List the available artifacts and what each is for: lift histograms, permutation JSON/CSV, rank-order diagnostics, pairwise ordering CSV, twin run audit, twin comparison page, crosstab/subgroup/conditional diagnostics when present.

Critical interpretation rules:
- A within-question permutation p-value near 0.5 means the twins are not showing respondent-specific matching beyond aggregate/marginal structure.
- Good lift over uniform with a null permutation test supports aggregate opinion structure, not individual predictive power.
- Pairwise option-ordering accuracy and Spearman can support directional ranking, but label them preliminary when based on few held-out questions or few option pairs.
- Do not call the empirical marginal baseline deployable for genuinely new questions; it is an oracle diagnostic because it uses observed held-out answers.
- The deployable one-shot model marginal is an aggregate baseline, not a full replacement for respondent-level twins.
- Joint distributions, subgroup slices, conditional consistency, counterfactuals, and reusable individual state are twin-specific claims. Discuss them separately from aggregate marginal prediction.
- The useful/not-useful sections must use reusable categories with brief examples from this survey only when they clarify the category.
- State each major recommendation once. Do not create separate competing recommendation sections.
- Prefer callouts and tables over undifferentiated bullet lists.
- Put metric detail in appendices unless it directly changes the decision.
- Do not use the internal tool name in the report prose.

Recorded report context:

{json.dumps(report_context, indent=2)}
"""


EXECUTIVE_REPORT_SECTIONS = [
    {
        "question_name": "executive_decision_markdown",
        "title": "Executive Decision Guidance",
        "context_keys": [
            "survey",
            "survey_summary",
            "filters",
            "heldout_questions",
            "executive_diagnostics",
            "twin_validation",
            "twin_specific_diagnostics",
            "one_shot_no_persona_baseline",
            "context_policy_warning",
            "ranking_sample_warning",
        ],
        "sections": [
            "Executive Summary",
            "What Digital Twins Are",
            "Decision Guidance",
            "What The Twins Are Useful For Now",
            "What The Twins Should Not Be Used For",
        ],
        "instructions": (
            "Write the decision-facing front matter. Do not open with 'Yes,'. Do not default to 'only a limited exploratory pilot' "
            "unless the evidence is weak. If a decision must be made now, explain how to use twins as one source of decision-support "
            "evidence with guardrails and how much weight to give them. Keep the tone constructive, commercially useful, and evidence-aware."
        ),
    },
    {
        "question_name": "validation_evidence_markdown",
        "title": "Validation Evidence",
        "context_keys": [
            "survey",
            "survey_summary",
            "source_filters",
            "heldout_questions",
            "executive_diagnostics",
            "twin_validation",
            "twin_specific_diagnostics",
            "one_shot_no_persona_baseline",
            "run_manifests",
        ],
        "sections": [
            "What We Tested",
            "How To Interpret This Validation",
            "Bottom-Line Findings",
            "Model Comparison",
            "Where Performance Was Stronger And Weaker",
            "What Personas Add Beyond Simpler Baselines",
            "Twin-Specific Capabilities",
        ],
        "instructions": (
            "Write the evidence section. Use tables for findings and model comparison. Translate metrics into practical meaning. "
            "Avoid repeating the operating recommendation from the executive section."
        ),
    },
    {
        "question_name": "next_steps_appendix_markdown",
        "title": "Next Steps And Appendices",
        "context_keys": [
            "survey",
            "executive_diagnostics",
            "twin_validation",
            "twin_specific_diagnostics",
            "one_shot_no_persona_baseline",
            "context_policy_warning",
            "ranking_sample_warning",
        ],
        "sections": [
            "Next Steps",
            "Risks And Required Checks Before Scaling",
            "Appendix A: Detailed Metrics",
            "Appendix B: Question-Level Results",
            "Appendix C: Failure Cases And Overconfident Misses",
            "Appendix D: Supporting Artifacts",
        ],
        "instructions": (
            "Write the operational next steps and appendices. Include copy/paste prompts or commands where useful. "
            "Do not create another Recommendation section; the recommendation belongs in the executive decision guidance."
        ),
    },
]


def executive_report_section_context(report_context: dict[str, Any], context_keys: list[str]) -> dict[str, Any]:
    return {key: report_context.get(key) for key in context_keys if key in report_context}


def build_executive_summary_report_section_prompt(
    report_context: dict[str, Any],
    section: dict[str, Any],
) -> str:
    section_context = executive_report_section_context(report_context, section["context_keys"])
    sections = "\n".join(f"- ## {name}" for name in section["sections"])
    return f"""You are writing one section group for a survey digital twin validation report.

Write Markdown only. Do not include a top-level title. Write only these sections, in this order:
{sections}

Section group: {section["title"]}

Instructions:
{section["instructions"]}

Global interpretation rules:
- Do not invent data or claims not supported by the supplied context.
- Avoid leading with technical terms; translate metrics into practical meaning.
- Uniform random is a sanity check, not the standard for product value.
- The empirical marginal baseline is an oracle diagnostic, not deployable for genuinely new questions.
- The no-persona one-shot baseline is the practical aggregate benchmark.
- Respondent-context twins are most valuable when respondent-level state, slicing, crosstabs, simulated follow-up, or respondent-specific lift matter.
- Do not present twins as reliable predictions for specific people unless the supplied diagnostics clearly support that claim.

Recorded context for this section:

{json.dumps(section_context, indent=2)}
"""


def build_executive_summary_report_section_prompts(report_context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "question_name": section["question_name"],
            "title": section["title"],
            "prompt": build_executive_summary_report_section_prompt(report_context, section),
        }
        for section in EXECUTIVE_REPORT_SECTIONS
    ]


def build_edsl_executive_summary_report_job_dict(
    args: argparse.Namespace,
    report_context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    section_prompts = build_executive_summary_report_section_prompts(report_context)
    prompt = "\n\n---\n\n".join(f"# {section['title']}\n\n{section['prompt']}" for section in section_prompts)
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = load_edsl_job_classes()
    question_names = [section["question_name"] for section in section_prompts]
    questions = [
        QuestionFreeText(question_name=section["question_name"], question_text=section["prompt"])
        for section in section_prompts
    ]
    model_params = parse_model_params(args)
    model_specs = parse_model_specs(args)
    job = Jobs(
        survey=Survey(questions=questions),
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
        "practitioner_report_question_name": question_names[0],
        "practitioner_report_question_names": question_names,
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
        "section_questions": [
            {"question_name": section["question_name"], "title": section["title"]}
            for section in section_prompts
        ],
    }
    return job_dict, context, prompt
