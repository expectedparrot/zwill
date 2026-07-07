from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import ZwillError
from .jsonlio import read_jsonl
from .probability import probability_job_id_from_job


@dataclass(frozen=True)
class ProbabilityJobBuilderDeps:
    require_survey: Callable[[str], Path]
    selected_question_names: Callable[[Any, list[dict[str, Any]]], list[str]]
    context_path: Callable[[Path], Path]
    load_edsl_job_classes: Callable[[], tuple[Any, Any, Any, Any, Any, Any, Any]]
    option_key: Callable[[int], str]
    parse_model_params: Callable[[Any], dict[tuple[str | None, str | None], dict[str, Any]]]
    parse_model_specs: Callable[[Any], list[tuple[str, str | None]]]
    model_kwargs_for: Callable[[str, str | None, dict[tuple[str | None, str | None], dict[str, Any]]], dict[str, Any]]


def probability_question_text() -> str:
    return """You are estimating response probabilities for a multiple-choice survey question.

Survey name:
{{ survey_name }}

Survey context:
{{ survey_context }}

Source question name:
{{ source_question_name }}

Source question text:
{{ source_question_text }}

Response options:
{{ options_text }}

Return only valid JSON. Do not include markdown fences, prose, or comments.

The JSON must have exactly this shape:
{
  "probabilities": [0.17, 0.83],
  "notes": "Brief explanation of the probability estimates."
}

The probabilities array must contain one number for each listed option, in the same order as the options. Each probability must be between 0 and 1. The probabilities should sum to 1."""


def build_edsl_probability_job_dict(survey_name: str, args: Any, deps: ProbabilityJobBuilderDeps) -> dict[str, Any]:
    sdir = deps.require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    selected = deps.selected_question_names(args, questions)
    selected_set = set(selected)
    selected_questions = [question for question in questions if question["question_name"] in selected_set]
    if not selected_questions:
        raise ZwillError("invalid_input", "No questions selected for EDSL probability job export.")

    unsupported = [
        question["question_name"]
        for question in selected_questions
        if question.get("question_type") != "multiple_choice" or not question.get("question_options")
    ]
    if unsupported:
        raise ZwillError(
            "invalid_input",
            "Probability job export only supports multiple-choice questions with expanded options.",
            context={"unsupported_questions": unsupported},
            hint="Import codebooks first so question_options contain human-readable labels.",
        )

    context_file = deps.context_path(sdir)
    context_text = context_file.read_text().strip() if context_file.exists() else ""

    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = deps.load_edsl_job_classes()

    probability_question = QuestionFreeText(
        question_name=args.job_question_name,
        question_text=probability_question_text(),
    )
    scenarios = []
    for question in selected_questions:
        option_keys = [deps.option_key(index) for index, _ in enumerate(question["question_options"])]
        option_lines = [
            f"{key}: {option}"
            for key, option in zip(option_keys, question["question_options"])
        ]
        scenarios.append(
            Scenario(
                {
                    "survey_name": survey_name,
                    "survey_context": context_text,
                    "source_question_name": question["question_name"],
                    "source_question_text": question["question_text"],
                    "options_text": "\n".join(option_lines),
                    "option_keys": option_keys,
                    "option_labels": question["question_options"],
                }
            )
        )

    model_params = deps.parse_model_params(args)
    job = Jobs(
        survey=Survey(questions=[probability_question]),
        scenarios=ScenarioList(scenarios),
        models=ModelList(
            [
                Model(
                    model_name=model_name,
                    service_name=service_name,
                    **deps.model_kwargs_for(model_name, service_name, model_params),
                )
                for model_name, service_name in deps.parse_model_specs(args)
            ]
        ),
    )
    data = job.to_dict()
    data["zwill"] = {"probability_job_id": probability_job_id_from_job(data)}
    return data
