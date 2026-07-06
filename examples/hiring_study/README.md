# Hiring Study Sample Dataset

This fixture is an import-ready survey dataset for testing the `zwill` CLI contract in `SPEC.md`.

## Files

- `raw/questionnaire.md`: human-readable source questionnaire.
- `raw/panel_export.csv`: raw wide-format panel export with respondent metadata and answers.
- `questions.jsonl`: normalized question definitions for `zwill question import`.
- `respondents.jsonl`: normalized respondent weights and metadata for `zwill respondent import`.
- `answers.jsonl`: clean normalized answers for `zwill answer import`.
- `answers_with_invalid.jsonl`: same shape as `answers.jsonl`, with one invalid answer option for quarantine tests.
- `expected/status_after_import.json`: expected survey counts after importing the clean fixture.
- `expected/truth_marginals.json`: deterministic weighted marginals for the clean fixture.

## Suggested Test Flow

```bash
zwill init
zwill survey create --name hiring_study
zwill raw add --survey hiring_study --id questionnaire --path examples/hiring_study/raw/questionnaire.md --kind questionnaire --title "Hiring Study Questionnaire"
zwill raw add --survey hiring_study --id panel_export --path examples/hiring_study/raw/panel_export.csv --kind panel_export --title "Hiring Study Panel Export"
zwill question import --survey hiring_study --path examples/hiring_study/questions.jsonl
zwill respondent import --survey hiring_study --path examples/hiring_study/respondents.jsonl
zwill answer import --survey hiring_study --path examples/hiring_study/answers.jsonl
zwill status
zwill commit --survey hiring_study
```

For quarantine tests, import `answers_with_invalid.jsonl` instead of `answers.jsonl`. The row for respondent `r006` and question `remote_work` uses `every_day`, which is not in the canonical options.
