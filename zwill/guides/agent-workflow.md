# zwill agent workflow: survey data → validated twin report

This guide walks you (an automated agent, in any harness) from raw survey data to
a full digital-twin validation report with an `index.html`. It is self-contained:
run `zwill next` at any point to see which stage you are in and the exact command
to run next, and `zwill guide show interpreting-results` when you reach the report.

You do not need any coding-assistant skill files. Everything you need is reachable
through `zwill` commands and their `next_steps`.

## What you are building

A digital-twin *validation study*: hold out some questions respondents already
answered, construct twins without those answers, have models predict them, and
score the predictions against what people actually said — against a cheap
conditional baseline, with confidence intervals and a leakage check, so the
conclusion is trustworthy rather than over-claimed.

The decisive question is **not** "does the twin beat random?" It is "does the twin
add individual-level signal beyond a cheap model, and is that gap real rather than
noise?" Do not report a positive result from a bare twin run.

## Prerequisites (check these first)

- `zwill` is installed and `edsl` is importable (installed as the sibling `../edsl`
  editable checkout). Running twins uses EDSL.
- Running twins uses **Expected Parrot remote inference**: `EXPECTED_PARROT_API_KEY`
  in a `.env` that `zwill edsl-run` can find (it loads the nearest `.env`).
- The **conditional baseline** embeds question/option text and needs
  `OPENAI_API_KEY` (for `text-embedding-3-small`). Without it the baseline is
  skipped and the comparison loses its point — set the key, or accept a weaker
  readout.
- Do **not** pass `temperature` to models. Newer Anthropic/OpenAI models reject it
  and error on every call; EDSL omits it automatically.

## The stages

Run `zwill next` after each stage — it inspects project state and tells you the
next command. The full path:

1. **Initialize** — `zwill init` creates the `.zwill/` project database.
2. **Create a survey** — `zwill survey create --name <survey>`.
3. **Archive the raw source** — `zwill raw add --survey <survey> --id <id>
   --path <file> --kind <workbook|csv|questionnaire|...>` records provenance.
   Then convert to structured records.
4. **Import structured data** — `questions.jsonl`, `respondents.jsonl`,
   `answers.jsonl` via `zwill question import` / `respondent import` /
   `answer import`. Expand codebooks to human-readable labels first; a code that
   cannot be expanded should be marked incomplete, not treated as a label.
5. **Commit** — `zwill commit --survey <survey>` freezes the observed truth
   marginals used to score twins.
6. **Inspect** — `zwill survey report --survey <survey> --format html --path
   survey_report.html` to verify wording, options, distributions, and missingness
   before spending on model calls.
7. **Run the twin jobs** — pick 5–10 held-out questions spanning different use
   cases, choose provider-qualified models (e.g. `openai:gpt-5.5`,
   `google:gemini-2.5-pro`), export and run:
   ```bash
   zwill edsl-export --survey <survey> --target twin-probability-job \
     --heldout-questions <q1,q2,...> --context-question-count 8 \
     --sample-respondents 200 --seed 20260706 --complete-cases \
     --model openai:gpt-5.5 --model google:gemini-2.5-pro \
     --path twin.edsl.json
   zwill edsl-run --job twin.edsl.json --path twin_results.json.gz
   zwill twin-results import --survey <survey> --path twin_results.json.gz
   ```
   (For a single survey you can also use `zwill twin-study run`.)
8. **Validate — one command** — run the whole rigorous flow:
   ```bash
   zwill twin-validate --survey <survey> --jobs <twin_job_ids> --out validation_bundle
   ```
   This runs the leakage audit, fits the conditional baseline on the *same
   respondents* the twins scored, computes bootstrap confidence intervals, and
   renders the report. The bundle contains `report.html`, `bootstrap.json`,
   `leakage_audit.json`, and `manifest.json`.
9. **Build the report index** — assemble the incremental HTML report folder with an
   `index.html` linking every ready page:
   ```bash
   zwill report build --survey <survey> --output-dir report_out
   ```
   Open `report_out/report/index.html`.
10. **Practitioner readout** — for a narrative report, `zwill twin-study
    practitioner-report --survey <survey> --job-id <job_id> --path
    practitioner_report.html`.

## Reading the result (do not over-claim)

When you reach the bundle, run `zwill guide show interpreting-results` for the full
gating rules. In short, a positive claim requires: the leakage audit is clean (or
leaky targets excluded); the twin beats the **conditional baseline**, not just
uniform/marginal; and that gap's **bootstrap interval clears zero**. Flag
overconfidence (an NLL delta the wrong way), coarse probabilities, and washed-out
cross-question structure (attenuation) when present.

## If you get stuck

- `zwill next` — where am I, what next.
- `zwill status` — project and survey state.
- `zwill next --survey <survey>` — stage for a specific survey.
- `zwill guide list` — other bundled guides.
- Every command returns `next_steps`; follow them.
