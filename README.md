# zwill

**An open validation harness for survey digital twins.**

> *zwill* — from the German *Zwilling*, "twin."

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
[![EDSL](https://img.shields.io/badge/built%20on-EDSL-brightgreen.svg)](https://github.com/expectedparrot/edsl)

Digital twins — LLM stand-ins for survey respondents — are only useful if you can
tell whether they actually match real people. `zwill` is the measurement layer
that answers that question, honestly. It ingests real survey microdata, holds out
a question, asks an LLM twin (built from each respondent's *other* answers and
covariates) to predict it, and **scores the prediction against the truth with
proper scoring rules** (NLL, Brier, pinball/CRPS), calibration diagnostics,
weighted population metrics, bootstrap confidence intervals, and a leakage audit —
so a positive claim has to clear a real bar, not vibes.

It covers every common question shape — multiple-choice, numeric (as predicted
quantile distributions), rank / MaxDiff batteries, and open-ended answers (coded
into themes) — and provides a **prompt-pipeline** surface for experimenting with
*how* a twin reasons and framing its evidence, then measuring whether that
actually improves calibration.

`zwill` is a CLI built on [EDSL](https://github.com/expectedparrot/edsl) and
developed by [Expected Parrot](https://www.expectedparrot.com). The methodology is
open by design: the point of a validation gate is that anyone can inspect and run
it. Read the [survey digital-twin validation tutorial](https://expectedparrot.github.io/zwill/)
for a worked example, or run `zwill guide` for the complete CLI walkthrough.

## Install

`zwill` depends on [EDSL](https://github.com/expectedparrot/edsl), which it
installs from PyPI:

```bash
pip install -e .        # from a clone of this repo
```

Run tests:

```bash
pip install -e ".[test]"
pytest -q
```

### Developing against a local EDSL

If you are co-developing EDSL, install zwill first, then overlay your local EDSL
checkout as an editable install — no changes to `pyproject.toml` needed:

```bash
pip install -e .
pip install -e ../edsl   # or wherever your EDSL checkout lives
```

## Projects

Initialize zwill once in a work directory:

```bash
zwill init
```

This creates `.zwill/HEAD` and a default active project under `.zwill/projects/default/`. Survey state, AgentStudy imports, practitioner reports, and workflow artifacts are partitioned by the active project.

```bash
zwill project create client_a --use
zwill project current
zwill project list
zwill project use default
```

Use `ZWILL_PROJECT=<project_id>` to temporarily select a project for one command without changing `.zwill/HEAD`.

## Hello World

The smallest useful `zwill` project is one survey, one multiple-choice question, and five respondents. Run it in a scratch directory so you can see the state files `zwill` creates without mixing them into the repo:

```bash
export ZWILL_REPO="$(pwd)"
export ZWILL_HELLO_DIR="$(mktemp -d)"
cd "$ZWILL_HELLO_DIR"
zwill init
```

`zwill init` creates a local `.zwill/` directory. That directory is the project database: raw provenance, survey definitions, respondent records, answers, commits, exported jobs, and reports all live under the active project.

Create a survey and archive the original questionnaire as raw provenance:

```bash
zwill survey create --name hello_world
zwill raw add \
  --survey hello_world \
  --id questionnaire \
  --input-path "$ZWILL_REPO/examples/hello_world/raw/questionnaire.md" \
  --kind questionnaire \
  --title "Hello World Questionnaire"
```

`raw add` does not parse `questionnaire.md` or define a Markdown survey format. It only copies the source artifact into `.zwill/` and records its hash, kind, title, and source path so the structured survey can be audited later. Real imports should preserve their original files the same way, whether the source was CSV, XLSX, JSON, Qualtrics, SPSS/Stata, a PDF codebook, or a Markdown note.

Now add the actual structured survey item. This is the step that defines the question text, type, and options in `zwill`. The answer options are human-readable labels; these are the canonical labels that answers must use too.

```bash
zwill question add \
  --survey hello_world \
  --question-name favorite_color \
  --question-type multiple_choice \
  --question-text "Which color do you like best?" \
  --question-option red \
  --question-option blue \
  --question-option green \
  --role survey_item \
  --source-raw questionnaire \
  --source-note "Single hello-world test question."
```

Respondents can be added one at a time. This makes the data model explicit: each respondent has an id, an optional weight, and optional metadata.

Choose either the row-by-row commands or the JSONL import command below. Do not run both in the same scratch project.

```bash
zwill respondent add --survey hello_world --respondent-id r001 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r002 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r003 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r004 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r005 --weight 1.0 --metadata "sample_source=demo"
```

For normal use, put those records in JSONL and import the file instead. The command below loads the same five respondents from `examples/hello_world/respondents.jsonl`; each line in that file corresponds to one `respondent add` command above.

```bash
zwill respondent import \
  --survey hello_world \
  --input-path "$ZWILL_REPO/examples/hello_world/respondents.jsonl"
```

Answers work the same way. You can add them one at a time, and each answer is validated against both the respondent id and the question's declared options.

Choose either the row-by-row commands or the JSONL import command below. Do not run both in the same scratch project.

```bash
zwill answer add --survey hello_world --respondent-id r001 --question favorite_color --answer red
zwill answer add --survey hello_world --respondent-id r002 --question favorite_color --answer blue
zwill answer add --survey hello_world --respondent-id r003 --question favorite_color --answer green
zwill answer add --survey hello_world --respondent-id r004 --question favorite_color --answer blue
zwill answer add --survey hello_world --respondent-id r005 --question favorite_color --answer red
```

Or load the same answer records from JSONL:

```bash
zwill answer import \
  --survey hello_world \
  --input-path "$ZWILL_REPO/examples/hello_world/answers.jsonl"
```

Inspect the respondent-by-question table:

```bash
zwill table --survey hello_world
```

You should see one row per respondent and one column for `favorite_color`. With the five answers above, the empirical marginal distribution is `red = 2/5`, `blue = 2/5`, and `green = 1/5`.

Check validation state before committing the imported truth:

```bash
zwill status
zwill commit --survey hello_world
zwill status
```

`zwill commit` snapshots the validated survey state and stores truth marginals for later probability and digital-twin comparisons. After this point, exported model jobs can be scored against the committed empirical baseline.

Build the report bundle:

```bash
zwill report build --survey hello_world --path hello_world_report/
```

Open `hello_world_report/index.html` and `hello_world_report/survey-profile.html` to inspect question text, options, respondent counts, missingness, and marginals.

The same sequence is available as a script when you only want a quick smoke test:

```bash
"$ZWILL_REPO/examples/hello_world/show_table.sh"
```

The next examples build on this same survey-state model:

- `examples/hello_world/agent_material_twin.sh` builds one agent twice, without and with a profile note saying the respondent's favorite color is blue, then runs both exported jobs.
- `examples/hello_world/agent_list_study.sh` exports an EDSL AgentList, inspects selected traits and instructions, exports an EDSL job that asks the constructed agent a new question, and runs it.
- `examples/hello_world/twin_plan_lifecycle.sh` creates a two-question survey, registers reusable twin approaches, exports an experiment plan into EDSL jobs, and shows plan status. Set `ZWILL_EXAMPLE_SYNTHETIC_RESULTS=1` to generate no-API Results, import them, and build the comparison bundle; set `ZWILL_EXAMPLE_RUN=1` to run real EDSL jobs.

For more detail on every file and script in the fixture, see `examples/hello_world/README.md`.

## PEW Demo

Build the persistent Pew W154 DIFF1 demo project:

```bash
zwill workflow pew-demo
```

Import a saved EDSL Results object and write JSON, CSV, and HTML probability reports:

```bash
zwill workflow pew-demo --results-path example_prob_job.json.gz
```

Generated state and exports are written to:

```text
examples/pew_w154_diff1/workdir/
```

## LLM Survey Priors Ingestion

Ingest the curated normalized survey batteries from `llm-survey-priors`:

```bash
python3 examples/llm_survey_priors/ingest_normalized.py
```

The default run imports and commits 18 normalized surveys into:

```text
examples/llm_survey_priors/workdir/
```

Use `--convert-only` to write JSONL imports and a manifest without mutating `.zwill` state.

## Reporting Workflow

Build a report folder whenever you want to inspect a survey or refresh the current validation readout:

```bash
zwill report build --survey <survey> --path reports/<survey>/
```

The report folder is incremental. `index.html` is always written and links to every page that can be generated from currently available inputs. Pages whose analyses have not been run yet are shown as not ready with the missing inputs and next command. Rerun the same command after importing one-shot, generated one-shot analysis, or twin results to refresh the same folder.

Report bundles also use a Makefile-like staged layout. You can run the stages explicitly:

```bash
zwill report facts --survey <survey> --path reports/<survey>/
zwill report analyze --survey <survey> --path reports/<survey>/ --job-id <job_id>
zwill report render --survey <survey> --path reports/<survey>/ --job-id <job_id>
zwill report render --survey <survey> --path reports/<survey>/ --job-id <job_id> --final
```

`report render --final` is gated: for twin validation bundles it fails until a frontier-model executive analysis has been exported, run, imported, and rendered. Until then, `executive-summary.html` is a diagnostics preview, not a final executive interpretation.

When a matching generated one-shot analysis or generated executive analysis already exists under `.zwill/projects/<project>/practitioner_reports/<report_id>/`, `zwill report build` discovers it and uses the imported Markdown in the corresponding HTML page. One-shot matching uses the survey, probability job id, probability-model filter, and question set. Executive matching uses the survey, selected twin job ids, held-out questions, and prediction-model filter.

```text
reports/<survey>/
  facts/
  analysis/
  report/
  index.html
  survey-profile.html
  one-shot-marginals.html
  one-shot-coverage.html
  twin-validation.html
  executive-summary.html
  validation-diagnostics.html
  twin-comparison.html
  audit/
  data/
  stage-manifest.json
  report-manifest.json
```

Ask zwill which report pages are ready for a survey and what each one needs:

```bash
zwill report list --survey <survey>
zwill report list --survey <survey> --format json --path report_catalog.json
```

The report catalog checks local survey state and lists page readiness, available inputs, suggested output paths, and copyable commands. Use it when you are not sure which analysis should come next.

| report | when to use it | command |
|---|---|---|
| Survey Profile | Before twin work: inspect question text, options, response distributions, missingness, and data-quality issues. | `zwill report build --survey <survey> --path reports/<survey>/` |
| One-Shot Marginals and Coverage | After importing frontier-model marginal predictions for survey questions; includes requested/imported/malformed rows by job and model. Generate the analysis section with a frontier-model report job before treating this page as interpreted. | `zwill prob-results analysis-export --survey <survey> --path reports/<survey>/one-shot-marginals.html` then run/import/render |
| Twin Validation | Evaluate one or more twin result sets against observed held-out answers and empirical marginals. | `zwill report build --survey <survey> --path reports/<survey>/ --job-id <job_id>` |
| Executive Summary and Diagnostics | Deterministic diagnostic bundle for a validated AgentList: uniform and empirical-oracle lift, within-question permutation tests, Spearman rank order, and option-ordering diagnostics. Use the generated executive flow below before circulating decision-facing prose. | `zwill report build --survey <survey> --path reports/<survey>/ --job-id <job_id>` |
| Twin Run Audit | Audit one imported twin job: construction metadata, prompt template, rendered prompts, twin identity, and raw model responses. | `zwill report build --survey <survey> --path reports/<survey>/ --audit-job-id <job_id>` |
| Twin Comparison | Compare two or more imported twin jobs side by side, including empirical versus twin-implied marginals and option-level winners. | `zwill report build --survey <survey> --path reports/<survey>/ --jobs <job1>,<job2>` |
| Twin Experiment Microdata Audit | Inspect respondent-level changes across recorded construction approaches. | `zwill twin-experiment microdata --survey <survey> --jobs <job1>,<job2> --path experiment_microdata.html` |
| Generated Executive Summary | Ask a frontier model to interpret the actual validation diagnostics and render a decision-facing executive report. | `zwill twin-results executive-summary-export --survey <survey> --job-id <job_id> --path executive-summary.html` then run/import/render |

One-shot generated analysis uses the same staged pattern as the executive report:

```bash
zwill prob-results analysis-export --survey <survey> --job-id <probability_job_id> --path reports/<survey>/one-shot-marginals.html
zwill edsl-run --job .zwill/projects/default/practitioner_reports/<report_id>/job.edsl.json --path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz
zwill prob-results analysis-import --report-id <report_id> --input-path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz
zwill prob-results analysis-render --report-id <report_id> --path reports/<survey>/one-shot-marginals.html
```

The exported one-shot prompt uses compact summary statistics and per-question best/worst summaries, not raw prediction-row dumps. The model-written analysis should explain what one-shot aggregate marginal prediction means, how it performed against uniform, where it worked or failed, and what that implies as a deployable baseline for later digital twin validation.
| Practitioner Narrative Report | Generate a model-authored final interpretation from recorded artifacts. | `zwill twin-study practitioner-report --survey <survey> --job-id <job_id> --path practitioner_report.html` |

For a higher-level company-facing validation plan, see [Evaluating Digital Twins With Existing Company Data](guides/company_digital_twin_evaluation.md).

## Probability Reports

Run an exported EDSL job from any workdir under the repo:

```bash
zwill edsl-run --job probability_job.edsl.json --path probability_results.json.gz
```

`edsl-run` loads `.env` files with `python-dotenv`. By default it walks up from the current directory to find the nearest `.env`; pass `--env-path /path/to/.env` when running from a copied package or another directory. It then calls `job.run()` directly unless run flags are supplied.

```bash
zwill prob-results report --survey pew_w154_diff1 --job-id <job_id>
zwill prob-results report --survey pew_w154_diff1 --job-id <job_id> --format json --path report.json
zwill prob-results report --survey pew_w154_diff1 --job-id <job_id> --format csv --path report.csv
zwill prob-results report --survey pew_w154_diff1 --job-id <job_id> --format html --path report.html
```

Reports compare predicted probabilities to committed respondent marginals and a uniform-over-options baseline. Metrics include MAE, Brier score, and actual KL divergence.

## Agent Material

Agent material is respondent-level material used to construct an EDSL Agent or digital twin, but it is not a survey answer. It is excluded from tables, marginals, empirical baselines, and EDSL Survey exports unless explicitly requested for agent construction.

```bash
zwill agent-material add \
  --survey demo \
  --respondent-id r1 \
  --kind profile \
  --title "Favorite color" \
  --text "The respondent's favorite color is blue."
```

Use it explicitly in AgentList or twin exports:

```bash
zwill edsl-export --survey demo --target agent-list --include-survey-context --include-agent-material
zwill edsl-export --survey demo --target twin-probability-job --heldout-question q1 --include-agent-material
```

For AgentList exports, choose answer traits with `--question` or `--questions`; survey context and agent material are written into each EDSL Agent's `instruction` field for construction. The exported AgentList also uses a default `traits_presentation_template` that presents traits as prior survey question-and-answer pairs, not generic persona traits. Override it with `--traits-presentation-template` or `--traits-presentation-template-path`; use `--no-default-traits-presentation-template` to fall back to EDSL's default trait rendering. Filter material with `--agent-material-kind`, `--agent-material-tag`, and `--max-agent-material-chars`.

Inspect an exported AgentList:

```bash
zwill agent-list inspect --input-path agent_list.edsl.json
```

Ask constructed agents a new question by exporting an EDSL job from the AgentList:

```bash
zwill agent-study export \
  --agent-list agent_list.edsl.json \
  --question-name ask_favorite_color_blue \
  --question-type multiple_choice \
  --question-text "Given your profile and prior answers, is your favorite color blue?" \
  --question-option "Yes" \
  --question-option "No" \
  --model openai:gpt-5.5 \
  --path agent_study_job.edsl.json

zwill edsl-run --job agent_study_job.edsl.json --path agent_study_results.json.gz
zwill agent-study import --input-path agent_study_results.json.gz
zwill agent-study report --format table
```

Imported AgentStudy results keep the raw EDSL Results object under `.zwill/projects/<project_id>/agent_studies/<job_id>/raw/` and append extracted answers to `.zwill/projects/<project_id>/agent_studies/answers.jsonl`. Use `zwill agent-study list` to see imported runs, `zwill agent-study show --job-id <job_id> --include-summary` for metadata and summary counts, and `zwill agent-study report --job-id <job_id> --format json|csv|html` for downstream analysis.

## Digital Twins

Export an EDSL job that predicts respondent-level probabilities for a held-out answer:

```bash
zwill edsl-export \
  --survey w158_ccpolicy \
  --target twin-probability-job \
  --heldout-question a \
  --heldout-question b \
  --context-question-count 5 \
  --leakage-exclusion b:b_followup \
  --sample-respondents 100 \
  --seed 123 \
  --complete-cases \
  --stratify-actual \
  --include-agent-material \
  --twin-material one_shot_prior_material.jsonl \
  --model openai:gpt-5.5 \
  --model google:gemini-2.5-pro \
  --model-param google:gemini-2.5-pro:max_tokens=8192 \
  --model-param google:gemini-2.5-pro:thinking_budget=4096 \
  --model-param google:gemini-2.5-pro:temperature=0 \
  --path twin_job.edsl.json
```

Use `--leakage-exclusion <heldout_question>:<context_question>` for target-specific downstream or skip-logic exclusions. For kitchen-sink context, this removes the excluded context variable only when predicting that held-out target and records the exclusion in the exported job metadata and scenarios.

Run with `zwill edsl-run`, then import and score:

```bash
zwill twin-results import --survey w158_ccpolicy --input-path twin_results.json.gz
zwill twin-results report --survey w158_ccpolicy --job-id <job_id>
zwill twin-results report --survey w158_ccpolicy --job-id <job_id> --format html --path twin_report.html
```

Twin reports compare predicted probabilities to each respondent's actual held-out answer, with probability assigned to the actual answer, negative log likelihood, one-hot Brier score, top-1 correctness, and random plus empirical-marginal baselines. HTML reports include study metadata, metric definitions, model summaries, held-out/model filters, wrong-only filtering, and sortable respondent-level raw model responses.

Use the run report when you need to audit exactly how one imported job was constructed and what the model saw:

```bash
zwill twin-results run-report \
  --survey w158_ccpolicy \
  --job-id <job_id> \
  --format html \
  --path twin_run_report.html
```

The run report reads the stored raw Results object when available and shows construction metadata, held-out questions, model parameters, prompt examples, the Jinja prompt template, rendered system/user prompts, scenario inputs, twin identity, and raw model responses.

Use the comparison report when you want a direct side-by-side view of two or more imported jobs:

```bash
zwill twin-results compare-report \
  --survey w158_ccpolicy \
  --jobs <job_id_1>,<job_id_2> \
  --format html \
  --path twin_job_comparison.html
```

The comparison report groups results by held-out question, plots empirical marginals against each job's twin-implied marginal, marks the uniform baseline, and highlights the closest overall and option-specific marginal winners.

For the common case, run the full export/run/import/report loop in one command:

```bash
zwill twin-study run \
  --survey w158_ccpolicy \
  --approved-plan policy_holdout_v1.json \
  --heldout-questions a,b,c,d,e,f \
  --context-question-count 5 \
  --leakage-exclusion b:b_followup \
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

This writes an EDSL job, runs it with `job.run()`, stores the serialized Results object, imports the raw and extracted predictions, and writes a standalone HTML report. Direct twin-study runs require `--approved-plan <plan.json>`; use `--allow-unapproved` only for explicit ad hoc/debug/leakage experiments. Use `--report-json` or `--report-csv` to also write machine-readable report exports.

The empirical marginal baseline uses the observed committed distribution for each held-out question. It is useful for known survey questions because it asks whether respondent context beats the population distribution, but it is not available for a truly new question.

Digital twin report metrics:

| metric | meaning | direction |
|---|---|---|
| Accuracy | Share of rows where the highest-probability option matched the respondent's actual answer. | Higher is better |
| Error | `1 - accuracy`. | Lower is better |
| p(actual) | Mean probability assigned to the respondent's actual answer. | Higher is better |
| NLL | `-log(p(actual))`; penalizes confident misses. | Lower is better |
| NLL p95 | 95th percentile NLL. Useful for spotting rare overconfident misses hidden by mean NLL. | Lower is better |
| Brier | Squared error against the respondent's one-hot actual answer. | Lower is better |
| ECE | Expected calibration error comparing top-option confidence to top-1 correctness. | Lower is better |
| Uniform baseline | Equal probability over each held-out option. | Basic random-choice signal check |
| Empirical marginal baseline | Observed distribution for the held-out question. | Oracle-style benchmark for known questions |

Known limitations:

- Digital twin probability jobs currently use free-text JSON instructions so provider responses can still be malformed. Imports keep valid rows and record malformed rows as issues.
- The empirical marginal baseline depends on committed truth marginals for an already-observed survey item. It is not available for a genuinely new question.
- Provider APIs may need different model parameters. Gemini runs have generally needed larger `max_tokens` and `thinking_budget` settings.
- `twin-study run` is convenient, but the lower-level `edsl-export`, `edsl-run`, `twin-results import`, and `twin-results report` commands remain better for debugging individual steps.

Supplemental twin material can be injected into each twin scenario with `--twin-material`. This is deliberately general: the material can be a one-shot model prior, an empirical marginal, a subgroup fact, a stimulus note, or any other information you want to test. Markdown files apply to every scenario. JSON/JSONL records can be scoped with optional `survey`, `question` or `heldout_question`, and `respondent_id` fields.

```json
{"material_id":"frontier_prior_q1","kind":"model_prior","question":"q1","title":"Frontier one-shot prior","body_markdown":"A frontier model estimated: yes 0.70, no 0.30."}
{"material_id":"empirical_marginal_q1","kind":"oracle_marginal","question":"q1","title":"Observed group marginal","body_markdown":"Committed survey marginal: yes 0.50, no 0.50."}
```

Digital twin runs are recorded in a per-survey manifest:

```bash
zwill twin-study list --survey w158_ccpolicy
zwill twin-study show --survey w158_ccpolicy --job-id <job_id> --include-summary
```

Compare multiple runs, for example to check whether a different seed gives the same conclusion:

```bash
zwill twin-study compare \
  --survey w158_ccpolicy \
  --job-id <job_id_1> \
  --job-id <job_id_2>
```

Use `--format json` or `--format csv --path comparison.csv` for machine-readable comparisons. Twin HTML reports include run health, baseline diagnostics, confidence calibration bins, expected calibration error, NLL percentiles, overconfident misses, option confusion summaries, and the largest individual misses.

When two runs contain the same respondent, held-out question, and model, `twin-study compare` also reports paired top-choice changes. These diagnostics show how many twins changed their predicted answer, how many changes corrected a wrong answer, how many introduced a regression, and how much the probability assigned to the actual answer changed.

### Other held-out target types

The multiple-choice `twin-probability-job` above is the headline gate, but three other target types share the same export → run → import shape:

- **Numeric** (`--target numeric-twin-job` → `numeric-results import/report`): the twin predicts a quantile distribution, scored with pinball loss / CRPS / interval coverage vs a marginal-quantile baseline. Import the target with `question_type: numeric`.
- **Ranking / MaxDiff** (`--target rank-utility-twin-job` → `twin-results rank-report`): the twin scores item utilities; the report gives spearman, pairwise, top-K identification vs chance, and rank MAE. See `zwill guide show rank`.
- **Open-ended** (`--target open-codebook-job` / `open-coding-job` → `open-coding` commands): free-text answers are coded into themes, producing a `multiple_choice` question you then validate with the normal gate.

Respondent metadata (panel covariates) is included as twin context by default across all of these; suppress it with `--exclude-metadata-context` or `--exclude-metadata-key`. See `SPEC.md` §10–§10b and `zwill guide` for details.

## Twin Development Experiments

Use `twin-approach` and `twin-experiment` as a small lab notebook for twin construction. The intended loop is: compile respondent/survey sources, define reusable construction approaches, export EDSL jobs from a validation plan, run/import the Results objects, then compare approaches with metrics, plots, audit tables, and model-authored reports.

Register reusable construction approaches:

```bash
zwill twin-approach scaffold \
  --survey w158_ccpolicy \
  --approach-id baseline_context \
  --name "Prior survey answers only" \
  --context-question-count 5 \
  --path baseline_context.approach.json

zwill twin-approach add \
  --survey w158_ccpolicy \
  --input-path baseline_context.approach.json

zwill twin-approach add \
  --survey w158_ccpolicy \
  --approach-id one_shot_prior \
  --name "Prior answers plus one-shot model prior" \
  --description "Inject a model-estimated marginal distribution as supplemental twin material." \
  --context-question-count 5 \
  --twin-material one_shot_prior_material.jsonl \
  --model openai:gpt-5.5
```

Approaches are stored under the survey's `digital_twin_jobs/approaches.json`. They can be listed or shown:

```bash
zwill twin-approach list --survey w158_ccpolicy
zwill twin-approach show --survey w158_ccpolicy --approach-id baseline_context
zwill twin-approach note --survey w158_ccpolicy --approach-id baseline_context --text "Hypothesis: prior survey answers alone should capture stable policy preference."
```

Export an experiment plan into EDSL job files:

```json
{
  "plan_id": "policy_holdout_v1",
  "survey": "w158_ccpolicy",
  "heldout_questions": "ccpolicy_a,ccpolicy_b",
  "defaults": {
    "sample_respondents": 100,
    "seed": 789,
    "complete_cases": true
  },
  "arms": [
    {"approach_id": "baseline_context"},
    {"approach_id": "one_shot_prior"}
  ]
}
```

```bash
zwill twin-experiment init-plan \
  --survey w158_ccpolicy \
  --plan-id policy_holdout_v1 \
  --heldout-questions ccpolicy_a,ccpolicy_b \
  --approach-id baseline_context \
  --approach-id one_shot_prior \
  --sample-respondents 100 \
  --seed 789 \
  --path policy_holdout_v1.json

zwill twin-experiment approve \
  --input-path policy_holdout_v1.json \
  --approved-by <reviewer> \
  --note "Approved held-out targets, context policy, leakage exclusions, sample size, models, and seed."

zwill twin-experiment export-plan \
  --input-path policy_holdout_v1.json \
  --output-dir policy_holdout_v1_jobs
```

Plans start as drafts and must be approved before export. `export-plan` writes a `manifest.json`, one EDSL job JSON per arm, approved-plan provenance, and planned experiment records in `experiments.json`. Running remains explicit: use `zwill edsl-run --job <job>` for each exported job, then `zwill twin-results import --survey <survey> --input-path <results>`. After import, the existing comparison, plot, microdata, and report commands use the planned experiment records.

The approval review should check the held-out targets, construction approaches, context policy, target-specific leakage exclusions, respondent sample and seed, model list, and the prediction count formula: `respondents x held-out questions x approaches x models`. If a draft plan must be exported only for debugging, pass `--allow-unapproved`; this is intentionally visible in the command history.

Track a plan and import a directory of completed Results objects:

```bash
zwill twin-experiment plan-status \
  --survey w158_ccpolicy \
  --plan-id policy_holdout_v1

zwill twin-experiment note \
  --survey w158_ccpolicy \
  --plan-id policy_holdout_v1 \
  --text "Compare whether injected one-shot priors improve probability quality over prior answers alone."

zwill twin-experiment package \
  --manifest policy_holdout_v1_jobs/manifest.json \
  --output-dir policy_holdout_v1_run_package \
  --env-path /Users/johnhorton/tools/ep/zwill/.env

zwill twin-experiment import-plan-results \
  --manifest policy_holdout_v1_jobs/manifest.json \
  --results-dir policy_holdout_v1_results
```

`package` creates a portable run directory containing the original export manifest, plan, approach records, copied EDSL job files, an empty `results/` directory, and `RUN.md` with the exact commands a runner or agent should execute.

Once at least two arms have imported results, create a local artifact bundle:

```bash
zwill twin-experiment bundle \
  --survey w158_ccpolicy \
  --plan-id policy_holdout_v1 \
  --metric nll \
  --model openai:gpt-5.5 \
  --output-dir policy_holdout_v1_bundle \
  --report-export

zwill twin-experiment dashboard \
  --survey w158_ccpolicy \
  --plan-id policy_holdout_v1 \
  --metric nll \
  --model openai:gpt-5.5 \
  --bundle-manifest policy_holdout_v1_bundle/manifest.json \
  --path policy_holdout_v1_dashboard.html

zwill twin-approach diff \
  --survey w158_ccpolicy \
  --left baseline_context \
  --right one_shot_prior \
  --format html \
  --path policy_holdout_v1_approach_diff.html
```

The bundle writes `comparison.json`, plot artifacts, standalone microdata audit HTML/JSON, and, with `--report-export`, an EDSL report-writing job plus prompt/context files. It does not run the report-writing model.

The dashboard gives a deterministic plan-level status and performance page: arms, imported rows, selected metric, winning approach, paired response-change diagnostics, and links to bundle artifacts. `twin-approach diff` compares construction settings and metadata so performance differences can be tied back to what actually changed.

Inspect bundle paths and the selected approach:

```bash
zwill twin-experiment bundle-show --manifest policy_holdout_v1_bundle/manifest.json
```

You can also record an approach after exporting/importing a one-off twin job:

```bash
zwill twin-experiment record \
  --survey w158_ccpolicy \
  --job-id <job_id> \
  --experiment-id baseline_context_v1 \
  --approach "Baseline context, 5 prior answers" \
  --description "Held out each policy item and used five other survey answers as twin context." \
  --tag baseline \
  --primary-metric nll
```

Compare recorded approaches:

```bash
zwill twin-experiment compare --survey w158_ccpolicy --metric nll
zwill twin-experiment compare --survey w158_ccpolicy --metric brier --format csv --path experiments.csv
zwill twin-experiment select --survey w158_ccpolicy --metric nll --model openai:gpt-5.5
```

Supported metrics are `nll`, `brier`, `accuracy`, `p_actual`, `nll_vs_empirical`, and `brier_vs_empirical`. The comparison output records whether higher or lower is better, the selected row, the model, the job id, and the human-readable approach description. It also includes paired response-change diagnostics when experiments share respondent/question/model rows, so you can distinguish “same answers with better confidence” from “different answers that corrected or worsened individual predictions.”

Generate deterministic plot artifacts from the same paired comparison:

```bash
zwill twin-experiment plots \
  --survey w158_ccpolicy \
  --metric nll \
  --model openai:gpt-5.5 \
  --path experiment_plots
```

This writes `manifest.json`, SVG plots, an interactive microdata HTML table, and the paired row data used to draw them. The first plot bundle includes a paired `p(actual)` scatter, a top-choice-change summary, and a filterable twin microdata table showing respondent traits/observed answers, prompt template text, supplemental material, actual answer, model probabilities, and model notes. This lets reports show whether an approach changed answers, corrected mistakes, introduced regressions, mainly changed confidence, or behaved differently for specific respondent profiles.

For deeper row-level inspection across any number of experiments or held-out questions, generate a standalone microdata audit table:

```bash
zwill twin-experiment microdata \
  --survey w158_ccpolicy \
  --metric nll \
  --model openai:gpt-5.5 \
  --path experiment_microdata.html
```

The audit table groups rows by `respondent_id × heldout_question × model`, then shows one row per experiment response inside each group. This makes the observed traits, injected material, prompt template, model notes, source row, top choice, full probabilities, `p(actual)`, NLL, Brier, and correctness unambiguously row-specific. Filters cover question, model, actual answer, group diagnostic, search, and experiment checkboxes.

Export a model-authored report job that explains the approaches, methods, comparison metric, and results:

```bash
zwill twin-experiment report-export \
  --survey w158_ccpolicy \
  --metric nll \
  --model openai:gpt-5.5 \
  --include-plots experiment_plots/manifest.json \
  --report-model openai:gpt-5.5

zwill edsl-run \
  --job .zwill/projects/default/practitioner_reports/<report_id>/job.edsl.json \
  --path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz

zwill twin-experiment report-import \
  --report-id <report_id> \
  --input-path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz

zwill twin-experiment report-render \
  --report-id <report_id> \
  --path twin_experiment_report.html
```

`twin-experiment report` is the one-step convenience command. The separated export/import/render flow is better for agents and reproducibility because the prompt, context JSON, EDSL job, raw Results object, generated Markdown, and HTML wrapper are all stored.

## Cross-Survey Benchmarks

Use cross-survey benchmarks as a preflight check before trusting digital twins on a new survey workflow. A benchmark config is JSON:

```json
{
  "name": "small_preflight",
  "output_dir": "examples/llm_survey_priors/workdir",
  "defaults": {
    "sample_respondents": 20,
    "seed": 789,
    "context_question_count": 5,
    "complete_cases": true,
    "stratify_actual": true
  },
  "models": ["openai:gpt-5.5", "google:gemini-2.5-pro"],
  "model_params": [
    "google:gemini-2.5-pro:max_tokens=8192",
    "google:gemini-2.5-pro:thinking_budget=4096",
    "google:gemini-2.5-pro:temperature=0"
  ],
  "studies": [
    {"survey": "w158_ccpolicy", "heldout_questions": "a,b,c,d,e,f"},
    {"survey": "w157_skillimp", "heldout_question": "a"}
  ]
}
```

Run jobs and write a manifest:

```bash
zwill twin-benchmark run --config benchmark.json --replace
```

Generate reports from that manifest:

```bash
zwill twin-benchmark report --manifest small_preflight_run.json --format html --path benchmark.html
zwill twin-benchmark report --manifest small_preflight_run.json --format csv --path benchmark.csv
```

If jobs have already been run, put `job_id` on each study and use `zwill twin-benchmark report --config benchmark.json`. Practitioner interpretation should focus on whether models beat empirical marginals, whether ECE is acceptable, and whether NLL p95/max reveals overconfident misses hidden by accuracy.

Executive and practitioner reports use the same artifact-first flow as digital twin jobs. The report bundle computes deterministic diagnostics, plots, and supporting tables, but the decision-facing executive interpretation should be written by a frontier model from those actual artifacts.

For an executive validation summary, export a report-writing job, run it, import the Results object, and render HTML:

```bash
zwill twin-results executive-summary-export \
  --survey w158_ccpolicy \
  --job-id <job_id> \
  --path executive-summary.html \
  --model openai:gpt-5.5

zwill edsl-run \
  --job .zwill/projects/default/practitioner_reports/<report_id>/job.edsl.json \
  --path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz

zwill twin-results executive-summary-import \
  --report-id <report_id> \
  --input-path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz

zwill twin-results executive-summary-render \
  --report-id <report_id> \
  --path executive-summary.html
```

The exported prompt includes the uniform lift, empirical-oracle diagnostics, within-question permutation test, pairwise option-ordering accuracy, Spearman/rank diagnostics, available one-shot/no-persona baseline, held-out question count, run metadata, and context-policy warnings. It intentionally uses compact summary statistics, per-question tables, aggregate diagnostics, and a capped set of illustrative failures; it does not put every respondent-level prediction row or full raw prompt context into the report-writing prompt. The model-authored report must reconcile those facts; for example, a null permutation test should be framed as aggregate opinion structure rather than individual predictive power.

If a report-writing Results object imports with null Markdown, `zwill` reports that the job ran but returned no text and includes answer diagnostics. Usually the next step is to inspect the stored Results object and rerun with the compact executive export or a report model/context budget that can handle the prompt.

For a broader practitioner-facing report, prefer the single-survey validation flow: one survey, one imported twin-study job, and one report about what that survey's twins can and cannot support.

```bash
zwill twin-study practitioner-report-export \
  --survey w158_ccpolicy \
  --job-id <job_id>
```

Run the exported report-writing job, import the Results object, and render HTML:

```bash
zwill edsl-run \
  --job .zwill/projects/default/practitioner_reports/<report_id>/job.edsl.json \
  --path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz

zwill twin-study practitioner-report-import \
  --input-path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz

zwill twin-study practitioner-report-render \
  --report-id <report_id> \
  --path practitioner_report.html
```

`twin-study practitioner-report` is the one-step version: it exports the EDSL job, runs it, imports the Results object, and renders HTML.

Cross-survey practitioner reports are still available, but they are better treated as benchmark/meta reports rather than the default practitioner narrative. To export an EDSL report-writing job from recorded cross-survey benchmark data:

```bash
zwill twin-benchmark practitioner-report-export \
  --manifest small_preflight_run.json
```

The export stores the prompt, assembled report context, and EDSL job under `.zwill/projects/<project_id>/practitioner_reports/<report_id>/`. Run the job, import the raw Results object, and render HTML:

```bash
zwill edsl-run \
  --job .zwill/projects/default/practitioner_reports/<report_id>/job.edsl.json \
  --path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz

zwill twin-benchmark practitioner-report-import \
  --input-path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz

zwill twin-benchmark practitioner-report-render \
  --report-id <report_id> \
  --path practitioner_report.html
```

`twin-benchmark practitioner-report` remains one-step syntactic sugar: it exports the EDSL job, runs it, imports the Results object, and renders HTML. The separated commands are better for agents, inspection, rerendering after wrapper changes, and preserving provenance. The final HTML uses Expected Parrot branding, embeds the benchmark JSON, includes a `Copy Markdown` button for LLM use, and adds canned methodology sections explaining digital twins, the held-out-question design, and practical use cases.

## Declarative Workflows

Use `zwill workflow run` for reproducible command sequences. A workflow is a JSON/YAML file that runs ordinary shell commands, captures stdout/stderr for every step, and writes a manifest.

```json
{
  "name": "hello-agent-study",
  "description": "Example reusable command sequence.",
  "vars": {
    "survey": "hello_world",
    "workdir": "examples/hello_world/workdir/workflow"
  },
  "steps": [
    {
      "id": "init",
      "run": "zwill init",
      "cwd": "{{ workdir }}"
    },
    {
      "id": "create-survey",
      "run": "zwill survey create --name {{ survey }}",
      "cwd": "{{ workdir }}"
    }
  ]
}
```

Render without executing:

```bash
zwill workflow explain workflow.json
zwill workflow dry-run workflow.json --var survey=my_survey
```

Run and capture artifacts:

```bash
zwill workflow run workflow.json --artifacts-dir workflow_artifacts
```

Each step writes `NN_<step-id>.stdout.txt` and `NN_<step-id>.stderr.txt`; `manifest.json` records commands, return codes, timestamps, and paths. Use `--resume --artifacts-dir <dir>` to skip steps already marked successful in an existing manifest.

The older `zwill workflow pew-demo` command is a packaged demo helper for the PEW W154 fixture. It remains available for compatibility, but user-defined workflows should use `workflow run`.

## Agent Skills

Package-installed Codex skills for survey twin workflows live in `zwill/skills/`:

- `digital-twin-study-runner`: plans, runs, validates, and benchmarks zwill digital twin studies.
- `digital-twin-practitioner-report`: writes practitioner-focused reports about practical uses, failure modes, baselines, and next validation steps.

These skills are intended to turn uploaded surveys and zwill artifacts into practical guidance, not just metric tables.

Discover installed skill paths with:

```bash
zwill skills list
zwill skills path digital-twin-practitioner-report
```

See `examples/llm_survey_priors/sample_digital_twin_practitioner_report.html` for a model-authored report generated by `zwill twin-benchmark practitioner-report` from the cross-survey benchmark artifacts.
