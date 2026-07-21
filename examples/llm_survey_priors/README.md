# LLM Survey Priors Normalized Ingestion

This example ingests normalized survey structures from:

```text
/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized
```

The default source root contains the curated normalized batteries used by the microdata twins workflows. Each metadata/respondents pair becomes one `zwill` survey.

Run conversion only:

```bash
python3 examples/llm_survey_priors/ingest_normalized.py --convert-only
```

Run conversion plus zwill import and commit:

```bash
python3 examples/llm_survey_priors/ingest_normalized.py
```

Generated state is written to:

```text
examples/llm_survey_priors/workdir/
```

The importer writes:

- `workdir/imports/<survey>/questions.jsonl`
- `workdir/imports/<survey>/respondents.jsonl`
- `workdir/imports/<survey>/answers.jsonl`
- `workdir/imports/<survey>/summary.json`
- `workdir/imports/<survey>/issues.jsonl`
- `workdir/manifest.json`
- `workdir/.zwill/`

The importer expands all coded response values through metadata codebooks before writing zwill questions or answers. If a respondent file already stores a human-readable label, it is accepted only when it exactly matches a known option label.

Current curated ingestion result:

```text
surveys: 18
questions: 175
respondents: 94,561
answers: 1,051,520
conversion failures: 0
unmapped-code issues: 0
```

Inspect the imported project:

```bash
cd examples/llm_survey_priors/workdir
zwill status
zwill table --survey w154_diff1 --limit 5
```

Run a small digital twin study dry-run:

```bash
examples/llm_survey_priors/run_small_twin_study.sh
```

The script builds a `.ep` Jobs package with `zwill twin-study build`. By default it stops before model execution. To run the package with EDSL's CLI and import the resulting `.ep` Results package:

```bash
ZWILL_RUN_EDSL=1 examples/llm_survey_priors/run_small_twin_study.sh
```

Useful overrides:

```bash
ZWILL_SURVEY=w158_ccpolicy \
ZWILL_HELDOUT=a \
ZWILL_SAMPLE=10 \
ZWILL_SEED=123 \
examples/llm_survey_priors/run_small_twin_study.sh
```

Use another normalized source root:

```bash
python3 examples/llm_survey_priors/ingest_normalized.py \
  --source-root /path/to/normalized \
  --workdir /path/to/workdir
```

Use `--survey <id>` to import one or more matching survey ids. Survey ids are derived from metadata filenames, lowercased and normalized to underscores.
