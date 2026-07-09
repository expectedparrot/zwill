# PEW W154 DIFF1 Twin-Building Tutorial

This tutorial shows how to compare three twin-construction arms for one held-out survey question:

- Baseline: prior answers only.
- Frontier prior: prior answers plus a one-shot model estimate for the held-out question.
- Empirical marginal: prior answers plus the observed group marginal for the held-out question.

The second and third arms use the same generic `--twin-material` mechanism. They are not special CLI modes.

## 1. Build The PEW Demo Survey

```bash
zwill workflow pew-demo --no-edsl
cd examples/pew_w154_diff1/workdir
```

Use `diff1_a` as the held-out question and the other DIFF1 items as respondent context:

```bash
export SURVEY=pew_w154_diff1
export HELDOUT=diff1_a
export CONTEXT=diff1_b,diff1_c,diff1_d,diff1_e
export OUT=twin_building
mkdir -p "$OUT"
```

## 2. Get A One-Shot Frontier Prior

Export a one-shot probability job for the held-out question:

```bash
zwill edsl-export \
  --survey "$SURVEY" \
  --target probability-job \
  --question "$HELDOUT" \
  --model openai:gpt-5.5 \
  --path "$OUT/one_shot_prior_job.edsl.json"
```

Run and import it:

```bash
zwill edsl-run \
  --job "$OUT/one_shot_prior_job.edsl.json" \
  --path "$OUT/one_shot_prior_results.json.gz"

zwill prob-results import \
  --survey "$SURVEY" \
  --input-path "$OUT/one_shot_prior_results.json.gz" \
  --replace
```

Create a `twin-material` JSONL row from the imported one-shot probabilities. The important part is not this exact script; it is the material format.

```bash
python - <<'PY'
import json
from pathlib import Path

survey = "pew_w154_diff1"
heldout = "diff1_a"
rows = [
    json.loads(line)
    for line in Path(".zwill/projects/default/surveys/pew_w154_diff1/probability_predictions.jsonl").read_text().splitlines()
]
row = next(row for row in rows if row["question"] == heldout)
body = "One-shot frontier model estimate for the held-out question:\n" + "\n".join(
    f"- {option}: {probability:.3f}"
    for option, probability in row["probabilities"].items()
)
Path("twin_building/one_shot_prior_material.jsonl").write_text(json.dumps({
    "material_id": f"one_shot_prior_{heldout}",
    "kind": "model_prior",
    "survey": survey,
    "question": heldout,
    "title": "One-shot frontier model prior",
    "body_markdown": body,
    "metadata": {
        "source_job_id": row["job_id"],
        "model": row["model_label"],
    },
}) + "\n")
PY
```

## 3. Create Empirical-Marginal Material

This arm is an oracle-style comparison because it uses the observed group marginal for the held-out question.

```bash
python - <<'PY'
import json
from pathlib import Path

survey = "pew_w154_diff1"
heldout = "diff1_a"
truth = json.loads(Path(".zwill/projects/default/surveys/pew_w154_diff1/committed/truth_marginals.json").read_text())
marginal = truth["marginals"][heldout]
total = sum(value["weighted_count"] for value in marginal.values())
body = "Observed group marginal for the held-out question:\n" + "\n".join(
    f"- {option}: {value['weighted_count'] / total:.3f}"
    for option, value in marginal.items()
)
Path("twin_building/empirical_marginal_material.jsonl").write_text(json.dumps({
    "material_id": f"empirical_marginal_{heldout}",
    "kind": "oracle_marginal",
    "survey": survey,
    "question": heldout,
    "title": "Observed group marginal",
    "body_markdown": body,
}) + "\n")
PY
```

## 4. Run The Three Arms

Baseline:

```bash
zwill twin-study run \
  --survey "$SURVEY" \
  --heldout-question "$HELDOUT" \
  --context-questions "$CONTEXT" \
  --sample-respondents 100 \
  --seed 123 \
  --complete-cases \
  --model openai:gpt-5.5 \
  --output-dir "$OUT/baseline" \
  --replace
```

One-shot prior material:

```bash
zwill twin-study run \
  --survey "$SURVEY" \
  --heldout-question "$HELDOUT" \
  --context-questions "$CONTEXT" \
  --sample-respondents 100 \
  --seed 123 \
  --complete-cases \
  --twin-material "$OUT/one_shot_prior_material.jsonl" \
  --model openai:gpt-5.5 \
  --output-dir "$OUT/one_shot_prior" \
  --replace
```

Empirical-marginal material:

```bash
zwill twin-study run \
  --survey "$SURVEY" \
  --heldout-question "$HELDOUT" \
  --context-questions "$CONTEXT" \
  --sample-respondents 100 \
  --seed 123 \
  --complete-cases \
  --twin-material "$OUT/empirical_marginal_material.jsonl" \
  --model openai:gpt-5.5 \
  --output-dir "$OUT/empirical_marginal" \
  --replace
```

## 5. Record And Compare Approaches

Use the job ids printed by the three `twin-study run` commands.

```bash
zwill twin-experiment record --survey "$SURVEY" --job-id <baseline_job_id> --experiment-id baseline --approach "Prior survey answers only"
zwill twin-experiment record --survey "$SURVEY" --job-id <one_shot_job_id> --experiment-id one_shot_prior --approach "Prior survey answers plus one-shot frontier prior"
zwill twin-experiment record --survey "$SURVEY" --job-id <empirical_job_id> --experiment-id empirical_marginal --approach "Prior survey answers plus observed group marginal"

zwill twin-experiment compare --survey "$SURVEY" --metric nll
zwill twin-experiment compare --survey "$SURVEY" --metric brier --format csv --path "$OUT/twin_experiment_compare.csv"
zwill twin-experiment select --survey "$SURVEY" --metric nll --model openai:gpt-5.5
```

Interpretation:

- If one-shot prior improves NLL/Brier over baseline, the frontier model prior is helping the respondent-level twin.
- If empirical marginal improves more than one-shot prior, the one-shot prior may be directionally useful but not as good as knowing the observed group answer pattern.
- If neither material arm improves over baseline, the extra material may be distracting or redundant with the respondent context.
