from __future__ import annotations

import json
import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "examples" / "llm_survey_priors" / "ingest_normalized.py"
SPEC = importlib.util.spec_from_file_location("ingest_normalized", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
ingest_normalized = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ingest_normalized)
convert_pair = ingest_normalized.convert_pair


def test_convert_pair_expands_codes_and_accepts_exact_labels(tmp_path: Path) -> None:
    metadata_path = tmp_path / "DEMO_metadata.json"
    respondents_path = tmp_path / "DEMO_respondents.csv"
    metadata_path.write_text(
        json.dumps(
            {
                "context": "Demo source",
                "respondent_id": "respondent_id",
                "weight": "weight",
                "items": {
                    "q1": {
                        "question_stem": "Choose",
                        "item_text": "one option",
                        "option_codes": [1, 2],
                        "option_labels": ["Yes", "No"],
                        "source_variable": "q1",
                    },
                    "q2": {
                        "question_stem": "Choose",
                        "item_text": "another option",
                        "option_codes": [1, 2],
                        "option_labels": ["Often", "Never"],
                        "source_variable": "q2",
                    },
                },
            }
        )
    )
    respondents_path.write_text(
        "respondent_id,weight,q1,q2\n"
        "r1,1,1,Often\n"
        "r2,2,No,2\n"
    )

    summary = convert_pair(metadata_path, respondents_path, tmp_path / "imports")

    assert summary["issue_count"] == 0
    answers = [
        json.loads(line)
        for line in (tmp_path / "imports" / "demo" / "answers.jsonl").read_text().splitlines()
    ]
    assert {row["answer"] for row in answers} == {"Yes", "No", "Often", "Never"}


def test_convert_pair_records_unmapped_codes_without_using_them_as_labels(tmp_path: Path) -> None:
    metadata_path = tmp_path / "DEMO_metadata.json"
    respondents_path = tmp_path / "DEMO_respondents.csv"
    metadata_path.write_text(
        json.dumps(
            {
                "respondent_id": "respondent_id",
                "items": {
                    "q1": {
                        "question_stem": "Choose",
                        "item_text": "one option",
                        "option_codes": [1, 2],
                        "option_labels": ["Yes", "No"],
                        "source_variable": "q1",
                    }
                },
            }
        )
    )
    respondents_path.write_text("respondent_id,q1\nr1,3\n")

    summary = convert_pair(metadata_path, respondents_path, tmp_path / "imports")

    assert summary["issue_count"] == 1
    assert summary["issue_examples"][0]["code"] == "unmapped_answer_code"
    answers_path = tmp_path / "imports" / "demo" / "answers.jsonl"
    assert answers_path.read_text() == ""
