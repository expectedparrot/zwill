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

### Agent Operating Rules

- Do not start with twin generation. Start by proving that the observed data are correctly imported, labeled, committed, and inspectable.
- Default to pushing the validation through to usable outputs. After the required human review/approval gates are satisfied, do not stop at survey import, job export, or raw model results. Continue through one-shot baselines, approved twin runs, imports, scoring, audit reports, comparison bundles, dashboards, generated executive/practitioner reports, and a concise readout.
- Do not claim that a digital twin is useful because it produced plausible prose or plausible probabilities. Score against held-out observed answers.
- Do not use held-out answers, empirical marginals for the held-out target, downstream variables, or summaries that reveal the target in the twin prompt unless the user explicitly approves an oracle/leakage experiment.
- Do not run costly EDSL jobs without first showing the user the plan, prediction count, model list, and likely cost/time implications.
- Run EDSL model jobs only through remote Expected Parrot inference. Before any `zwill edsl-run`, verify the run environment is configured for the remote EP profile/key, and do not fall back to local inference or direct local provider calls. If remote execution cannot be verified or fails because credentials/profile are missing, stop and report the blocker with the exact resume command instead of trying a local run.
- Prefer a small approved debug sample before a full run. Debug samples must still use the same held-out policy and leakage exclusions as the intended validation.
- For Gemini or other providers prone to verbose/truncated JSON, set a generous output-token cap before running probability or twin-probability jobs. Prefer at least `maxOutputTokens=8192` for Gemini twin validation jobs unless a smaller cap is explicitly justified, and keep probability prompts/output formats terse enough that `probabilities` cannot be lost behind long notes.
- After exporting a plan, compare the approved prediction estimate with the exported manifest's actual scenario count and model count before running. If complete-case filtering, missing actuals, stratification, provider limits, or any other export-time behavior materially changes the sample size or prediction count, stop and update/re-approve the plan instead of running the stale export.
- Keep artifacts reproducible: save import manifests, approved plans, exported jobs, raw Results objects, imported reports, bundle manifests, generated-report prompts, and rendered HTML outputs.
- Use `--allow-unapproved` only when the user explicitly asks for an ad hoc/debug/leakage run. Mention that results from that path are not an approved validation.

### Push-Through Completion Standard

For digital twin work, "done" means a decision-ready validation bundle exists, not merely that jobs were exported or run. Unless the user explicitly asks to stop earlier, push through this sequence:

1. import and validate source data;
2. build the survey/profile report and get user confirmation;
3. run one-shot predictions for every eligible question when approved;
4. import one-shot results, rebuild reports, and generate the one-shot analysis report;
5. draft, get approval for, and export the twin validation plan;
6. run/import approved twin jobs for all eligible held-out targets or the approved staged subset;
7. audit at least one run;
8. score against uniform and empirical-marginal baselines;
9. compare construction approaches when more than one exists;
10. build the report bundle, comparison bundle, plots, microdata audit, and dashboard;
11. run/import/render generated executive or practitioner interpretation;
12. give the user a final readout with links/paths, metrics, failure modes, and an operating recommendation.

If cost, missing credentials, provider failures, malformed responses, or user approval blocks a step, stop at the blocking gate, record what is complete, and give the exact next command(s) needed to resume.

### End-to-End Twin Validation Playbook

Use this playbook when the user asks whether data can support digital twins or asks to build and assess twins.

1. **Create an isolated workdir and project.**

   ```bash
   mkdir -p workdirs/<study_id>
   cd workdirs/<study_id>
   zwill init
   zwill project create <study_id> --use
   ```

2. **Convert source files into normalized JSONL without mutating `.zwill` first.**

   The conversion phase should write:

   ```text
   imports/<survey>/questions.jsonl
   imports/<survey>/respondents.jsonl
   imports/<survey>/answers.jsonl
   imports/<survey>/manifest.json
   imports/<survey>/issues.jsonl
   ```

   The manifest must include source paths, codebooks used, row counts, question/respondent/answer counts, issue counts, and full issue-log paths. If the source contains coded values, expand them through codebooks before writing `question_options` or `answer` values.

3. **Import, preserve provenance, and commit truth.**

   ```bash
   zwill survey create --name <survey>
   zwill raw add --survey <survey> --id source_data --path <raw_data_path> --kind source_data --title "Original source data"
   zwill raw add --survey <survey> --id codebook --path <codebook_path> --kind codebook --title "Source codebook"
   zwill question import --survey <survey> --path imports/<survey>/questions.jsonl
   zwill respondent import --survey <survey> --path imports/<survey>/respondents.jsonl
   zwill answer import --survey <survey> --path imports/<survey>/answers.jsonl
   zwill quarantine list --survey <survey>
   zwill commit --survey <survey>
   zwill status
   ```

   Stop if there are open quarantine issues, unknown labels, missing respondent mappings, or unexpanded coded values. Fix the conversion rather than patching the final `.zwill` state by hand.

4. **Build the survey/profile report and ask for human review.**

   ```bash
   zwill report build --survey <survey> --path <survey>_report/
   zwill report list --survey <survey>
   ```

   Review `index.html`, `survey-profile.html`, `stage-manifest.json`, and files under `facts/`. Ask the user to confirm that question text, option labels, respondent population, missingness, skip handling, free-text treatment, and obvious distributions look right before running model jobs.

5. **Optionally establish one-shot aggregate baselines.**

   Use this when the user wants a baseline for whether frontier models understand the aggregate survey distribution without respondent context. Default to exporting one-shot predictions for every eligible closed-ended survey question unless the user limits scope or cost requires a staged run. Eligible questions generally have committed empirical marginals, explicit answer options, enough observed responses to score, and no unresolved import/quarantine issues.

   Before export, produce an eligibility inventory:

   ```bash
   zwill report build --survey <survey> --path <survey>_report/
   zwill report list --survey <survey>
   ```

   Inspect `survey-profile.html`, `facts/`, and `stage-manifest.json`. Record which questions are included, excluded, and why. Exclude open text, malformed option sets, questions with too few observed answers for meaningful scoring, and items whose labels or missingness are not yet validated.

   ```bash
   zwill edsl-export --survey <survey> --target probability-job --path one_shot_probability_job.edsl.json
   # Remote Expected Parrot inference only; verify the EP profile/key first.
   zwill edsl-run --job one_shot_probability_job.edsl.json --path one_shot_probability_results.json.gz
   zwill prob-results import --survey <survey> --path one_shot_probability_results.json.gz
   zwill prob-results report --survey <survey> --job-id <probability_job_id> --format html --path one_shot_probability_report.html
   zwill report build --survey <survey> --path <survey>_report/
   ```

   For interpretation, export a generated one-shot analysis rather than writing deterministic prose:

   ```bash
   zwill prob-results analysis-export --survey <survey> --job-id <probability_job_id> --path <survey>_report/one-shot-marginals.html
   zwill edsl-run --job .zwill/projects/<project>/practitioner_reports/<report_id>/job.edsl.json --path .zwill/projects/<project>/practitioner_reports/<report_id>/results.json.gz
   zwill prob-results analysis-import --report-id <report_id> --path .zwill/projects/<project>/practitioner_reports/<report_id>/results.json.gz
   zwill prob-results analysis-render --report-id <report_id> --path <survey>_report/one-shot-marginals.html
   ```

   Treat this as aggregate marginal validation only. It does not show respondent-level twin quality.

6. **Draft a validation plan before any twin export/run.**

   Default to considering every eligible observed closed-ended question as a held-out twin target, not just a hand-picked subset. "Eligible" means the question has observed answers, validated human-readable options, enough complete respondent context to build twins, and no unresolved leakage/missingness issue that would make scoring misleading. If running every eligible question is too expensive, create a staged plan: smoke/debug subset first, pilot subset second, then final full eligible question set when approved.

   Prefer combined EDSL jobs, not one job per held-out question. `zwill edsl-export --target twin-probability-job` and `zwill twin-experiment export-plan` can take multiple held-out questions and build one scenario grid per approach: roughly `respondents x held-out questions`, with the requested models attached as the job's `ModelList`. Split by question only for explicit debugging, provider/runtime limits, retry isolation, or staged cost control.

   The plan must specify:

   - held-out observed questions or outcomes;
   - all eligible questions considered and any excluded questions with reasons;
   - respondent sample and whether the final run is full-sample or sampled;
   - construction approaches and the intended difference between them;
   - context fields available at prediction time;
   - leakage exclusions such as `<heldout_question>:<context_question>`;
   - models and model parameters;
   - seed and complete-case/stratification policy;
   - prediction count: `respondents x held-out questions x approaches x models`;
   - expected cost/time risk;
   - outputs to produce and decision criteria.

   Prefer `twin-experiment` for planned validation:

   ```bash
   zwill twin-approach scaffold --survey <survey> --approach-id baseline_context --name "Prior answers only" --context-question-count 5 --path baseline_context.approach.json
   zwill twin-approach add --survey <survey> --path baseline_context.approach.json
   zwill twin-approach list --survey <survey>

   zwill twin-experiment init-plan \
     --survey <survey> \
     --plan-id <plan_id> \
     --heldout-questions <q1,q2,...> \
     --approach-id baseline_context \
     --sample-respondents <n> \
     --seed <seed> \
     --path <plan_id>.json
   ```

   Ask the user to approve or edit the plan. Only after explicit approval:

   ```bash
   zwill twin-experiment approve \
     --path <plan_id>.json \
     --approved-by <reviewer> \
     --note "Approved held-out targets, context policy, leakage exclusions, sample size, models, and seed."
   ```

7. **Export, run, and import twin jobs from the approved plan.**

   ```bash
   zwill twin-experiment export-plan --path <plan_id>.json --output-dir <plan_id>_jobs
   zwill twin-experiment package --manifest <plan_id>_jobs/manifest.json --output-dir <plan_id>_run_package
   ```

   `export-plan` should normally write one EDSL job per approach/arm. Each job should contain all approved held-out questions as concatenated scenarios, not separate per-question jobs. Check the export manifest's `scenario_count` before running; it should approximately equal the eligible respondent count times the held-out question count for that arm, adjusted for complete-case filtering, missing actual answers, or stratified sampling.

   Run each exported approach job through remote Expected Parrot inference only. If the package is handed to another runner, use its `RUN.md`. The local command wrapper pattern is:

   ```bash
   zwill edsl-run --job <plan_id>_jobs/<job>.edsl.json --path <plan_id>_results/<job>.results.json.gz
   zwill twin-experiment import-plan-results --manifest <plan_id>_jobs/manifest.json --results-dir <plan_id>_results
   ```

   `import-plan-results` imports every Results object that matches the exported plan manifest and records the planned experiment metadata. Use `zwill twin-results import --survey <survey> --path <results.json.gz>` only for one-off jobs that are not being imported through a plan manifest.

   For a one-command debug run, use `zwill twin-study run --approved-plan <plan_id>.json ...`. Prefer the separated export/run/import flow when auditing prompts, provider failures, malformed rows, or construction metadata.

8. **Audit at least one imported job before scoring claims.**

   ```bash
   zwill twin-study list --survey <survey>
   zwill twin-study show --survey <survey> --job-id <job_id> --include-summary
   zwill twin-results run-report --survey <survey> --job-id <job_id> --format html --path <job_id>_run_audit.html
   ```

   Inspect construction metadata, held-out questions, prompt template, rendered user prompts, scenario inputs, twin identity, raw model responses, malformed rows, and import issues. Confirm held-out answers and leakage variables are absent from prompts.

9. **Score the twin run against baselines.**

   ```bash
   zwill twin-results report --survey <survey> --job-id <job_id> --view summary
   zwill twin-results report --survey <survey> --job-id <job_id> --format html --path <job_id>_twin_validation.html
   zwill report build --survey <survey> --path <survey>_report/ --job-id <job_id> --audit-job-id <job_id>
   ```

   Inspect accuracy, p(actual), NLL, NLL p95, Brier, ECE/calibration, marginal L1/JS, overconfident misses, malformed-row rate, per-question failures, and lift versus uniform and empirical-marginal baselines. The empirical marginal baseline is an oracle-style benchmark available only because the target was observed.

10. **Compare construction approaches.**

    ```bash
    zwill twin-results compare-report --survey <survey> --jobs <job_id_1>,<job_id_2> --format html --path twin_job_comparison.html
    zwill twin-experiment compare --survey <survey> --metric nll
    zwill twin-experiment select --survey <survey> --metric nll --model <model>
    zwill twin-experiment plots --survey <survey> --jobs <job_id_1>,<job_id_2> --path <plan_id>_plots
    zwill twin-experiment microdata --survey <survey> --jobs <job_id_1>,<job_id_2> --path <plan_id>_microdata.html
    ```

    Prefer comparisons with clear construction differences, such as survey-only context versus survey plus transaction summaries. Avoid declaring a winner from many opaque prompt tweaks without a predeclared plan.

11. **Build a decision-facing report only after diagnostics exist.**

    ```bash
    zwill twin-experiment bundle \
      --survey <survey> \
      --plan-id <plan_id> \
      --metric nll \
      --model <model> \
      --output-dir <plan_id>_bundle \
      --report-export

    zwill twin-experiment dashboard \
      --survey <survey> \
      --plan-id <plan_id> \
      --metric nll \
      --model <model> \
      --bundle-manifest <plan_id>_bundle/manifest.json \
      --path <plan_id>_dashboard.html
    ```

    For a single imported job, use the generated executive-summary flow:

    ```bash
    zwill twin-results executive-summary-export --survey <survey> --job-id <job_id> --path <survey>_executive_summary.html
    zwill edsl-run --job .zwill/projects/<project>/practitioner_reports/<report_id>/job.edsl.json --path .zwill/projects/<project>/practitioner_reports/<report_id>/results.json.gz
    zwill twin-results executive-summary-import --report-id <report_id> --path .zwill/projects/<project>/practitioner_reports/<report_id>/results.json.gz
    zwill twin-results executive-summary-render --report-id <report_id> --path <survey>_executive_summary.html
    zwill report render --survey <survey> --path <survey>_report/ --job-id <job_id> --final
    ```

    The generated report must discuss data used, held-out design, construction approaches, baselines, performance by target family, calibration, wins/failures, leakage risks, subgroup or population coverage limits, and a practical recommendation: use now, use only for exploratory work, or collect better validation data first.

### What Good Twin Evidence Looks Like

- **Basic viability:** high import coverage, zero open quarantine issues, low malformed prediction rate, and audited prompts that exclude held-out answers and leakage fields.
- **Aggregate signal:** twin-implied marginals beat uniform and are competitive with one-shot aggregate baselines for most held-out questions.
- **Individual signal:** p(actual), NLL, Brier, and calibration improve over the empirical marginal baseline, not just over uniform. Within-question permutation diagnostics should support respondent-level predictive power.
- **Robustness:** conclusions hold across held-out question families, reasonable seeds/samples, and at least one clear construction comparison.
- **Operational caution:** if performance only matches empirical marginals or permutation tests are null, say the evidence supports aggregate opinion structure, not individual predictive power.

### Twin Evaluation Sample Sizes

Pick sample sizes from the evaluation purpose, not from convenience alone. Always compute and show the prediction count before running: `respondents x held-out questions x approaches x models`.

- **Prompt/export smoke test:** 2-5 respondents, 1 held-out question, 1 approach, 1 model. Use this only to verify exports, prompt rendering, EDSL execution, import parsing, and audit reports.
- **Debug validation run:** 20-50 exported, scored respondents per held-out question after complete-case and missing-actual filtering. Use this to find leakage, malformed responses, bad context selection, missing actual answers, and obvious model-parameter problems. Keep the same held-out policy and leakage exclusions intended for the final run. If the approved sample was larger but export filtering leaves fewer than the approved/debug target, treat the run as a systems test and revise the plan or sampling policy before drawing validation conclusions.
- **Pilot comparison:** 100-200 respondents when cost allows. Use `--stratify-actual` for categorical held-out questions so rare options appear in the sample. Treat pilot metrics as directional, not final.
- **Minimum credible scored run:** prefer at least 200 respondents or all eligible respondents if the eligible pool is smaller. For per-question conclusions, aim for at least 30 observed actual answers in the smallest important option or subgroup; otherwise flag that question or subgroup as underpowered.
- **Final validation:** prefer full-sample scoring across all eligible respondents when API cost and time allow. If sampling is necessary, predeclare the sample size, seed, stratification policy, and why the sampled population is adequate for the user's intended decision.
- **Multiple approaches/models:** reduce held-out questions or start with a pilot rather than silently shrinking respondent count below a useful validation size. A 25-respondent run across many arms is usually a systems test, not evidence.

Example staged plan:

```bash
# Smoke test: prompt/render/import only.
zwill twin-experiment init-plan \
  --survey <survey> \
  --plan-id <plan_id>_smoke \
  --heldout-questions <q1> \
  --approach-id baseline_context \
  --sample-respondents 5 \
  --seed 101 \
  --path <plan_id>_smoke.json

# Pilot: enough rows to compare obvious approach differences.
zwill twin-experiment init-plan \
  --survey <survey> \
  --plan-id <plan_id>_pilot \
  --heldout-questions <q1,q2,q3> \
  --approach-id baseline_context \
  --approach-id richer_context \
  --sample-respondents 150 \
  --seed 20260706 \
  --path <plan_id>_pilot.json
```

For final plans, omit `--sample-respondents` when full-sample scoring is feasible. If using `--sample-respondents`, include `--seed` and usually `--stratify-actual` for multiple-choice held-out targets.

### Common Failure Modes to Call Out

- Coded options were imported as raw numeric/source codes rather than human-readable labels.
- Respondents were implicitly created from answer rows because respondent metadata failed to import.
- Held-out answers or target-specific follow-ups leaked into context.
- The model learned the population marginal but not the respondent-level answer pattern.
- A small sample was stratified or filtered in a way that no longer represents the deployment population.
- Provider responses were malformed or silently dropped, changing the effective sample.
- The empirical marginal baseline was described as a deployable baseline even though it is only available for already-observed targets.
- A generated executive report was circulated without `zwill report render --final` or without imported generated analysis.

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
