#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_ROOT = Path(
    "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/"
    "papers/microdata_twins/data/computed_objects/normalized"
)


def safe_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return value or "unnamed"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def code_variants(value: Any) -> set[str]:
    values = {str(value).strip()}
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return {item for item in values if item}
    if math.isfinite(numeric):
        values.add(str(numeric))
        if numeric.is_integer():
            values.add(str(int(numeric)))
    return {item for item in values if item}


def codebook_map(codes: list[Any], labels: list[Any]) -> dict[str, str]:
    if len(codes) != len(labels):
        raise ValueError(f"codebook length mismatch: {len(codes)} codes, {len(labels)} labels")
    mapped: dict[str, str] = {}
    for code, label in zip(codes, labels, strict=True):
        label_text = str(label)
        for variant in code_variants(code):
            mapped[variant] = label_text
        mapped[label_text] = label_text
    return mapped


def metadata_pairs(source_root: Path) -> list[tuple[Path, Path]]:
    pairs = []
    metadata_paths = sorted({*source_root.rglob("*_metadata.json"), *source_root.rglob("metadata.json")})
    for metadata_path in metadata_paths:
        if ".venv" in metadata_path.parts:
            continue
        candidates = []
        if metadata_path.name.endswith("_metadata.json"):
            candidates.append(metadata_path.with_name(metadata_path.name[: -len("_metadata.json")] + "_respondents.csv"))
        candidates.append(metadata_path.with_name("respondents.csv"))
        respondents_path = next((path for path in candidates if path.exists()), None)
        if respondents_path is not None:
            pairs.append((metadata_path, respondents_path))
    return pairs


def survey_name_from(metadata_path: Path, metadata: dict[str, Any]) -> str:
    return safe_id(metadata_path.stem.replace("_metadata", ""))


def item_question_name(item_key: str) -> str:
    return safe_id(item_key)


def option_maps_for(metadata: dict[str, Any], item: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    codes = item.get("option_codes", metadata.get("option_codes"))
    labels = item.get("option_labels", metadata.get("option_labels"))
    if not codes or not labels:
        raise ValueError("missing option_codes/option_labels")
    options = [str(label) for label in labels]
    answer_map = codebook_map(codes, labels)
    missing_map = codebook_map(item.get("missing_codes", []), item.get("missing_labels", []))
    return answer_map, missing_map, options


def item_column(item_key: str, item: dict[str, Any], header: set[str]) -> str:
    candidates = [
        item.get("source_variable"),
        item.get("variable"),
        f"item_{item_key}",
        f"item_{item.get('source_variable')}" if item.get("source_variable") else None,
        f"item_{item.get('variable')}" if item.get("variable") else None,
        item_key,
    ]
    for candidate in candidates:
        if candidate and candidate in header:
            return str(candidate)
    raise ValueError(f"no respondent column found for item {item_key}; tried {[c for c in candidates if c]}")


def convert_pair(metadata_path: Path, respondents_path: Path, out_dir: Path) -> dict[str, Any]:
    metadata = json.loads(metadata_path.read_text())
    survey_name = survey_name_from(metadata_path, metadata)
    survey_dir = out_dir / survey_name
    survey_dir.mkdir(parents=True, exist_ok=True)

    items = metadata.get("items")
    if not isinstance(items, dict) or not items:
        raise ValueError("metadata has no item dictionary")

    with respondents_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = set(reader.fieldnames or [])

    id_column = metadata.get("respondent_id") or metadata.get("id_column") or "respondent_id"
    weight_column = metadata.get("weight") or metadata.get("weight_column") or "weight"
    if id_column not in header:
        raise ValueError(f"respondent id column not found: {id_column}")

    questions = []
    item_specs = []
    for item_key, item in items.items():
        if not isinstance(item, dict):
            raise ValueError(f"item {item_key} is not an object")
        answer_map, missing_map, options = option_maps_for(metadata, item)
        column = item_column(str(item_key), item, header)
        question_stem = str(item.get("question_stem") or metadata.get("common_question_stem") or "").strip()
        item_text = str(item.get("item_text") or item.get("source_label") or item_key).strip()
        question_text = f"{question_stem} {item_text}".strip()
        source_variable = item.get("source_variable") or item.get("variable") or column
        question_name = item_question_name(str(item_key))
        questions.append(
            {
                "question_name": question_name,
                "question_type": "multiple_choice",
                "question_text": question_text,
                "question_options": options,
                "option_labels": {label: label for label in options},
                "role": "survey_item",
                "source": {
                    "raw_id": f"{survey_name}_metadata",
                    "note": (
                        f"Mapped from source variable {source_variable}. "
                        f"Source codes expanded through metadata codebook; raw codes are not canonical answers."
                    ),
                },
            }
        )
        item_specs.append(
            {
                "question": question_name,
                "column": column,
                "answer_map": answer_map,
                "missing_map": missing_map,
            }
        )

    respondents = []
    answers = []
    issues = []
    covariates = list(metadata.get("covariates", []))
    with respondents_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row_number, row in enumerate(reader, 2):
            source_respondent_id = str(row[id_column])
            respondent_id = f"{survey_name}_{source_respondent_id}"
            weight_raw = row.get(weight_column, "1") if weight_column in row else "1"
            try:
                weight = float(weight_raw)
            except ValueError:
                weight = 1.0
                issues.append({"row": row_number, "code": "invalid_weight", "value": weight_raw})
            respondents.append(
                {
                    "respondent_id": respondent_id,
                    "weight": weight,
                    "metadata": {name: row.get(name) for name in covariates if name in row},
                    "source": {
                        "raw_id": f"{survey_name}_respondents",
                        "note": f"Source respondent id {source_respondent_id}.",
                    },
                }
            )
            for spec in item_specs:
                raw_value = str(row.get(spec["column"], "")).strip()
                if raw_value == "":
                    answers.append({"respondent_id": respondent_id, "question": spec["question"], "missing_code": "blank"})
                elif raw_value in spec["answer_map"]:
                    answers.append({"respondent_id": respondent_id, "question": spec["question"], "answer": spec["answer_map"][raw_value]})
                elif raw_value in spec["missing_map"]:
                    answers.append({"respondent_id": respondent_id, "question": spec["question"], "missing_code": spec["missing_map"][raw_value]})
                else:
                    issues.append(
                        {
                            "row": row_number,
                            "code": "unmapped_answer_code",
                            "question": spec["question"],
                            "column": spec["column"],
                            "value": raw_value,
                        }
                    )

    write_jsonl(survey_dir / "questions.jsonl", questions)
    write_jsonl(survey_dir / "respondents.jsonl", respondents)
    write_jsonl(survey_dir / "answers.jsonl", answers)
    context = str(metadata.get("context") or metadata.get("source") or "").strip()
    (survey_dir / "context.md").write_text(context + "\n" if context else "")
    write_jsonl(survey_dir / "issues.jsonl", issues)
    summary = {
        "survey": survey_name,
        "metadata_path": str(metadata_path),
        "respondents_path": str(respondents_path),
        "questions": len(questions),
        "respondents": len(respondents),
        "answers": len(answers),
        "issue_count": len(issues),
        "issue_examples": issues[:20],
        "issues_path": str(survey_dir / "issues.jsonl"),
    }
    (survey_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def run_zwill(args: list[str], cwd: Path) -> None:
    completed = subprocess.run(["zwill", *args], cwd=cwd, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "zwill command failed: "
            + " ".join(["zwill", *args])
            + "\nstdout:\n"
            + completed.stdout
            + "\nstderr:\n"
            + completed.stderr
        )


def import_survey(summary: dict[str, Any], imports_dir: Path, workdir: Path, *, source_root: Path, commit: bool) -> None:
    survey = summary["survey"]
    survey_import_dir = imports_dir / survey
    metadata_path = Path(summary["metadata_path"])
    respondents_path = Path(summary["respondents_path"])
    run_zwill(["survey", "create", "--name", survey], workdir)
    context_path = survey_import_dir / "context.md"
    if context_path.exists() and context_path.read_text().strip():
        run_zwill(["context", "set", "--survey", survey, "--path", str(context_path)], workdir)
    run_zwill(
        [
            "raw",
            "add",
            "--survey",
            survey,
            "--id",
            f"{survey}_metadata",
            "--path",
            str(metadata_path),
            "--kind",
            "metadata",
            "--title",
            f"{survey} normalized metadata",
        ],
        workdir,
    )
    run_zwill(
        [
            "raw",
            "add",
            "--survey",
            survey,
            "--id",
            f"{survey}_respondents",
            "--path",
            str(respondents_path),
            "--kind",
            "respondent_data",
            "--title",
            f"{survey} normalized respondents",
        ],
        workdir,
    )
    run_zwill(["question", "import", "--survey", survey, "--path", str(survey_import_dir / "questions.jsonl")], workdir)
    run_zwill(["respondent", "import", "--survey", survey, "--path", str(survey_import_dir / "respondents.jsonl")], workdir)
    run_zwill(["answer", "import", "--survey", survey, "--path", str(survey_import_dir / "answers.jsonl")], workdir)
    if commit:
        run_zwill(["commit", "--survey", survey], workdir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--workdir", type=Path, default=Path(__file__).resolve().parent / "workdir")
    parser.add_argument("--fresh", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--commit", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--convert-only", action="store_true")
    parser.add_argument("--survey", action="append", help="Only ingest matching survey id. Repeatable.")
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    workdir = args.workdir.resolve()
    imports_dir = workdir / "imports"
    if args.fresh:
        shutil.rmtree(workdir / ".zwill", ignore_errors=True)
        shutil.rmtree(imports_dir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    imports_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    failures = []
    selected = {safe_id(value) for value in args.survey or []}
    for metadata_path, respondents_path in metadata_pairs(source_root):
        metadata = json.loads(metadata_path.read_text())
        survey = survey_name_from(metadata_path, metadata)
        if selected and survey not in selected:
            continue
        try:
            summary = convert_pair(metadata_path, respondents_path, imports_dir)
            summaries.append(summary)
        except (OSError, ValueError, json.JSONDecodeError, csv.Error) as exc:
            failures.append({"metadata_path": str(metadata_path), "respondents_path": str(respondents_path), "error": str(exc)})

    manifest = {
        "source_root": str(source_root),
        "workdir": str(workdir),
        "survey_count": len(summaries),
        "failure_count": len(failures),
        "summaries": summaries,
        "failures": failures,
    }
    (workdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if failures:
        print(json.dumps(manifest, indent=2))
        return 1

    if not args.convert_only:
        run_zwill(["init"], workdir)
        for summary in summaries:
            import_survey(summary, imports_dir, workdir, source_root=source_root, commit=args.commit)

    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
