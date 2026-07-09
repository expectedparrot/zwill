# Evaluating Digital Twins With Existing Company Data

This guide is for a company that already has survey data, customer research data, CRM records, product usage logs, or transactional data and wants to answer a practical question:

> If we build digital twins from the data we already have, how well would they predict responses to new questions or scenarios?

The recommended first project is not a production deployment. It is a validation study: hold out some known outcomes, construct twins without those answers, ask the twins to predict them, and compare the predictions with what people actually said or did.

## What You Need

Good inputs:

- A respondent/customer identifier that links records across tables.
- Survey questions, response options, and answer labels.
- Existing outcomes that can be treated as held-out targets, such as survey answers, choices, purchases, churn, feature adoption, referral behavior, or product ratings.
- Context variables that would be available when predicting a new question: demographics, prior survey answers, account features, transaction summaries, usage summaries, support history, or qualitative notes.
- A clear codebook for coded values. Numeric or abbreviated source codes should be expanded to human-readable labels before import.

Be careful with:

- Sensitive personal data. Minimize, redact, or aggregate where possible.
- Leakage. Do not include the target answer, downstream variables caused by the target, or summaries that encode the target.
- Free text. It can be useful, but inspect it for PII and leakage before including it in twin prompts.
- Non-representative data. The validation result describes the population and task you tested, not every future use case.

## The Core Validation Design

Use known answers as if they were unknown:

1. Pick held-out targets.
2. Construct each twin using only allowed context.
3. Ask a model for a probability distribution over each held-out answer option.
4. Score the predicted probabilities against actual answers.
5. Compare twins with simple baselines: uniform random and empirical marginals.
6. Inspect where twins work, where they fail, and whether failures are acceptable for the intended use.

The strongest evidence comes from targets that resemble future questions the company actually wants to ask.

## Step 1: Set Up A Project

```bash
zwill init
zwill project create client_eval --use
zwill survey create --name customer_validation
```

Import raw files as provenance before converting them into questions, respondents, and answers:

```bash
zwill raw add --survey customer_validation --id source_workbook --input-path customer_data.xlsx --kind workbook
```

For survey data, import:

- `questions.jsonl`: one row per question, with human-readable `question_options`.
- `respondents.jsonl`: one row per person/customer.
- `answers.jsonl`: one row per respondent/question answer.

Then commit the observed truth marginals:

```bash
zwill question import --survey customer_validation --input-path questions.jsonl
zwill respondent import --survey customer_validation --input-path respondents.jsonl
zwill answer import --survey customer_validation --input-path answers.jsonl
zwill commit --survey customer_validation
```

After import, run the survey profile report:

```bash
zwill survey report \
  --survey customer_validation \
  --format html \
  --path customer_validation_survey_report.html
```

Use this report to verify question wording, answer options, response distributions, missingness, and data-quality issues before doing any twin work.

## Step 2: Choose Held-Out Targets

Pick a small first batch, usually 5-10 targets:

- Include questions or outcomes that matter commercially.
- Include a mix of easy and hard targets.
- Prefer targets with enough observed cases per answer option.
- Avoid targets that are mechanically determined by context variables.
- Keep some targets for final validation and avoid tuning on all of them.

For survey questions, examples include:

- product satisfaction
- likelihood to refer
- reaction to an offer
- product choice
- risk tolerance
- service preference

For transactional data, first turn outcomes into question-like targets:

- `Will this customer renew in the next 90 days?` with options `Yes`, `No`.
- `Which plan did this customer choose?` with plan options.
- `How many referrals did this customer make?` with categorical bins.
- `Which product category did this customer buy next?` with category options.

## Step 3: Establish Baselines

Start with one-shot marginal predictions. These ask a frontier model to predict the population distribution for each target question, not individual respondents.

```bash
zwill edsl-export \
  --survey customer_validation \
  --target probability-job \
  --questions q12,q18,q24,q31 \
  --model openai:gpt-5.5 \
  --path one_shot_marginals.edsl.json

zwill edsl-run \
  --job one_shot_marginals.edsl.json \
  --path one_shot_marginals_results.json.gz

zwill prob-results import \
  --survey customer_validation \
  --input-path one_shot_marginals_results.json.gz

zwill prob-results report \
  --survey customer_validation \
  --format html \
  --path one_shot_marginals_report.html
```

This tells you whether a model can guess reasonable aggregate distributions from question text and context alone. It does not test individual-level twin quality.

### The conditional baseline (the one that matters)

Uniform and empirical-marginal baselines both ignore the individual. But the whole claim of a digital twin is *individual-level* prediction from a respondent's own answers. To know whether the frontier model is earning its keep, compare it against a cheap model that uses the same observed answers with no LLM reasoning:

```bash
zwill twin-baseline run \
  --survey customer_validation \
  --heldout-questions q12,q18,q24,q31
```

This embeds every (question, option) pair and each option label (OpenAI `text-embedding-3-small`, so `OPENAI_API_KEY` must be set), represents each respondent by the centroid of the options they actually chose, and fits a small logistic regression across the *non*-held-out questions. Because every feature is a semantic similarity rather than a question identity, the fitted model transfers to held-out target questions it never saw — the same generalization a twin claims. It writes predictions in the normal twin schema under a `baseline:conditional-embedding` label, so every `twin-results` report and comparison works on it directly.

If a frontier-model twin cannot beat this cheap conditional baseline, the LLM is not adding individual-level signal beyond what a trivial embedding model already recovers. That is the decisive comparison, not the twin-versus-marginal one.

## Step 4: Export A Digital Twin Job

A digital twin job asks the model to predict each held-out target for each respondent using that respondent's allowed context.

Start simple:

```bash
zwill edsl-export \
  --survey customer_validation \
  --target twin-probability-job \
  --heldout-questions q12,q18,q24,q31 \
  --context-question-count 20 \
  --sample-respondents 200 \
  --seed 123 \
  --complete-cases \
  --model openai:gpt-5.5 \
  --path twin_validation.edsl.json
```

Run and import:

```bash
zwill edsl-run \
  --job twin_validation.edsl.json \
  --path twin_validation_results.json.gz

zwill twin-results import \
  --survey customer_validation \
  --input-path twin_validation_results.json.gz
```

For larger studies, use full respondent sets rather than small samples once the workflow is working. Sampling is useful for debugging, but it can make marginal diagnostics noisy.

## Step 5: Inspect What Was Actually Run

Before interpreting scores, audit one run:

```bash
zwill twin-results run-report \
  --survey customer_validation \
  --job-id <job_id> \
  --format html \
  --path twin_run_report.html
```

Check:

- Which held-out questions were predicted.
- Which respondent context was included.
- Whether response options were available to the model.
- The Jinja prompt template and rendered user prompt.
- Example twin identities and raw model responses.
- Import issues or malformed responses.

This is where many validation mistakes show up: target leakage, missing option lists, unexpected respondent filtering, or prompts that do not match the intended task.

## Step 6: Score Twin Quality

Generate the high-level diagnostics report:

```bash
zwill twin-results report \
  --survey customer_validation \
  --job-id <job_id> \
  --format html \
  --view summary \
  --path twin_diagnostics_summary.html
```

Key metrics:

- Accuracy: whether the top predicted option matched the actual answer.
- `p(actual)`: probability assigned to the answer the person actually gave.
- NLL: `-log(p(actual))`; strongly penalizes confident wrong predictions.
- Brier: squared error against the actual one-hot answer.
- ECE: whether stated confidence matches observed correctness.
- Marginal L1/JS: how close the twin-implied population distribution is to the empirical distribution.

Compare against:

- Uniform baseline: no-information random prediction.
- Empirical marginal oracle: the true population distribution for the target question. This is not available for a genuinely new future question, but it is a useful validation benchmark when testing on known data.

## Step 7: Compare Construction Approaches

Try a few clear variants rather than many opaque prompt tweaks:

- survey answers only
- survey answers plus transaction summaries
- survey answers plus CRM/customer-success notes
- shorter versus longer context windows
- different held-out target families
- different frontier models

For two imported jobs:

```bash
zwill twin-results compare-report \
  --survey customer_validation \
  --jobs <job_id_1>,<job_id_2> \
  --format html \
  --path twin_job_comparison.html
```

For a more formal experiment log, record approaches:

```bash
zwill twin-experiment record \
  --survey customer_validation \
  --job-id <job_id_1> \
  --experiment-id baseline \
  --approach "Survey answers only"

zwill twin-experiment record \
  --survey customer_validation \
  --job-id <job_id_2> \
  --experiment-id transactions \
  --approach "Survey answers plus transaction summaries"

zwill twin-experiment compare --survey customer_validation --metric nll
zwill twin-experiment microdata --survey customer_validation --jobs <job_id_1>,<job_id_2> --path experiment_microdata.html
```

Use the microdata audit to inspect individual rows where an approach corrected a prediction, introduced a regression, or changed confidence without changing the top answer.

## Step 8: Decide Whether Twins Are Useful

Twins are promising when:

- They beat uniform baselines by a meaningful margin.
- They beat or approach empirical-marginal baselines on individual-level scores.
- They beat the cheap conditional baseline (`zwill twin-baseline run`) on individual-level scores. This is the strongest evidence that the frontier model adds real individual signal rather than rediscovering simple correlations.
- Their implied marginals are close enough for the intended decision.
- Calibration is acceptable: high confidence usually means high correctness.
- Performance is stable across target families and respondent subgroups.
- Row-level inspection shows plausible reasoning rather than leakage or artifacts.

Twins are not ready when:

- They only work on targets with obvious leakage.
- They are worse than uniform on important questions.
- They are much worse than empirical marginals and add little individual signal.
- They are severely overconfident on misses.
- They fail for commercially important subgroups.
- Marginals are badly distorted for decisions that depend on population shares.

The right threshold depends on the use case. For exploratory research or prioritizing survey questions, moderate lift may be useful. For automated customer decisions, the bar should be much higher.

## Step 9: Produce A Final Readout

### The one-command validation (recommended)

Once one or more twin jobs are imported, `zwill twin-validate` runs the whole
rigorous flow in a single gated step and writes a self-contained bundle:

```bash
zwill twin-validate \
  --survey customer_validation \
  --jobs <twin_job_id_1>,<twin_job_id_2> \
  --out validation_bundle
```

This runs, in order:

1. the **context leakage audit** over the held-out targets;
2. the **conditional baseline**, fit on the *same respondents* the twin jobs
   scored (so every model is compared on equal footing — needs `OPENAI_API_KEY`
   for embeddings; pass `--skip-baseline` to omit it);
3. **bootstrap confidence intervals** on each model's scores and on the paired
   twin-minus-baseline deltas; and
4. the **HTML validation report**, which embeds the skill scores, the bootstrap
   panel, probability-granularity check, correlation-attenuation verdict, and the
   baseline appendix.

The bundle directory contains `report.html`, `bootstrap.json`,
`leakage_audit.json`, and a `manifest.json`. The individual commands below remain
available when you want to run or re-run a single step.

### The report catalog

Use the report catalog to see what is available:

```bash
zwill report list --survey customer_validation
```

For a final narrative report:

```bash
zwill twin-study practitioner-report \
  --survey customer_validation \
  --job-id <job_id> \
  --path practitioner_report.html
```

The final readout should include:

- What data was used.
- What was held out.
- What baselines were used.
- Which twin construction approaches were tested.
- Performance by target family.
- Calibration and overconfidence risks.
- Examples of wins and failures.
- A recommendation: use now, use only for limited exploratory work, or collect better validation data first.

## Practical First Project

A good first project is deliberately small:

- 500-5,000 respondents/customers if available.
- 5-10 held-out targets.
- 2-3 construction approaches.
- 1-2 frontier models.
- Full-sample scoring once the import and prompt audit are clean.

The output should not just be a score. It should be a decision about whether twins are good enough for the company's intended use, and what data or prompt changes would most likely improve them.
