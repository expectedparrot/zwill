# Digital Twins Across Survey Microdata

This paper is a clean, current-`zwill` successor to `papers/microdata_twins`.
It reuses the normalized survey batteries tested there, but delegates ingestion,
provenance, held-out prediction, leakage checks, conditional baselines,
uncertainty, and evidence bundles to `zwill`.

The checked-in source is intentionally reproducible without copying the large
microdata files a second time. The source manifest points to the normalized
files in the earlier paper and `build.py` records each file with `zwill raw
add`. Generated state and reports live under `workdir/` and are ignored by git.

## Study cohort

`config/surveys.json` contains 15 usable batteries. Two normalized batteries
from the earlier paper are declared exclusions because they have fewer than two
complete-case respondents:

- `DATAVERSE_QHXQTH_QHXQTH_TRUST_CORRUPTION` (0)
- `DATAVERSE_MHG3A9_LA_POLICE_STOP_EXPERIENCE` (1)

The GSS extension is separately declared in
`config/gss_2024_batteries.json`. It downloads checksum-pinned 2024 GSS Release
3a data from NORC and uses `zwill survey extract-stata` to create five reviewed
batteries: institutional confidence, socializing frequency, abortion
circumstances, government responsibility, and national spending. The extension
contains 38 questions. Its complete-case sample sizes range from 1,572 to
2,593.

The primary design selects up to five target questions per battery and withholds
that entire target set from twin construction. It samples 200 complete-case
respondents with seed `20260730`. For a given respondent, the readable profile
and up to eight eligible non-target answers are therefore fixed across targets;
only the target question and response options change. This is a simultaneous
multi-question holdout, not a rotating leave-one-question-out design. The full
prediction count is printed in `workdir/study_manifest.json`. A companion
by-question design creates 69 single-target plans. In those jobs the other
targets may enter context, producing another 13,800 predictions.

## Build

The Makefile is the executable specification of the paper. From this directory:

```bash
make offline
```

This refuses to overwrite an existing `workdir`. Pass `--fresh` only when
intentionally replacing all generated state, imported results, and reports.

The offline target augments source-specific declarative metadata, then calls the
`zwill` ingestion, commit, reporting, approach, and study commands. It:

1. uses `zwill survey ingest-matrix` to validate codes and convert normalized
   batteries to the canonical JSONL import contract;
2. initializes an isolated multi-survey project;
3. archives normalized metadata and respondent files as raw provenance;
4. imports labeled questions, respondents, and answers;
5. commits each survey and writes its pre-run HTML report;
6. registers the baseline twin approach;
7. writes one cross-survey manifest and uses `zwill study expand` to create and
   validate a draft simultaneous plan for each survey;
8. writes a cross-survey study manifest and a generated LaTeX cohort table.

The offline build deliberately stops before plan approval, job export, and
remote inference. Review `workdir/plans/*.json`, the prediction count, model
choice, leakage exclusions, and likely cost before approving any plan.

To reproduce the GSS download, extraction, strict ingestion, survey reports,
and 38 leave-one-question-out draft plans:

```bash
make gss-plans
```

Every GSS target is held out separately, leaving every other admissible battery
answer available to both the twin and the exact-context conditional benchmark.
At 200 respondents and one GPT-5.5 model, the plan contains 7,600 paid twin
predictions. The same Makefile also builds one independent no-persona,
same-model marginal job per battery (38 additional calls), so adding a battery
does not require rerunning the existing one-shot jobs:

```bash
make gss-one-shot-infer ALLOW_REMOTE=1
make gss-validate ALLOW_REMOTE=1
```

To reproduce the complete paid study and paper from raw normalized inputs:

```bash
make reproduce ALLOW_REMOTE=1 APPROVED_BY="Your Name"
```

Remote execution is deliberately gated: `ep run-manifest` verifies only unless
explicit `ALLOW_REMOTE=1` adds `--execute`. Existing `.ep` result packages and validation
bundles are reused, so interrupted runs resume rather than repurchasing completed
calls. The live dependency graph visibly uses:

1. `zwill study prepare` for reviewable plans, approval, and export;
2. `ep run-manifest` for count-checked resumable execution;
3. `zwill study finish` for import, prompt auditing, and validation;
4. `zwill twin-validate --require-baseline` within that lifecycle, with an exact-information baseline
   using the same respondents and each prompt's stored `observed_answers`;
5. `zwill report build` and `zwill report render`;
6. `zwill study summarize` and `zwill study costs` for stable cross-survey
   artifacts, followed by presentation-only scripts for LaTeX tables and plots; and
7. LuaLaTeX for `output/paper.pdf`.

`make all` is safe and offline when the remote and validation artifacts already
exist. `make check` verifies the Make dependency graph without executing remote
inference. The Makefile uses the three lifecycle commands directly for the 69
single-target plans; there is no paper-specific orchestration script.

## Live validation

After human plan approval, follow the exact per-survey commands emitted in
`workdir/RUN.md`. The required sequence is:

```text
approve -> export-plan -> ep run -> import-plan-results -> run audit
-> twin-validate --require-baseline -> report build/render
```

Run `zwill next --survey <survey>` after every completed stage and follow its
`next_steps`. Do not interpret respondent-level skill until the leakage audit is
clean and the bootstrap interval for lift over the conditional baseline clears
zero.

## Completed run

The current `workdir` contains a completed `openai:gpt-5.5` run:

- 13,800 simultaneous predictions, 13,800 by-question predictions, 200
  corrective predictions, and 8,800 matched cross-model predictions;
- 36,600/36,600 result rows parsed, with no malformed rows;
- observed inference cost: $404.99;
- on 69 by-question targets, GPT-5.5 beats uniform NLL on 65 and its 295
  implied option shares correlate 0.925 with observed shares;
- GPT-5.5, Claude Sonnet 4.6, and Gemini 3.5 Flash each beat exact-context
  XGBoost in all five batteries of the 22-question model comparison;
- 15 simultaneous plans, 69 single-target plans, and their run audits;
- exact-information conditional baselines using `text-embedding-3-small`; and
- among 14 clean surveys: 14 simultaneous NLL passes; in the by-question
  design, 13 passes and 1 inconclusive result.

See `workdir/cross_survey/study_summary.json`,
`workdir/cross_survey/validation_summary.csv`, and `output/paper.pdf`.
