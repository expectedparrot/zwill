"""Import commands for the open-end coding pipeline (codebook + coding).

The pure builders and parsers live in ``open_ends.py``; this module wires them to
the survey on disk via the ``cli.py`` helpers.
"""

from __future__ import annotations

import argparse
from typing import Any

from .cli import *  # noqa: F403
from .open_ends import (
    UNCLASSIFIED_CODE,
    coded_question_and_answers,
    normalize_codebook,
    render_codebook_text,
)
from .probability import parse_probability_json


def open_coding_dir(sdir: "Path") -> "Path":  # noqa: F821
    return sdir / "open_coding"


def codebook_path(sdir: "Path", question_name: str) -> "Path":  # noqa: F821
    return open_coding_dir(sdir) / question_name / "codebook.json"


def _raw_answer(row: dict[str, Any]) -> Any:
    answer = row.get("answer", {}) or {}
    raw = next((value for value in answer.values() if value is not None), None) if isinstance(answer, dict) else answer
    parsed, _error = parse_probability_json(raw)
    return parsed


def cmd_open_codebook_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    zwill_meta = results.get("zwill", {}) or {}
    question_name = args.question or zwill_meta.get("source_question")
    if not question_name:
        raise ZwillError("invalid_input", "Pass --question (the free_text question these results were derived from).")

    data = results.get("data", []) or []
    if not data:
        raise ZwillError("invalid_input", "Codebook results contained no rows.")
    parsed = _raw_answer(data[0])
    codebook = normalize_codebook(parsed, n_themes=zwill_meta.get("n_themes"))

    warnings = []
    if len(data) > 1:
        warnings.append(
            f"Codebook results had {len(data)} rows (e.g. multiple models); used the first and ignored the rest. "
            "Derive the codebook with a single model to make it deterministic."
        )

    out_path = codebook_path(sdir, question_name)
    write_json(out_path, {"source_question": question_name, "themes": codebook, "imported_at": utc_now()})
    return envelope(
        "zwill open-coding codebook-import",
        "ok",
        {
            "source_question": question_name,
            "theme_count": len(codebook),
            "themes": codebook,
            "codebook_path": str(out_path),
        },
        warnings=warnings or None,
        next_steps=[
            f"zwill edsl-export --survey {args.survey} --target open-coding-job --heldout-question {question_name} --model <model> --path coding_job.json",
        ],
    )


def cmd_open_coding_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    zwill_meta = results.get("zwill", {}) or {}
    source_question = args.question or zwill_meta.get("source_question")
    if not source_question:
        raise ZwillError("invalid_input", "Pass --question (the source free_text question).")
    coded_question_name = args.coded_question_name or zwill_meta.get("coded_question_name") or f"{source_question}_coded"

    questions = read_jsonl(sdir / "questions.jsonl")
    question_by_name = {q["question_name"]: q for q in questions}
    if source_question not in question_by_name:
        raise ZwillError("not_found", f"Source question {source_question!r} is not in this survey.")
    if coded_question_name in question_by_name and not args.replace:
        raise ZwillError(
            "already_exists",
            f"Coded question {coded_question_name!r} already exists.",
            hint="Use --replace to overwrite it (and its coded answers).",
        )

    codebook = zwill_meta.get("codebook")
    if not codebook:
        cb_path = codebook_path(sdir, source_question)
        if not cb_path.exists():
            raise ZwillError("not_found", f"No codebook available for {source_question!r} (not in results or on disk).")
        codebook = read_json_or_gzip(cb_path)["themes"]

    question, answer_rows, distribution, meta = coded_question_and_answers(
        results,
        source_question=source_question,
        coded_question_name=coded_question_name,
        codebook=codebook,
        source_text=question_by_name[source_question]["question_text"],
        parse_answer=_raw_answer,
    )
    if not answer_rows:
        raise ZwillError("invalid_input", "No respondents were coded (results had no usable rows).")

    # replace any existing coded question + its answers, then append fresh
    remaining_questions = [q for q in questions if q["question_name"] != coded_question_name]
    rewrite_jsonl(sdir / "questions.jsonl", [*remaining_questions, question])
    remaining_answers = [a for a in read_jsonl(sdir / "answers.jsonl") if a.get("question") != coded_question_name]
    rewrite_jsonl(sdir / "answers.jsonl", [*remaining_answers, *answer_rows])

    total = sum(distribution.values())
    unclassified = distribution.get(UNCLASSIFIED_CODE, 0)
    warnings = []
    if total and unclassified / total > 0.2:
        warnings.append(f"{unclassified}/{total} answers were unclassified (>20%); the codebook may not fit the data well.")
    if meta.get("duplicate_respondents"):
        warnings.append(
            f"{meta['duplicate_respondents']} extra coding rows per respondent were ignored "
            f"(kept the first per respondent; {meta.get('disagreements', 0)} disagreed). "
            "Code with a single model to avoid ambiguity."
        )
    return envelope(
        "zwill open-coding import",
        "ok",
        {
            "source_question": source_question,
            "coded_question": coded_question_name,
            "coded_count": len(answer_rows),
            "options": question["question_options"],
            "distribution": distribution,
            "codebook_preview": render_codebook_text(codebook),
        },
        warnings=warnings or None,
        next_steps=[
            f"zwill edsl-export --survey {args.survey} --target twin-probability-job --heldout-question {coded_question_name} --model <model> --path twin_job.json",
        ],
    )
