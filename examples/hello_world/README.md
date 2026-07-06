# Hello World Survey Fixture

This is the smallest useful `zwill` example: one survey question and five responses, using keyword-argument commands instead of bulk import.

## Suggested Test Flow

```bash
zwill init
zwill survey create --name hello_world
zwill raw add --survey hello_world --id questionnaire --path examples/hello_world/raw/questionnaire.md --kind questionnaire --title "Hello World Questionnaire"
zwill question add --survey hello_world --question-name favorite_color --question-type multiple_choice --question-text "Which color do you like best?" --question-option red --question-option blue --question-option green --role survey_item --source-raw questionnaire --source-note "Single hello-world test question."
zwill respondent add --survey hello_world --respondent-id r001 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r002 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r003 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r004 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r005 --weight 1.0 --metadata "sample_source=demo"
zwill answer add --survey hello_world --respondent-id r001 --question favorite_color --answer red
zwill answer add --survey hello_world --respondent-id r002 --question favorite_color --answer blue
zwill answer add --survey hello_world --respondent-id r003 --question favorite_color --answer green
zwill answer add --survey hello_world --respondent-id r004 --question favorite_color --answer blue
zwill answer add --survey hello_world --respondent-id r005 --question favorite_color --answer red
zwill table --survey hello_world
zwill status
zwill commit --survey hello_world
```

Expected counts after the five `answer add` commands:

- raw files: 1
- questions: 1
- respondents: 5
- answers: 5
- open quarantine issues: 0

## Table Output

The proposed display command is:

```bash
zwill table --survey hello_world
```

Unlike the state-changing commands in `SPEC.md`, `table` is meant for human inspection and prints a Rich table to stdout with one row per respondent and one column per question.

Run the shell script to build the fixture through `zwill` commands and print the table:

```bash
./examples/hello_world/show_table.sh
```

The `table` command depends on `rich`, which is declared in the project `pyproject.toml`.

## Declarative Workflow

The same small survey can be built through `zwill workflow run`:

```bash
zwill workflow dry-run examples/hello_world/workflow.json
zwill workflow run examples/hello_world/workflow.json --artifacts-dir /tmp/zwill_hello_workflow_artifacts
```

The workflow file runs ordinary `zwill` commands and captures each step's stdout/stderr plus a `manifest.json`.

## Agent Material Twin Check

Run a one-respondent digital twin example with and without non-survey agent material:

```bash
./examples/hello_world/agent_material_twin.sh
```

The script creates `examples/hello_world/workdir/agent_material/`, exports two EDSL jobs, runs both with `zwill edsl-run`, imports the Results objects, and lists the two twin runs. The only intended difference between the jobs is that the second one passes `--include-agent-material`, giving the agent a profile note that says the respondent's favorite color is blue.

For a no-network smoke run that only exports and dry-runs the EDSL jobs:

```bash
ZWILL_EXAMPLE_DRY_RUN=1 ./examples/hello_world/agent_material_twin.sh
```

## AgentList Study Check

Run a one-respondent AgentList construction example:

```bash
./examples/hello_world/agent_list_study.sh
```

The script creates `examples/hello_world/workdir/agent_list_study/`, exports an EDSL AgentList with selected answer traits plus construction instructions, inspects the AgentList, exports an EDSL job that asks the constructed agent a new question, runs that job, imports the serialized Results object, lists imported AgentStudy runs, and prints a table report of the extracted agent answer.

For a no-network smoke run:

```bash
ZWILL_EXAMPLE_DRY_RUN=1 ./examples/hello_world/agent_list_study.sh
```

The live run writes `agent_study_results.json.gz` in the workdir and stores imported data under `.zwill/projects/default/agent_studies/`.

## Twin Plan Lifecycle

Run a tiny digital-twin experiment-plan example:

```bash
./examples/hello_world/twin_plan_lifecycle.sh
```

The default run is export-only. It creates a two-question survey, registers two reusable twin approaches, exports an EDSL job for each approach from `twin_plan.json`, and prints `twin-experiment plan-status`.

To package the exported jobs for handoff after the default run:

```bash
cd examples/hello_world/workdir/twin_plan_lifecycle
zwill twin-experiment package --manifest jobs/manifest.json --output-dir run_package
```

The package contains copied job files, the plan, approach records, an empty `results/` directory, and `RUN.md` with the commands to execute and import the run.

To generate deterministic no-API Results, import them, and build comparison artifacts:

```bash
ZWILL_EXAMPLE_SYNTHETIC_RESULTS=1 ./examples/hello_world/twin_plan_lifecycle.sh
```

To run the exported jobs through EDSL, import Results, and build comparison artifacts:

```bash
ZWILL_EXAMPLE_RUN=1 ./examples/hello_world/twin_plan_lifecycle.sh
```

The live run writes:

- `jobs/manifest.json`
- one EDSL job JSON per approach
- imported twin Results under `.zwill/projects/default/surveys/hello_twin_plan/digital_twin_jobs/`
- `bundle/comparison.json`
- `bundle/plots/`
- `bundle/microdata.html`
- report-export prompt/context/job files under `bundle/`

After a synthetic or live run, inspect the plan with:

```bash
cd examples/hello_world/workdir/twin_plan_lifecycle
zwill twin-experiment dashboard \
  --survey hello_twin_plan \
  --plan-id hello_twin_plan \
  --metric nll \
  --model openai:gpt-5.5 \
  --bundle-manifest bundle/manifest.json \
  --path dashboard.html

zwill twin-approach diff \
  --survey hello_twin_plan \
  --left survey_answers_only \
  --right survey_answers_plus_prior \
  --format html \
  --path approach_diff.html
```
