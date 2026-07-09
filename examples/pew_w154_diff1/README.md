# Pew W154 DIFF1 Real Survey Ingestion

This example ingests real respondent-level Pew Research Center American Trends Panel Wave 154 data from:

```text
/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/
```

The ingested survey is the `DIFF1` battery: five questions about whether men and women are basically similar or basically different across domains. It imports 6,104 respondents and 30,520 answer rows.

The source data uses coded responses, but the importer expands the codebook before writing zwill files. Canonical `question_options` and answer values are the human-readable labels; source codes `1` and `2` are retained only in provenance notes.

Run:

```bash
./examples/pew_w154_diff1/ingest.sh
```

Or run the equivalent built-in workflow:

```bash
zwill workflow pew-demo
```

Both paths create a fresh persistent `zwill` project, convert the source CSV and metadata to JSONL import files, import them with `zwill`, and commit the survey. The shell script also prints status and displays the first 12 respondent rows with:

```bash
zwill table --survey pew_w154_diff1 --limit 12
```

It also adds source documentation with:

```bash
zwill context add --survey pew_w154_diff1 --input-path examples/pew_w154_diff1/context.md
```

Export the imported questions as an EDSL survey serialization:

```bash
zwill edsl-export --survey pew_w154_diff1 --path pew_w154_diff1.edsl.json
```

Export respondents as an EDSL `AgentList`, using selected questions as traits:

```bash
zwill edsl-export \
  --survey pew_w154_diff1 \
  --target agent-list \
  --questions diff1_a,diff1_e \
  --path pew_w154_diff1_agents.edsl.json
```

## AgentList Study Example

Run a full AgentList study that asks new, related gender-attitudes questions and writes a short literate HTML research report:

```bash
ZWILL_PYTHON=/Users/johnhorton/tools/ep/edsl/.venv/bin/python \
  ./examples/pew_w154_diff1/agent_study_example.sh
```

The example:

- starts from the normalized PEW metadata and respondent CSV by running `zwill workflow pew-demo --fresh --no-edsl`;
- imports and commits the codebook-expanded `pew_w154_diff1` survey;
- exports 30 respondents as EDSL agents with the five DIFF1 answers as traits, using zwill's default AgentList trait presentation template so the prompt labels them as prior survey question-and-answer pairs;
- asks: "In general, when it comes to being effective leaders in politics, are men and women basically similar or basically different?";
- also asks a free-text follow-up: "Given this respondent's prior answers, briefly describe this respondent's likely views on gender roles in society. Mention the evidence from their prior survey answers.";
- runs the serialized EDSL job with `zwill edsl-run`;
- imports both EDSL Results objects with `zwill agent-study import`;
- writes a standalone HTML report at `examples/pew_w154_diff1/workdir/agent_study_leadership/pew_w154_diff1_agent_study_report.html`;
- includes the stdout/stderr from each called `zwill` command in the report.

Set `ZWILL_PEW_SKIP_IMPORT=1` to reuse an existing workdir instead of rebuilding the PEW import.

Validate the job without model API calls:

```bash
ZWILL_EXAMPLE_DRY_RUN=1 \
ZWILL_PYTHON=/Users/johnhorton/tools/ep/edsl/.venv/bin/python \
  ./examples/pew_w154_diff1/agent_study_example.sh
```

## Twin-Building Tutorial

See `twin_building_tutorial.md` for a three-arm digital twin development example: baseline context only, context plus a one-shot frontier model estimate injected as `--twin-material`, and context plus the observed empirical marginal injected through the same generic material mechanism.

Export an EDSL probability job for the selected survey questions:

```bash
zwill edsl-export \
  --survey pew_w154_diff1 \
  --target probability-job \
  --questions diff1_a,diff1_e \
  --model openai:gpt-5.5 \
  --model google:gemini-2.5-pro \
  --model-param google:gemini-2.5-pro:max_tokens=8192 \
  --model-param google:gemini-2.5-pro:thinking_budget=4096 \
  --model-param google:gemini-2.5-pro:temperature=0 \
  --path pew_w154_diff1_probability_job.edsl.json
```

Run the exported EDSL probability job and write a serialized Results object:

```bash
zwill edsl-run \
  --job pew_w154_diff1_probability_job.edsl.json \
  --path pew_w154_diff1_probability_results.json.gz \
  --fresh \
  --progress-bar
```

Validate the job without model API calls:

```bash
zwill edsl-run \
  --job pew_w154_diff1_probability_job.edsl.json \
  --path pew_w154_diff1_probability_results.json.gz \
  --dry-run
```

Import EDSL Results and extract one-shot probability predictions:

```bash
zwill prob-results import \
  --survey pew_w154_diff1 \
  --input-path pew_w154_diff1_probability_results.json.gz
```

Compare elicited probabilities to committed respondent marginals:

```bash
zwill prob-results report --survey pew_w154_diff1 --job-id <job_id>
```

Write a standalone HTML report:

```bash
zwill prob-results report \
  --survey pew_w154_diff1 \
  --job-id <job_id> \
  --format html \
  --path pew_w154_diff1_probability_report.html
```

Use `--format json` for labeled probability vectors and machine-readable metrics. Use `--format csv --path pew_w154_diff1_probability_report.csv` for flat question/model metric rows. The HTML report embeds its report payload in a `report-data` JSON script tag and shows actual metric levels with a yellow uniform-baseline marker plus green/red arrows indicating whether each model beats or trails uniform for the selected metric.

Run the full workflow and import a saved EDSL Results object in one step:

```bash
zwill workflow pew-demo \
  --results-path /path/to/results.json.gz
```

This writes JSON, CSV, and HTML probability reports into the workdir.

By default, generated state and exports are written to `examples/pew_w154_diff1/workdir/`. Set `ZWILL_EXAMPLE_DIR=/path/to/workdir` to use a different persistent directory.
