# zwill agent workflow: survey data → validated twin report

This guide walks you (an automated agent, in any harness) from raw survey data to
a full digital-twin validation report with an `index.html`. It is self-contained:
run `zwill next` at any point to see which stage you are in and the exact command
to run next, and `zwill guide show interpreting-results` when you reach the report.

You do not need any coding-assistant skill files. Everything you need is reachable
through `zwill` commands and their `next_steps`.

## What you are building

A digital-twin *validation study*: hold out some questions respondents already
answered, construct twins without those answers, have models predict them, and
score the predictions against what people actually said — against a cheap
conditional baseline, with confidence intervals and a leakage check, so the
conclusion is trustworthy rather than over-claimed.

The decisive question is **not** "does the twin beat random?" It is "does the twin
add individual-level signal beyond a cheap model, and is that gap real rather than
noise?" Do not report a positive result from a bare twin run.

## Prerequisites (check these first)

- `zwill` is installed and `edsl` is importable (installed as the sibling `../edsl`
  editable checkout). Running twins uses EDSL.
- Running twins uses **Expected Parrot remote inference**: `EXPECTED_PARROT_API_KEY`. If the user needs a key, direct them to [Expected Parrot signup](https://www.expectedparrot.com/).
  in the environment where the EDSL `ep` CLI runs the exported `.ep` job.
- The **conditional baseline** embeds question/option text and is an XGBoost model.
  `--embedder auto` (default) tries the **Expected Parrot embeddings endpoint
  first**, behind a short health probe so an unavailable endpoint **fails over in
  seconds instead of hanging** the gated `--require-baseline` validation. It then
  falls back to a direct `OPENAI_API_KEY`, then a local sentence-transformers model
  when installed (`pip install 'zwill[conditional-baseline]'`), then a built-in
  lexical embedder that always runs (weaker — it leans on covariates). Force a
  backend with `--embedder edsl|openai|sentence-transformers|hashing` (`edsl` has
  no failover). Install the extra so the fallback is the strong semantic model.
- Do **not** pass `temperature` to models. Newer Anthropic/OpenAI models reject it
  and error on every call; EDSL omits it automatically.
- Validate twins on a **current frontier model**. `edsl build --target
  twin-probability-job` defaults to `gpt-5.5` when `--model` is omitted, and names
  the chosen model in its output; if you pass a superseded model (e.g. `gpt-4o`,
  `gpt-4.1`, `claude-3-*`) it emits a `superseded_twin_model` warning, because a
  weak model understates twin capability and makes the whole validation
  uninformative. Only use an older model when you are deliberately benchmarking it.

## Where outputs go

Reports, bundles, executive summaries, and exported CSVs are **contained under an
output root** so they do not sprawl across the working directory. A relative
`--path`/`--out` (and a command's CWD-relative default like `<survey>_report/`)
is rebased under that root; the default root is `zwill_work/` beside `.zwill/`.
Override it with the `ZWILL_OUT` environment variable or `zwill init
--output-dir <dir>` (persisted in `.zwill/config.json`). An **absolute** path is
written verbatim (an escape hatch) with a one-line warning that it left the root.

Two things are deliberately *not* rebased: managed state stays in `.zwill/`, and
intermediate EDSL plumbing that is read back by a later command — the `.ep`
packages written by `edsl build` and `ep run` — stays at the current directory
so the external runner and later import command find them. Input files you
pass with `--input-path` are reads and are never rebased.

## Non-negotiable guardrails

- Treat ingestion as a validation boundary, not a best-effort conversion. Convert
  first, write JSONL imports plus a manifest and full issue log, then mutate
  `.zwill` only after conversion succeeds.
- Expand coded survey values through codebooks before import/export. Use
  human-readable response labels as canonical `question_options` and answers.
  Preserve raw source codes as provenance. Unknown codes should fail/quarantine
  the affected item, not become option labels.
- Preserve provenance with `zwill raw add`, stable filesystem-safe ids, source
  variable names in `source.note`, and source paths in the import manifest.
- Do not start with twin generation. First prove the observed data are imported,
  labeled, committed, inspectable, and free of open quarantine issues.
- Do not use held-out answers, empirical marginals for the held-out target,
  downstream variables, or summaries that reveal the target in twin prompts unless
  the user explicitly approves an oracle/leakage experiment.
- Do not run costly EDSL jobs until the user has seen the plan, prediction count,
  model list, and likely cost/time risk. For twins, use an approved plan unless
  the user explicitly requests an ad hoc/debug run with `--allow-unapproved`.
- zwill builds and imports artifacts; it does not own model execution. Run exported
  `Jobs.ep` packages with `ep run <jobs.ep> --output <results.ep>` through remote
  Expected Parrot inference, then give the result package to the appropriate
  zwill import command.
- For Gemini or other verbose providers, set a generous output-token cap for
  probability jobs. `maxOutputTokens` is Google-specific — scope it to the model
  with a per-model param so other providers don't warn about an unknown
  parameter: `--model-param google:gemini-2.5-pro:maxOutputTokens=8192`
  (OpenAI uses `max_tokens`). Passing `maxOutputTokens` globally makes OpenAI
  warn `Unknown parameter(s) for model ...: maxOutputTokens`.
- After exporting an approved plan, compare approved prediction count with the
  exported manifest's actual scenario/model count. If filtering or limits change
  the count materially, stop and re-approve the updated plan.
- Keep artifacts reproducible: import manifests, approved plans, exported jobs,
  raw Results objects, imported reports, bundle manifests, generated-report
  prompts/contexts, and rendered HTML outputs.

## The stages

Run `zwill next` after each stage — it inspects project state and tells you the
next command. The full path:

1. **Initialize** — `zwill init` creates the `.zwill/` project database.
2. **Create a survey** — `zwill survey create --name <survey>`.
3. **Archive the raw source** — `zwill raw add --survey <survey> --id <id>
   --title <title> --path <file> --kind <workbook|csv|questionnaire|...>` records
   provenance (`--title` is required). Then convert to structured records.
4. **Import structured data** — `questions.jsonl`, `respondents.jsonl`,
   `answers.jsonl` via `zwill question import` / `respondent import` /
   `answer import`. Run `zwill guide show import-format` for the full per-file
   JSONL schema (fields, option-validation rule, rank/multi-select conventions,
   and copy-pasteable example rows). Expand codebooks to human-readable labels
   first; a code that cannot be expanded should be marked incomplete, not treated
   as a label.
5. **Commit** — `zwill commit --survey <survey>` freezes the observed truth
   marginals used to score twins.
6. **Inspect** — `zwill survey report --survey <survey> --format html --path
   survey_report.html` to verify wording, options, distributions, and missingness
   before spending on model calls.
7. **Run one-shot marginals** — before respondent-level twin claims, run aggregate
   one-shot probability predictions for the held-out questions you will evaluate
   (or all eligible closed-ended questions when cost allows):
   ```bash
   zwill edsl build --survey <survey> --target probability-job \
     --questions <q1,q2,...> --model <service:model> --path one_shot_jobs.ep
   ep run one_shot_jobs.ep --output one_shot_results.ep
   zwill prob-results import --survey <survey> --input-path one_shot_results.ep
   zwill prob-results report --survey <survey> --job-id <probability_job_id> \
     --format html --path one-shot-marginals.html
   zwill prob-results report --survey <survey> --job-id <probability_job_id> \
     --format svg --path one-shot-marginals.svg
   ```
   The optional SVG is a portable observed-versus-one-shot marginal comparison
   for reports and slides; it requires no browser runtime or plotting dependency.
   Treat these outputs as structured evidence. Explain the observed-versus-model
   comparison in the final narrative; zwill does not ask another model to write
   that interpretation.
8. **Run the twin jobs** — pick 5–10 held-out questions spanning different use
   cases, choose provider-qualified models (e.g. `openai:gpt-5.5`,
   `google:gemini-2.5-pro`), export and run:
   ```bash
   zwill edsl build --survey <survey> --target twin-probability-job \
     --heldout-questions <q1,q2,...> --context-question-count 8 \
     --sample-respondents 200 --seed 20260706 --complete-cases \
     --model openai:gpt-5.5 --model google:gemini-2.5-pro \
     --allow-unapproved \
     --path twin_jobs.ep
   ep run twin_jobs.ep --output twin_results.ep
   zwill twin-results import --survey <survey> --input-path twin_results.ep
   ```
   Respondent metadata (panel covariates like age/party/region) is included as
   twin context **by default** — rendered as a "Respondent profile" block — for
   the multiple-choice, numeric, rank, and agent-list exports alike. Drop it with
   `--exclude-metadata-context`, or a single key with `--exclude-metadata-key <key>`.
   If your covariates are stored as raw numeric codes (e.g. `F_AGECAT=4`), the
   export warns (`uncoded_metadata`): map them to readable labels first, or the
   twin sees uninterpretable numbers.
   To experiment with *how the twin reasons or how its evidence is framed*, pass
   `--twin-prompt-pipeline <file.json>` — an ordered pipeline of prompt steps
   (e.g. "argue why each option, note thin evidence" → "weigh and predict" as two
   piped model calls). The final step carries `{{ output_contract }}` and is
   scored, so any pipeline stays comparable through the gate. Run
   `zwill guide show prompt-pipelines` for the full mechanism (step spec, template
   variables, the A/B experiment loop) and `examples/twin_pipelines/` for
   ready-to-copy strategies.
   `twin-probability-job` exports require an approved plan; this one-off/debug
   form passes `--allow-unapproved`. For a validation run, drop that flag and use
   `--approved-plan <plan.json>` (see the `twin-experiment` plan flow below).
   For a single survey, `zwill twin-study build` packages the same job and returns the exact `ep run` command.
   If import reports a non-zero `issue_count` (a few provider rows returned
   malformed JSON), recover just those without re-running the whole job:
   ```bash
   zwill twin-results retry-malformed --survey <survey> --job-id <twin_job_id> --job twin_jobs.ep
   ep run <retry_jobs.ep> --output <retry_results.ep>
   zwill twin-results import --survey <survey> --job-id <twin_job_id> --merge --input-path <retry_results.ep>
   ```
   For validation runs beyond one-off debugging, prefer `twin-experiment` plans:
   the plan must specify held-out targets, all eligible questions considered,
   excluded questions and reasons, respondent sample, construction approaches,
   context fields, leakage exclusions, models/parameters, seed, complete-case or
   stratification policy, prediction count (`respondents x held-out questions x
   approaches x models`), cost/time risk, outputs, and decision criteria. Ask the
   user to approve or edit the plan before export/run.
9. **Audit prompts before scoring** — immediately after import, inspect at least
   one run before opening performance tables:
   ```bash
   zwill twin-results run-report --survey <survey> --job-id <twin_job_id> \
     --format html --path run-audit.html
   ```
   Open the report and confirm construction metadata, prompt template, rendered
   scenarios, raw model responses, malformed rows, and import issues. Held-out
   answers, target marginals, target-revealing fields, and identifiers or weights
   presented as personality evidence must be absent. Stop and fix a failed audit;
   do not let a promising score excuse a questionable prompt.
10. **Validate — one command** — run the whole rigorous flow:
   ```bash
   zwill twin-validate --survey <survey> --jobs <twin_job_ids> --out validation_bundle --require-baseline
   ```
   This runs the leakage audit, fits the conditional baseline on the *same
   respondents* the twins scored, computes bootstrap confidence intervals, and
   renders the report. The bundle contains `report.html`, `bootstrap.json`,
   `bootstrap-intervals.svg`, `calibration.svg`, `leakage_audit.json`, and
   `manifest.json`.
11. **Build the evidence bundle** — assemble the incremental HTML report folder
   with an `index.html` linking every ready page:
   ```bash
   zwill report build --survey <survey> --path report_out \
     --job-id <twin_job_id> --probability-job-id <probability_job_id>
   ```
   Inspect the bundle's contextualized tables, diagnostics, plots, audit pages,
   and machine-readable facts. These are inputs to the coding agent's report,
   not a model-authored final narrative.
12. **Write and check the interpretation** — use the assembled evidence to
   author the study's claims and limitations. Keep observed results, baseline
   comparisons, uncertainty, leakage findings, sample restrictions, and costs
   traceable to bundle artifacts. Then render the deterministic bundle:
   ```bash
   zwill report render --survey <survey> --path report_out \
     --job-id <twin_job_id> --probability-job-id <probability_job_id>
   ```
   Open `report_out/index.html` or `report_out/report/index.html`.

## Ranking questions (a separate validation flow)

`twin-validate` gates **twin-probability-jobs only** — multiple-choice held-out
targets. Ranking / MaxDiff batteries are validated through a parallel
rank-utility flow and are **not** covered by the headline gate. If your survey
has rank batteries, `twin-validate` warns you (`rank_tasks_not_validated_here`).
Validate them separately:

```bash
zwill edsl build --survey <survey> --target rank-utility-twin-job \
  --rank-task-id <rank_task_id> --allow-unapproved --path rank_jobs.ep
ep run rank_jobs.ep --output rank_results.ep
zwill twin-results import --survey <survey> --input-path rank_results.ep
zwill twin-results rank-report --survey <survey> --rank-task-id <rank_task_id> \
  --format html --path report_out/rank-<rank_task_id>.html
```

Declare batteries with an explicit `rank_task_id` at import so they are detected
reliably (see `zwill guide show import-format`), and link the rank report pages
from your report folder. For the rank metrics, partial/top-N rankings, and rank
leakage checks, see `zwill guide show rank`.

## Numeric questions (continuous targets)

For a `numeric` held-out question the twin predicts a **quantile distribution**
(p05/p25/p50/p75/p95) rather than option probabilities, scored with proper
scoring rules (pinball loss, CRPS), interval coverage, and skill vs a marginal-
quantile climatology baseline:

```bash
zwill edsl build --survey <survey> --target numeric-twin-job \
  --heldout-question <numeric_q> --allow-unapproved --path numeric_jobs.ep
ep run numeric_jobs.ep --output numeric_results.ep
zwill numeric-results import --survey <survey> --input-path numeric_results.ep
zwill numeric-results report --survey <survey> --job-id <job_id> \
  --format html --path report_out/numeric.html
```

Import the target with `question_type: numeric` (and optional `numeric_min` /
`numeric_max` bounds).

## Open-ended questions (code, then validate)

Free-text (`free_text`) questions are validated by **coding them into themes** and
running the coded multiple-choice question through the normal gate. Two ordinary
export → run → import cycles:

```bash
# 1. Derive a codebook of themes from a sample of the answers
zwill edsl build --survey <survey> --target open-codebook-job \
  --heldout-question <free_text_q> --n-themes 8 --model openai:gpt-5.5 --path cb_jobs.ep
ep run cb_jobs.ep --output cb_results.ep
zwill open-coding codebook-import --survey <survey> --input-path cb_results.ep

# 2. Code every respondent's answer into one theme -> new multiple_choice question
zwill edsl build --survey <survey> --target open-coding-job \
  --heldout-question <free_text_q> --model openai:gpt-5.5 --path coding_jobs.ep
ep run coding_jobs.ep --output coding_results.ep
zwill open-coding import --survey <survey> --input-path coding_results.ep \
  --coded-question-name <free_text_q>_coded
```

`open-coding import` writes a `multiple_choice` question (options = theme codes)
plus one coded answer per respondent, and warns if the unclassified bucket
exceeds 20% (a sign the codebook does not fit). Then validate
`<free_text_q>_coded` exactly like any other multiple-choice target with
`twin-probability-job` → `twin-validate`. Code with a single model for
deterministic results.

## Reading the result (do not over-claim)

When you reach the bundle, run `zwill guide show interpreting-results` for the full
gating rules. In short, a positive claim requires: the leakage audit is clean (or
leaky targets excluded); the twin beats the **conditional baseline**, not just
uniform/marginal; and that gap's **bootstrap interval clears zero**. Flag
overconfidence (an NLL delta the wrong way), coarse probabilities, and washed-out
cross-question structure (attenuation) when present.

Use accuracy/top-1 as a sanity check, not the headline. Inspect p(actual), NLL,
Brier, calibration/ECE, marginal fit, overconfident misses, malformed-row rate,
per-question failures, and lift versus uniform, empirical marginal, one-shot, and
conditional baselines. The empirical marginal is an oracle benchmark because the
target is already observed; do not describe it as deployable for new targets.

If the permutation or bootstrap evidence is null, say the evidence supports
aggregate opinion structure or directional exploration, not validated
respondent-level predictive power. Do not present twins as validated for
production decisions unless the held-out evaluation supports that use case.

## Sample-size guide

Always compute and show prediction count before running:
`respondents x held-out questions x approaches x models`.

- Prompt/export smoke test: 2-5 respondents, 1 held-out question, 1 approach,
  1 model. This verifies rendering, remote execution, import parsing, and audit
  reports only.
- Debug validation: 20-50 scored respondents per held-out question after
  filtering. Use the same held-out and leakage policy as the intended final run.
- Pilot comparison: 100-200 respondents when cost allows, usually with
  `--stratify-actual` for categorical targets. Treat results as directional.
- Minimum credible scored run: prefer at least 200 respondents, or all eligible
  respondents if the eligible pool is smaller. For per-question conclusions, flag
  questions whose smallest important option/subgroup has too few observed cases.
- Final validation: prefer full-sample scoring when cost/time allow. If sampling,
  predeclare sample size, seed, stratification, and why the sample is adequate.
- When adding approaches/models, reduce held-out questions or stage the run rather
  than silently shrinking respondent count below a useful validation size.

## Common failure modes

- Coded options imported as raw numeric/source codes instead of labels.
- Respondents implicitly created from answer rows because metadata failed to
  import.
- Held-out answers or target-specific follow-ups leaked into context.
- The model learned population marginals but not respondent-level answer patterns.
- Small samples or filtering changed the deployment population being evaluated.
- Malformed provider responses were silently dropped, changing effective sample.
- Decision-facing claims circulated without checking them against the evidence bundle.
- Many opaque prompt tweaks were compared without a predeclared construction plan.

## If you get stuck

- `zwill next` — where am I, what next.
- `zwill status` — project and survey state.
- `zwill next --survey <survey>` — stage for a specific survey.
- `zwill guide list` — other bundled guides.
- Every command returns `next_steps`; follow them.
