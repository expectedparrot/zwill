#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DEFAULT_SOURCE_DIR = Path(
    "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/"
    "papers/microdata_twins/data/computed_objects/normalized"
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    metadata_path = args.source_dir / "W154_DIFF1_metadata.json"
    respondents_path = args.source_dir / "W154_DIFF1_respondents.csv"
    metadata = json.loads(metadata_path.read_text())
    option_codes = [str(code) for code in metadata["option_codes"]]
    option_labels = list(metadata["option_labels"])
    option_by_code = {
        str(code): label
        for code, label in zip(metadata["option_codes"], option_labels, strict=True)
    }

    questions = []
    for item_key, item in metadata["items"].items():
        question_name = f"diff1_{item_key}"
        questions.append(
            {
                "question_name": question_name,
                "question_type": "multiple_choice",
                "question_text": f"{item['question_stem']} {item['item_text']}",
                "question_options": option_labels,
                "option_labels": {label: label for label in option_labels},
                "role": "survey_item",
                "source": {
                    "raw_id": "w154_diff1_metadata",
                    "note": f"Mapped from {item['variable']} in normalized Pew W154 DIFF1 metadata. Source codes {option_by_code}.",
                },
            }
        )

    respondents = []
    answers = []
    covariates = metadata["covariates"]
    item_columns = {f"item_{key}": f"diff1_{key}" for key in metadata["items"]}

    with respondents_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            respondent_id = f"pew_w154_{row['respondent_id']}"
            respondents.append(
                {
                    "respondent_id": respondent_id,
                    "weight": float(row["weight"]),
                    "metadata": {name: row[name] for name in covariates},
                    "source": {
                        "raw_id": "w154_diff1_respondents",
                        "note": "Weight and covariates from normalized Pew W154 DIFF1 respondent file.",
                    },
                }
            )
            for column, question_name in item_columns.items():
                value = row[column]
                if value in option_codes:
                    answers.append(
                        {
                            "respondent_id": respondent_id,
                            "question": question_name,
                            "answer": option_by_code[value],
                        }
                    )
                else:
                    answers.append(
                        {
                            "respondent_id": respondent_id,
                            "question": question_name,
                            "missing_code": "missing_or_refused",
                        }
                    )

    write_jsonl(args.out_dir / "questions.jsonl", questions)
    write_jsonl(args.out_dir / "respondents.jsonl", respondents)
    write_jsonl(args.out_dir / "answers.jsonl", answers)
    (args.out_dir / "summary.json").write_text(
        json.dumps(
            {
                "source_metadata": str(metadata_path),
                "source_respondents": str(respondents_path),
                "questions": len(questions),
                "respondents": len(respondents),
                "answers": len(answers),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {len(questions)} questions, {len(respondents)} respondents, {len(answers)} answers to {args.out_dir}")


if __name__ == "__main__":
    main()
