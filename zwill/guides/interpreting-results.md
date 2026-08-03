# Interpreting a zwill twin validation bundle

Read `report.html` (and the JSON artifacts) from a `zwill twin-validate` bundle in
this order. The goal is a claim you can defend, not the highest-looking number.

## 1. Leakage first (`leakage_audit.json`)

If any held-out target has a flagged context pair (high bias-corrected Cramér's V),
a context answer near-deterministically predicts the target — the twin may be
copying, not modelling, and that target's score is inflated. Exclude the flagged
context and re-run, or caveat that target explicitly. Do this before trusting any
score.

## 2. Skill scores, not accuracy

The report leads with **skill scores**: `1 − loss / baseline_loss` vs uniform and
vs the empirical marginal, unit-free and comparable across questions. Positive "vs
marginal" means the model beats the population distribution on individuals.

Read skill scores as the headline. Top-1 **accuracy is a sanity check, not a
result** — one answer per respondent cannot validate an individual probability, and
accuracy rewards confident mode-guessing.

## 3. The conditional baseline is the bar

The `baseline:conditional-embedding` row is a cheap "individual information, no
frontier model" baseline: an XGBoost classifier over question/option embeddings,
the respondent's panel covariates, and embedding-similarity scalars. It is fit on
the same respondents using only what is available for a genuinely new question
(question/option wording + the respondent's other answers + covariates,
leave-one-question-out — it never sees the target's marginal). A twin earns a
positive recommendation only if it **beats this baseline**, not merely uniform or
the group average. This is a strong bar: with demographic covariates the baseline
often rivals or beats the LLM twin on accuracy, so clearing it is real evidence the
twin adds individual-level signal. If it only beats those, the added value over a cheap
model is unproven — say so.

## 4. Is the gap real? (`bootstrap.json` / report bootstrap panel)

Every difference has a bootstrap confidence interval over respondents. A twin beats
the baseline only if the **paired delta interval clears zero** in the improving
direction. A difference whose interval straddles zero is noise — do not build a
recommendation on it. The report marks a ✓ when a gap is a real improvement and a ✗
when it clears zero the wrong way.

## 5. Calibration and overconfidence

- **Median vs mean NLL**: a good median with a bad mean is the signature of a few
  confident wrong guesses.
- A **positive NLL delta vs the baseline** (✗) means the twin is *worse* on
  confidence even if its accuracy is higher — flag it; its probabilities are not
  trustworthy for cutoffs or ranking.
- **Probability granularity**: a model flagged "coarse" piled probability mass on
  round numbers; its Brier/calibration are quantization-limited.

## 6. Cross-question structure (attenuation)

If the correlation-attenuation verdict says the twin under-models cross-question
association, the twin regresses respondents toward a common distribution — its
per-question marginals can look fine while the relationships *between* questions are
washed out. Flag this when the decision depends on how answers relate (segments,
cross-tabs), not just single-question shares.

## The gate

State a positive result only when: leakage is clean (or excluded), the twin beats
the conditional baseline with a bootstrap interval that clears zero, and confidence
quality is acceptable (or its limits are stated). Otherwise report what is actually
supported — often "beats random and the group average, but not a cheap individual
model" or "directional, not exact."
