#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


SURVEY_NAME = "pew_w154_diff1"
SURVEY_CONTEXT = """# Pew W154 DIFF1 Source Context

This survey uses Pew Research Center American Trends Panel Wave 154 data, fielded in September 2024.

The imported battery is `DIFF1`, which asks:

> In general, how do you think men and women compare when it comes to each of the following?

The five imported items cover hobbies and personal interests, physical abilities, approach to parenting, expression of feelings, and workplace strengths.

Responses are:

- Men and women are basically similar
- Men and women are basically different
"""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def observed_answers_text(rows: list[dict[str, Any]]) -> str:
    blocks = []
    for row in rows:
        blocks.append(
            "\n".join(
                [
                    f"Question: {row['question_name']}",
                    f"Text: {row['question_text']}",
                    "Options: " + "; ".join(row["question_options"]),
                    f"Respondent answered: {row['answer']}",
                ]
            )
        )
    return "\n\n".join(blocks)


def make_survey(template_job: dict[str, Any]) -> dict[str, Any]:
    return template_job["survey"]


def make_models(template_job: dict[str, Any], model: str) -> list[dict[str, Any]]:
    if template_job.get("models"):
        model_spec = dict(template_job["models"][0])
    else:
        model_spec = {
            "model": "gpt-5.5",
            "parameters": {
                "temperature": 0.5,
                "max_tokens": 1000,
                "top_p": 1,
                "frequency_penalty": 0,
                "presence_penalty": 0,
                "logprobs": False,
                "top_logprobs": 3,
                "reasoning_effort": None,
            },
            "inference_service": "openai",
            "edsl_version": "1.0.8.dev1",
            "edsl_class_name": "LanguageModel",
        }
    if ":" in model:
        service, model_name = model.split(":", 1)
    else:
        service, model_name = "openai", model
    model_spec["inference_service"] = service
    model_spec["model"] = model_name
    return [model_spec]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--imports-dir", type=Path, default=Path("workdir/imports"))
    parser.add_argument("--template-job", type=Path, required=True)
    parser.add_argument("--out-job", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", default="openai:gpt-5.5")
    parser.add_argument("--max-examples", type=int, default=200)
    args = parser.parse_args()

    template_job = json.loads(args.template_job.read_text())
    questions = {
        row["question_name"]: row
        for row in read_jsonl(args.imports_dir / "questions.jsonl")
    }
    answers_by_respondent: dict[str, dict[str, str]] = defaultdict(dict)
    for row in read_jsonl(args.imports_dir / "answers.jsonl"):
        if "answer" in row:
            answers_by_respondent[row["respondent_id"]][row["question"]] = row["answer"]

    eval_rows = read_jsonl(args.run_dir / "eval_prompts.jsonl")[: args.max_examples]
    scenarios = []
    for row in eval_rows:
        target_question = row["target_question"]
        respondent_id = row["respondent_id"]
        observed = []
        for question_name in sorted(questions):
            if question_name == target_question:
                continue
            answer = answers_by_respondent[respondent_id].get(question_name)
            if answer is None:
                raise ValueError(f"Missing {question_name} for {respondent_id}")
            q = questions[question_name]
            observed.append(
                {
                    "question_name": question_name,
                    "question_text": q["question_text"],
                    "question_options": q["question_options"],
                    "answer": answer,
                }
            )
        target = questions[target_question]
        options = target["question_options"]
        scenarios.append(
            {
                "survey_name": SURVEY_NAME,
                "survey_context": SURVEY_CONTEXT,
                "respondent_id": respondent_id,
                "heldout_question_name": target_question,
                "heldout_question_text": target["question_text"],
                "heldout_options": options,
                "heldout_option_keys": ["a", "b"],
                "heldout_options_text": "\n".join(
                    f"{key}: {option}" for key, option in zip(["a", "b"], options, strict=True)
                ),
                "actual_answer": row["actual"],
                "agent_material": [],
                "agent_material_text": "No non-survey agent material provided.",
                "twin_material": [],
                "twin_material_text": "No supplemental twin material supplied.",
                "observed_answers": observed,
                "observed_answers_text": observed_answers_text(observed),
                "tinker_eval_id": row["id"],
                "train_majority_baseline": row["train_majority_baseline"],
            }
        )

    job = {
        "survey": make_survey(template_job),
        "agents": [],
        "models": make_models(template_job, args.model),
        "scenarios": scenarios,
        "edsl_version": template_job.get("edsl_version", "1.0.8.dev1"),
        "edsl_class_name": "Jobs",
        "zwill": {
            "kind": "apples_to_apples_frontier_twin_job",
            "source_run_dir": str(args.run_dir),
            "scenario_count": len(scenarios),
            "model": args.model,
            "heldout_questions": sorted({row["target_question"] for row in eval_rows}),
        },
    }

    args.out_job.parent.mkdir(parents=True, exist_ok=True)
    args.out_job.write_text(json.dumps(job, indent=2) + "\n")
    manifest = {
        "kind": "apples_to_apples_frontier_twin_manifest",
        "run_dir": str(args.run_dir),
        "job_path": str(args.out_job),
        "model": args.model,
        "scenario_count": len(scenarios),
        "heldout_questions": sorted({row["target_question"] for row in eval_rows}),
        "respondent_question_cases": [
            {
                "id": row["id"],
                "respondent_id": row["respondent_id"],
                "target_question": row["target_question"],
                "actual": row["actual"],
            }
            for row in eval_rows
        ],
    }
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
