"""Open-ended (free-text) answer coding: turn a free_text question into a coded
multiple-choice question the existing twin flow can validate.

Two LLM steps, each an ordinary EDSL export -> run -> import cycle:

1. **Codebook derivation** — a single scenario shows a sample of the free-text
   answers and asks the model for a small, mutually-exclusive set of themes
   (a codebook). Stored as ``open_coding/<question>/codebook.json``.
2. **Coding** — one scenario per respondent classifies that respondent's actual
   answer into exactly one codebook theme. The import step then writes a new
   ``multiple_choice`` question (options = theme codes) plus the coded answers,
   so ``edsl-export --target twin-probability-job`` validates it unchanged.

This module holds the pure pieces (prompts, parsing, job builders) with no
dependency on ``cli.py``; the import commands live in ``open_ends_commands.py``.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from .errors import ZwillError
from .jsonlio import read_jsonl
from .twin import digital_twin_job_id_from_job
from .twin_jobs import DigitalTwinJobBuilderDeps, slug_id

# Every coding run reserves this bucket for answers that fit no theme, so the
# coded question stays exhaustive (and a large bucket signals a weak codebook).
UNCLASSIFIED_CODE = "unclassified"
UNCLASSIFIED_LABEL = "Unclassified / other"
DEFAULT_N_THEMES = 8
DEFAULT_SAMPLE_ANSWERS = 150


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
def codebook_question_text() -> str:
    return """You are a survey methodologist building a coding scheme (codebook) for an open-ended question.

Survey name:
{{ survey_name }}

The open-ended question respondents answered:
{{ source_question_text }}

Here is a random sample of the free-text answers people gave:
{{ sample_answers_text }}

Design a codebook of at most {{ n_themes }} themes that a human coder could use to
classify EVERY answer into exactly one theme. The themes must be:
- mutually exclusive (an answer belongs to one theme, not several),
- collectively exhaustive of the substantive content you see,
- about WHAT the respondent expressed, not surface features like length or language.

For each theme give a short snake_case code, a human-readable label, and a one-sentence
description a coder would use to decide membership.

Return ONLY JSON in exactly this form:
{"themes": [{"code": "<snake_case>", "label": "<short label>", "description": "<one sentence>"}], "notes": "<one sentence overview>"}
"""


def coding_question_text() -> str:
    return """You are a survey coder assigning one open-ended answer to exactly one theme from a fixed codebook.

The open-ended question that was asked:
{{ source_question_text }}

The codebook (choose exactly one code):
{{ codebook_text }}

If the answer genuinely fits none of the themes, use the code "{{ unclassified_code }}".

The respondent's actual answer:
{{ answer_text }}

Return ONLY JSON in exactly this form:
{"code": "<one code from the codebook>", "notes": "<short reason>"}
"""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def normalize_codebook(parsed: Any, *, n_themes: int | None = None) -> list[dict[str, str]]:
    """Coerce a model's codebook JSON into a clean list of {code,label,description}.

    Accepts ``{"themes": [...]}``, ``{"codebook": [...]}``, or a bare list. Each
    theme may be a dict or a bare string. Codes are slugified and de-duplicated;
    the reserved unclassified code is never emitted here (it is appended only if
    coding actually uses it).
    """
    if isinstance(parsed, dict):
        raw = parsed.get("themes") or parsed.get("codebook") or parsed.get("codes")
    else:
        raw = parsed
    if not isinstance(raw, list) or not raw:
        raise ZwillError(
            "invalid_input",
            "Could not find a list of themes in the codebook results.",
            context={"parsed_type": type(parsed).__name__},
            hint="The codebook model must return JSON like {\"themes\": [{\"code\", \"label\", \"description\"}]}.",
        )
    themes: list[dict[str, str]] = []
    seen: set[str] = {UNCLASSIFIED_CODE}
    for item in raw:
        if isinstance(item, str):
            label = item.strip()
            code_hint = item
            description = ""
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or item.get("theme") or item.get("code") or "").strip()
            code_hint = str(item.get("code") or label)
            description = str(item.get("description") or "").strip()
        else:
            continue
        if not label:
            continue
        code = slug_id(code_hint).lower()[:40] or slug_id(label).lower()[:40]
        if not code or code in seen:
            # keep distinct codes even when labels collide
            suffix = 2
            base = code or "theme"
            while f"{base}_{suffix}" in seen:
                suffix += 1
            code = f"{base}_{suffix}"
        seen.add(code)
        themes.append({"code": code, "label": label, "description": description})
    if not themes:
        raise ZwillError("invalid_input", "The codebook results contained no usable themes.")
    if n_themes is not None and len(themes) > n_themes:
        themes = themes[:n_themes]
    return themes


def render_codebook_text(codebook: list[dict[str, str]]) -> str:
    lines = []
    for theme in codebook:
        desc = f" — {theme['description']}" if theme.get("description") else ""
        lines.append(f"- {theme['code']}: {theme['label']}{desc}")
    return "\n".join(lines)


def parse_coded_answer(parsed: Any, valid_codes: set[str]) -> str:
    """Map a coding model's response to a valid code, falling back to unclassified.

    Accepts ``{"code": ...}``, ``{"theme"/"label": ...}``, or a bare string, and
    matches case-insensitively against the codebook codes (exact code wins).
    """
    candidate = None
    if isinstance(parsed, dict):
        candidate = parsed.get("code") or parsed.get("theme") or parsed.get("label") or parsed.get("answer")
    elif isinstance(parsed, str):
        candidate = parsed
    if candidate is None:
        return UNCLASSIFIED_CODE
    text = str(candidate).strip()
    if text in valid_codes:
        return text
    lowered = text.lower()
    by_lower = {code.lower(): code for code in valid_codes}
    if lowered in by_lower:
        return by_lower[lowered]
    # last resort: a slugified match (handles "Theme: worried" style answers)
    slug = slug_id(text).lower()
    if slug in by_lower:
        return by_lower[slug]
    return UNCLASSIFIED_CODE


# --------------------------------------------------------------------------- #
# Job builders
# --------------------------------------------------------------------------- #
def _free_text_question(question_by_name: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    question = question_by_name.get(name)
    if question is None:
        raise ZwillError("not_found", f"Question {name!r} is not in this survey.")
    if question.get("question_type") != "free_text":
        raise ZwillError(
            "invalid_input",
            f"Open-end coding requires a free_text question; {name!r} is {question.get('question_type')!r}.",
            hint="Only genuinely open-ended (free_text) questions need coding.",
        )
    return question


def _nonnull_answers(sdir: Any, question_name: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    for row in read_jsonl(sdir / "answers.jsonl"):
        if row.get("question") == question_name and str(row.get("answer") or "").strip():
            answers[row["respondent_id"]] = str(row["answer"]).strip()
    return answers


def build_open_codebook_job_dict_impl(survey_name: str, args: Any, deps: DigitalTwinJobBuilderDeps) -> dict[str, Any]:
    sdir = deps.require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    question_by_name = {q["question_name"]: q for q in questions}
    question_name = args.heldout_question[0] if getattr(args, "heldout_question", None) else getattr(args, "question", None)
    if isinstance(question_name, list):
        question_name = question_name[0] if question_name else None
    if not question_name:
        raise ZwillError("invalid_input", "Pass --heldout-question <free_text question> to derive a codebook.")
    question = _free_text_question(question_by_name, question_name)

    answers = _nonnull_answers(sdir, question_name)
    if not answers:
        raise ZwillError("invalid_input", f"No non-empty answers found for {question_name!r}.")
    sample_size = int(getattr(args, "sample_answers", None) or DEFAULT_SAMPLE_ANSWERS)
    n_themes = int(getattr(args, "n_themes", None) or DEFAULT_N_THEMES)
    values = list(answers.values())
    rng = random.Random(getattr(args, "seed", None) or 0)
    if len(values) > sample_size:
        values = rng.sample(values, sample_size)
    sample_answers_text = "\n".join(f"- {value}" for value in values)

    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = deps.load_edsl_job_classes()
    codebook_question = QuestionFreeText(question_name="codebook", question_text=codebook_question_text())
    scenario = Scenario(
        {
            "survey_name": survey_name,
            "source_question_name": question_name,
            "source_question_text": question["question_text"],
            "sample_answers_text": sample_answers_text,
            "n_themes": n_themes,
        }
    )
    model_params = deps.parse_model_params(args)
    job = Jobs(
        survey=Survey(questions=[codebook_question]),
        scenarios=ScenarioList([scenario]),
        models=ModelList(
            [
                Model(model_name=model_name, service_name=service_name, **deps.model_kwargs_for(model_name, service_name, model_params))
                for model_name, service_name in deps.parse_model_specs(args)
            ]
        ),
    )
    data = job.to_dict()
    data["zwill"] = {
        "open_codebook_job_id": digital_twin_job_id_from_job(data),
        "kind": "open_codebook",
        "source_question": question_name,
        "n_themes": n_themes,
        "sample_size": len(values),
        "population_size": len(answers),
    }
    return data


def build_open_coding_job_dict_impl(survey_name: str, args: Any, deps: DigitalTwinJobBuilderDeps) -> dict[str, Any]:
    from .open_ends_commands import codebook_path  # local import: cli-dependent path helper

    sdir = deps.require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    question_by_name = {q["question_name"]: q for q in questions}
    question_name = args.heldout_question[0] if getattr(args, "heldout_question", None) else getattr(args, "question", None)
    if isinstance(question_name, list):
        question_name = question_name[0] if question_name else None
    if not question_name:
        raise ZwillError("invalid_input", "Pass --heldout-question <free_text question> for the coding job.")
    question = _free_text_question(question_by_name, question_name)

    cb_path = codebook_path(sdir, question_name)
    if not cb_path.exists():
        raise ZwillError(
            "not_found",
            f"No codebook found for {question_name!r}.",
            hint="Run the codebook step first: edsl-export --target open-codebook-job, edsl-run, then open-coding codebook-import.",
        )
    import json as _json

    codebook = _json.loads(cb_path.read_text())["themes"]
    codebook_text = render_codebook_text(codebook)

    answers = _nonnull_answers(sdir, question_name)
    respondent_ids = list(answers)
    sample = getattr(args, "sample_respondents", None)
    if sample and len(respondent_ids) > sample:
        rng = random.Random(getattr(args, "seed", None) or 0)
        respondent_ids = rng.sample(respondent_ids, sample)

    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = deps.load_edsl_job_classes()
    coding_question = QuestionFreeText(question_name="theme_code", question_text=coding_question_text())
    scenarios = [
        Scenario(
            {
                "survey_name": survey_name,
                "respondent_id": respondent_id,
                "source_question_name": question_name,
                "source_question_text": question["question_text"],
                "codebook_text": codebook_text,
                "unclassified_code": UNCLASSIFIED_CODE,
                "answer_text": answers[respondent_id],
            }
        )
        for respondent_id in respondent_ids
    ]
    if not scenarios:
        raise ZwillError("invalid_input", f"No answers to code for {question_name!r}.")

    model_params = deps.parse_model_params(args)
    job = Jobs(
        survey=Survey(questions=[coding_question]),
        scenarios=ScenarioList(scenarios),
        models=ModelList(
            [
                Model(model_name=model_name, service_name=service_name, **deps.model_kwargs_for(model_name, service_name, model_params))
                for model_name, service_name in deps.parse_model_specs(args)
            ]
        ),
    )
    data = job.to_dict()
    data["zwill"] = {
        "open_coding_job_id": digital_twin_job_id_from_job(data),
        "kind": "open_coding",
        "source_question": question_name,
        "codebook": codebook,
        "coded_question_name": getattr(args, "coded_question_name", None) or f"{question_name}_coded",
        "scenario_count": len(scenarios),
    }
    return data


def coded_question_and_answers(
    results: dict[str, Any],
    *,
    source_question: str,
    coded_question_name: str,
    codebook: list[dict[str, str]],
    source_text: str,
    parse_answer: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    """Turn coding Results into a coded multiple_choice question + answer rows.

    ``parse_answer`` extracts the raw model answer string from a result row and
    returns a parsed JSON object (injected so this stays free of cli.py).
    Returns (question_dict, answer_rows, code_distribution).
    """
    valid_codes = {theme["code"] for theme in codebook}
    label_by_code = {theme["code"]: theme["label"] for theme in codebook}
    label_by_code[UNCLASSIFIED_CODE] = UNCLASSIFIED_LABEL

    answer_rows: list[dict[str, Any]] = []
    distribution: Counter[str] = Counter()
    for row in results.get("data", []):
        scenario = row.get("scenario", {}) or {}
        respondent_id = scenario.get("respondent_id")
        if respondent_id is None:
            continue
        parsed = parse_answer(row)
        code = parse_coded_answer(parsed, valid_codes)
        distribution[code] += 1
        answer_rows.append({"respondent_id": respondent_id, "question": coded_question_name, "answer": code})

    # options: codebook order, then unclassified only if it was actually used
    options = [theme["code"] for theme in codebook]
    if distribution.get(UNCLASSIFIED_CODE):
        options.append(UNCLASSIFIED_CODE)
    question = {
        "question_name": coded_question_name,
        "question_type": "multiple_choice",
        "question_text": source_text,
        "question_options": options,
        "option_labels": {code: label_by_code.get(code, code) for code in options},
        "role": "survey_item",
        "source": {"raw_id": "open_coding", "note": f"coded from free_text question {source_question}"},
    }
    return question, answer_rows, dict(distribution)
