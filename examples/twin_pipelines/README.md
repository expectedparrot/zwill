# Twin prompt pipelines

A **prompt pipeline** is a general slot for experimenting with how a digital twin
reasons and how its evidence is framed. It is an ordered list of **steps**; each
step is a free-text question whose template can use the scenario evidence and,
via EDSL answer-piping, every prior step's answer as `{{ <step_name>.answer }}`.
Only the **final step** is scored — it must include the `{{ output_contract }}`
marker (zwill replaces it with the canonical "return JSON probabilities"
instruction), so any pipeline stays measurable through the normal validation gate.

Use one on a twin export:

```bash
zwill edsl build --survey <survey> --target twin-probability-job \
  --heldout-question <q> --allow-unapproved \
  --twin-prompt-pipeline examples/twin_pipelines/dialectical.json \
  --model openai:gpt-5.5 --path twin_jobs.ep
```

Then `ep run twin_jobs.ep --output twin_results.ep`, followed by
`twin-results import` and `twin-validate` — the gate's
NLL / ECE / p(actual) tell you whether the pipeline builds a better-calibrated
twin than plain `raw`. Register a pipeline on a `twin-approach` to A/B several
head-to-head with `twin-experiment` / `twin-study compare`.

## Template variables

Each step template has access to the scenario evidence (rendered by EDSL at run
time): `survey_name`, `survey_context`, `observed_answers_text`,
`respondent_metadata`, `heldout_question_text`, `heldout_options_text`,
`agent_material_text`, `twin_material_text`, and each prior step's
`{{ <step_name>.answer }}`. The final step must contain `{{ output_contract }}`.

## Bundled examples

- **`dialectical.json`** — two steps: `reason` (argue why the respondent would
  lean each way, note where evidence is thin) → `predict` (weigh the arguments,
  commit to a calibrated distribution). Targets overconfidence by forcing
  reasoning before the answer format is seen.
- **`persona_reframe.json`** — one step: reframe the observed answers as a
  first-person persona sketch, then predict as that person (evidence reframing).

Copy and edit these to try your own strategies.
