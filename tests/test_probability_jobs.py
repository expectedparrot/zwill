from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from zwill.errors import ZwillError
from zwill.probability_jobs import ProbabilityJobBuilderDeps, build_edsl_probability_job_dict, probability_question_text


class FakeQuestionFreeText:
    def __init__(self, question_name: str, question_text: str) -> None:
        self.question_name = question_name
        self.question_text = question_text


class FakeScenario(dict):
    pass


class FakeScenarioList(list):
    pass


class FakeSurvey:
    def __init__(self, questions: list | None = None) -> None:
        self.questions = questions or []


class FakeModel:
    def __init__(self, model_name: str, service_name: str | None = None, **parameters: Any) -> None:
        self.model_name = model_name
        self.service_name = service_name
        self.parameters = parameters


class FakeModelList(list):
    pass


class FakeJobs:
    def __init__(self, survey: FakeSurvey, scenarios: FakeScenarioList, models: FakeModelList) -> None:
        self.survey = survey
        self.scenarios = scenarios
        self.models = models

    def to_dict(self) -> dict[str, Any]:
        return {
            "edsl_class_name": "Jobs",
            "survey": {
                "questions": [
                    {
                        "question_name": question.question_name,
                        "question_text": question.question_text,
                        "question_type": "free_text",
                    }
                    for question in self.survey.questions
                ]
            },
            "scenarios": list(self.scenarios),
            "models": [
                {
                    "model": model.model_name,
                    "inference_service": model.service_name,
                    "parameters": model.parameters,
                }
                for model in self.models
            ],
        }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))


def option_key(index: int) -> str:
    return chr(ord("a") + index)


def deps_for(tmp_path: Path) -> ProbabilityJobBuilderDeps:
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    (survey_dir / "context.md").write_text("Survey context.")

    def selected_question_names(args: argparse.Namespace, questions: list[dict[str, Any]]) -> list[str]:
        if args.question:
            return args.question
        return [question["question_name"] for question in questions]

    def parse_model_specs(args: argparse.Namespace) -> list[tuple[str, str | None]]:
        specs = []
        for model in args.model or ["gpt-5.5"]:
            if ":" in model:
                service, name = model.split(":", 1)
                specs.append((name, service))
            else:
                specs.append((model, args.service_name))
        return specs

    def parse_model_params(args: argparse.Namespace) -> dict[tuple[str | None, str | None], dict[str, Any]]:
        return {(None, None): {"temperature": 0}, ("openai", "gpt-5.5"): {"max_tokens": 1000}}

    def model_kwargs_for(model_name: str, service_name: str | None, params: dict[tuple[str | None, str | None], dict[str, Any]]) -> dict[str, Any]:
        kwargs = {}
        kwargs.update(params.get((None, None), {}))
        kwargs.update(params.get((service_name, model_name), {}))
        return kwargs

    return ProbabilityJobBuilderDeps(
        require_survey=lambda _survey: survey_dir,
        selected_question_names=selected_question_names,
        context_path=lambda sdir: sdir / "context.md",
        load_edsl_job_classes=lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
        option_key=option_key,
        parse_model_params=parse_model_params,
        parse_model_specs=parse_model_specs,
        model_kwargs_for=model_kwargs_for,
    )


def args(**overrides: Any) -> argparse.Namespace:
    values = {
        "question": None,
        "questions": None,
        "exclude_question": None,
        "model": ["openai:gpt-5.5"],
        "models": None,
        "service_name": None,
        "model_param": None,
        "job_question_name": "response_probabilities",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_probability_question_text_declares_json_shape() -> None:
    text = probability_question_text()

    assert '"probabilities": [0.17, 0.83]' in text
    assert "Response options:" in text


def test_probability_job_builder_preserves_option_order_and_model_params(tmp_path: Path) -> None:
    deps = deps_for(tmp_path)
    write_jsonl(
        tmp_path / "survey" / "questions.jsonl",
        [
            {
                "question_name": "q1",
                "question_type": "multiple_choice",
                "question_text": "Pick one",
                "question_options": ["yes", "no"],
            }
        ],
    )

    job = build_edsl_probability_job_dict("demo", args(question=["q1"]), deps)

    assert job["edsl_class_name"] == "Jobs"
    assert job["scenarios"] == [
        {
            "survey_name": "demo",
            "survey_context": "Survey context.",
            "source_question_name": "q1",
            "source_question_text": "Pick one",
            "options_text": "a: yes\nb: no",
            "option_keys": ["a", "b"],
            "option_labels": ["yes", "no"],
        }
    ]
    assert job["models"][0]["parameters"] == {"temperature": 0, "max_tokens": 1000}
    assert "probability_job_id" in job["zwill"]


def test_probability_job_builder_rejects_unsupported_question_types(tmp_path: Path) -> None:
    deps = deps_for(tmp_path)
    write_jsonl(
        tmp_path / "survey" / "questions.jsonl",
        [
            {
                "question_name": "q1",
                "question_type": "free_text",
                "question_text": "Why?",
                "question_options": [],
            }
        ],
    )

    with pytest.raises(ZwillError) as excinfo:
        build_edsl_probability_job_dict("demo", args(question=["q1"]), deps)

    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.context["unsupported_questions"] == ["q1"]
