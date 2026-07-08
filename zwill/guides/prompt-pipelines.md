# Twin prompt pipelines: experimenting with how a twin reasons

A **prompt pipeline** is a general slot for trying construction strategies — how a
digital twin *reasons* and how its evidence is *framed* — and measuring whether
they help, through the same validation gate. It is not a fixed menu of named
strategies: you write the steps, zwill guarantees the output stays scoreable.

## The idea

Instead of one fixed prompt → one probability, a pipeline is an **ordered list of
steps**. Each step is a free-text question asked to the twin in a single EDSL
survey. A later step can see every earlier step's answer, so you can decompose
question-answering — e.g. reason first, then predict — into separate model calls.

- A **length-1** pipeline is just one custom prompt (full control over evidence framing).
- A **length-2** pipeline is e.g. `reason` → `predict` as two calls, where `predict`
  pipes the reasoning forward.

Only the **final step is scored**. It must contain the `{{ output_contract }}`
marker, which zwill replaces with the canonical "return JSON `{probabilities:[…]}`"
instruction — so *any* pipeline you write produces a scoreable distribution and
runs through `twin-validate` / `twin-study compare` unchanged.

## Spec shape

A pipeline is a JSON file: a list of step objects.

```json
[
  {"name": "reason",  "template_path": "dialectical_reason.md"},
  {"name": "predict", "template_path": "dialectical_predict.md"}
]
```

Each step has:

| Field | Notes |
|---|---|
| `name` | Unique, `[A-Za-z_][A-Za-z0-9_]*`. Downstream steps pipe it as `{{ <name>.answer }}`. |
| `template` | Inline Jinja template string, **or** … |
| `template_path` | Path to a template file, resolved relative to the pipeline JSON. |

Rules (enforced at export): non-empty list; unique valid names; the **final** step
must include `{{ output_contract }}`; a **non-final** step must **not**.

## Template variables

A step template is rendered by EDSL at run time against the scenario evidence and
prior answers. Available variables:

- `survey_name`, `survey_context`
- `observed_answers_text` — the respondent's other answers, pre-formatted; loop the
  structured `observed_answers` list to reframe them yourself
- `respondent_metadata` — panel covariates (age/party/region …) as a dict
- `heldout_question_text`, `heldout_options_text`, `heldout_question_name`
- `agent_material_text`, `twin_material_text`
- `{{ <step_name>.answer }}` — any prior step's answer (EDSL answer-piping)
- `{{ output_contract }}` — final step only; zwill fills the scoreable-JSON instruction

## Running one

```bash
zwill edsl-export --survey <survey> --target twin-probability-job \
  --heldout-question <q> --allow-unapproved \
  --twin-prompt-pipeline examples/twin_pipelines/dialectical.json \
  --model openai:gpt-5.5 --path twin.edsl.json
zwill edsl-run --job twin.edsl.json --path twin_results.json.gz
zwill twin-results import --survey <survey> --path twin_results.json.gz
```

`--twin-prompt-pipeline` overrides `--prompt-variant`. The import scores the final
step (recorded as `scored_question_name`) and keeps the intermediate step answers
as `reasoning_steps`, so you can read *why* each twin predicted what it did.

## The experiment loop (does the strategy actually help?)

The whole point is to measure, not assume. Run the pipeline and a plain `raw`
baseline on the **same held-out respondents** (same `--sample-respondents` and
`--seed`), then compare through the gate:

```bash
# raw baseline and the pipeline, same seed -> same respondents
zwill edsl-export … --seed 21 --sample-respondents 40 --path raw.edsl.json
zwill edsl-export … --seed 21 --sample-respondents 40 \
  --twin-prompt-pipeline dialectical.json --path dial.edsl.json
# run + import both, then:
zwill twin-validate --survey <survey> --job-id <raw_job>  --out raw_val  --skip-baseline
zwill twin-validate --survey <survey> --job-id <dial_job> --out dial_val --skip-baseline
```

Read **NLL, ECE, and p(actual)**: a reasoning strategy earns its keep only if it
improves calibration. "Consider-the-opposite" (why-high/why-low) typically helps
*overconfident* targets and can slightly *hurt* already-confident ones — the gate
tells you which case you're in. Register a pipeline on a `twin-approach` to A/B
several head-to-head via `twin-experiment` / `twin-study compare`.

## Bundled examples

In `examples/twin_pipelines/`:

- **`dialectical.json`** — 2 steps: `reason` (argue why the respondent would lean
  each way, note where evidence is thin) → `predict` (weigh and commit to a
  calibrated distribution).
- **`persona_reframe.json`** — 1 step: reframe the observed answers as a
  first-person persona sketch, then predict as that person.

Copy and edit these to try your own strategies. Backward compatible: with no
`--twin-prompt-pipeline`, the ordinary single `--prompt-variant` prompt is used.
