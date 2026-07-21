---
name: digital-twin-study-runner
description: Use when planning, running, or debugging zwill digital twin studies from uploaded or ingested surveys — including held-out question selection, respondent sampling, model selection, the conditional baseline, leakage auditing, bootstrap confidence intervals, calibration diagnostics, and validation artifact generation.
---

# Digital Twin Study Runner

Use this skill to design and run survey-based digital twin studies with `zwill`, and to validate them rigorously enough that a practitioner can trust the conclusion.

Core rule: always expand survey codebooks before import/export. Use human-readable answer labels everywhere (questions, answers, EDSL exports, reports, baselines). If a code cannot be expanded, mark the affected item incomplete instead of treating the raw code as a label.

**The decisive question is not "does the twin beat random?" It is "does the twin add individual-level signal beyond what a cheap model already recovers, and is that gap real rather than noise?"** The workflow below is built to answer that. Do not make a positive claim from a bare twin run and a basic report.

## Workflow

1. **Inspect the survey**
   - Confirm questions, options, respondent count, missingness, and codebook expansion.
   - Digital twin scoring expects held-out multiple-choice questions with known options.
   - Prefer complete-case respondent samples for the held-out/context set unless the study is explicitly about missingness.

2. **Choose study design**
   - Use 3-10 context questions for small studies; increase only when the survey has enough clean prior answers.
   - Hold out questions spanning different practical use cases: factual attitudes, policy preferences, sensitive items, behavioral self-reports, and demographic-like traits.
   - Sample respondents with a fixed seed. Use `--stratify-actual` when the held-out answer distribution is imbalanced.
   - Start with 20-100 respondents per held-out question for preflight; scale after the pipeline and report look correct.

3. **Run the twin jobs**
   - Use provider-qualified models, such as `openai:gpt-5.5` and `google:gemini-2.5-pro`.
   - Use the explicit `zwill edsl build` → `ep run` → `twin-results import` flow. EDSL's CLI owns credentials and execution; zwill only builds and imports packages.
   - **Do not set `temperature`.** Newer Anthropic and OpenAI models (Fable 5, Opus 4.7+, Sonnet 5, etc.) reject the `temperature` parameter and will error on every call; EDSL omits it for those models automatically. Only pass `temperature` for an older model that requires it, and never rely on `temperature=0` for "comparability."
   - For Gemini free-text JSON jobs, pass a larger `max_tokens` (and `thinking_budget` when supported) so responses are not truncated.

4. **Validate — run the full flow with one command**

   Once one or more twin jobs are imported, run the complete validation in one gated step. This is the appropriate exercise; the individual commands in the next section are for running or re-running a single piece.

   ```bash
   zwill twin-validate \
     --survey <survey_id> \
     --jobs <twin_job_1>,<twin_job_2> \
     --out validation_bundle
   ```

   `twin-validate` runs, in order:
   - **Leakage audit** over the held-out targets — flags any context question that near-deterministically predicts a target (bias-corrected Cramér's V). Leakage is the top validity threat; a "twin" that only works on leaky targets is copying, not modelling.
   - **Conditional baseline** — an **XGBoost** model over question/option embeddings **plus the respondent's panel covariates**, fit on the *same respondents* the twins scored, leave-one-question-out (it never sees the target's own marginal). This is the fair, deployable yardstick, and with covariates it is a *strong* bar. The embedder auto-selects a working backend — Expected Parrot first (behind a short health probe, so an unavailable endpoint fails over in seconds instead of hanging), then a direct `OPENAI_API_KEY`, then a local sentence-transformers model, then a built-in lexical embedder — so it **runs even with no key and never hangs**. Install `zwill[conditional-baseline]` for the strongest (semantic) baseline. Pass `--skip-baseline` to omit it, or `--require-baseline` to fail rather than warn.
   - **Bootstrap confidence intervals** on each model's scores and on the paired twin-minus-baseline deltas, resampling respondents. This answers "is the gap real or sampling noise?"
   - **HTML report** embedding skill scores, the bootstrap panel, the probability-granularity check, the correlation-attenuation verdict, and the baseline appendix.

   The bundle contains `report.html`, `bootstrap.json`, `leakage_audit.json`, and `manifest.json`.

5. **Interpret the validation** — open `report.html` and read it in this order:
   - **Leakage** (`leakage_audit.json`): if any target has a flagged context pair, exclude that context and re-run, or treat that target's result as leakage-inflated. Do this before trusting any score.
   - **Skill scores** (unit-free, comparable across questions): `1 − loss/baseline_loss` vs uniform and vs the empirical marginal. Positive vs marginal means the model beats the population distribution on individuals. Read these as the headline, not top-1 accuracy — one answer per respondent cannot validate an individual probability, and accuracy rewards confident mode-guessing.
   - **Median vs mean NLL**: a good median NLL with a bad mean NLL is the signature of a few confident wrong guesses. Report both.
   - **Bootstrap paired deltas**: a twin beats the baseline only if its delta interval clears zero in the improving direction. A ✗ on the NLL delta (worse, interval clears zero) flags overconfidence even when accuracy improves.
   - **Probability granularity**: a model flagged "coarse" (mass piled on round numbers) has quantization-limited Brier/calibration — read those scores with that ceiling.
   - **Correlation attenuation**: if the twin's implied cross-question association is systematically below the empirical association, the twin is over-shrinking toward a common distribution — its marginals can look fine while its joint structure is washed out.

6. **Build and deliver the full report — do not stop before this.**

   The validation bundle above is the technical readout. The *full* report — survey profile, one-shot marginals, twin validation, and executive summary as **one scrollable page with a table of contents** — comes from:

   ```bash
   zwill report build --survey <survey_id> --path report_out
   ```

   This writes `report_out/report.html` (also `report_out/index.html`). **The task is not complete until that file exists and you have told the user its path.** The bundle supplies contextualized evidence; use it to author the interpretation rather than delegating the narrative to another model job.

## Useful single-step commands

Run or re-run one piece (all of these feed the same prediction store `twin-validate` reads):

```bash
zwill twin-baseline run --survey <survey> --heldout-questions <q1,q2,...>   # conditional baseline
zwill twin-results leakage-audit --survey <survey> --jobs <job> --path leakage.json
zwill twin-results bootstrap --survey <survey> --jobs <job1,job2> \
  --baseline-model baseline:conditional-embedding --path bootstrap.json
zwill twin-results report --survey <survey> --jobs <job1,job2> --format html --path report.html
zwill twin-results compare-report --survey <survey> --jobs <job_a,job_b> --path compare.html
zwill twin-results run-report --survey <survey> --job-id <job> --path run_audit.html   # audit prompts/import
```

## Stop Conditions

Do not make a positive claim until:

- codebook expansion is confirmed and the report has valid rows for each intended model;
- import issues and malformed responses are reviewed;
- the **leakage audit is clean** (or leaky targets are excluded);
- the **conditional baseline is present**, and any "twins add individual signal" claim is supported by a **bootstrap delta interval that clears zero** — not a bare point estimate;
- calibration is inspected: a model with high accuracy but poor NLL/ECE (overconfident misses) is not reported as good without that caveat;
- the held-out questions are documented well enough to interpret successes and failures.

The conditional baseline now **always runs** — with no embedding key it falls back to a built-in lexical embedder. If only that lexical fallback was available (a stderr warning says so), note the baseline is a *weaker* bar leaning mostly on covariates — install `zwill[conditional-baseline]` for the semantic model — but you can still make the twin-vs-baseline claim.
