# Rank Battery Twin Elicitation Spec

This spec describes how zwill should handle survey ranking batteries for digital-twin validation and prediction.

The current approach treats each ranking item as an independent multiple-choice question over rank labels. For example, a 16-item feature ranking battery becomes 16 separate questions, each asking the twin to predict whether one feature received rank `1`, `2`, ..., `16`.

That is workable as a lossy baseline, but it is not the right representation of a ranking task. A ranking task is joint: ranks across items form a permutation. If one item is ranked `1`, no other item can also be ranked `1`. The current item-level categorical approach does not preserve this constraint and makes rank batteries look like ordinary multiple-choice questions.

This document proposes a lower-rank utility-score elicitation that preserves most benefits of probabilistic twin prediction without requiring a distribution over all permutations.

## Goals

1. Represent rank batteries as joint tasks, not independent item-level multiple-choice tasks.
2. Avoid enumerating the full permutation space, which is enormous:
   - 10-item ranking: `10! = 3,628,800`
   - 16-item ranking: `16! = 20,922,789,888,000`
3. Produce outputs that can imply a ranking, top-k choices, and pairwise preferences.
4. Score rankings with rank-aware metrics, not ordinary categorical accuracy alone.
5. Keep the output schema simple enough for EDSL/LLM jobs and robust parsing.
6. Support both validation and future hypothetical ranking tasks.

## Proposed Representation

For each ranking battery, zwill should create one rank task with:

- `rank_task_id`
- `rank_task_text`
- ordered list of `items`
- respondent's actual rank for each item when validating
- optional item metadata such as concept family, randomized cell, or source question names

Example:

```json
{
  "rank_task_id": "c11_bplus_feat_app",
  "rank_task_text": "Please review the list of Business Plus plan features below and rank each from most appealing to least appealing.",
  "rank_direction": "1_is_best",
  "items": [
    {
      "item_id": "q021_c11_bplus_feat_app_1",
      "label": "Get AI-curated shortlists of freelancers delivered in under 5 hours",
      "actual_rank": 13
    },
    {
      "item_id": "q022_c11_bplus_feat_app_2",
      "label": "Powered by Uma, your always-on hiring agent, get AI-curated shortlists of freelancers in under 5 hours",
      "actual_rank": 6
    }
  ]
}
```

The `item_id` can remain the existing zwill question name for compatibility, but the validation task should be the full battery.

## Preferred Elicitation: Latent Utility Scores

Ask the twin for a latent appeal/utility score for each item.

The output is `O(n)` values for `n` ranked items, not an `O(n!)` distribution over permutations.

### Prompt Shape

For a rank battery, the twin prompt should differ from ordinary multiple-choice prompts.

Instead of:

```text
Held-out question text:
Please rank ... - Get AI-curated shortlists...

Held-out response options:
a: 9
b: 11
c: 4
...
```

Use:

```text
You are acting as a digital twin for one survey respondent.

Observed answers from this respondent:
...

Held-out rank task:
Please review the list of Business Plus plan features below and rank each from most appealing to least appealing to you as a client, with 1 being the most appealing and 16 being the least appealing.

Items to score:
item_01: Get AI-curated shortlists of freelancers delivered in under 5 hours
item_02: Powered by Uma, your always-on hiring agent, get AI-curated shortlists of freelancers in under 5 hours
...

Estimate this respondent's latent appeal score for each item on a 0-100 scale.

Use the full scale:
- 0 means no appeal to this respondent.
- 50 means neutral/moderate appeal.
- 100 means strongest appeal among this kind of feature.

Scores may be close or tied if the respondent would see items as similarly appealing.
The implied ranking is obtained by sorting items from highest score to lowest score.

Return only valid JSON.
```

### Output Schema

```json
{
  "scores": {
    "item_01": 78,
    "item_02": 72,
    "item_03": 31
  },
  "confidence": 0.64,
  "notes": "Brief respondent-level explanation."
}
```

Optional richer output:

```json
{
  "scores": {
    "q021_c11_bplus_feat_app_1": 78,
    "q022_c11_bplus_feat_app_2": 72
  },
  "top_items": [
    "q021_c11_bplus_feat_app_1",
    "q022_c11_bplus_feat_app_2"
  ],
  "bottom_items": [
    "q034_c11_bplus_feat_app_14"
  ],
  "confidence": 0.64,
  "notes": "Brief respondent-level explanation."
}
```

Recommendation: start with `scores`, `confidence`, and `notes`. Derive everything else downstream.

## How Scores Imply Rankings

Given utility scores:

1. Sort items by descending score.
2. Assign predicted rank `1` to the highest score, `2` to the next highest, etc.
3. Break exact ties deterministically for metrics, but also track tie counts.

For probabilistic top-choice summaries, transform scores using softmax:

```text
P(item is top) = exp(score_i / temperature) / sum_j exp(score_j / temperature)
```

The temperature controls sharpness. This should be calibrated on validation data. Initially, use temperature values like `10`, `15`, or `20` and report sensitivity.

## Scoring Metrics

Rank tasks should be scored with rank-aware metrics.

### Individual-Level Metrics

For each respondent and rank battery:

1. **Spearman rank correlation**
   Correlation between predicted ranks and actual ranks.

2. **Kendall tau**
   Pairwise agreement between predicted and actual ordering.

3. **Pairwise order accuracy**
   Share of item pairs where the predicted ordering matches the actual ordering.

4. **Mean absolute rank error**
   Mean absolute difference between predicted rank and actual rank across items.

5. **Top-1 hit**
   Whether the predicted top item equals the respondent's actual top item.

6. **Top-k hit**
   Whether the actual top item appears in predicted top `k`, or overlap between actual top `k` and predicted top `k`.

7. **NDCG-style score**
   Treat actual rank as relevance and score whether high-actual-appeal items appear near the top of the predicted list.

Recommended initial metrics:

- Spearman
- pairwise order accuracy
- top-3 overlap
- mean absolute rank error

### Aggregate Metrics

Across respondents:

1. **Mean Spearman**
2. **Mean pairwise order accuracy**
3. **Mean top-k overlap**
4. **Aggregate rank correlation**
   Compare average predicted item score/rank against average actual item rank.
5. **Top item agreement**
   Whether the model's predicted most appealing item at aggregate level matches the survey's observed most appealing item.

### Baselines

Use multiple baselines:

1. **Random ranking baseline**
   Expected pairwise order accuracy is `0.5`.

2. **Empirical marginal rank baseline**
   Predict the same aggregate item order for every respondent based on observed population average ranks.

3. **Uniform utility baseline**
   Equal scores for all items. This is mostly useful as a degenerate reference.

4. **Format-only baseline**
   For validation, use average rank by item or item family if available from training/previous surveys.

The empirical marginal baseline is strong and should be treated as an oracle-style benchmark, analogous to the empirical marginal oracle for multiple choice.

## Validation Output Rows

Store one row per respondent per rank battery, not one row per item.

Example validation row:

```json
{
  "survey": "prospect_fulltext",
  "respondent_id": "prospect_r0012",
  "rank_task_id": "c11_bplus_feat_app",
  "items": [
    {"item_id": "q021_c11_bplus_feat_app_1", "label": "..."},
    {"item_id": "q022_c11_bplus_feat_app_2", "label": "..."}
  ],
  "actual_ranks": {
    "q021_c11_bplus_feat_app_1": 13,
    "q022_c11_bplus_feat_app_2": 6
  },
  "predicted_scores": {
    "q021_c11_bplus_feat_app_1": 78,
    "q022_c11_bplus_feat_app_2": 72
  },
  "predicted_ranks": {
    "q021_c11_bplus_feat_app_1": 1,
    "q022_c11_bplus_feat_app_2": 2
  },
  "metrics": {
    "spearman": 0.42,
    "pairwise_order_accuracy": 0.63,
    "top_3_overlap": 0.67,
    "mean_absolute_rank_error": 3.1
  }
}
```

For item-level analysis, zwill can also export a long table with one row per respondent-item:

```json
{
  "respondent_id": "prospect_r0012",
  "rank_task_id": "c11_bplus_feat_app",
  "item_id": "q021_c11_bplus_feat_app_1",
  "actual_rank": 13,
  "predicted_score": 78,
  "predicted_rank": 1,
  "rank_error": -12
}
```

## Prompt Construction Changes in Zwill

Zwill needs to distinguish:

1. ordinary multiple-choice held-out questions,
2. free-text held-out questions,
3. rank-battery held-out tasks.

### Rank Battery Detection

Initial detection can be heuristic:

- question text contains "rank each from most appealing to least appealing",
- answer options are numeric ranks,
- multiple questions share the same stem and differ only by item suffix after `" - "`,
- source names follow a known battery pattern such as:
  - `q011_c10a_msg_appeal_*`
  - `q021_c11_bplus_feat_app_*`

Longer term, survey ingestion should preserve battery metadata from the raw instrument.

### Rank Task Construction

For each detected battery:

1. Group item-level columns into one rank task.
2. Extract common stem as `rank_task_text`.
3. Extract item labels from the suffix after `" - "`.
4. Build actual ranks as `{item_id: rank}` for each respondent.
5. Exclude all item-level rank columns from ordinary multiple-choice twin validation unless explicitly requested.

### Context Leakage

When holding out a rank battery:

- exclude all item-level columns in that same battery from observed context,
- exclude derived top-item fields from that battery,
- optionally allow related but separate batteries as context.

For example:

- Holding out message ranking `c10a_msg_appeal` should exclude all `q011`-`q020` and `q073_top_message`.
- Holding out feature ranking `c11_bplus_feat_app` should exclude all `q021`-`q036` and `q074_top_feature`.

## Reporting Changes

Rank validation should get its own report section, separate from multiple-choice validation.

Recommended report blocks:

1. **Rank Battery Summary**
   Rows, tasks, item counts, model label.

2. **Individual Rank Performance**
   Mean Spearman, mean Kendall tau, pairwise order accuracy, top-k overlap, rank MAE.

3. **Aggregate Rank Fit**
   Predicted average item score versus observed average rank.

4. **Top-K Accuracy**
   How often predicted top 1/top 3 overlaps observed top 1/top 3.

5. **Item-Level Diagnostics**
   Which items are systematically over-ranked or under-ranked.

6. **Calibration / Sharpness**
   Distribution of score spreads by respondent and relationship to accuracy.

## Comparison to Current Item-Level MC Approach

Current approach:

- one held-out task per item,
- answer options are rank labels,
- predicts a categorical probability distribution over ranks,
- ignores permutation constraints,
- evaluates like multiple choice.

Proposed utility approach:

- one held-out task per ranking battery,
- output is utility score per item,
- ranking is derived from utilities,
- preserves joint item comparison,
- evaluates with rank-aware metrics,
- can still produce top-choice probabilities through softmax.

## Open Design Questions

1. **Score scale**
   Should scores be `0-100`, `0-10`, or unconstrained real-valued utilities?

   Recommendation: start with `0-100` because it is human-readable and easy to validate.

2. **Probability calibration**
   How should scores become probabilistic top-choice estimates?

   Recommendation: use softmax with calibrated temperature, report sensitivity.

3. **Ties**
   Should the model be allowed to tie items?

   Recommendation: yes for scores, but deterministic tie-breaking for metrics.

4. **Top-k versus full ranking**
   Do we care about exact full ranking or mostly top items?

   Recommendation: report both, but emphasize top-k and pairwise ordering over exact rank.

5. **Prompt size**
   A 16-item rank battery is manageable in one prompt. Very large batteries may need chunking or pairwise methods.

## Alternative Elicitations

### Pairwise Win Probabilities

Ask the twin to estimate which item wins for each pair. Fit Bradley-Terry utilities.

Pros:

- naturally rank-aware,
- gives pairwise probabilities,
- good theoretical fit.

Cons:

- `n(n-1)/2` comparisons,
- 16 items means 120 pairwise judgments per respondent,
- larger prompt/output.

### Top-K Sequential Choice

Ask for the probability distribution over first choice, then second choice conditional on first, stopping at top-k.

Pros:

- closer to Plackett-Luce,
- focuses on business-relevant top items.

Cons:

- conditional prompts are more complex,
- still can be expensive if done deeply.

### Tier Assignment

Ask the twin to put items into buckets:

- must-have,
- attractive,
- neutral,
- low appeal.

Pros:

- stable and easy,
- useful for qualitative product analysis.

Cons:

- loses exact rank information.

## Recommended Initial Implementation

Start with utility scores.

1. Add rank-battery detection to survey/profile processing.
2. Add an EDSL export mode for `rank-utility-twin-job`.
3. Prompt for `0-100` utility scores for every item in a battery.
4. Parse scores robustly.
5. Derive ranks and metrics in zwill.
6. Produce a rank-validation report page.
7. Keep the current item-level MC approach only as a compatibility/debug baseline.

## Why This Should Help

The performance analysis in `PERFORMANCE.md` suggests the current item-level rank treatment is one reason rank questions perform poorly. The current setup asks the twin to predict one item's exact rank in isolation, while the true survey task asks the respondent to compare all items jointly.

Utility-score elicitation better matches the cognitive structure of ranking:

1. respondent has latent appeal for each item,
2. respondent compares items,
3. observed ranking is the sorted utility order.

This avoids the full permutation distribution while preserving the key object: relative appeal across items.
