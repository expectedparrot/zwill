# Rank batteries (ranking / MaxDiff)

A **rank battery** is a set of per-item questions scored together: the respondent
orders (or picks a best/worst among) a fixed list of items, and each item carries
the rank the respondent gave it. Ranking is validated through a **separate
rank-utility flow** — `twin-validate` gates multiple-choice held-out targets only
and will warn (`rank_tasks_not_validated_here`) if your survey has rank batteries.

## Import shape

Import one row per **item** in the battery, and group the items by giving every
item row the same `rank_task_id`. This explicit id is strongly preferred over the
detection heuristic (see below).

| Field | Required | Notes |
|---|---|---|
| `question_name` | yes | Stable id for the item (e.g. `q11_site_1 … q11_site_14`). |
| `rank_task_id` | recommended | Same value on every item row in the battery. Groups them into one task. |
| `question_text` | yes | Item wording; ideally names the item (e.g. `… - Shopee`). |
| `rank_item_label` | no | Explicit human-readable item label. Falls back to the trailing `-`-delimited fragment of `question_text`, then `question_name`. |
| `rank_direction` | no | `1_is_best` (default) means rank 1 is the top/most-preferred item. |
| `question_type` | no | Leave unset; import annotates battery items as `rank_item` and synthesizes one `rank` question named after the `rank_task_id`. |

Each **answer** row records the rank that respondent assigned to that item — the
integer position as a string — using the normal `answer import` path:

```json
{"question_name": "q11_site_1", "rank_task_id": "site_spend", "question_text": "Order sites by total spend - Mercado Livre"}
{"question_name": "q11_site_2", "rank_task_id": "site_spend", "question_text": "Order sites by total spend - Shopee"}
```
```json
{"respondent_id": "R017", "question": "q11_site_1", "answer": "2"}
{"respondent_id": "R017", "question": "q11_site_2", "answer": "1"}
```

**Qualtrics exports.** Rank-order questions typically explode into one
`..._<n>_RANK` column per item (the rank value) plus `..._GROUP_<k>` columns
(item labels/positions). Import one item row per `_RANK` column, take the item
label from the matching `_GROUP` column or the `_RANK` header, set a shared
`rank_task_id`, and store each respondent's `_RANK` cell value as the answer.

## Detection heuristic (fallback)

Without an explicit `rank_task_id`, zwill falls back to a heuristic — a stem of
sibling questions whose options are numeric `1..N` and whose wording mentions
"rank"/"most appealing"/etc. This can **miss** batteries (or split one across
stems), so declare `rank_task_id` explicitly whenever you can. `zwill survey
report` flags likely-undetected batteries.

## Validation flow

Rank batteries are validated on their own, not through the headline gate:

```bash
zwill edsl-export --survey <survey> --target rank-utility-twin-job \
  --rank-task-id <rank_task_id> --allow-unapproved --path rank.edsl.json
zwill edsl-run --job rank.edsl.json --path rank_results.json.gz
zwill twin-results import --survey <survey> --path rank_results.json.gz
zwill twin-results rank-report --survey <survey> --rank-task-id <rank_task_id> \
  --format html --path report_out/rank-<rank_task_id>.html
```

Link the rendered rank report pages from your report folder. See
`zwill guide show agent-workflow` for where this fits in the end-to-end flow and
`zwill guide show import-format` for the general import schema.
