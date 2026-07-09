# Import file formats (questions / respondents / answers)

`zwill question import`, `zwill respondent import`, and `zwill answer import` each
read a JSONL file (one JSON object per line) passed with `--input-path`. Every row is
stored keyed by its id (`question_name` / `respondent_id`), so re-importing a row
with the same id overwrites the previous one. Extra fields you include are
preserved verbatim; the fields below are the ones zwill reads.

## questions.jsonl — `zwill question import --survey <s> --input-path questions.jsonl`

One row per question.

| Field | Required | Notes |
|---|---|---|
| `question_name` | yes | Stable, filesystem-safe id. The key used everywhere else. |
| `question_type` | yes | `multiple_choice` is the only closed/eligible type for one-shot probability jobs and twin held-out targets. Anything else imports fine but can only serve as context, not as a scored target. |
| `question_text` | yes | The question wording shown to models. |
| `question_options` | for `multiple_choice` | Ordered list of the canonical (human-readable) answer labels. Answer rows are validated against this list (see below). |
| `role` | no | Free-form role tag (e.g. `demographic`, `outcome`). |
| `option_labels` | no | Map of raw code -> display label, for provenance when you expanded codes. |
| `source` | no | Provenance object, e.g. `{"raw_id": "<raw id>", "note": "<source variable name>"}`. |
| `source.known_options` | no | Fallback option universe for a **context** question that has no `question_options` (e.g. free-text-ish fields you still want to present with a fixed set). Used only when `question_options` is empty. |
| `rank_task_id` | no | Explicitly groups this row into a named rank battery (see rank section). |
| `context_priority` | no | Number that pulls this question toward the front when a twin's context is count-limited (`--context-question-count`). Higher wins; ties keep `questions.jsonl` order. |

**Context selection is positional by default.** When `--context-question-count`
limits how many observed answers a twin sees, zwill keeps the respondent's
answered questions in `questions.jsonl` order and takes the first N. Order your
questions deliberately, or set `context_priority` on the ones that matter most.

Example row:

```json
{"question_name": "q3_seniority", "question_type": "multiple_choice", "question_text": "What is your seniority level?", "question_options": ["Junior", "Mid", "Senior", "Executive"], "role": "demographic", "source": {"raw_id": "smb_workbook", "note": "Q3"}}
```

## respondents.jsonl — `zwill respondent import --survey <s> --input-path respondents.jsonl`

One row per respondent.

| Field | Required | Notes |
|---|---|---|
| `respondent_id` | yes | Stable id used to join answers. |
| `weight` | no | Survey weight (default 1.0 when omitted). |
| `metadata` | no | Arbitrary object (e.g. cohort, region). |
| `source` | no | Provenance object. |

Note: `answer import` auto-creates any respondent it sees that isn't already
present (with `weight: 1.0`, empty `metadata`), so a respondents file is
optional if you only need weights/metadata for some respondents.

Example row:

```json
{"respondent_id": "R017", "weight": 1.0, "metadata": {"region": "US"}, "source": {"raw_id": "smb_workbook"}}
```

## answers.jsonl — `zwill answer import --survey <s> --input-path answers.jsonl`

One row per (respondent, question) cell.

| Field | Required | Notes |
|---|---|---|
| `respondent_id` | yes | Must match a respondent (auto-created if new). |
| `question` | yes | Must match an imported `question_name` or the row is quarantined (`unknown_question`). |
| `answer` | answer **or** `missing_code` | The chosen label. When the question has a non-empty `question_options`, `answer` **must** be one of them or the row is quarantined (`invalid_answer_option`). |
| `missing_code` | answer **or** `missing_code` | Use instead of `answer` to record a non-response (e.g. `"skipped"`, `"NA"`). A row with neither is quarantined (`answer or missing_code is required`). |

Quarantined rows are not imported; the count is surfaced as a `partial_import`
warning and the details go to the survey's quarantine log. Fix the labels
(usually a codebook expansion) and re-import.

Example rows:

```json
{"respondent_id": "R017", "question": "q3_seniority", "answer": "Senior"}
{"respondent_id": "R017", "question": "q9_income", "missing_code": "prefer_not_to_say"}
```

## Rank batteries (MaxDiff / ranking questions)

A rank battery is a set of per-item questions scored together. Declare it
explicitly by giving every item row the same `rank_task_id` (recommended), e.g.
`q13_message_1 ... q13_message_10` all with `"rank_task_id": "top_message"`.
Without an explicit id, zwill falls back to a heuristic (numeric `1..N` options
plus "rank"/"most appealing" wording) that can miss batteries — so prefer the
explicit id. Run `zwill guide show rank` for the full rank import shape and the
separate rank-utility validation flow.

## Multi-select / checkbox questions

If a question let respondents pick several options, import it with
`question_type: "checkbox"` and put the full option universe in
`question_options`. Store each respondent's selection as a delimited string in
`answer` (e.g. `"Email|Phone"`). Answer import splits on the question's
`option_delimiter` (default `|`) and validates **each selected token** against
`question_options`, quarantining a row that selects an unknown option.

Because option text often contains commas, pick a delimiter that does not appear
in the labels and set it on the question row:

```json
{"question_name": "q7_channels", "question_type": "checkbox", "question_text": "Which channels do you use?", "question_options": ["Email", "Phone", "In-person"], "option_delimiter": "|"}
{"respondent_id": "R017", "question": "q7_channels", "answer": "Email|Phone"}
```

Checkbox questions serve as context; they are not eligible as scored twin/one-shot
targets (only `multiple_choice` questions are).
