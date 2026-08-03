# zwill

**An open validation harness for survey digital twins.**

> *zwill* — from the German *Zwilling*, "twin."

<p align="center">
  <img src="docs/assets/zwill-package.png" width="760" alt="Zwill: an Expected Parrot looking at its reflection, framed by an E and brackets">
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
[![EDSL](https://img.shields.io/badge/built%20on-EDSL-brightgreen.svg)](https://github.com/expectedparrot/edsl)


`zwill` is a CLI built on [EDSL](https://github.com/expectedparrot/edsl) and
developed by [Expected Parrot](https://www.expectedparrot.com). 
It is a tool for developing and evaluating so-called `digital twins`. 
Read the [survey digital-twin validation tutorial](https://expectedparrot.github.io/zwill/)
for a worked example, or run `zwill guide` for the complete CLI walkthrough.

## Copy and paste into Codex or Claude Code

```text
Set up Zwill and help me run an auditable survey digital-twin validation in this
repository.

Install the current Zwill and EDSL `main` branches from GitHub. If `uv` is not
installed, first run `python -m pip install --upgrade uv`. Then install both
CLIs as a managed tool:

uv tool install --upgrade --force \
  --with-executables-from "edsl @ git+https://github.com/expectedparrot/edsl.git@main" \
  "zwill @ git+https://github.com/expectedparrot/zwill.git@main"

Verify that the expected command-line tools are available:

uv tool dir --bin
command -v zwill
command -v ep
zwill --help
ep --help

Confirm that `command -v` resolves both commands inside the directory reported
by `uv tool dir --bin`. If either command resolves to an older installation,
do not use it: run `uv tool update-shell`, verify again, or invoke the commands
by their absolute paths in the uv tool bin directory.

Let the EDSL CLI manage repository-local authentication. Run `ep auth status`.
If authentication is missing, run `ep auth login` and follow its login flow;
do not log in again when the existing `.env` or EDSL profile is already
configured. Never display, copy, or commit API keys. Then run
`ep profiles current` to inspect the redacted configuration and `ep check` to
verify connectivity before any paid model execution.

Once setup succeeds, run:

zwill guide
zwill next

Treat the bundled guide and each command's returned `next_steps` as the source
of truth. Run `zwill next` after every completed stage and continue until the
validation and report are complete or my input or approval is required. Start
with ingestion and codebook validation, preserve raw provenance, and do not run
remote inference until you have shown me the plan, model choice, prediction
count, and likely cost/time risk and I have approved it. Never expose held-out
answers or target-revealing fields to a twin.
```

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

## Output contract

Every command prints exactly one JSON envelope to stdout:

```json
{"schema_version": "2.0", "command": "zwill ...", "status": "ok", "argv": [...],
 "data": {...}, "warnings": [], "errors": [], "next_steps": [...]}
```

Failures set `status` to `"error"` and exit nonzero, with structured
`errors[].code`/`message`/`hint`. Pass `--human` (`-H`) on any command for a
human-readable rendering instead of the envelope; agents parsing output should
never use `--human`. File artifacts (`--path`, and `--format json|csv|html`
where offered) are written the same way in both modes.

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

### Archiving and deletion

Archive a survey when it should stop participating in normal workflows but may
be needed again. Archiving is reversible, keeps every survey artifact together,
and removes the survey from the active survey registry:

```bash
zwill survey archive --survey pilot --dry-run
zwill survey archive --survey pilot
zwill survey archived
zwill survey restore --survey pilot
```

Permanent deletion is deliberately more explicit. Start with `--dry-run` to get
the exact file inventory and byte count, then repeat the survey or project id as
the confirmation value:

```bash
zwill survey delete --survey pilot --dry-run
zwill survey delete --survey pilot --confirm pilot

zwill project delete old_project --dry-run
zwill project delete old_project --confirm old_project
```

`project delete` refuses to delete the active project or the only remaining
project. Successful permanent deletions leave a JSON receipt under
`.zwill/deletion_receipts/`; the deleted files themselves are not recoverable.
Study manifests are ordinary reviewable configuration files rather than managed
objects: removing one does not remove imported surveys, inference artifacts, or
reports.

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

## Reporting Workflow

Build a report folder whenever you want to inspect a survey or refresh the current validation readout:

```bash
zwill report build --survey <survey> --path reports/<survey>/
```

For one publishable evidence tree, write rigorous validation beneath the same
directory first: `zwill twin-validate ... --out reports/<survey>/validation`.
The subsequent `report build` detects that bundle and links its conditional
baseline, bootstrap, leakage, and structured evidence from the consolidated
index.

The report folder is incremental. `index.html` is always written and links to every page that can be generated from currently available inputs. Pages whose analyses have not been run yet are shown as not ready with the missing inputs and next command. Rerun the same command after importing one-shot or twin results to refresh the same folder.

Report bundles also use a Makefile-like staged layout. You can run the stages explicitly:

```bash
zwill report facts --survey <survey> --path reports/<survey>/
zwill report analyze --survey <survey> --path reports/<survey>/ --job-id <job_id>
zwill report render --survey <survey> --path reports/<survey>/ --job-id <job_id>
```

Zwill renders contextualized evidence, diagnostics, plots, and machine-readable facts. The coding agent using zwill is responsible for writing the final interpretation and keeping claims traceable to those artifacts.

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
zwill report list --survey <survey> --path report_catalog.json
```

The report catalog checks local survey state and lists page readiness, available inputs, suggested output paths, and copyable commands. Use it when you are not sure which analysis should come next.

| report | when to use it | command |
|---|---|---|
| Survey Profile | Before twin work: inspect question text, options, response distributions, missingness, and data-quality issues. | `zwill report build --survey <survey> --path reports/<survey>/` |
| One-Shot Marginals and Coverage | After importing model marginal predictions; compares predicted distributions with committed survey marginals and reports coverage. | `zwill report build --survey <survey> --path reports/<survey>/` |
| Twin Validation | Evaluate one or more twin result sets against observed held-out answers and empirical marginals. | `zwill report build --survey <survey> --path reports/<survey>/ --job-id <job_id>` |
| Executive Summary and Diagnostics | Deterministic diagnostic bundle for a validated AgentList: uniform and empirical-oracle lift, within-question permutation tests, Spearman rank order, and option-ordering diagnostics. | `zwill report build --survey <survey> --path reports/<survey>/ --job-id <job_id>` |
| Twin Run Audit | Audit one imported twin job: construction metadata, prompt template, rendered prompts, twin identity, and raw model responses. | `zwill report build --survey <survey> --path reports/<survey>/ --audit-job-id <job_id>` |
| Twin Comparison | Compare two or more imported twin jobs side by side, including empirical versus twin-implied marginals and option-level winners. | `zwill report build --survey <survey> --path reports/<survey>/ --jobs <job1>,<job2>` |
| Twin Experiment Microdata Audit | Inspect respondent-level changes across recorded construction approaches. | `zwill twin-experiment microdata --survey <survey> --jobs <job1>,<job2> --path experiment_microdata.html` |

For a higher-level company-facing validation plan, see [Evaluating Digital Twins With Existing Company Data](guides/company_digital_twin_evaluation.md).

## Probability Reports

Run a built EDSL job package with EDSL's CLI:

```bash
ep run probability_jobs.ep --output probability_results.ep
```

Zwill builds and imports artifacts; it does not execute model jobs. The `ep` CLI owns credentials, execution, caching, and writing the `Results` package.

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
zwill edsl build --survey demo --target agent-list --include-survey-context --include-agent-material --path agents.ep
zwill edsl build --survey demo --target twin-probability-job --heldout-question q1 --include-agent-material --path twin_jobs.ep
```

For AgentList exports, choose answer traits with `--question` or `--questions`; survey context and agent material are written into each EDSL Agent's `instruction` field for construction. The exported AgentList also uses a default `traits_presentation_template` that presents traits as prior survey question-and-answer pairs, not generic persona traits. Override it with `--traits-presentation-template` or `--traits-presentation-template-path`; use `--no-default-traits-presentation-template` to fall back to EDSL's default trait rendering. Filter material with `--agent-material-kind`, `--agent-material-tag`, and `--max-agent-material-chars`.

Inspect an exported AgentList:

```bash
zwill agent-list inspect --input-path agents.ep
```

Ask constructed agents a new question by exporting an EDSL job from the AgentList:

```bash
zwill agent-study export \
  --agent-list agents.ep \
  --question-name ask_favorite_color_blue \
  --question-type multiple_choice \
  --question-text "Given your profile and prior answers, is your favorite color blue?" \
  --question-option "Yes" \
  --question-option "No" \
  --model openai:gpt-5.5 \
  --path agent_study_jobs.ep

ep run agent_study_jobs.ep --output agent_study_results.ep
zwill agent-study import --input-path agent_study_results.ep
zwill agent-study report --human
```

Imported AgentStudy results keep the raw EDSL Results object under `.zwill/projects/<project_id>/agent_studies/<job_id>/raw/` and append extracted answers to `.zwill/projects/<project_id>/agent_studies/answers.jsonl`. Use `zwill agent-study list` to see imported runs, `zwill agent-study show --job-id <job_id> --include-summary` for metadata and summary counts, and `zwill agent-study report --job-id <job_id> --format json|csv|html` for downstream analysis.

## Digital Twins

Export an EDSL job that predicts respondent-level probabilities for a held-out answer:

```bash
zwill edsl build \
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
  --path twin_jobs.ep
```

Use `--leakage-exclusion <heldout_question>:<context_question>` for target-specific downstream or skip-logic exclusions. For kitchen-sink context, this removes the excluded context variable only when predicting that held-out target and records the exclusion in the exported job metadata and scenarios.

Run with EDSL's `ep` CLI, then import and score:

```bash
ep run twin_jobs.ep --output twin_results.ep
zwill twin-results import --survey w158_ccpolicy --input-path twin_results.ep
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

For the common case, build the job package, run it with EDSL, then import and report:

```bash
zwill twin-study build \
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
  --output-dir examples/llm_survey_priors/workdir

ep run examples/llm_survey_priors/workdir/jobs.ep \
  --output examples/llm_survey_priors/workdir/results.ep

zwill twin-results import --survey w158_ccpolicy \
  --input-path examples/llm_survey_priors/workdir/results.ep

zwill twin-results report --survey w158_ccpolicy --job-id <job_id> \
  --format html --path examples/llm_survey_priors/workdir/report.html
```

This separation is deliberate: zwill builds and analyzes packages, while EDSL's `ep` CLI owns remote execution and credentials. Approved plans remain required for production twin builds; use `--allow-unapproved` only for explicit ad hoc, debugging, or leakage experiments.

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
- Use the explicit `zwill edsl build` → `ep run` → `twin-results import` → `twin-results report` stages so job execution remains outside zwill.

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

Respondent metadata (panel covariates) is included as twin context by default across all of these; suppress it with `--exclude-metadata-context` or `--exclude-metadata-key`. See `zwill guide` and the worked tutorial for details.

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

Plans start as drafts and must be approved before export. `export-plan` writes a `manifest.json`, one EDSL `.ep` Jobs package per arm, approved-plan provenance, and planned experiment records in `experiments.json`. Run each package with `ep run <jobs.ep> --output <results.ep>`, then use `zwill twin-results import --survey <survey> --input-path <results.ep>`. After import, the comparison, plot, microdata, and report commands use the planned experiment records.

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
  --output-dir policy_holdout_v1_bundle

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

The bundle writes `comparison.json`, plot artifacts, and standalone microdata audit HTML/JSON.

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

ep run .zwill/projects/default/practitioner_reports/<report_id>/jobs.ep \
  --output .zwill/projects/default/practitioner_reports/<report_id>/results.ep

zwill twin-experiment report-import \
  --report-id <report_id> \
  --input-path .zwill/projects/default/practitioner_reports/<report_id>/results.ep

zwill twin-experiment report-render \
  --report-id <report_id> \
  --path twin_experiment_report.html
```

The separated export/import/render flow keeps model execution external (`ep run` between export and import) and is reproducible because the prompt, context JSON, EDSL job, raw Results object, generated Markdown, and HTML wrapper are all stored.

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

The bundle is intentionally evidence-first: it computes diagnostics, plots, and supporting tables. The coding agent authors decision-facing prose from those artifacts and is responsible for reconciling null tests, baseline comparisons, uncertainty, leakage checks, and sample limitations.

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
The study-runner skill turns uploaded surveys and zwill artifacts into a validated evidence bundle and guides the coding agent's interpretation.

Discover installed skill paths with:

```bash
zwill skills list
zwill skills path digital-twin-study-runner
```

## Command reference

Every registered command (options and defaults live in `zwill <command> --help`;
this table exists so documentation and CLI cannot drift apart —
`tests/test_contract_sync.py` enforces it in both directions).

| Command | Purpose |
|---|---|
| `zwill agent-list inspect` | Inspect an EDSL AgentList .ep package. |
| `zwill agent-material add` |  |
| `zwill agent-material import` |  |
| `zwill agent-material list` |  |
| `zwill agent-material show` |  |
| `zwill agent-study export` | Build an EDSL Jobs .ep package that asks an AgentList a new question. |
| `zwill agent-study import` | Import serialized EDSL Results from an agent-study job. |
| `zwill agent-study list` | List imported agent-study runs. |
| `zwill agent-study report` | Report imported agent-study answers. |
| `zwill agent-study show` | Show metadata for one imported agent-study run. |
| `zwill answer add` |  |
| `zwill answer import` | Import answers from a JSONL file (see `zwill guide show import-format`). |
| `zwill capabilities` | Describe the agent-facing output contract and execution boundary. |
| `zwill commit` |  |
| `zwill context add` |  |
| `zwill context set` |  |
| `zwill context show` |  |
| `zwill edsl build` | Build a durable EDSL .ep package for external execution with the ep CLI. |
| `zwill example fetch` | Write a bundled example dataset into a new local directory. |
| `zwill example list` | List example datasets bundled with the installed package. |
| `zwill guide` | Print the zwill agent workflow guide (or `guide list` / `guide show <name>`). |
| `zwill guide list` | List the bundled guides. |
| `zwill guide show` | Print a bundled guide by name. |
| `zwill init` |  |
| `zwill next` | Show which pipeline stage you are in and the exact command to run next. |
| `zwill numeric-results import` | Import and score continuous (quantile) twin predictions. |
| `zwill numeric-results report` | Summarize numeric twin predictions (pinball/CRPS/coverage vs marginal baseline). |
| `zwill open-coding codebook-import` | Store a derived codebook of themes for a free_text question. |
| `zwill open-coding import` | Create a coded multiple_choice question from an open-coding-job run. |
| `zwill prob-results import` |  |
| `zwill prob-results report` |  |
| `zwill project create` | Create a project under .zwill/projects/. |
| `zwill project current` | Show the active project. |
| `zwill project delete` | Inventory and permanently delete a non-active, non-sole project with exact confirmation. |
| `zwill project list` | List projects. |
| `zwill project show` | Show project details. |
| `zwill project use` | Set the active project. |
| `zwill quarantine list` |  |
| `zwill quarantine resolve` |  |
| `zwill question add` |  |
| `zwill question import` | Import questions from a JSONL file (see `zwill guide show import-format`). |
| `zwill raw add` |  |
| `zwill raw list` |  |
| `zwill report analyze` | Build deterministic analysis artifacts for the report bundle. |
| `zwill report build` | Build an incremental HTML report folder with an index page and all ready report pages. |
| `zwill report facts` | Build deterministic fact artifacts for the report bundle. |
| `zwill report list` | List available reports for a survey and show readiness/suggested commands. |
| `zwill report render` | Render report HTML from available deterministic facts and analysis. |
| `zwill respondent add` |  |
| `zwill respondent import` | Import respondents from a JSONL file (see `zwill guide show import-format`). |
| `zwill skills list` | List zwill agent skills installed with the package. |
| `zwill skills path` | Print the installed path for one zwill agent skill. |
| `zwill status` |  |
| `zwill survey create` |  |
| `zwill survey archive` | Reversibly move a survey out of normal project workflows. |
| `zwill survey archived` | List archived surveys and their inventories. |
| `zwill survey delete` | Inventory and permanently delete a live or archived survey with exact confirmation. |
| `zwill survey restore` | Restore an archived survey. |
| `zwill survey report` | Report survey questions, options, distributions, and data-quality issues. |
| `zwill survey ingest-matrix` | Strictly convert and optionally import a metadata/codebook plus respondent-by-variable CSV. |
| `zwill survey extract-csv` | Extract reviewed batteries and explicit recodes from a CSV into strict matrix-ingestion inputs. |
| `zwill survey extract-stata` | Extract reviewed batteries from a labeled Stata file into strict matrix-ingestion inputs. |
| `zwill study expand` | Expand a cross-survey manifest into simultaneous or leave-one-question-out draft plans. |
| `zwill study prepare` | Compose expansion, named approval, and export into an inspectable workflow directory. |
| `zwill study init` | Create a minimal empty JSON or YAML study manifest without guessing source files. |
| `zwill study add-matrix` | Add one explicitly named metadata/respondent source pair to a study manifest. |
| `zwill study set-matrix-defaults` | Record shared matrix naming, missing-code, and codebook policy. |
| `zwill study check` | Validate and inventory declared study inputs without changing project state. |
| `zwill study approve` | Approve every plan named by an expanded study manifest. |
| `zwill study export` | Export approved study plans and write a resumable remote-run manifest. |
| `zwill study import` | Import all completed Results packages named by a study run manifest. |
| `zwill study audit` | Produce per-job run audits from a study run manifest. |
| `zwill study validate` | Validate imported study jobs by battery, with optional one-shot baselines. |
| `zwill study ingest` | Convert and optionally apply a batch of respondent matrices declaratively. |
| `zwill study one-shot-build` | Build one independently runnable marginal-probability job per battery. |
| `zwill study one-shot-import` | Import battery-specific one-shot results and write their job-id mapping. |
| `zwill study summarize` | Normalize validation bundles into tidy survey, question, marginal, and option artifacts. |
| `zwill study finish` | Import complete manifest results, render prompt audits, and validate each survey. |
| `zwill study plot` | Render reusable SVG diagnostics from stable study summary artifacts. |
| `zwill study semantic-proximity` | Estimate exploratory within-battery links between held-out performance and the semantic proximity of actual context questions. |
| `zwill study costs` | Roll up actual call counts, token usage, and costs from EDSL Results packages. |

The complete matrix metadata, cross-survey manifest, and generated artifact
schemas are available from `zwill guide show study-reference`.
| `zwill survey show` |  |
| `zwill table` |  |
| `zwill twin-approach add` | Add or update a reusable digital twin construction approach. |
| `zwill twin-approach diff` | Compare two reusable approaches or planned experiment approaches. |
| `zwill twin-approach list` | List reusable twin construction approaches. |
| `zwill twin-approach note` | Show or set markdown notes for a reusable twin approach. |
| `zwill twin-approach scaffold` | Write a starter JSON approach definition. |
| `zwill twin-approach show` | Show a reusable twin construction approach. |
| `zwill twin-baseline run` | Fit an embedding + logistic conditional baseline and store its held-out predictions. |
| `zwill twin-benchmark practitioner-report-export` | Build a model-authored benchmark-report EDSL job for external execution with the ep CLI. |
| `zwill twin-benchmark practitioner-report-import` | Import externally-run practitioner-report Results into the stored report. |
| `zwill twin-benchmark practitioner-report-render` | Render the imported practitioner report to HTML. |
| `zwill twin-benchmark report` | Report a cross-survey twin benchmark. |
| `zwill twin-benchmark run` | Run a config-driven cross-survey twin benchmark. |
| `zwill twin-experiment approve` | Mark a reviewed twin experiment plan as approved for export/run. |
| `zwill twin-experiment bundle` | Create deterministic comparison, plot, and microdata artifacts for a plan. |
| `zwill twin-experiment bundle-show` | Show paths and selected result for a bundle manifest. |
| `zwill twin-experiment compare` | Rank recorded twin experiments by a selected metric. |
| `zwill twin-experiment dashboard` | Write a deterministic HTML dashboard for a twin experiment plan. |
| `zwill twin-experiment export-plan` | Export EDSL jobs from a reusable twin experiment plan. |
| `zwill twin-experiment import-plan-results` | Import Results files that match jobs in a plan manifest. |
| `zwill twin-experiment init-plan` | Write a starter JSON twin experiment plan. |
| `zwill twin-experiment list` | List recorded twin-development experiments. |
| `zwill twin-experiment microdata` | Generate a standalone HTML audit table of twin microdata across experiments. |
| `zwill twin-experiment note` | Show or set markdown notes for a twin experiment plan. |
| `zwill twin-experiment package` | Create a portable run package from an exported twin experiment plan. |
| `zwill twin-experiment plan-status` | Show exported/imported status for a twin experiment plan. |
| `zwill twin-experiment plots` | Generate deterministic plot artifacts for recorded twin experiment comparisons. |
| `zwill twin-experiment record` | Record an approach for an existing digital twin job. |
| `zwill twin-experiment report-export` | Build a model-authored experiment-report EDSL job for external execution with the ep CLI. |
| `zwill twin-experiment report-import` | Import externally-run experiment-report Results into the stored report. |
| `zwill twin-experiment report-render` | Render the imported experiment report to HTML. |
| `zwill twin-experiment select` | Return the best recorded twin experiment for a selected metric. |
| `zwill twin-experiment validate` | Lint a plan file (resolve questions/models/counts) before approve/export. |
| `zwill twin-results bootstrap` | Bootstrap confidence intervals over respondents for model scores and paired deltas vs a baseline. |
| `zwill twin-results calibrate-marginal` | KL/IPF calibrate twin probabilities to target question marginals. |
| `zwill twin-results compare-report` | Compare two or more imported digital twin jobs side by side. |
| `zwill twin-results executive-summary` | Build a deterministic twin validation summary with plots and diagnostics. |
| `zwill twin-results export` | Export stored digital twin predictions to CSV. |
| `zwill twin-results import` |  |
| `zwill twin-results leakage-audit` | Flag context questions that near-deterministically predict a target (Cramer's V on observed answers). |
| `zwill twin-results marginal-diagnostics` | Compare twin-implied aggregate marginals to one-shot or empirical marginals. |
| `zwill twin-results package` | Export stored digital twin predictions to CSV and zip it. |
| `zwill twin-results rank-report` | Report rank-utility digital twin validation results. |
| `zwill twin-results report` |  |
| `zwill twin-results retry-malformed` | Build a retry job containing only the scenarios whose model rows were dropped as malformed, so a couple of provider hiccups don't force a full re-run. |
| `zwill twin-results run-report` | Audit a digital twin job's construction, import, and prompt examples. |
| `zwill twin-study build` | Build a digital-twin Jobs.ep package for external execution with the ep CLI. |
| `zwill twin-study compare` | Compare two or more digital twin study runs. |
| `zwill twin-study export-holdout` | Export chunked EDSL jobs for true held-out question specs. |
| `zwill twin-study import-results-dir` | Import all EDSL twin result files in a directory with stable chunk job ids. |
| `zwill twin-study list` | List recorded digital twin study runs. |
| `zwill twin-study practitioner-report-export` | Build a model-authored single-study report EDSL job for external execution with the ep CLI. |
| `zwill twin-study practitioner-report-import` | Import externally-run practitioner-report Results into the stored report. |
| `zwill twin-study practitioner-report-render` | Render the imported practitioner report to HTML. |
| `zwill twin-study show` | Show metadata for one digital twin study run. |
| `zwill twin-validate` | Run the full twin validation flow (leakage audit + conditional baseline + bootstrap CIs + HTML report) for one or more twin jobs. |
| `zwill version` | Report the installed zwill build and envelope schema version. |
| `zwill workflow dry-run` | Render a workflow file without executing commands. |
| `zwill workflow explain` | Show rendered workflow steps without executing commands. |
| `zwill workflow pew-demo` | Build the persistent PEW W154 DIFF1 demo project. |
| `zwill workflow run` | Run a declarative workflow file and capture step artifacts. |
