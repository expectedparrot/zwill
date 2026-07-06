# Agent Instructions

## Survey Codebooks

Always expand coded survey values through available codebooks before importing or exporting survey data.

- Use human-readable response labels as canonical `question_options`.
- Store answer values using those same human-readable labels, not numeric or abbreviated source codes.
- Preserve original source codes as provenance, such as in `source.note`, raw files, or codebook metadata.
- Do not skip codebook expansion for performance reasons. Correct labels are more important than speed because coded options create ambiguous EDSL surveys and can hide validation errors.
- If a code has no known label, quarantine the affected row or mark the question/import as incomplete rather than silently treating the raw code as a label.

## Ingestion Guide

When adding or modifying ingestion scripts, treat ingestion as a validation boundary rather than a best-effort file conversion.

- Do not hide exceptions. Catch only expected exception classes at the per-survey or per-file boundary, record the failing source path and error in a manifest, and return a non-zero exit code when failures are present.
- Keep conversion and import as separable phases. A `--convert-only` or equivalent dry phase should write JSONL imports and a manifest before any `.zwill` state is mutated.
- Write an import manifest with source paths, survey counts, question/respondent/answer counts, issue counts, and paths to full issue logs.
- Cap issue examples in stdout or manifests, but preserve full issue detail in a sidecar JSONL file. Large repeated issue lists make tool output unusable.
- Validate every source item against its respondent column. If no column can be found for a metadata item, fail that survey conversion clearly.
- Accept raw source codes only through an explicit codebook map. If a normalized source already stores human-readable labels, accept them only when they exactly match a known option label.
- Treat blank values and declared missing-code values as missing answers with explicit `missing_code`; do not turn missingness into a regular response option.
- Preserve provenance by adding raw metadata/respondent files with `zwill raw add`, carrying source variable names in `source.note`, and keeping raw source paths in the manifest.
- Use stable, filesystem-safe survey and question identifiers, but keep source variable names in provenance so mappings are auditable.
- After import, run `zwill commit` and `zwill status` from the generated workdir. A successful ingestion should have zero open quarantine issues and committed truth marginals for each imported survey.

## Digital Twin Evaluation Workflow

A user may arrive with a survey workbook, survey exports, CRM data, transaction logs, product usage records, or other respondent/customer-level data and ask whether it can be used to build digital twins. Treat this as a validation study, not just a file conversion.

### Typical Survey Microdata Workflow

When starting from survey respondent-level microdata, follow this default sequence unless the user gives a different plan.

1. Get the survey microdata and any codebooks, data dictionaries, workbook sheets, raw exports, or respondent metadata needed to interpret it.
2. Ingest and validate the data using the codebook and ingestion rules above. Expand coded values to human-readable labels before creating EDSL surveys, AgentLists, probability jobs, or twin jobs.
3. Generate the initial report bundle with `zwill report build --survey <survey> --path <survey>_report/` or the staged target `zwill report facts --survey <survey> --path <survey>_report/`. Use `index.html`, `stage-manifest.json`, `facts/`, and `survey-profile.html` to inspect question text, response options, missingness, respondent counts, free-text samples, and data-quality issues.
4. Ask the user to review the survey/profile report before continuing. Confirm that the imported questions, labels, respondent population, skip/missing handling, and obvious distributions look right.
5. After the user confirms the survey import looks right, ask whether to get one-shot marginal predictions for all eligible questions from frontier models. Make clear this may require model/API spend.
6. Export, run, import, and report one-shot marginal probability jobs. Compare predicted one-shot marginals to committed empirical marginals by rebuilding the report bundle with `zwill report build --survey <survey> --path <survey>_report/`; then export a generated one-shot analysis with `zwill prob-results analysis-export --survey <survey> --path <survey>_report/one-shot-marginals.html`, run it, import it with `zwill prob-results analysis-import`, and render it with `zwill prob-results analysis-render`.
7. Summarize one-shot-only performance by question and question family from the generated one-shot analysis. The report-writing prompt should explain where frontier models can already predict aggregate marginals, where they fail, whether one-shot marginals are useful as baselines or calibration material for later twin work, and that this validates aggregate marginal prediction rather than respondent-level matching.
8. Draft a digital twin validation plan before exporting or running twin jobs. The plan must specify held-out questions/outcomes, construction approaches, context policy, leakage exclusions, respondent sample, models, seed, prediction count (`respondents x held-out questions x approaches x models`), and cost/time notes when available.
9. Ask the user to approve or edit the validation plan. Use `zwill twin-experiment approve --path <plan.json>` only after explicit user approval. Do not silently choose held-out questions and run jobs.
10. Only then move into digital twin held-out validation, AgentList construction, or executive reporting.

- First ingest and validate the source data using the codebook and ingestion rules above. Preserve raw files as provenance and make response labels human-readable before constructing any EDSL survey, AgentList, probability job, or twin job.
- Convert non-survey outcomes into question-like targets when needed. For example, churn, renewal, product choice, next purchase category, referral count, or feature adoption should become stable held-out questions with explicit answer options or bins.
- Identify held-out questions or outcomes that are already observed. These are the validation targets. The twin prompt must not include the held-out answer, summaries that reveal it, downstream variables caused by it, or empirical marginals for that target unless the user explicitly requests an oracle/leakage experiment. For target-specific downstream variables, pass repeatable exclusions as `--leakage-exclusion <heldout_question>:<context_question>` when exporting or running twin jobs.
- Choose context fields that would realistically be available for future prediction: prior survey answers, demographics, account metadata, transactional summaries, product usage summaries, support history, or vetted qualitative notes.
- Start with a survey/profile report before twin work. Prefer the report bundle entry point: `zwill report build --survey <survey> --path <survey>_report/`, or use staged targets `zwill report facts`, `zwill report analyze`, and `zwill report render` when you want Makefile-like gates. For a single standalone survey page, use `zwill survey report --survey <survey> --format html --path <survey>_survey_report.html`.
- Use one-shot probability jobs as aggregate baselines when helpful, then import and report them with `zwill prob-results report`. For the interpretive analysis section, use the frontier-model report flow: `zwill prob-results analysis-export`, `zwill edsl-run`, `zwill prob-results analysis-import`, and `zwill prob-results analysis-render`. Do not bake the one-shot interpretation into deterministic templates.
- Export, run, and import digital twin probability jobs for the held-out targets only from an approved plan. `zwill twin-experiment export-plan`, `zwill twin-study run`, `zwill twin-study export-holdout`, and `zwill edsl-export --target twin-probability-job` require an approved plan unless `--allow-unapproved` is passed for an explicit ad hoc/debug/leakage experiment. For debugging, use a small approved sample; for final validation, prefer full-sample scoring when cost allows.
- Always audit at least one imported twin job with `zwill twin-results run-report`. Check construction metadata, held-out questions, prompt template, rendered user prompts, scenario inputs, twin identity, raw model responses, and import issues.
- Score twins with `zwill twin-results report --view summary`. Compare against uniform and empirical-marginal baselines, and inspect accuracy, p(actual), NLL, Brier, calibration/ECE, marginal L1/JS, overconfident misses, and question-level failures.
- For executive-facing validation, do not rely on deterministic prose templates. First compute the diagnostics with the report bundle, `zwill report analyze`, or `zwill twin-results executive-summary`, then generate the decision-facing interpretation with a frontier-model report job: `zwill twin-results executive-summary-export --survey <survey> --job-id <job_id> --path <survey>_executive_summary.html`, `zwill edsl-run --job <job.edsl.json> --path <results.json.gz>`, `zwill twin-results executive-summary-import --report-id <report_id> --path <results.json.gz>`, and `zwill twin-results executive-summary-render --report-id <report_id> --path <survey>_executive_summary.html`. The generated report prompt must include compact summary statistics, per-question tables, aggregate diagnostics, capped illustrative failures, the uniform baseline, no-persona/one-shot baseline when available, empirical-oracle diagnostics, p(actual) lift distribution, within-question permutation test for individual predictive power, pairwise option-ordering accuracy, rank-order diagnostics, held-out question count, failure modes, context-policy/leakage risks, and operating recommendation. Do not dump every respondent-level prediction row, full prompt text, or full raw diagnostics into report-writing context. If the permutation test is null, the executive summary must say the evidence supports aggregate opinion structure rather than individual predictive power. Use `zwill report render --final` as the gate before circulating; it must block until generated executive analysis is available.
- Compare multiple construction approaches with `zwill twin-results compare-report` or the `twin-experiment` workflow. Prefer clear approach differences, such as survey-only context versus survey plus transaction summaries, rather than many opaque prompt tweaks.
- Use `zwill report list --survey <survey>` to show the user which reports are ready and what commands to run next.
- Produce a final report or readout that explains the data used, held-out design, twin construction approaches, baselines, performance by target family, calibration risks, examples of wins/failures, and a practical recommendation: use now, use only for exploratory work, or collect better validation data first.
- Do not present digital twin results as validated for new production decisions unless the held-out evaluation supports that use case. Be explicit about leakage risks, population coverage, subgroup failures, and whether the empirical marginal baseline is an oracle only available because the target was already observed.
