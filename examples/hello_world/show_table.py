#!/usr/bin/env python3
"""Render the hello_world fixture as the proposed `zwill table` output."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table


FIXTURE_DIR = Path(__file__).resolve().parent


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def build_answer_table(questions: list[dict], answers: list[dict]) -> Table:
    question_names = [question["question_name"] for question in questions]
    respondent_ids = sorted({answer["respondent_id"] for answer in answers})
    answers_by_respondent = {
        respondent_id: {question_name: "" for question_name in question_names}
        for respondent_id in respondent_ids
    }

    for answer in answers:
        value = answer.get("answer")
        if value is None:
            value = f"missing:{answer.get('missing_code', 'unknown')}"
        answers_by_respondent[answer["respondent_id"]][answer["question"]] = value

    table = Table(title="hello_world answers")
    table.add_column("respondent_id", style="bold")
    for question_name in question_names:
        table.add_column(question_name)

    for respondent_id in respondent_ids:
        row = [respondent_id]
        row.extend(answers_by_respondent[respondent_id][name] for name in question_names)
        table.add_row(*row)

    return table


def main() -> None:
    questions = read_jsonl(FIXTURE_DIR / "questions.jsonl")
    answers = read_jsonl(FIXTURE_DIR / "answers.jsonl")
    Console().print(build_answer_table(questions, answers))


if __name__ == "__main__":
    main()
