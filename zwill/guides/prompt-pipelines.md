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
zwill edsl build --survey <survey> --target twin-probability-job \
  --heldout-question <q> --allow-unapproved \
  --twin-prompt-pipeline examples/twin_pipelines/dialectical.json \
  --model openai:gpt-5.5 --path twin_jobs.ep
ep run twin_jobs.ep --output twin_results.ep
zwill twin-results import --survey <survey> --input-path twin_results.ep
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
zwill edsl build … --seed 21 --sample-respondents 40 --path raw_jobs.ep
zwill edsl build … --seed 21 --sample-respondents 40 \
  --twin-prompt-pipeline dialectical.json --path dialectical_jobs.ep
# run + import both, then:
zwill twin-validate --survey <survey> --job-id <raw_job>  --out raw_val  --skip-baseline
zwill twin-validate --survey <survey> --job-id <dial_job> --out dial_val --skip-baseline
```

Read **NLL, ECE, and p(actual)**: a reasoning strategy earns its keep only if it
improves calibration. Record the imported raw and pipeline jobs under one
`twin-experiment` id to compare them head-to-head. Reusable `twin-approach`
records cover construction inputs; the pipeline itself remains part of the
exported job's construction audit.

## How to actually run these experiments (what past sessions learned)

A reasoning strategy is **not universally good or bad** — it is a *targeted fix
for a specific miscalibration*. Treat prompt design as an empirical loop, not a
guessing game:

1. **Diagnose the baseline first.** Run plain `raw` and look at its **ECE** and at
   *where it is confident and wrong*. Reasoning/hedging strategies help when the
   twin is **overconfident** (high ECE; it puts ~0.85 on answers it gets right
   only ~65% of the time). They tend to **slightly hurt an already-well-calibrated
   target**, because spreading mass pulls probability off answers the twin already
   had right. Don't add reasoning to a target that doesn't need it.
2. **A/B on identical respondents.** Export every arm with the same `--seed` and
   `--sample-respondents` so they hit the same held-out people; then the
   comparison is paired and the deltas are meaningful at small n.
3. **Compare the proper score (NLL) plus ECE and accuracy.** They can disagree: a
   strategy can improve accuracy/ECE while slightly worsening NLL (spreading mass
   helps the top pick and calibration but can cost log-loss on confident-correct
   rows). Decide which you care about for the use case.
4. **Break down where it helps.** Split raw's rows into *confident-and-wrong* vs
   *confident-and-right* and look at each arm's NLL delta on each group. A good
   calibration strategy shows a large positive delta on the confident-**wrong**
   group and only a small cost on the confident-**right** group; the net win is
   whether the rescue outweighs the cost.

**Worked example (two real experiments).** On a *well-calibrated* 5-point Likert
target (importance of a skill; raw ECE ~0.13), dialectical / base-rate / ordinal
strategies all slightly *worsened* NLL — `raw` was already hard to beat. On an
*overconfident* binary target (favor/oppose a carbon tax; raw ECE ~0.11 with
confident-wrong rows), an explicit **confidence-calibration ("hedge")** prompt —
"assign only the confidence the evidence warrants; don't go near 90/10 without a
strong signal" — improved NLL, Brier, *and* ECE (ECE 0.107 → 0.082), by rescuing
the confident-wrong rows (+0.4 NLL each) at only a small cost on confident-right
rows. Consider-the-opposite ("dialectical") rescued the wrong rows just as much
but paid a larger cost on the right rows, netting roughly a tie. Same strategies,
opposite verdicts on the two targets — which is the whole point of measuring.

## Bundled examples

In `examples/twin_pipelines/`:

- **`dialectical.json`** — 2 steps: `reason` (argue why the respondent would lean
  each way, note where evidence is thin) → `predict` (weigh and commit to a
  calibrated distribution).
- **`persona_reframe.json`** — 1 step: reframe the observed answers as a
  first-person persona sketch, then predict as that person.

Copy and edit these to try your own strategies. Backward compatible: with no
`--twin-prompt-pipeline`, the ordinary single `--prompt-variant` prompt is used.
