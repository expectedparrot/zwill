# Matrix ingestion and cross-survey study reference

This guide specifies the reproducible, script-free interfaces used to turn a
respondent-by-variable matrix into zwill records and to combine validation
bundles across surveys. For the full validation sequence, run `zwill guide`.

## Labeled Stata source extraction

When a public release is a labeled Stata file, first declare the batteries and
full questionnaire wording in a reviewable JSON specification, then extract
ordinary matrix-ingestion inputs:

```bash
zwill survey extract-stata --data survey.dta --spec batteries.json --out extracted
```

The specification declares `respondent_id`, `weight`, readable covariates, and
a `batteries` list. Each battery has an `id`, optional common `question_stem`,
and at least two `items`, each with a source `variable` and reviewed
`question_text`. Response and covariate labels come from the Stata value-label
tables by default; an item may supply an explicit `options` code-to-label map
when the archive has incomplete scale labels. A covariate can set
`use_value_labels: false` when its raw numeric value is itself meaningful, such
as age in years.

The command writes one `<battery>_metadata.json` and
`<battery>_respondents.csv` pair per declared battery plus `manifest.json` with
question, total-respondent, and complete-case counts. Stata extended missing
values become blank cells and are subsequently handled by the strict matrix
converter. Extraction never mutates `.zwill`; review its artifacts before
adding them with `zwill study add-matrix`. Install the optional reader with
`pip install 'zwill[survey-formats]'`.

## CSV source extraction and explicit recodes

CSV releases do not carry Stata-style variable and value labels. Declare those
labels, complete question wording, and any source transformations in a reviewed
JSON specification, then extract the batteries without a bespoke wrangling
script:

```bash
zwill survey extract-csv --data respondents.csv --spec batteries.json --out extracted
```

The top level requires `respondent_id` and either the name of a source `weight`
column or an explicit positive `weight_value` for uniform weights. Covariates
may declare `labels`. Every item requires `variable`, `question_text`, and an
`options` object mapping output codes to human-readable labels:

```json
{
  "source": {"title": "Example public-use survey"},
  "respondent_id": "caseid",
  "weight_value": 1,
  "covariates": [
    {"variable": "region", "labels": {"1": "North", "2": "South"}}
  ],
  "batteries": [
    {
      "id": "farming_practices",
      "items": [
        {
          "variable": "used_compost",
          "question_text": "Have you ever used or tried composting?",
          "options": {"0": "No", "1": "Yes"},
          "recode": {"": "0", "0": "0", "1": "1"}
        },
        {
          "variable": "used_manure",
          "question_text": "Have you ever used or tried farmyard manure?",
          "options": {"0": "No", "1": "Yes"}
        }
      ]
    }
  ]
}
```

When `recode` is present, it is exhaustive: an undeclared source code fails
with its CSV row number instead of passing through. This makes consequential
rules such as a questionnaire skip encoded as blank becoming `No` visible and
reviewable. Without `recode`, non-missing values must already match an option
code. `missing_codes` can be set globally or per item; after recoding, those
values become explicit missing answers in matrix ingestion.

The command writes the same metadata/matrix pair used by
`zwill survey ingest-matrix`. Its manifest reports total respondents,
post-transformation complete cases, and transformed-cell counts for every
battery. Extraction is read-only with respect to `.zwill`.

## Matrix metadata schema

`zwill survey ingest-matrix` accepts a UTF-8 CSV with one respondent per row and
a JSON metadata object. Conversion is strict and completes before `--apply`
changes project state.

```json
{
  "battery": "institutional_trust",
  "respondent_id": "caseid",
  "weight": "weight",
  "missing_codes": ["", 9, 99],
  "option_codes": [1, 2, 3, 4],
  "option_labels": ["A lot", "Some", "Not much", "None"],
  "covariates": [
    {
      "source": "party_code",
      "name": "party",
      "labels": {"1": "Democrat", "2": "Republican", "3": "Independent"}
    },
    "region"
  ],
  "items": {
    "courts": {
      "source_variable": "Q12A",
      "data_variable": "q12a_recoded",
      "question_name": "trust_courts",
      "question_stem": "How much do you trust",
      "item_text": "the courts?",
      "role": "survey_item"
    },
    "congress": {
      "source_variable": "Q12B",
      "question_stem": "How much do you trust",
      "item_text": "Congress?",
      "option_codes": [1, 2],
      "option_labels": ["Trust", "Do not trust"]
    }
  }
}
```

Top-level `option_codes` and `option_labels` are defaults; an item may override
both. Their lengths must match. `source_variable` identifies the original
questionnaire variable and is retained in question provenance. `data_variable`
is the CSV column to read after any upstream recode and defaults to
`source_variable`. `question_name` defaults to a stable slug made from `battery`
and the item key.

`respondent_id` and `weight` name required CSV columns (defaults:
`respondent_id` and `weight`). Each covariate can be a column-name string or an
object with `source`, optional output `name`, and an optional code-to-label
`labels` map. Missing covariates are omitted from respondent metadata. Every
non-missing value covered by a labels map must be known.

Codes are normalized so numeric JSON `1`, CSV `1`, and CSV `1.0` match. Values
listed in `missing_codes` produce an answer row with
`missing_code: "missing_or_refused"`. Unknown answer or covariate codes and
invalid weights are collected in `issues.jsonl`; the command then fails and
does not import anything.

## Matrix outputs and application

```bash
zwill survey ingest-matrix --survey trust --metadata metadata.json --respondents respondents.csv --out converted
```

The conversion directory contains:

- `questions.jsonl`: canonical labeled multiple-choice questions.
- `respondents.jsonl`: stable survey-prefixed ids, weights, labeled metadata,
  and matrix-row provenance.
- `answers.jsonl`: labeled answers or explicit missing-answer records.
- `manifest.json`: source paths, row counts, artifact paths, and schema version.
- `issues.jsonl`: all detected coded-value/weight issues, written only when the
  conversion fails.

Inspect these files, then apply the exact same conversion:

```bash
zwill survey ingest-matrix --survey trust --metadata metadata.json --respondents respondents.csv --out converted --apply
```

`--apply` creates the survey if necessary, archives the metadata and respondent
matrix with `zwill raw add`, and imports all three JSONL streams. When the JSON
conversion specification is derived from a different original metadata file,
pass `--raw-metadata original-file` to archive the original source instead.

## Cross-survey manifest schema

Study manifests may be JSON or YAML. Paths in a manifest are interpreted
relative to the manifest file unless a command explicitly receives an absolute
path.

```yaml
schema_version: 1
design:
  sample_respondents: 200
  seed: 20260706
  context_question_count: 8
  stratify_actual: true
  approach_id: battery_context
  models:
    - openai:gpt-5.5
  model_param:
    - google:gemini-2.5-pro:maxOutputTokens=8192
  primary_metric: nll
surveys:
  - survey: trust
    source_id: source_release_name
    plan_id: trust_validation_v1
    by_question_plan_ids:
      trust_courts: trust_byq_trust_courts_v1
    heldout_questions: [trust_courts, trust_congress]
    leakage_exclusions:
      trust_courts: [courts_followup]
result_paths:
  - results/**/*_results.ep
```

`survey` is required. `source_id` is an optional source-facing identifier used
in summary outputs and bundle templates. For `simultaneous` expansion, optional
`plan_id` preserves a predeclared or historically registered plan identifier.
Optional `by_question_plan_ids` maps targets to existing identifiers; targets
without a mapping receive deterministic derived ids. `heldout_questions` (or its alias
`questions`) can be a list or comma-separated string. `leakage_exclusions` maps
each target to context questions that must not enter its twin. Command-line
model, approach, sample-size, context-count, seed, and survey filters override
the corresponding design defaults.

## Expanding validation plans

```bash
zwill study expand --manifest study.yaml --mode by-question --out plans
```

The same lifecycle can be composed without a project-specific shell wrapper:

```bash
zwill study prepare --manifest study.yaml --mode by-question \
  --out workflow --results-dir results --approved-by "Reviewer Name"
ep run-manifest workflow/jobs/run-manifest.json --parallel 3 --execute
zwill study finish --manifest workflow/jobs/run-manifest.json \
  --results-dir results --audit-out reports/audits \
  --out 'reports/{survey}/validation'
```

`by-question` creates one draft plan per survey-question pair: the target answer
is held out and that plan's twin is reconstructed with all allowed evidence
except the target and declared leakage exclusions. This is the cleanest
leave-one-question-out comparison because twins and conditional models predict
the same cases from admissible information. `simultaneous` instead puts every
declared target for a survey in one plan and withholds them together.

The output `manifest.json` records every generated plan, target group, model,
path, and estimated prediction count. Existing plan files are never overwritten;
they are marked `existing: true` in the expanded manifest, preserving approval
and manual edits. Review and approve draft plans before exporting paid jobs.

## Stable summary artifacts

```bash
zwill study summarize --manifest study.yaml --bundle-template 'reports/{survey}/validation' --out summary
```

The template supports `{survey}` and `{source_id}`. Missing bundles fail the
command by default; `--allow-missing` writes a partial summary and lists omitted
bundle paths in `summary.json`.

- `surveys.csv`: one row per survey/model with respondent and question counts,
  mean model/uniform/marginal NLL, model-minus-benchmark NLL deltas, conditional
  and one-shot bootstrap estimates and confidence bounds, leakage count, ECE,
  source id, and bundle path.
- `questions.csv`: one row per survey/model/question with all available score
  fields plus conditional and one-shot NLL differences and intervals.
- `options.csv`: observed-versus-predicted option-level marginal records copied
  from `report_data.json` with survey/source identifiers.
- `marginals.csv`: question/model aggregate marginal diagnostics copied from
  `report_data.json` with survey/source identifiers.
- `summary.json`: schema version, counts, missing bundles, and artifact paths.

NLL deltas use `model mean NLL - benchmark mean NLL`; negative values favor the
model. Current bundles supply explicit benchmark means and deltas. For older
bundles, `study summarize` reconstructs them from loss and skill when possible.
Bootstrap bounds are copied from the paired exact-case validation artifacts,
not recomputed during summarization.

## Cost rollups

## Batch and run orchestration

For many matrices, a study manifest can define `matrix_defaults` plus
per-survey `matrix` overrides. Paths and naming templates may use `{survey}` and
`{source_id}`; `metadata_overlay` augments conversion metadata and
`covariate_codebook` centralizes readable labels. Every matrix is converted
successfully before any survey is applied.

```bash
zwill study init --path study.json
zwill study set-matrix-defaults --manifest study.json --data-variable-template 'item_{item}' --question-name-template '{battery}_{item}'
zwill study add-matrix --manifest study.json --survey trust --source-id TRUST_RELEASE --metadata trust_metadata.json --respondents trust_respondents.csv
zwill study check --manifest study.json
zwill study ingest --manifest study.json --out imports --apply
```

These commands do not discover or pair files implicitly. `init` writes an empty
manifest, `add-matrix` records exactly the paths supplied by the operator, and
`set-matrix-defaults` records only supplied policy. `check` is read-only: it
resolves paths and templates, loads overlays and codebooks, checks unique survey
ids and required matrix columns, and reports question/respondent/column counts
before conversion or project mutation.

Named `comparisons` expand model matrices without shell loops. Each comparison
can specify `mode`, `surveys`, `models` (with model and approach id), and a
`plan_id_template` using survey, model, model slug, question, and suffix fields.

```bash
zwill study expand --manifest study.yaml --comparison frontier_models --out plans
```

Approval, export, import, audit, and validation use machine-readable manifests.
Expected Parrot remains the explicit remote boundary: execute each `ep_command`
recorded by export, then return to zwill.

```bash
zwill study approve --manifest plans/manifest.json --approved-by reviewer
zwill study export --manifest plans/manifest.json --out jobs --results-dir results
zwill study import --manifest jobs/run-manifest.json --results-dir results
zwill study audit --manifest jobs/run-manifest.json --out reports/audits
zwill study validate --manifest jobs/run-manifest.json --out 'reports/{survey}/validation'
```

One-shot jobs are battery-specific, so adding a battery does not rerun completed
batteries. Summaries also expose `aggregates.json`, and reusable SVG diagnostics
consume the stable CSV outputs.

```bash
zwill study one-shot-build --manifest study.yaml --out jobs/one-shot --model openai:gpt-5.5
zwill study one-shot-import --manifest jobs/one-shot/run-manifest.json --results-dir results/one-shot --path probability-jobs.json
zwill study plot --summary-dir summary --out plots --model openai:gpt-5.5
```

## Semantic proximity diagnostic

An exploratory question-level diagnostic embeds the held-out wording and the
exact context questions actually shown in imported twin rows. It estimates
within-battery relationships between cosine proximity and twin NLL, accuracy,
and advantage over the matched conditional baseline. Intervals resample whole
batteries; the output is descriptive and has no causal interpretation.

```bash
zwill study semantic-proximity --manifest jobs/run-manifest.json \
  --questions summary/questions.csv --out semantic-proximity \
  --model openai:gpt-5.5 --embedder edsl \
  --embedding-model text-embedding-3-small
```

The command writes question-level proximity and outcome data, within-battery
residuals, and `analysis.json` with fixed-effect slopes and battery-cluster
bootstrap intervals.

Both `--manifest` and `--questions` are repeatable. This permits a pooled
fixed-effect analysis across independently resumable study extensions without
rewriting or concatenating their generated artifacts.

## Cost rollups

```bash
zwill study costs --manifest study.yaml --path study-costs.json
```

Alternatively repeat `--result PATH_OR_GLOB`. Recursive globs are supported and
duplicate paths are counted once. The result records total USD cost, call count,
per-model totals, and a per-file audit trail. These are realized costs read from
completed EDSL Results packages, not pre-run estimates. The command fails when
no files match so an empty glob cannot silently become a zero-cost study.
