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


# Standard Pew American Trends Panel "F_" profile-variable codings. The public-use
# file stores these covariates as numeric codes; expanding them to labels here (a
# converter responsibility, mirroring how the survey items are expanded) makes the
# respondent profile usable as twin context. Each entry maps raw variable -> (a
# readable key, {code: label}). Refused/missing codes (99, "") are dropped.
PEW_ATP_COVARIATES: dict[str, tuple[str, dict[str, str]]] = {
    "F_GENDER": ("gender", {"1": "A man", "2": "A woman", "3": "In some other way"}),
    "F_AGECAT": ("age", {"1": "18-29", "2": "30-49", "3": "50-64", "4": "65+"}),
    "F_EDUCCAT2": (
        "education",
        {
            "1": "Less than high school",
            "2": "High school graduate",
            "3": "Some college, no degree",
            "4": "Associate's degree",
            "5": "Bachelor's degree",
            "6": "Postgraduate degree",
        },
    ),
    "F_RACETHNMOD": (
        "race_ethnicity",
        {"1": "White, non-Hispanic", "2": "Black, non-Hispanic", "3": "Hispanic", "4": "Other", "5": "Asian, non-Hispanic"},
    ),
    "F_PARTYSUMIDEO_FINAL": (
        "party_and_ideology",
        {
            "1": "Conservative Republican",
            "2": "Moderate/Liberal Republican",
            "3": "Conservative/Moderate Democrat",
            "4": "Liberal Democrat",
            "9": "Other / no lean",
        },
    ),
    "F_IDEO": (
        "ideology",
        {"1": "Very conservative", "2": "Conservative", "3": "Moderate", "4": "Liberal", "5": "Very liberal"},
    ),
    "F_INC_TIER2": ("income_tier", {"1": "Lower income", "2": "Middle income", "3": "Upper income"}),
    "F_CREGION": ("census_region", {"1": "Northeast", "2": "Midwest", "3": "South", "4": "West"}),
    "F_METRO": ("metro_status", {"1": "Metropolitan", "2": "Non-metropolitan"}),
    "F_INTFREQ": (
        "internet_use_frequency",
        {
            "1": "Almost constantly",
            "2": "Several times a day",
            "3": "About once a day",
            "4": "Several times a week",
            "5": "Less often",
        },
    ),
}


def _normalize_code(value: str) -> str:
    """Codes arrive as '1' or '1.0'; normalize to the integer string used as a key."""
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except ValueError:
        return text


def labeled_covariates(row: dict, covariates: list[str]) -> dict[str, str]:
    """Expand a respondent's raw covariate codes into a readable {key: label} profile.

    Only the standard ATP profile variables above are included; refused/missing and
    unrecognized codes are dropped so the profile shown to a twin is clean.
    """
    profile: dict[str, str] = {}
    for name in covariates:
        mapping = PEW_ATP_COVARIATES.get(name)
        if mapping is None:
            continue
        readable_key, code_labels = mapping
        label = code_labels.get(_normalize_code(row.get(name, "")))
        if label is not None:
            profile[readable_key] = label
    return profile


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
                    "metadata": labeled_covariates(row, covariates),
                    "source": {
                        "raw_id": "w154_diff1_respondents",
                        "note": "Weight and covariates (expanded to labels via the standard Pew ATP F_ codebook) from the normalized Pew W154 DIFF1 respondent file.",
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
