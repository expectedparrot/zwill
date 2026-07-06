# Predicting Twin Performance by Question

This is a working notebook for understanding which survey questions digital twins answer well, and why.

The current validation run is the expanded held-out twin job `9442a8f9dfd28059`, with 59 held-out questions and 2,721 validation rows.

## Working Hypotheses

1. **Baseline difficulty matters.** Questions with many options, high answer entropy, and low top-option share should be harder.
2. **Question family matters.** Firmographics, business descriptors, rankings, message tests, feature rankings, and collaboration questions may have different levels of predictability.
3. **Context similarity may matter, but not naively.** Distance to the single closest included context question may be confounded by repeated battery wording. A better measure may be top-k support or same-family/same-stem support.
4. **Twin output sharpness matters.** Questions where the twin gives sharper probability distributions may perform better, unless the twin is confidently wrong.
5. **Marginal mismatch matters.** Some questions may fail because the model gets the population prior wrong, not because it lacks respondent-level signal.

## First Result: Closest-Question Distance

The first exploratory plot is:

- `reports/prospect_fulltext_twin_preflight/twin_performance_vs_question_distance.svg`
- `reports/prospect_fulltext_twin_preflight/data/twin_performance_vs_question_distance.csv`

It plots per-question mean lift vs uniform against the median embedding cosine distance from the held-out question to the closest observed context question in that question's twin prompts.

Initial result: the relationship is positive, not negative.

- Pearson r: `0.49`
- Spearman rho: `0.54`

Interpretation: this does **not** support the simple hunch that closer context questions produce better twin predictions. The likely confound is question family. Near-duplicate batteries and ranking items are close in embedding space but still hard, while broader firmographic/business descriptor questions are farther from context but easier to infer.

## Next Analyses

I am building a question-level feature table with:

- Twin performance: lift vs uniform, p(actual), NLL improvement, Brier improvement, top-1 accuracy.
- Baseline difficulty: option count, empirical entropy, normalized entropy, top-option share.
- Question family/type: heuristic family, ranking flag, binary flag, multi-select-ish flag.
- Context support: closest distance, top-3/top-5 mean similarity, context similarity mass, same-stem count.
- Twin sharpness: prediction entropy, normalized prediction entropy, mean top predicted probability.
- Marginal fit: L1 distance between twin-implied marginal and empirical marginal.

Findings below are working notes and should be read as exploratory; with only 59 held-out questions, this is diagnostic rather than a serious predictive model.

## Feature Table

Generated files:

- `reports/prospect_fulltext_twin_preflight/data/question_performance_features.csv`
- `reports/prospect_fulltext_twin_preflight/data/question_performance_feature_summary.json`
- `reports/prospect_fulltext_twin_preflight/data/question_performance_model_summary.json`

Reusable scripts:

- `scripts/question_performance_features.py`
- `scripts/twin_performance_distance_plot.py`
- `scripts/question_text_tsne_svg.py`

The feature table has one row per held-out validation question. The target column I am using most often is `mean_lift_vs_uniform`, defined row-wise as:

`probability_actual / uniform_probability_actual`

and then averaged by question.

## Finding 1: Baseline Difficulty Is the Strongest Signal

The strongest non-tautological correlation with twin lift is empirical normalized answer entropy.

Top non-tautological feature correlations with `mean_lift_vs_uniform`:

| Feature | Pearson | Spearman | Read |
|---|---:|---:|---|
| `empirical_normalized_entropy` | -0.70 | -0.70 | Flatter answer distributions are harder. |
| `mean_similarity_mass_gt_0_75` | -0.53 | -0.58 | Lots of very-close context support is not helping; likely battery confounding. |
| `is_rank` | -0.48 | -0.55 | Rank tasks are hard. |
| `mean_top5_similarity` | -0.56 | -0.55 | More close neighbors correlates with worse performance, again likely a rank/battery artifact. |
| `median_closest_distance` | +0.49 | +0.54 | Farther questions perform better, opposite the simple closeness hunch. |
| `mean_prediction_normalized_entropy` | -0.54 | -0.54 | Sharper twin outputs tend to do better. |
| `empirical_top_option_share` | +0.35 | +0.40 | Questions with a dominant answer are easier. |
| `marginal_l1` | -0.30 | -0.39 | Marginal mismatch hurts. |

This says the dominant factor is probably not semantic closeness to one included question. It is whether the target question has a predictable answer distribution.

Important caveat: empirical entropy is only known after we have real responses. For a brand-new question, we would need a proxy, such as one-shot marginal forecasts, historical answer distributions for similar questions, or simple option/scale features.

## Finding 2: Question Family Matters

Mean lift by question family:

| Family | Questions | Mean Lift | Mean Normalized Entropy | Mean Option Count | Mean Marginal L1 |
|---|---:|---:|---:|---:|---:|
| collaboration | 2 | 2.03 | 0.86 | 5.5 | 0.47 |
| firmographic_business | 10 | 1.49 | 0.82 | 6.8 | 0.51 |
| message_feature_likelihood | 11 | 1.45 | 0.81 | 3.1 | 0.24 |
| hiring_context | 8 | 1.16 | 0.93 | 2.4 | 0.28 |
| other | 2 | 1.13 | 0.95 | 23.0 | 0.59 |
| message_rank | 10 | 1.06 | 0.99 | 10.0 | 0.42 |
| feature_rank | 16 | 1.05 | 0.99 | 16.0 | 0.67 |

The ranking tasks are the clearest weak spot. They are semantically close to many other questions, but they have high entropy and many near-exchangeable options. That is exactly where the twin struggles.

## Finding 3: The Original Context-Closeness Hunch Looks Confounded

The closest-distance plot showed:

- Pearson r: `+0.49`
- Spearman rho: `+0.54`

If closer context questions helped directly, we expected a **negative** relationship between distance and lift. Instead we got a positive one.

After subtracting each question family's mean lift, the distance relationships mostly disappear:

| Feature | Family-residual Spearman |
|---|---:|
| `median_closest_distance` | +0.08 |
| `mean_closest_distance` | +0.07 |
| `mean_top3_similarity` | -0.07 |
| `mean_top5_similarity` | -0.06 |

Interpretation: the distance effect is mostly a family/difficulty effect. "Close" questions are often repeated rank batteries, and those are hard. "Far" questions include business facts and firmographics, which are easier.

## Finding 4: A Small Predictive Exercise

I ran leave-one-question-out ridge regressions to predict `mean_lift_vs_uniform`.

| Feature Set | LOO R2 | LOO MAE |
|---|---:|---:|
| difficulty only | 0.60 | 0.154 |
| family plus difficulty | 0.25 | 0.193 |
| context only | 0.15 | 0.219 |
| family only | 0.11 | 0.222 |
| twin output only | 0.09 | 0.222 |
| all non-derived features | -0.07 | 0.204 |

The dataset is tiny, so these numbers should not be over-read. Still, the ordering is useful: simple difficulty features beat context-similarity features.

The "all features" model overfits. With 59 questions, adding many correlated features is worse than a small model.

## Concrete Examples

Low-entropy, high-performing examples:

| Question | Lift | Entropy | Options | Family |
|---|---:|---:|---:|---|
| `q003_q185` | 2.34 | 0.44 | 3 | firmographic_business |
| `q002_q184` | 1.56 | 0.59 | 3 | firmographic_business |
| `q047_q11` | 2.27 | 0.67 | 5 | message_feature_likelihood |
| `q044_c12_bplus_control_2` | 1.77 | 0.71 | 4 | message_feature_likelihood |
| `q068_c32_biz_stage` | 1.98 | 0.80 | 5 | firmographic_business |

High-entropy, low-performing examples:

| Question | Lift | Entropy | Options | Family |
|---|---:|---:|---:|---|
| `q032_c11_bplus_feat_app_12` | 0.88 | 0.98 | 16 | feature_rank |
| `q034_c11_bplus_feat_app_14` | 0.90 | 0.98 | 16 | feature_rank |
| `q031_c11_bplus_feat_app_11` | 0.93 | 0.99 | 16 | feature_rank |
| `q016_c10a_msg_appeal_8` | 0.93 | 0.98 | 10 | message_rank |
| `q020_c10a_msg_appeal_12` | 0.96 | 0.99 | 10 | message_rank |

## Current Takeaway

The best current predictor of twin performance is **question difficulty**, especially answer entropy and rank-task structure. Context closeness is not useless, but the naive "closest included question" measure is misleading because close questions are often members of hard rank batteries.

For a future question, I would predict twin reliability using a small checklist:

1. Is the response distribution likely concentrated or diffuse?
2. Is it a ranking/conjoint/preference among near-substitutes?
3. Is it factual/behavioral versus attitudinal/preference?
4. Are there multiple context questions that support the construct, not just one close text match?
5. Does a one-shot marginal forecast produce a sharp or diffuse distribution?
6. Does the twin output look calibrated and not merely confident?

The next useful analysis would replace empirical entropy with a pre-survey proxy: one-shot predicted entropy from the marginal job, plus question metadata such as option count, rank flag, and text family.

## Finding 5: One-Shot Marginals Are a Useful but Weaker Pre-Survey Proxy

I added one-shot marginal features from:

- `reports/prospect_fulltext_twin_preflight/data/one-shot-marginals.json`

New feature columns include:

- `one_shot_predicted_normalized_entropy`
- `one_shot_predicted_top_share`
- `one_shot_mae`
- `one_shot_brier`
- `one_shot_kl_divergence`

Focused correlations with twin lift:

| Feature | Pearson | Spearman | Missing |
|---|---:|---:|---:|
| `one_shot_predicted_normalized_entropy` | -0.32 | -0.17 | 2 |
| `one_shot_predicted_top_share` | +0.26 | +0.35 | 2 |
| `one_shot_mae` | +0.12 | +0.28 | 2 |
| `empirical_normalized_entropy` | -0.70 | -0.70 | 0 |
| `empirical_top_option_share` | +0.35 | +0.40 | 0 |
| `is_rank` | -0.48 | -0.55 | 0 |

The one-shot predicted entropy is pointing in the expected direction, but it is much weaker than the actual empirical entropy. The one-shot top share is a better one-shot signal here than one-shot entropy.

Leave-one-question-out models:

| Feature Set | LOO R2 | LOO MAE | Interpretation |
|---|---:|---:|---|
| empirical difficulty oracle | 0.60 | 0.154 | Uses true empirical entropy/top share, so not available for brand-new questions. |
| pre-survey proxy only | 0.34 | 0.186 | Option count + rank/binary flags + one-shot entropy/top share. |
| metadata only, no actual entropy | 0.25 | 0.200 | Option count + rank/binary flags. |
| one-shot entropy/top share only | -0.02 | 0.262 | Too weak alone. |

This is useful: if we want to estimate twin reliability before running validation, the practical model should combine:

- option count,
- rank/binary flags,
- question family,
- one-shot predicted top share / entropy,
- and maybe text-derived family labels.

One-shot forecasts alone are not enough.

## Revised Practical Recipe

For a proposed new held-out or hypothetical question, I would score expected twin reliability with a small additive rubric:

1. **Start with response format.**
   Penalize rank tasks, many-option tasks, and near-substitute option sets.
2. **Estimate baseline concentration.**
   Use one-shot marginal predicted top share and predicted entropy as a proxy for how concentrated real responses might be.
3. **Use question family.**
   Firmographics/business descriptors and likelihood questions get a higher prior; message/feature rankings get a lower prior.
4. **Use context support carefully.**
   Count support from related constructs, but do not trust nearest text similarity by itself. Repeated wording in rank batteries can look close while still being unhelpful.
5. **Flag marginal-risk questions.**
   If one-shot marginals are diffuse or implausible, expect the twin to have a poor population prior.

The short version: **predictability of the answer distribution beats semantic proximity.**

## What To Do When We Do Not Know the Answer Yet

For a future or hypothetical question, we do not know the empirical answer distribution, so we cannot directly use the strongest predictor from this analysis: observed answer entropy. We need pre-answer proxies.

### Useful Pre-Answer Signals

1. **One-shot marginal forecast**

   Ask a frontier model to predict the aggregate response distribution before building twins. Use:

   - predicted top-option share,
   - predicted entropy,
   - predicted probability spread,
   - whether the forecast is concentrated or diffuse.

   In this dataset, one-shot top-option share was more useful than one-shot entropy by itself. One-shot features alone were weak, but they improved a metadata-only pre-survey model.

2. **Question format heuristics**

   These are available before any answers exist:

   - Binary and 3-point questions are usually easier.
   - 10-rank and 16-rank tasks are hard.
   - Many near-substitute options are hard.
   - Factual/behavioral questions are easier than subtle preference rankings.
   - Questions with a likely dominant answer should be easier than questions where every option is plausible.

3. **Question family prior**

   Use historical validation results as priors:

   - Stronger families in this run: firmographics/business descriptors, message/feature likelihood questions, and some collaboration questions.
   - Weaker families in this run: message-rank and feature-rank batteries.

   This prior should be updated as we validate more surveys.

4. **Context support audit**

   Check whether existing twin context answers actually constrain the target answer. Do not rely only on nearest embedding distance.

   Better indicators:

   - multiple context questions measure the same construct,
   - context questions are behaviorally or logically upstream of the target,
   - same family support exists without being merely repeated wording,
   - the context contains facts that narrow plausible answers.

   Bad indicator:

   - a close text neighbor from the same rank battery, where closeness mostly reflects repeated wording rather than predictive content.

5. **Borrow priors from similar past questions**

   For a new question, find past questions with similar format, family, and option structure. Borrow:

   - observed entropy,
   - top-option share,
   - twin lift,
   - marginal L1,
   - calibration behavior.

   This is probably better than raw embedding similarity alone because it includes format and answer-structure similarity.

6. **Tiny pilot validation**

   If feasible, collect or hold out even 20-50 actual responses. A small validation sample is far more informative than pure heuristics, especially for rank/preference questions.

### Practical Pre-Answer Reliability Score

A simple rubric could score a proposed question like this:

Positive signals:

- high one-shot predicted top-option share,
- low one-shot predicted entropy,
- factual or behavioral target,
- strong construct support in context,
- historically high-performing question family,
- small number of response options.

Negative signals:

- rank task,
- many options,
- near-substitute options,
- diffuse one-shot marginal forecast,
- weak or purely textual context support,
- historically low-performing family,
- expected marginal distribution close to uniform.

The output should be a coarse class rather than a fake precision estimate:

| Class | Meaning |
|---|---|
| Good twin target | Likely useful for directional and possibly quantitative work. |
| Directional only | Use for qualitative ranking, scenario exploration, or rough directional readout. |
| Poor target until validated | Do not rely on twin answers without actual validation or a pilot. |

### Candidate Formula

One possible starting score:

```text
 one-shot_top_share
- one-shot_normalized_entropy
- rank_task_penalty
- many_option_penalty
+ factual_behavioral_bonus
+ context_construct_support
+ historical_family_prior
- near_substitute_option_penalty
```

This score should be tuned against validation runs. The current analysis suggests the largest weights should be on answer-distribution predictability and rank/option-structure penalties, not on nearest-question embedding distance.
