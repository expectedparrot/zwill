# zwill CLI Spec

This spec defines the command surface first. Each command returns JSON. The examples below are the contract a new implementation should satisfy.

## Output Envelope

Every command writes one JSON object to stdout.

Success:

```json
{
  "command": "zwill survey create",
  "status": "ok",
  "data": {},
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

Failure:

```json
{
  "command": "zwill question add",
  "status": "error",
  "data": {},
  "warnings": [],
  "errors": [
    {
      "code": "invalid_input",
      "message": "question_name is required.",
      "context": {},
      "hint": "Pass --question-name."
    }
  ],
  "next_steps": []
}
```

## 1. Initialize A Project

```bash
zwill init
```

Returns:

```json
{
  "command": "zwill init",
  "status": "ok",
  "data": {
    "path": ".zwill",
    "schema_version": 1,
    "active_project": "default",
    "project_path": ".zwill/projects/default"
  },
  "warnings": [],
  "errors": [],
  "next_steps": [
    "zwill survey create --name <survey>"
  ]
}
```

State written:

```text
.zwill/
  config.json
  HEAD
  projects/
    default/
      project.json
      surveys.json
```

## 2. Manage Projects

Projects partition all zwill state inside one `.zwill` workspace. The active project is stored in `.zwill/HEAD`; `ZWILL_PROJECT=<project_id>` can override it for one command.

```bash
zwill project create client_a --use
zwill project current
zwill project list
zwill project show client_a
zwill project use default
```

Each project owns its own surveys, generated EDSL runs, agent-study imports, practitioner reports, and workflow artifacts:

```text
.zwill/projects/<project_id>/
  project.json
  surveys.json
  surveys/
  agent_studies/
  practitioner_reports/
  workflows/
```

## 3. Create A Survey

```bash
zwill survey create --name hiring_study
```

Returns:

```json
{
  "command": "zwill survey create",
  "status": "ok",
  "data": {
    "survey": {
      "name": "hiring_study",
      "status": "draft",
      "created_at": "2026-06-27T12:00:00Z"
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": [
    "zwill raw add --survey hiring_study --id <id> --input-path <file-or-dir>",
    "zwill question add --survey hiring_study ..."
  ]
}
```

State written:

```text
.zwill/projects/default/surveys/hiring_study/
  raw/
  raw_files.json
  questions.jsonl
  respondents.jsonl
  answers.jsonl
  assertions.jsonl
  ingest_log.jsonl
  quarantine.jsonl
```

## 3. Add Raw Source Files

```bash
zwill raw add \
  --survey hiring_study \
  --id questionnaire \
  --input-path raw/questionnaire.pdf \
  --kind questionnaire \
  --title "Hiring Study Questionnaire"
```

Returns:

```json
{
  "command": "zwill raw add",
  "status": "ok",
  "data": {
    "raw_file": {
      "id": "questionnaire",
      "kind": "questionnaire",
      "title": "Hiring Study Questionnaire",
      "source_path": "raw/questionnaire.pdf",
      "source_hash": "sha256:...",
      "stored_path": ".zwill/projects/default/surveys/hiring_study/raw/questionnaire/questionnaire.pdf",
      "stored_hash": "sha256:...",
      "added_at": "2026-06-27T12:01:00Z"
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

List raw files:

```bash
zwill raw list --survey hiring_study
```

Returns:

```json
{
  "command": "zwill raw list",
  "status": "ok",
  "data": {
    "raw_files": [
      {
        "id": "questionnaire",
        "kind": "questionnaire",
        "title": "Hiring Study Questionnaire",
        "stored_path": ".zwill/projects/default/surveys/hiring_study/raw/questionnaire/questionnaire.pdf",
        "stored_hash": "sha256:..."
      }
    ],
    "raw_file_count": 1
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

## 4. Add Questions

Questions use EDSL field names: `question_name`, `question_type`, `question_text`, and `question_options`.

### Add A Survey Question

```bash
zwill question add \
  --survey hiring_study \
  --question-name remote_work \
  --question-type multiple_choice \
  --question-text "How many days per week do you prefer to work remotely?" \
  --question-option "0" \
  --question-option "1-2" \
  --question-option "3-4" \
  --question-option "5" \
  --option-label "0=Never" \
  --option-label "1-2=One or two days" \
  --option-label "3-4=Three or four days" \
  --option-label "5=Five days" \
  --role survey_item \
  --source-raw questionnaire \
  --source-note "Question text and option order from questionnaire page 3."
```

Returns:

```json
{
  "command": "zwill question add",
  "status": "ok",
  "data": {
    "question": {
      "question_name": "remote_work",
      "question_type": "multiple_choice",
      "question_text": "How many days per week do you prefer to work remotely?",
      "question_options": ["0", "1-2", "3-4", "5"],
      "option_labels": {
        "0": "Never",
        "1-2": "One or two days",
        "3-4": "Three or four days",
        "5": "Five days"
      },
      "role": "survey_item",
      "source": {
        "raw_id": "questionnaire",
        "note": "Question text and option order from questionnaire page 3."
      },
      "registered_at": "2026-06-27T12:02:00Z"
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": [
    "zwill answer import --survey hiring_study --input-path answers.jsonl"
  ]
}
```

### Add A Covariate As A Question

Covariates are questions. They differ by role and provenance, not by storage model.

```bash
zwill question add \
  --survey hiring_study \
  --question-name region \
  --question-type multiple_choice \
  --question-text "Respondent region" \
  --question-option northeast \
  --question-option midwest \
  --question-option south \
  --question-option west \
  --role covariate \
  --source-raw panel_export \
  --source-note "Pre-pended from panel metadata before survey fielding."
```

Returns:

```json
{
  "command": "zwill question add",
  "status": "ok",
  "data": {
    "question": {
      "question_name": "region",
      "question_type": "multiple_choice",
      "question_text": "Respondent region",
      "question_options": ["northeast", "midwest", "south", "west"],
      "role": "covariate",
      "source": {
        "raw_id": "panel_export",
        "note": "Pre-pended from panel metadata before survey fielding."
      }
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

### Bulk Import Questions

```bash
zwill question import --survey hiring_study --input-path questions.jsonl
```

Each JSONL row is one question:

```json
{"question_name":"remote_work","question_type":"multiple_choice","question_text":"How many days per week do you prefer to work remotely?","question_options":["0","1-2","3-4","5"],"role":"survey_item"}
{"question_name":"region","question_type":"multiple_choice","question_text":"Respondent region","question_options":["northeast","midwest","south","west"],"role":"covariate"}
```

Returns:

```json
{
  "command": "zwill question import",
  "status": "ok",
  "data": {
    "imported_count": 2,
    "skipped_count": 0,
    "question_names": ["remote_work", "region"]
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

## 5. Add Respondents

Respondents are optional unless weights or respondent-level metadata are needed. Weight is respondent-level and must not be repeated in answer rows.

```bash
zwill respondent add \
  --survey hiring_study \
  --respondent-id r1 \
  --weight 1.25 \
  --metadata "sample_source=panel_a" \
  --source-raw panel_export \
  --source-note "Weight from final_weight column."
```

Returns:

```json
{
  "command": "zwill respondent add",
  "status": "ok",
  "data": {
    "respondent": {
      "respondent_id": "r1",
      "weight": 1.25,
      "metadata": {
        "sample_source": "panel_a"
      },
      "source": {
        "raw_id": "panel_export",
        "note": "Weight from final_weight column."
      }
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

Bulk import respondents:

```bash
zwill respondent import --survey hiring_study --input-path respondents.jsonl
```

Input:

```json
{"respondent_id":"r1","weight":1.25,"metadata":{"sample_source":"panel_a"}}
{"respondent_id":"r2","weight":0.85,"metadata":{"sample_source":"panel_b"}}
```

Returns:

```json
{
  "command": "zwill respondent import",
  "status": "ok",
  "data": {
    "imported_count": 2,
    "respondent_count": 2
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

## 6. Add Agent Material

Agent material is non-survey respondent-level material used to construct an EDSL Agent or digital twin. It is not a survey question and not a survey answer. It must not be included in response tables, truth marginals, empirical marginal baselines, or EDSL Survey exports.

Examples include panel profile notes, interview excerpts, CRM notes, prior behavior summaries, persona sketches, and uploaded documents attached to a respondent.

Add markdown material for one respondent:

```bash
zwill agent-material add \
  --survey hiring_study \
  --respondent-id r1 \
  --kind profile \
  --title "Panel profile" \
  --text "Respondent manages a remote team and prefers asynchronous work." \
  --tag background
```

Bulk import agent material:

```bash
zwill agent-material import --survey hiring_study --input-path agent_material.jsonl
```

Input:

```json
{"material_id":"profile_r1","respondent_id":"r1","kind":"profile","title":"Panel profile","body_markdown":"Respondent manages a remote team.","tags":["background"]}
{"material_id":"note_r2","respondent_id":"r2","kind":"interview_note","title":"Interview note","body_markdown":"Respondent dislikes mandatory office days.","tags":["work_style"]}
```

List or show material:

```bash
zwill agent-material list --survey hiring_study --respondent-id r1
zwill agent-material show --survey hiring_study --material-id profile_r1
```

Agent material is opt-in for EDSL exports and twin studies:

```bash
zwill edsl-export \
  --survey hiring_study \
  --target agent-list \
  --include-agent-material \
  --agent-material-kind profile \
  --path hiring_agents.edsl.json
```

```bash
zwill edsl-export \
  --survey hiring_study \
  --target twin-probability-job \
  --heldout-question remote_work \
  --include-agent-material \
  --agent-material-kind profile \
  --path hiring_twin_job.edsl.json
```

When included in an AgentList, material is written into each EDSL Agent's `instruction` field as construction context, not as a survey-answer trait. When included in a twin job, each scenario contains `agent_material` and `agent_material_text`.

## 7. Add Answers

### Add One Answer

```bash
zwill answer add \
  --survey hiring_study \
  --respondent-id r1 \
  --question remote_work \
  --answer "3-4"
```

Returns:

```json
{
  "command": "zwill answer add",
  "status": "ok",
  "data": {
    "answer": {
      "respondent_id": "r1",
      "question": "remote_work",
      "answer": "3-4",
      "recorded_at": "2026-06-27T12:03:00Z"
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

If `r1` was not added through `respondent add` or `respondent import`, zwill should implicitly create respondent `r1` with weight `1.0`.

### Add Missingness

```bash
zwill answer add \
  --survey hiring_study \
  --respondent-id r3 \
  --question remote_work \
  --missing-code refused
```

Returns:

```json
{
  "command": "zwill answer add",
  "status": "ok",
  "data": {
    "answer": {
      "respondent_id": "r3",
      "question": "remote_work",
      "answer": null,
      "missing_code": "refused"
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

### Bulk Import Answers

```bash
zwill answer import --survey hiring_study --input-path answers.jsonl
```

Input:

```json
{"respondent_id":"r1","question":"remote_work","answer":"3-4"}
{"respondent_id":"r1","question":"region","answer":"northeast"}
{"respondent_id":"r2","question":"remote_work","answer":"1-2"}
{"respondent_id":"r2","question":"region","answer":"south"}
```

Returns:

```json
{
  "command": "zwill answer import",
  "status": "ok",
  "data": {
    "imported_count": 4,
    "answer_count": 4,
    "respondent_count": 2,
    "question_count": 2,
    "quarantined_count": 0
  },
  "warnings": [],
  "errors": [],
  "next_steps": [
    "zwill commit --survey hiring_study"
  ]
}
```

Invalid answer rows are quarantined, not silently coerced:

```json
{
  "command": "zwill answer import",
  "status": "ok",
  "data": {
    "imported_count": 3,
    "answer_count": 3,
    "quarantined_count": 1,
    "quarantine_examples": [
      {
        "line": 4,
        "code": "invalid_answer_option",
        "question": "remote_work",
        "answer": "every_day",
        "valid_options": ["0", "1-2", "3-4", "5"]
      }
    ]
  },
  "warnings": [
    {
      "code": "partial_import",
      "message": "1 answer row was quarantined."
    }
  ],
  "errors": [],
  "next_steps": [
    "zwill quarantine list --survey hiring_study"
  ]
}
```

## 8. Add Survey Context

Users can attach markdown context to a survey. This is intended for source descriptions, provenance notes, fielding details, methodological caveats, and other human-readable documentation.

Append markdown context from a file:

```bash
zwill context add --survey hiring_study --input-path source_context.md
```

Or from a command argument:

```bash
zwill context add --survey hiring_study --text "Source: Pew ATP Wave 154, fielded September 2024."
```

Returns:

```json
{
  "command": "zwill context add",
  "status": "ok",
  "data": {
    "context": {
      "survey": "hiring_study",
      "path": ".zwill/projects/default/surveys/hiring_study/context.md",
      "source_path": "source_context.md",
      "chars": 512,
      "total_chars": 512,
      "updated_at": "2026-06-27T12:04:00Z"
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

Replace existing context:

```bash
zwill context set --survey hiring_study --input-path source_context.md
```

Show stored context:

```bash
zwill context show --survey hiring_study
```

State written:

```text
.zwill/projects/default/surveys/hiring_study/context.md
```

## 9. Inspect State

Export survey questions as an EDSL Survey serialization:

```bash
zwill edsl-export --survey hiring_study
```

`edsl-export` is an export command. It prints the EDSL `Survey.to_dict()` JSON serialization to stdout instead of the JSON output envelope. Each stored zwill question is instantiated as an EDSL `Question(question_type, ...)` and added to an EDSL `Survey` in registration order.

Write the same JSON to a file:

```bash
zwill edsl-export --survey hiring_study --path hiring_study.edsl.json
```

Export respondents as an EDSL AgentList:

```bash
zwill edsl-export --survey hiring_study --target agent-list --path hiring_agents.edsl.json
```

By default, every survey question becomes an agent trait. Each agent represents one respondent. Trait keys are `question_name`; trait values are that respondent's expanded answer labels. The AgentList-level codebook maps each trait key to the corresponding `question_text`.

Export only a subset of questions as traits:

```bash
zwill edsl-export \
  --survey hiring_study \
  --target agent-list \
  --question remote_work \
  --question region \
  --path hiring_agents.edsl.json
```

Equivalent comma-separated form:

```bash
zwill edsl-export --survey hiring_study --target agent-list --questions remote_work,region
```

Include survey context markdown and non-survey agent material as Agent instructions:

```bash
zwill edsl-export \
  --survey hiring_study \
  --target agent-list \
  --questions remote_work,region \
  --include-survey-context \
  --include-agent-material \
  --agent-material-kind profile \
  --path hiring_agents.edsl.json
```

Question answers are exported as Agent traits. Survey context and agent material are exported as Agent instructions for construction. They must not be counted as survey responses or included in truth marginals.

AgentList exports should include a shared EDSL `traits_presentation_template` by default. The default template must explain that traits are observed source-survey question-and-answer pairs for the respondent, then render each selected trait as:

```text
- Survey question: <question text from codebook>
  Recorded answer: <respondent answer>
```

This avoids presenting survey answers as cryptic generic traits. Users can override the template with `--traits-presentation-template` or `--traits-presentation-template-path`, and can disable zwill's default with `--no-default-traits-presentation-template`.

Inspect an exported AgentList:

```bash
zwill agent-list inspect --input-path hiring_agents.edsl.json
```

The inspector reports agent count, trait keys, codebook keys, instruction coverage, mean instruction length, export metadata, and a small agent preview. Use `--format json` for machine-readable output.

Export an EDSL job that asks an exported AgentList a new question:

```bash
zwill agent-study export \
  --agent-list hiring_agents.edsl.json \
  --question-name new_remote_policy \
  --question-type multiple_choice \
  --question-text "Would this respondent support a four-day remote-first policy?" \
  --question-option "Yes" \
  --question-option "No" \
  --model openai:gpt-5.5 \
  --path hiring_agent_study.edsl.json
```

The question can also come from a JSON file:

```bash
zwill agent-study export \
  --agent-list hiring_agents.edsl.json \
  --question-path new_question.json \
  --model openai:gpt-5.5 \
  --path hiring_agent_study.edsl.json
```

The exported job is a normal EDSL `Jobs.to_dict()` serialization with the AgentList attached as EDSL agents. Run it with `zwill edsl-run`, then import the serialized Results object:

```bash
zwill edsl-run --job hiring_agent_study.edsl.json --path hiring_agent_study_results.json.gz
zwill agent-study import --input-path hiring_agent_study_results.json.gz
```

Imported AgentStudy results are stored in two forms:

- Raw EDSL Results files are copied to `.zwill/projects/<project_id>/agent_studies/<job_id>/raw/`.
- Extracted answer rows are stored in `.zwill/projects/<project_id>/agent_studies/answers.jsonl`.
- Run metadata is tracked in `.zwill/projects/<project_id>/agent_studies/manifest.json`.

Inspect and report imported runs:

```bash
zwill agent-study list
zwill agent-study show --job-id <job_id> --include-summary
zwill agent-study report --job-id <job_id> --format table
zwill agent-study report --job-id <job_id> --format html --path hiring_agent_study_report.html
```

Reports preserve the raw model answer, question attributes, model/service, agent traits, and construction instruction coverage so analysts can see what each constructed agent was asked and how it answered.

Export an EDSL job that asks a frontier model for option-level response probabilities:

```bash
zwill edsl-export \
  --survey hiring_study \
  --target probability-job \
  --questions remote_work,region \
  --model openai:gpt-5.5 \
  --model google:gemini-2.5-pro \
  --model-param google:gemini-2.5-pro:max_tokens=8192 \
  --model-param google:gemini-2.5-pro:thinking_budget=4096 \
  --model anthropic:claude-sonnet-4-5 \
  --path hiring_probability_job.edsl.json
```

The probability job is serialized with `Jobs.to_dict()` and prints raw JSON to stdout unless `--path` is supplied. It uses a `ScenarioList` with one scenario per selected multiple-choice question. Scenario fields include `survey_name`, `survey_context`, `source_question_name`, `source_question_text`, `options_text`, `option_keys`, and `option_labels`; `options_text` uses letter keys such as `a: Fully remote`, `b: Hybrid`, and so on. The EDSL question is a `QuestionFreeText` named `response_probabilities` by default. Its prompt instructs the model to return only valid JSON with a `probabilities` array in the same order as `option_labels` and an optional short `notes` string. The export includes `zwill.probability_job_id`, a deterministic hash over the survey, scenarios, models, and model parameters.

Models can be provided with repeatable `--model` flags or comma-separated `--models`. Each entry may be either `model_name` or `service_name:model_name`. `--service-name` applies to unqualified model names. If no model is provided, the default is `gpt-5.5`. Model parameters can be supplied with repeatable `--model-param` flags. Use `key=value` to apply a parameter to every model, or `service_name:model_name:key=value` to target one model. Values are parsed as JSON when possible, so `4096`, `0`, `true`, and `0.2` become typed values.

Run an exported EDSL job and write a serialized EDSL `Results` object:

```bash
zwill edsl-run \
  --job hiring_probability_job.edsl.json \
  --path hiring_probability_results.json.gz \
  --fresh \
  --progress-bar
```

`edsl-run` loads an EDSL `Jobs` serialization, runs it with `Jobs.run(...)`, and writes `Results.to_dict()` to the requested path. Paths ending in `.gz` are gzip-compressed JSON. If the job contains `zwill.probability_job_id`, that metadata is copied onto the Results JSON so later `prob-results import` uses the same ID. Before importing EDSL, zwill loads `.env` files with `python-dotenv`, without overriding already-set environment variables. By default it loads the nearest `.env` found by walking up from the current working directory; `--env-path` must force a specific `.env` for run packages outside the repo tree. With no run flags, zwill calls `job.run()` directly and lets EDSL use its normal defaults. Use `--dry-run` to validate the job file and show the run parameters plus loaded env path without making model API calls. Optional flags such as `--fresh`, `--progress-bar`, `--offload-execution`, `--use-api-proxy`, and repeatable `--run-param key=value` are passed through only when supplied.

Import serialized EDSL `Results` from a probability job:

```bash
zwill prob-results import \
  --survey hiring_study \
  --input-path example_prob_job.json.gz
```

The command stores the raw Results file under `.zwill/projects/<project_id>/surveys/<survey>/probability_jobs/<job_id>/raw/` and extracts one predicted probability distribution per EDSL result row. The default `job_id` is a deterministic hash of the Results survey/scenario/model payload; it can be overridden with `--job-id`. JSON answers wrapped in markdown fences are accepted. Extracted rows are stored in `.zwill/projects/<project_id>/surveys/<survey>/probability_predictions.jsonl`.

Compare elicited probabilities to committed respondent marginals:

```bash
zwill prob-results report --survey hiring_study --job-id <job_id>
```

The report joins predictions to committed weighted truth marginals and compares against a uniform-over-options baseline. Metrics include MAE, Brier score, actual KL divergence, uniform Brier/KL, and improvement over uniform. Use `--format json` for labeled probability distributions and machine-readable summaries. Use `--format csv --path report.csv` for flat question/model metric rows. Use `--format html --path report.html` for a standalone browser-readable report with an embedded `report-data` JSON blob. The HTML performance plot shows actual metric levels, a yellow uniform-baseline marker, and green/red arrows showing whether each model beats or trails uniform for the selected metric.

## 10. Digital Twin Probability Jobs

Export an EDSL job that asks a model to act as a digital twin for each respondent and predict that respondent's probability distribution over a held-out multiple-choice question:

```bash
zwill edsl-export \
  --survey hiring_study \
  --target twin-probability-job \
  --heldout-question remote_work \
  --heldout-question region \
  --context-question-count 5 \
  --sample-respondents 100 \
  --seed 123 \
  --complete-cases \
  --stratify-actual \
  --model openai:gpt-5.5 \
  --path hiring_twin_job.edsl.json
```

Each scenario represents one respondent and one held-out question. Scenario fields include `respondent_id`, `heldout_question_name`, `heldout_question_text`, `heldout_options`, `actual_answer`, and `observed_answers`. `observed_answers` contains the context questions, their options, and the respondent's actual answers. Held-out questions are always excluded from context. Use repeatable `--heldout-question` or comma-separated `--heldout-questions` to run multiple held-out questions in one job.

Supplemental twin material can be injected with repeatable `--twin-material <path>`. This is a general mechanism for testing extra information in twin construction, not a prior-specific API. The file can be Markdown/text, a JSON object, a JSON list, a JSON object with `materials`, or JSONL. Material records support `material_id`, `kind`, `title`, `body_markdown` or `text`, and optional selectors `survey`, `question`/`heldout_question`, and `respondent_id`. A record applies when all provided selectors match the current scenario. Matching records are stored on the scenario as `twin_material`; the rendered text is stored as `twin_material_text`. Use `--max-twin-material-chars` to truncate the rendered block per scenario.

Example JSONL:

```json
{"material_id":"frontier_prior_q1","kind":"model_prior","question":"q1","title":"Frontier one-shot prior","body_markdown":"A frontier model estimated: yes 0.70, no 0.30."}
{"material_id":"empirical_marginal_q1","kind":"oracle_marginal","question":"q1","title":"Observed group marginal","body_markdown":"Committed survey marginal: yes 0.50, no 0.50."}
```

Use `--context-question` or `--context-questions` to choose context questions explicitly; otherwise all non-held-out questions with available answers are candidates. Use `--respondent`, `--respondents`, `--sample-respondents`, `--seed`, or `--limit-respondents` to control respondent selection. Sampling is deterministic for a fixed seed. `--complete-cases` restricts the study to respondents with all selected context and held-out answers. `--stratify-actual` samples within actual-answer strata while preserving approximate observed proportions. `--balance-actual` draws a balanced sample across actual-answer options for each held-out question.

Run the exported job with `zwill edsl-run`, then import and score the Results:

```bash
zwill twin-results import \
  --survey hiring_study \
  --input-path hiring_twin_results.json.gz

zwill twin-results report \
  --survey hiring_study \
  --job-id <job_id> \
  --format html \
  --path hiring_twin_report.html
```

Digital twin scoring compares the predicted distribution to the respondent's actual held-out answer. Metrics include probability assigned to the actual answer, negative log likelihood, one-hot Brier score, top-1 correctness, and rank of the actual answer. Baselines include uniform random choice and, when committed truth marginals are available, the empirical marginal distribution for the held-out question. Reports support table, JSON, CSV, and standalone HTML output. HTML reports include study metadata, metric definitions, overall model summaries, held-out/model filters, wrong-only filtering, sorting by respondent, lowest `p(actual)`, or highest NLL, and respondent-level raw model responses.

The empirical marginal baseline is an oracle-style benchmark for already-observed survey items. It should be labeled as empirical marginal or observed marginal in reports. It must not be described as available for a genuinely new held-out question; its role is to show whether digital-twin context adds respondent-level signal beyond the known population distribution.

`zwill twin-study run` provides the one-step version of the digital twin workflow:

```bash
zwill twin-study run \
  --survey hiring_study \
  --heldout-questions remote_work,region \
  --context-question-count 5 \
  --sample-respondents 100 \
  --seed 123 \
  --complete-cases \
  --stratify-actual \
  --model openai:gpt-5.5 \
  --model google:gemini-2.5-pro \
  --model-param google:gemini-2.5-pro:max_tokens=8192 \
  --model-param google:gemini-2.5-pro:thinking_budget=4096 \
  --model-param google:gemini-2.5-pro:temperature=0 \
  --output-dir workdir \
  --replace
```

The command must write the exported EDSL Jobs JSON, call EDSL `job.run()` through the same `edsl-run` machinery, serialize the Results object to JSON or JSON.GZ, import the raw Results and extracted probabilities under the same deterministic digital twin job id, and write an HTML report by default. Optional `--report-json` and `--report-csv` paths write machine-readable report exports. Model summaries should use provider-qualified labels such as `openai:gpt-5.5` and `google:gemini-2.5-pro` so providers are not conflated when model names overlap.

Each digital twin import or one-step run should upsert a per-survey run manifest entry under the survey's `digital_twin_jobs` directory. `zwill twin-study list --survey <survey>` lists known runs, and `zwill twin-study show --survey <survey> --job-id <job_id>` returns run metadata, import metadata, and optionally summary diagnostics. `zwill twin-study compare --survey <survey> --job-id <a> --job-id <b>` compares two or more imported runs by model, including rows, accuracy, probability assigned to the actual answer, NLL, Brier score, and deltas versus empirical marginals when available. When compared runs share respondent/question/model rows, output must include paired response-change diagnostics: changed top-choice count/rate, unchanged count, corrections, regressions, wrong-to-wrong changes, correct-to-correct changes, mean probability-on-actual delta, mean NLL delta, and capped examples. Comparison output supports table, JSON, and CSV.

Digital twin HTML reports should include a run health panel, overall baseline comparisons, confidence calibration bins, expected calibration error, NLL percentiles, overconfident misses, option confusion summaries, largest misses, and question/model cases where the model beats or trails the empirical marginal baseline.

Twin development experiments are a reproducible workflow for constructing, validating, and comparing digital twins. A practitioner should be able to compile survey/context/respondent sources, define reusable construction approaches, export EDSL validation jobs from a plan, run/import Results objects, compare approaches, inspect row-level failures, and generate practitioner reports.

Reusable twin approaches are stored under `.zwill/projects/<project_id>/surveys/<survey>/digital_twin_jobs/approaches.json`.

```bash
zwill twin-approach add \
  --survey hiring_study \
  --approach-id baseline_context \
  --name "Prior survey answers only" \
  --description "Use observed survey answers as twin context." \
  --context-question-count 5 \
  --complete-cases \
  --model openai:gpt-5.5

zwill twin-approach add --survey hiring_study --input-path approach.json
zwill twin-approach scaffold --survey hiring_study --approach-id baseline_context --path approach.json
zwill twin-approach list --survey hiring_study
zwill twin-approach show --survey hiring_study --approach-id baseline_context
zwill twin-approach note --survey hiring_study --approach-id baseline_context --text "Hypothesis and intended failure mode."
```

Each approach record must include `approach_id`, `name`, `description`, `notes`, `tags`, `construction`, `created_at`, and `updated_at`. The `construction` object may contain twin export settings such as context question selection, respondent sampling, agent-material filters, supplemental twin-material paths, model specs, and model parameters. Approach records describe construction recipes; they do not imply that a job has been run.
`twin-approach scaffold` must write a valid editable JSON approach definition without mutating project state.

Experiment plans compile one or more registered or inline approaches into EDSL job files:

```json
{
  "plan_id": "holdout_v1",
  "survey": "hiring_study",
  "heldout_questions": "q1,q2",
  "defaults": {
    "sample_respondents": 100,
    "seed": 789,
    "complete_cases": true
  },
  "arms": [
    {"approach_id": "baseline_context"},
    {
      "approach_id": "profile_context",
      "name": "Prior answers plus profile material",
      "construction": {
        "include_agent_material": true,
        "agent_material_kind": ["profile"]
      }
    }
  ]
}
```

```bash
zwill twin-experiment init-plan --survey hiring_study --plan-id holdout_v1 --heldout-questions q1,q2 --approach-id baseline_context --path holdout_v1.json
zwill twin-experiment export-plan --input-path holdout_v1.json --output-dir holdout_v1_jobs
```

`init-plan` must write a valid editable JSON plan using existing survey question names, defaulting to the first multiple-choice question when no held-out question is provided. It must not mutate project state or export jobs.
`export-plan` must write `manifest.json` plus one serialized EDSL Jobs JSON per arm. It must also upsert planned experiment records in `experiments.json` with `experiment_id`, `job_id`, `approach`, `approach_id`, `description`, `tags`, `primary_metric`, and `plan` metadata containing `plan_id`, `plan_path`, `job_path`, and the merged construction settings. It must not call a remote model. Agents or users run the exported jobs separately with `zwill edsl-run`, then import Results with `zwill twin-results import`; comparison/reporting should work from the planned experiment records once matching job results exist.

Plan lifecycle helpers:

```bash
zwill twin-experiment plan-status --survey hiring_study --plan-id holdout_v1
zwill twin-experiment note --survey hiring_study --plan-id holdout_v1 --text "Plan hypothesis and decision rule."
zwill twin-experiment package --manifest holdout_v1_jobs/manifest.json --output-dir holdout_v1_run_package --env-path /path/to/.env
zwill twin-experiment import-plan-results --manifest holdout_v1_jobs/manifest.json --results-dir holdout_v1_results
zwill twin-experiment bundle --survey hiring_study --plan-id holdout_v1 --metric nll --model openai:gpt-5.5 --output-dir holdout_v1_bundle --report-export
zwill twin-experiment dashboard --survey hiring_study --plan-id holdout_v1 --metric nll --model openai:gpt-5.5 --bundle-manifest holdout_v1_bundle/manifest.json --path holdout_v1_dashboard.html
zwill twin-experiment bundle-show --manifest holdout_v1_bundle/manifest.json
zwill twin-approach diff --survey hiring_study --left baseline_context --right profile_context --format html --path holdout_v1_approach_diff.html
```

`plan-status` must show each planned arm, job id, job path, whether results have been imported, extracted prediction row counts, imported models, held-out questions, issue counts, and whether the plan is ready for comparison. `package` must create a portable run directory from an export manifest, copying the export manifest, plan file, approach records, EDSL job files, an empty `results/` directory, a package `manifest.json`, and a `RUN.md` with commands for running jobs and importing results. `import-plan-results` must scan a directory for serialized EDSL Results JSON/JSON.GZ files, infer each result job id, import files whose job ids match the plan manifest, and report unmatched paths plus plan jobs still missing results. `bundle` must write `comparison.json`, deterministic plot artifacts when paired rows exist, standalone microdata audit HTML/JSON, and a `manifest.json`. With `--report-export`, it must also export the EDSL report-writing job, prompt, and context without running the model. `dashboard` must write standalone deterministic HTML and JSON summarizing plan status, comparison metrics, selected approach, paired response-change diagnostics, and linked bundle artifacts. `bundle-show` must read a bundle manifest and display the important artifact paths, selected approach, selected metric value, and next commands. `twin-approach diff` must compare registered approaches or planned experiment approaches by metadata and construction fields, with table, JSON, and HTML output. Plan export manifests should include `duplicate_job_ids` when multiple arms compile to identical job JSON, so identical construction recipes are auditable.

Twin development experiments can also be recorded after the fact as human-readable approach records over existing twin jobs. This is useful for one-off jobs that were not exported from a plan.

```bash
zwill twin-experiment record \
  --survey hiring_study \
  --job-id <job_id> \
  --experiment-id baseline_context_v1 \
  --approach "Baseline context, 5 prior answers" \
  --description "Held out each item and used five other survey answers as twin context." \
  --tag baseline \
  --primary-metric nll

zwill twin-experiment list --survey hiring_study
zwill twin-experiment compare --survey hiring_study --metric nll
zwill twin-experiment select --survey hiring_study --metric nll --model openai:gpt-5.5
```

Experiment records are stored under `.zwill/projects/<project_id>/surveys/<survey>/digital_twin_jobs/experiments.json`. Each record must include `experiment_id`, `job_id`, `approach`, `description`, `tags`, `primary_metric`, and creation metadata. Supported selection metrics are:

- `nll`: mean negative log likelihood; lower is better.
- `brier`: mean one-hot Brier score; lower is better.
- `accuracy`: top-1 accuracy; higher is better.
- `p_actual`: mean probability on the actual answer; higher is better.
- `nll_vs_empirical`: improvement over empirical marginal NLL; higher is better.
- `brier_vs_empirical`: improvement over empirical marginal Brier; higher is better.

`twin-experiment compare` should rank each experiment/model row by the requested metric and include the selected row, metric direction, raw loss/score values, job id, model, approach name, and approach description. It should also include paired response-change diagnostics for shared respondent/question/model rows so experiment reports can distinguish probability-quality improvements from actual top-choice answer changes. `twin-experiment select` should return only the best row plus the metric definition.

`zwill twin-experiment plots --survey <survey> --metric <metric> [--model <model>]` should generate deterministic plot artifacts from recorded experiment comparisons. The first required plot bundle is a paired `p(actual)` scatter, a top-choice-change summary, and an interactive paired microdata table for shared respondent/question/model rows. The microdata table should include respondent id, held-out question, actual answer, top choices, probabilities, probability deltas, observed-answer traits, supplemental twin material, prompt template text, and model notes when available. The command must write SVG files, HTML table artifacts, paired row data JSON, and a `manifest.json` under either `--path` or `.zwill/projects/<project_id>/surveys/<survey>/digital_twin_jobs/plots/<plot_id>/`. Plot manifests should include artifact paths, compact response-change summaries, the selected metric, and comparison rows. These plots/tables are deterministic artifacts produced from stored zwill data; frontier models may interpret them in reports but must not generate them.

`zwill twin-experiment microdata --survey <survey> --metric <metric> [--model <model>]` should generate a standalone HTML microdata audit table and JSON sidecar without requiring a full report context. The grouping key is `respondent_id × heldout_question × model`, so it must support multiple experiment arms and multiple held-out questions. The HTML should render one group header per grouping key and one audit row per experiment response within that group, rather than combining multiple experiments into one row. Each experiment response row should include respondent id, experiment/approach, actual answer, top choice, full probabilities, `p(actual)`, NLL, Brier, top-1 correctness, observed-answer traits, model notes, supplemental twin material, prompt template, agent material, and source row metadata when available. The JSON sidecar should expose both `groups` and flat `prediction_rows`. The HTML should provide filters for held-out question, model, actual answer, diagnostic category, search, and experiment checkboxes. This audit table is the primary artifact for inspecting twin microdata; pairwise plots are companion summaries.

Twin experiment reports follow the same export/run/import/render lifecycle as practitioner reports, but the assembled context is experiment-specific:

```bash
zwill twin-experiment report-export \
  --survey hiring_study \
  --metric nll \
  --model openai:gpt-5.5 \
  --include-plots experiment_plots/manifest.json \
  --report-model openai:gpt-5.5

zwill edsl-run --job .zwill/projects/default/practitioner_reports/<report_id>/job.edsl.json --path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz
zwill twin-experiment report-import --report-id <report_id> --input-path .zwill/projects/default/practitioner_reports/<report_id>/results.json.gz
zwill twin-experiment report-render --report-id <report_id> --path experiment_report.html
```

The exported context must include the survey context, selected metric and direction, selected winning row, all comparison rows, each approach description, run manifests, import metadata, held-out question metadata, paired response-change diagnostics, optional plot summaries from `--include-plots`, and examples of injected `twin_material` when present. The report-writing prompt should ask the frontier model to describe the two or more approaches, the methods, what differed, the loss comparison, whether the difference affects probability quality or top-choice accuracy, how to interpret paired response changes, and caveats such as small sample size or benchmark leakage. The report should be Markdown generated by EDSL and rendered later without additional model calls. When plot manifests are included, HTML rendering should inline the deterministic SVG artifacts without another model call.

### Twin prompt pipelines (custom reasoning / evidence framing)

`--twin-prompt-pipeline <file.json>` replaces the built-in `--prompt-variant` prompt with an ordered pipeline of **steps** — a general slot for experimenting with how a twin reasons and how its evidence is framed. The file is a JSON list of `{"name", "template" | "template_path"}` steps. Each step becomes a free-text question in one EDSL survey; a step's template may use the scenario evidence (`observed_answers_text`, `respondent_metadata`, `heldout_question_text`, `heldout_options_text`, `survey_context`, …) and, via EDSL answer-piping, any prior step's answer as `{{ <step_name>.answer }}`. The **final step must include the `{{ output_contract }}` marker**, which zwill replaces with the canonical "return JSON `{probabilities:[…]}`" instruction; that step's answer is the one scored, so any pipeline stays measurable through the normal gate. A length-1 pipeline is a single custom prompt; a length-2 pipeline is e.g. "argue why each option / note thin evidence" → "weigh and predict", as two model calls. Import records `scored_question_name` and keeps intermediate step answers as `reasoning_steps` for inspection. Bundled examples live in `examples/twin_pipelines/` (`dialectical.json`, `persona_reframe.json`). A `twin-approach` can carry a pipeline so `twin-experiment` / `twin-study compare` rank pipelines head-to-head on NLL / ECE / p(actual).

### Respondent metadata as twin context

Respondent metadata (panel covariates such as age, party, region) is included as twin context **by default** for the `twin-probability-job`, `numeric-twin-job`, `rank-utility-twin-job`, and `agent-list` exports. For the twin jobs it is rendered as a `Respondent profile:` block prepended to the observed answers; for `agent-list` it is merged into the agent traits. Suppress all of it with `--exclude-metadata-context`, or a single key with `--exclude-metadata-key <key>` (repeatable). When the included metadata values look like unlabeled numeric codes (e.g. `F_AGECAT=4`), the export returns an `uncoded_metadata` warning so the operator can map codes to readable labels before running.

## 10a. Numeric (Continuous) Twin Jobs

For a held-out question with `question_type: numeric`, `zwill edsl-export --survey <survey> --target numeric-twin-job --heldout-question <q>` builds a twin job in which the model predicts a **quantile distribution** (5th/25th/50th/75th/95th percentiles) of the value the respondent would give, honoring optional `numeric_min` / `numeric_max` bounds. Run it with `zwill edsl-run`, then:

```bash
zwill numeric-results import --survey <survey> --input-path numeric_results.json.gz
zwill numeric-results report --survey <survey> --job-id <job_id> --format html --path numeric_report.html
```

Scoring uses proper scoring rules for distributions: mean pinball loss (headline), CRPS (≈ 2× mean pinball), interval coverage at the 50% and 90% nominal levels, and median absolute error, all survey-weighted. The baseline is the population marginal-quantile "climatology"; the report shows pinball skill versus it and a reliability (calibration) diagram. Non-monotone or out-of-bounds quantiles from the model are repaired (running-max projection plus bounds clamp) rather than dropped; rows with too few or non-numeric quantiles are recorded as issues.

## 10b. Open-Ended Answer Coding

Free-text (`free_text`) questions are validated by **coding them into themes** and running the resulting `multiple_choice` question through the standard twin gate. Two ordinary export → run → import cycles:

```bash
# 1. Derive a codebook of themes from a sample of the free-text answers.
zwill edsl-export --survey <survey> --target open-codebook-job --heldout-question <free_text_q> \
  --n-themes 8 --sample-answers 150 --model openai:gpt-5.5 --path codebook.edsl.json
zwill edsl-run --job codebook.edsl.json --path codebook_results.json.gz
zwill open-coding codebook-import --survey <survey> --input-path codebook_results.json.gz

# 2. Classify each respondent's answer into one codebook theme.
zwill edsl-export --survey <survey> --target open-coding-job --heldout-question <free_text_q> \
  --coded-question-name <free_text_q>_coded --model openai:gpt-5.5 --path coding.edsl.json
zwill edsl-run --job coding.edsl.json --path coding_results.json.gz
zwill open-coding import --survey <survey> --input-path coding_results.json.gz
```

`open-codebook-job` builds a single scenario that shows a sample of the answers (`--sample-answers`, default 150) and asks for at most `--n-themes` (default 8) mutually-exclusive themes. `codebook-import` stores it under `open_coding/<question>/codebook.json`. `open-coding-job` builds one scenario per respondent that classifies their actual answer into a codebook theme (or a reserved `unclassified` bucket). `open-coding import` writes a new `multiple_choice` question (`--coded-question-name`, default `<question>_coded`; options = theme codes, labels = theme labels) plus one coded answer per respondent, and returns a warning if more than 20% of answers were `unclassified` (the codebook may not fit) or if the results carried multiple codings per respondent (code with a single model). The coded question is then validated exactly like any other multiple-choice target.

## 11. Cross-Survey Twin Benchmarks

`zwill twin-benchmark run --config <config.json>` runs a set of digital twin studies from a JSON config. The config contains benchmark metadata, optional default sampling/model settings, and a `studies` list. Each study must name a `survey` and one or more held-out questions; per-study keys such as `context_questions`, `exclude_context_question`, and `allow_unapproved` (or a top-level `allow_unapproved`) are honored. The command calls the same `twin-study run` machinery for each study, then writes a benchmark manifest containing survey names, job ids, paths, and run status. `--dry-run` exports jobs without model calls. Because the run makes model calls, it loads a `.env` file first: pass `--env-path <file>` or rely on auto-discovery (skipped for `--dry-run`).

`zwill twin-benchmark report` accepts either `--manifest` from a prior run or `--config` where every study already has a `job_id`. Reports support JSON, CSV, and standalone HTML. Benchmark reports should summarize metrics by survey/model and aggregate by model across surveys, including accuracy, p(actual), NLL, NLL p95, Brier, ECE, and NLL/Brier deltas versus empirical marginals. HTML reports should include practical guidance for model selection and overconfidence risk.

Practitioner report generation follows the same export/run/import/render lifecycle as digital twin probability jobs. The primary practitioner-facing report is single-survey: `zwill twin-study practitioner-report-export --survey <survey> --job-id <job_id>` collects the survey context, held-out question text/options, run metadata, import metadata, model summaries, baseline comparisons, calibration/confidence warnings, overconfident misses, and worst misses for one survey twin-validation job, then writes an EDSL Jobs JSON whose free-text question asks a frontier model to write detailed Markdown from that context and the packaged report-writing guidance. The report should be framed around the uploaded survey and its validation evidence, not as a cross-survey benchmark. The export must store a stable `practitioner_report_id`, prompt, assembled report context, and EDSL job under `.zwill/projects/<project_id>/practitioner_reports/<report_id>/`, while also honoring user-supplied output paths.

`zwill twin-benchmark practitioner-report-export` accepts the same `--manifest` or `--config` sources for cross-survey benchmark/meta reports. Cross-survey reports should be used to compare exercises and models, not as the default practitioner narrative for one uploaded survey.

The exported job is run through `zwill edsl-run` or by another agent using EDSL `job.run()`. `zwill twin-benchmark practitioner-report-import --input-path <results.json.gz>` stores the raw Results object under the matching report id and extracts the generated Markdown. `zwill twin-benchmark practitioner-report-render --report-id <report_id>` wraps the stored Markdown as standalone HTML using the stored benchmark payload; rendering must not call a frontier model. The HTML should embed source JSON and include a button that copies the Markdown report to the clipboard for LLM use. Public-facing report HTML should use Expected Parrot branding, should not mention the internal tool name, and should include canned boilerplate explaining why held-out questions are used as a proxy for new questions asked of instantiated digital twins. That boilerplate should explain that this is a high-bar test because survey designers typically avoid asking highly correlated questions.

`zwill twin-study practitioner-report` and `zwill twin-benchmark practitioner-report` remain one-step convenience commands. They should call the same export, EDSL run, import, and render machinery rather than using a separate hidden model-call path.

## 12. Declarative Workflows

`zwill workflow run <workflow.json|workflow.yaml>` runs user-defined reusable command sequences. Workflows orchestrate normal public commands; they must not use a private hidden API. This keeps reports and artifacts explainable because every step is a command the user could run directly.

Workflow file shape:

```json
{
  "name": "pew-agent-study",
  "description": "Import PEW, export an AgentList, run AgentStudy jobs, and render a report.",
  "vars": {
    "survey": "pew_w154_diff1",
    "workdir": "examples/pew_w154_diff1/workdir",
    "model": "openai:gpt-5.5"
  },
  "steps": [
    {
      "id": "init",
      "run": "zwill init",
      "cwd": "{{ workdir }}"
    },
    {
      "id": "agent-list",
      "run": "zwill edsl-export --survey {{ survey }} --target agent-list --questions diff1_a,diff1_b --limit 30 --path agents.edsl.json",
      "cwd": "{{ workdir }}"
    }
  ]
}
```

Required behavior:

- Support JSON and YAML workflow files.
- Support simple `{{ var }}` substitution from the workflow `vars` object plus repeatable CLI overrides, `--var key=value`.
- Each step must have a `run` command. Optional step fields include `id`, `name`, `cwd`, `env`, `ok_return_codes`, and `continue_on_error`.
- Commands run as shell commands in the requested `cwd`.
- `zwill workflow explain <path>` and `zwill workflow dry-run <path>` must render variables and list the commands without executing them.
- `zwill workflow run <path>` must execute steps in order, stop on the first failing step unless `continue_on_error` is true, and write a manifest.
- `--artifacts-dir <dir>` controls where artifacts are written. Without it, write under `.zwill/projects/<project_id>/workflows/<workflow-name>-<timestamp>/`.
- Each step writes `NN_<step-id>.stdout.txt` and `NN_<step-id>.stderr.txt`.
- `manifest.json` records workflow path, name, variables, artifact directory, timestamps, commands, cwd, return code, status, stdout path, and stderr path.
- `--resume --artifacts-dir <dir>` skips steps already marked successful in the existing manifest.

Example:

```bash
zwill workflow explain workflow.json
zwill workflow dry-run workflow.json --var model=openai:gpt-5.5
zwill workflow run workflow.json --artifacts-dir workflow_artifacts
zwill workflow run workflow.json --artifacts-dir workflow_artifacts --resume
```

Packaged demo helpers may remain under `workflow` for compatibility, but they are not the primary workflow abstraction.

### Packaged PEW Demo

Build the persistent Pew W154 DIFF1 demo project in one command:

```bash
zwill workflow pew-demo
```

By default this command:

- reads normalized Pew inputs from the `llm-survey-priors` package path;
- writes persistent state to `examples/pew_w154_diff1/workdir/`;
- expands source codebook values into human-readable question options and answers;
- imports questions, respondents, and answers;
- commits the survey;
- exports an EDSL Survey JSON file;
- exports an EDSL probability job using `openai:gpt-5.5` and `google:gemini-2.5-pro`.

Use a custom source directory or workdir:

```bash
zwill workflow pew-demo \
  --source-dir /path/to/normalized \
  --workdir /tmp/pew_workdir
```

Import a saved EDSL `Results` object and write JSON, CSV, and HTML reports in the same workflow:

```bash
zwill workflow pew-demo \
  --results-path example_prob_job.json.gz
```

Use `--no-edsl` to skip EDSL exports. Use `--no-fresh` to reuse existing `.zwill` state and imports in the workdir.

Render answers as a table:

```bash
zwill table --survey hiring_study
```

`table` is a display command. It prints a Python Rich table to stdout instead of the JSON output envelope. The first column is `respondent_id`; subsequent columns are questions ordered by registration order. Each row contains one respondent's answer values.

Example shape:

```text
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┓
┃ respondent_id ┃ remote_work ┃ region ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━┩
│ r1            │ 3-4         │ north… │
│ r2            │ 1-2         │ south  │
└───────────────┴─────────────┴────────┘
```

```bash
zwill status
```

Returns:

```json
{
  "command": "zwill status",
  "status": "ok",
  "data": {
    "surveys": [
      {
        "name": "hiring_study",
        "status": "draft",
        "raw_files": 2,
        "questions": 2,
        "respondents": 2,
        "answers": 4,
        "has_context": true,
        "open_quarantine_issues": 0,
        "committed": false
      }
    ]
  },
  "warnings": [],
  "errors": [],
  "next_steps": [
    "zwill commit --survey hiring_study"
  ]
}
```

Show one survey:

```bash
zwill survey show --name hiring_study
```

Returns:

```json
{
  "command": "zwill survey show",
  "status": "ok",
  "data": {
    "survey": {
      "name": "hiring_study",
      "status": "draft",
      "raw_files": 2,
      "questions": 2,
      "respondents": 2,
      "answers": 4,
      "has_context": true,
      "open_quarantine_issues": 0
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

## 13. Commit

```bash
zwill commit --survey hiring_study
```

Returns:

```json
{
  "command": "zwill commit",
  "status": "ok",
  "data": {
    "survey": "hiring_study",
    "respondent_count": 2,
    "question_count": 2,
    "answer_count": 4,
    "truth_marginal_count": 2,
    "committed_paths": {
      "respondents": ".zwill/projects/default/surveys/hiring_study/committed/respondents.json",
      "truth_marginals": ".zwill/projects/default/surveys/hiring_study/committed/truth_marginals.json",
      "context": ".zwill/projects/default/surveys/hiring_study/committed/context.md"
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

Commit should fail if open quarantine issues exist:

```json
{
  "command": "zwill commit",
  "status": "error",
  "data": {},
  "warnings": [],
  "errors": [
    {
      "code": "gate_blocked",
      "message": "Survey has open quarantine issues.",
      "context": {
        "open_quarantine_issues": 1
      },
      "hint": "Run `zwill quarantine list --survey hiring_study`."
    }
  ],
  "next_steps": [
    "zwill quarantine list --survey hiring_study"
  ]
}
```

## 14. Quarantine

```bash
zwill quarantine list --survey hiring_study
```

Returns:

```json
{
  "command": "zwill quarantine list",
  "status": "ok",
  "data": {
    "issues": [
      {
        "issue_id": "q00001",
        "status": "open",
        "code": "invalid_answer_option",
        "line": 4,
        "question": "remote_work",
        "answer": "every_day",
        "valid_options": ["0", "1-2", "3-4", "5"]
      }
    ],
    "issue_count": 1
  },
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

Resolve an issue:

```bash
zwill quarantine resolve --survey hiring_study --issue-id q00001 --action exclude --note "Bad source row."
```

Returns:

```json
{
  "command": "zwill quarantine resolve",
  "status": "ok",
  "data": {
    "issue": {
      "issue_id": "q00001",
      "status": "resolved",
      "resolution": {
        "action": "exclude",
        "note": "Bad source row."
      }
    }
  },
  "warnings": [],
  "errors": [],
  "next_steps": [
    "zwill commit --survey hiring_study"
  ]
}
```

## 15. State Files

Draft state:

```text
.zwill/
  config.json
  HEAD
  projects/
    default/
      project.json
      surveys.json
      surveys/hiring_study/
        raw/
        context.md
        raw_files.json
        questions.jsonl
        respondents.jsonl
        answers.jsonl
        agent_material.jsonl
        assertions.jsonl
        ingest_log.jsonl
        quarantine.jsonl
```

Committed state:

```text
.zwill/projects/default/surveys/hiring_study/committed/
  context.md
  respondents.json
  truth_marginals.json
```

Project generated-study state:

```text
.zwill/projects/default/
  agent_studies/
    manifest.json
    answers.jsonl
    <job_id>/
      import.json
      raw/
        <edsl_results_file>
  practitioner_reports/
  workflows/
```

## 16. Design Decisions

- Covariates are questions with `role: "covariate"`.
- Weights are respondent-level fields, never answer-level fields.
- Agent material is respondent-level construction material, not survey truth data.
- Agent material is opt-in for EDSL AgentList and digital twin exports.
- Single-object commands use keyword arguments.
- Bulk imports use JSONL.
- `question_options` are canonical answer labels, not raw source codes.
- Coded survey values must be expanded through available codebooks before import or export, even when this adds processing time.
- Answer rows should store the same expanded labels used in `question_options`.
- Original source codes should be preserved as provenance metadata, such as in `source.note`, raw files, or codebook-derived metadata.
- If a source code cannot be mapped to a label, the importer should quarantine the row or fail clearly rather than silently using the raw code as a label.
- `option_labels` are display/provenance metadata for canonical options, not a substitute for codebook expansion.
- Imports should validate and quarantine bad rows immediately.
- Commit should be deterministic and should not mutate raw files or draft input files.
