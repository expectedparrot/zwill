---
name: digital-twin-study-runner
description: Use when planning, running, or debugging zwill digital twin studies from uploaded or ingested surveys, including held-out question selection, respondent sampling, model selection, baselines, cross-survey benchmarks, and artifact generation.
---

# Digital Twin Study Runner

Use this skill to design and run survey-based digital twin studies with `zwill`.

Core rule: always expand survey codebooks before import/export. Use human-readable answer labels in questions, answers, EDSL exports, reports, and baselines. If a code cannot be expanded, mark the affected item incomplete instead of treating the raw code as a label.

## Workflow

1. Inspect the survey
   - Confirm questions, options, respondent count, missingness, and codebook expansion.
   - Identify question types. Digital twin scoring currently expects held-out multiple-choice questions with known options.
   - Prefer complete-case respondent samples for the held-out/context set unless the study is explicitly about missingness.

2. Choose study design
   - Use 3-10 context questions for small studies; increase only when the survey has enough clean prior answers.
   - Hold out questions that represent different practical use cases: factual attitudes, policy preferences, sensitive items, behavioral self-reports, and demographic-like traits.
   - Sample respondents with a fixed seed. Use `--stratify-actual` when the held-out answer distribution is imbalanced.
   - Start with 20-100 respondents per held-out question for preflight; scale after the pipeline and report look correct.

3. Choose baselines
   - Always include uniform random baseline.
   - Include empirical marginal baseline for already-observed survey questions, but explain that it is an oracle-style baseline unavailable for truly new questions.
   - For new-question claims, evaluate whether the twin beats uniform and whether context improves over plausible simple priors.

4. Run jobs
   - Prefer `zwill twin-study run` for one survey and `zwill twin-benchmark run` for multiple surveys.
   - Use provider-qualified models, such as `openai:gpt-5.5` and `google:gemini-2.5-pro`.
   - For Gemini free-text JSON jobs, usually pass larger `max_tokens` and `thinking_budget`, and set `temperature=0` for benchmark comparability.
   - Let `zwill edsl-run` load the nearest `.env`; do not manually reconstruct EDSL run code unless debugging.

5. Validate artifacts
   - Check run status, imported row counts, malformed-response issues, and whether every selected model has results.
   - Open HTML reports and inspect respondent-level rows, calibration, largest misses, and option confusion.
   - If a model has high accuracy but poor NLL/ECE, inspect overconfident misses before making any positive claim.

## Useful Commands

Single-survey run:

```bash
zwill twin-study run \
  --survey <survey_id> \
  --heldout-questions <q1,q2> \
  --context-question-count 5 \
  --sample-respondents 100 \
  --seed 123 \
  --complete-cases \
  --stratify-actual \
  --model openai:gpt-5.5 \
  --model google:gemini-2.5-pro \
  --model-param google:gemini-2.5-pro:max_tokens=8192 \
  --model-param google:gemini-2.5-pro:thinking_budget=4096 \
  --model-param google:gemini-2.5-pro:temperature=0 \
  --output-dir examples/llm_survey_priors/workdir \
  --replace
```

Inspect and compare runs:

```bash
zwill twin-study list --survey <survey_id>
zwill twin-study show --survey <survey_id> --job-id <job_id> --include-summary
zwill twin-study compare --survey <survey_id> --job-id <job_a> --job-id <job_b>
```

Cross-survey benchmark:

```bash
zwill twin-benchmark run --config benchmark.json --replace
zwill twin-benchmark report --manifest benchmark_run.json --format html --path benchmark.html
```

## Stop Conditions

Do not proceed to a practitioner report until:

- codebook expansion is confirmed,
- the report has valid rows for each intended model,
- import issues are reviewed,
- baselines are present,
- and the held-out questions are documented well enough to interpret successes and failures.

