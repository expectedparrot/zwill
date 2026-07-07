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
- Running twins uses **Expected Parrot remote inference**: `EXPECTED_PARROT_API_KEY`
  in a `.env` that `zwill edsl-run` can find (it loads the nearest `.env`).
- The **conditional baseline** embeds question/option text and needs
  `OPENAI_API_KEY` (for `text-embedding-3-small`). Without it the baseline is
  skipped and the comparison loses its point — set the key, or accept a weaker
  readout.
- Do **not** pass `temperature` to models. Newer Anthropic/OpenAI models reject it
  and error on every call; EDSL omits it automatically.

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
- Run model jobs only through remote Expected Parrot inference with plain
  `zwill edsl-run`. Verify the EP profile/key is available. Do not inject
  `use_api_proxy`, `disable_remote_inference`, `offload_execution`, or local
  provider calls unless explicitly requested.
- For Gemini or other verbose providers, set a generous output-token cap for
  probability jobs. Prefer `maxOutputTokens=8192` unless there is a specific
  reason not to.
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
   --path <file> --kind <workbook|csv|questionnaire|...>` records provenance.
   Then convert to structured records.
4. **Import structured data** — `questions.jsonl`, `respondents.jsonl`,
   `answers.jsonl` via `zwill question import` / `respondent import` /
   `answer import`. Expand codebooks to human-readable labels first; a code that
   cannot be expanded should be marked incomplete, not treated as a label.
5. **Commit** — `zwill commit --survey <survey>` freezes the observed truth
   marginals used to score twins.
6. **Inspect** — `zwill survey report --survey <survey> --format html --path
   survey_report.html` to verify wording, options, distributions, and missingness
   before spending on model calls.
7. **Run one-shot marginals** — before respondent-level twin claims, run aggregate
   one-shot probability predictions for the held-out questions you will evaluate
   (or all eligible closed-ended questions when cost allows):
   ```bash
   zwill edsl-export --survey <survey> --target probability-job \
     --questions <q1,q2,...> --model <service:model> --path one_shot.edsl.json
   zwill edsl-run --job one_shot.edsl.json --path one_shot_results.json.gz
   zwill prob-results import --survey <survey> --path one_shot_results.json.gz
   zwill prob-results report --survey <survey> --job-id <probability_job_id> \
     --format html --path one-shot-marginals.html
   ```
   For the final report gate, also generate the one-shot interpretation:
   ```bash
   zwill prob-results analysis-export --survey <survey> --job-id <probability_job_id> \
     --path report_out/one-shot-marginals.html
   zwill edsl-run --job <generated_report_job.edsl.json> --path <generated_report_results.json.gz>
   zwill prob-results analysis-import --report-id <report_id> --path <generated_report_results.json.gz>
   zwill prob-results analysis-render --report-id <report_id> --path report_out/one-shot-marginals.html
   ```
8. **Run the twin jobs** — pick 5–10 held-out questions spanning different use
   cases, choose provider-qualified models (e.g. `openai:gpt-5.5`,
   `google:gemini-2.5-pro`), export and run:
   ```bash
   zwill edsl-export --survey <survey> --target twin-probability-job \
     --heldout-questions <q1,q2,...> --context-question-count 8 \
     --sample-respondents 200 --seed 20260706 --complete-cases \
     --model openai:gpt-5.5 --model google:gemini-2.5-pro \
     --path twin.edsl.json
   zwill edsl-run --job twin.edsl.json --path twin_results.json.gz
   zwill twin-results import --survey <survey> --path twin_results.json.gz
   ```
   (For a single survey you can also use `zwill twin-study run`.)
   For validation runs beyond one-off debugging, prefer `twin-experiment` plans:
   the plan must specify held-out targets, all eligible questions considered,
   excluded questions and reasons, respondent sample, construction approaches,
   context fields, leakage exclusions, models/parameters, seed, complete-case or
   stratification policy, prediction count (`respondents x held-out questions x
   approaches x models`), cost/time risk, outputs, and decision criteria. Ask the
   user to approve or edit the plan before export/run.
9. **Validate — one command** — run the whole rigorous flow:
   ```bash
   zwill twin-validate --survey <survey> --jobs <twin_job_ids> --out validation_bundle --require-baseline
   ```
   This runs the leakage audit, fits the conditional baseline on the *same
   respondents* the twins scored, computes bootstrap confidence intervals, and
   renders the report. The bundle contains `report.html`, `bootstrap.json`,
   `leakage_audit.json`, and `manifest.json`.
   Audit at least one imported run with `zwill twin-results run-report` before
   scoring claims. Confirm construction metadata, prompt template, rendered
   prompts, scenario inputs, raw model responses, malformed rows, and import
   issues; held-out answers and leakage fields must be absent from prompts.
10. **Build the report index and generated twin interpretation** — assemble the
   incremental HTML report folder with an `index.html` linking every ready page:
   ```bash
   zwill report build --survey <survey> --path report_out \
     --job-id <twin_job_id> --probability-job-id <probability_job_id>
   ```
   If `zwill report render --final` reports missing generated interpretation,
   follow its hint, usually:
   ```bash
   zwill twin-results executive-summary-export --survey <survey> --job-id <twin_job_id> \
     --path report_out/executive-summary.html
   zwill edsl-run --job <generated_report_job.edsl.json> --path <generated_report_results.json.gz>
   zwill twin-results executive-summary-import --report-id <report_id> --path <generated_report_results.json.gz>
   zwill twin-results executive-summary-render --report-id <report_id> --path report_out/executive-summary.html
   ```
11. **Final gate** — render the final report only after the one-shot and twin
   generated interpretations are imported:
   ```bash
   zwill report render --survey <survey> --path report_out \
     --job-id <twin_job_id> --probability-job-id <probability_job_id> --final
   ```
   Open `report_out/index.html` or `report_out/report/index.html`.

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
- Generated executive reports circulated without `zwill report render --final`.
- Many opaque prompt tweaks were compared without a predeclared construction plan.

## If you get stuck

- `zwill next` — where am I, what next.
- `zwill status` — project and survey state.
- `zwill next --survey <survey>` — stage for a specific survey.
- `zwill guide list` — other bundled guides.
- Every command returns `next_steps`; follow them.
