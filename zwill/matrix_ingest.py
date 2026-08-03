"""Strict conversion of a respondent-by-variable matrix to zwill JSONL."""

from __future__ import annotations

import argparse
import csv

from .cli import *  # noqa: F403


def _code(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def convert_matrix(metadata_path: Path, respondents_path: Path, output: Path, *, survey: str) -> dict[str, Any]:
    metadata = read_json(metadata_path, {})
    items = metadata.get("items")
    if not isinstance(items, dict) or not items:
        raise ZwillError("invalid_input", "Matrix metadata needs a non-empty 'items' object.")
    top_codes = metadata.get("option_codes") or []
    top_labels = metadata.get("option_labels") or []
    missing_codes = {_code(value) for value in metadata.get("missing_codes", ["", "NA", "N/A"])}
    questions: list[dict[str, Any]] = []
    mappings: dict[str, tuple[str, str, dict[str, str]]] = {}
    issues: list[dict[str, Any]] = []
    for item_key, item in items.items():
        codes = item.get("option_codes", top_codes)
        labels = item.get("option_labels", top_labels)
        if not codes or len(codes) != len(labels):
            raise ZwillError("invalid_input", f"{item_key}: option_codes and option_labels must have equal length.")
        by_code = {_code(raw): str(label) for raw, label in zip(codes, labels, strict=True)}
        source_variable = str(item.get("source_variable", item.get("variable", item_key)))
        variable_template = metadata.get("data_variable_template")
        variable = str(item.get("data_variable") or (
            str(variable_template).format(item=item_key, source_variable=source_variable)
            if variable_template else source_variable
        ))
        question_template = metadata.get("question_name_template")
        question_name = str(item.get("question_name") or (
            str(question_template).format(item=item_key, battery=metadata.get("battery", survey), survey=survey)
            if question_template else slugify(f"{metadata.get('battery', survey)}_{item_key}").lower()
        ))
        text = " ".join(
            part for part in (str(item.get("question_stem", "")).strip(), str(item.get("item_text", "")).strip()) if part
        )
        questions.append(
            {
                "question_name": question_name,
                "question_type": "multiple_choice",
                "question_text": text,
                "question_options": list(by_code.values()),
                "option_labels": {label: label for label in by_code.values()},
                "role": item.get("role", "survey_item"),
                "source": {"raw_id": f"{survey}_metadata", "note": f"Source variable {source_variable}; code mapping {by_code}."},
            }
        )
        mappings[str(item_key)] = (question_name, variable, by_code)

    raw_id = str(metadata.get("respondent_id", metadata.get("id_column", "respondent_id")))
    weight_col = str(metadata.get("weight", metadata.get("weight_column", "weight")))
    covariates = metadata.get("covariates") or []
    covariate_codebook = metadata.get("covariate_codebook") or {}
    covariate_specs = []
    for value in covariates:
        if isinstance(value, str):
            declared = covariate_codebook.get(value) or {}
            covariate_specs.append({"source": value, "name": declared.get("name", value), "labels": declared.get("labels", {})})
        else:
            covariate_specs.append(
                {"source": value.get("source", value.get("variable")), "name": value.get("name"), "labels": value.get("labels", {})}
            )
    respondent_rows: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    with respondents_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        required = {raw_id, weight_col, *(variable for _q, variable, _map in mappings.values())}
        absent = sorted(required - columns)
        if absent:
            raise ZwillError("invalid_input", "Respondent matrix is missing required columns.", context={"columns": absent})
        for line, row in enumerate(reader, 2):
            rid = f"{survey}_{row[raw_id]}"
            profile: dict[str, Any] = {}
            for spec in covariate_specs:
                source = spec["source"]
                if not source or source not in row:
                    continue
                raw_value = _code(row[source])
                labels = {_code(key): label for key, label in (spec.get("labels") or {}).items()}
                if labels and raw_value not in labels and raw_value not in missing_codes:
                    issues.append({"line": line, "variable": source, "code": raw_value, "error": "unknown_covariate_code"})
                elif raw_value not in missing_codes:
                    profile[str(spec.get("name") or source)] = labels.get(raw_value, row[source])
            try:
                weight = float(row.get(weight_col) or 1.0)
            except ValueError:
                issues.append({"line": line, "variable": weight_col, "code": row.get(weight_col), "error": "invalid_weight"})
                continue
            respondent_rows.append(
                {"respondent_id": rid, "weight": weight, "metadata": profile,
                 "source": {"raw_id": f"{survey}_respondents", "note": f"Matrix row {line}."}}
            )
            for _key, (question, variable, labels) in mappings.items():
                raw_value = _code(row.get(variable))
                if raw_value in labels:
                    answer_rows.append({"respondent_id": rid, "question": question, "answer": labels[raw_value]})
                elif raw_value in missing_codes:
                    answer_rows.append({"respondent_id": rid, "question": question, "missing_code": "missing_or_refused"})
                else:
                    issues.append({"line": line, "variable": variable, "code": raw_value, "error": "unknown_answer_code"})
    if issues:
        issue_path = output / "issues.jsonl"
        _jsonl(issue_path, issues)
        raise ZwillError(
            "invalid_input",
            f"Matrix conversion found {len(issues)} unknown/invalid coded values; nothing was imported.",
            context={"issues_path": str(issue_path), "examples": issues[:5]},
        )
    _jsonl(output / "questions.jsonl", questions)
    _jsonl(output / "respondents.jsonl", respondent_rows)
    _jsonl(output / "answers.jsonl", answer_rows)
    manifest = {
        "schema_version": 1,
        "survey": survey,
        "metadata_path": str(metadata_path),
        "respondents_path": str(respondents_path),
        "question_count": len(questions),
        "respondent_count": len(respondent_rows),
        "answer_count": len(answer_rows),
        "files": {name: str(output / f"{name}.jsonl") for name in ("questions", "respondents", "answers")},
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def cmd_survey_ingest_matrix(args: argparse.Namespace) -> dict[str, Any]:
    output = resolve_output_path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    manifest = convert_matrix(Path(args.metadata), Path(args.respondents), output, survey=args.survey)
    if args.apply:
        try:
            require_survey(args.survey)
        except ZwillError:
            cmd_survey_create(argparse.Namespace(name=args.survey))
        cmd_raw_add(argparse.Namespace(survey=args.survey, id=f"{args.survey}_metadata",
                                        input_path=getattr(args, "raw_metadata", None) or args.metadata,
                                        kind="metadata", title=f"{args.survey} matrix metadata"))
        cmd_raw_add(argparse.Namespace(survey=args.survey, id=f"{args.survey}_respondents", input_path=args.respondents,
                                        kind="respondent_data", title=f"{args.survey} respondent matrix"))
        for noun, command in (("questions", cmd_question_import), ("respondents", cmd_respondent_import), ("answers", cmd_answer_import)):
            command(argparse.Namespace(survey=args.survey, input_path=str(output / f"{noun}.jsonl")))
        manifest["applied"] = True
        write_json(output / "manifest.json", manifest)
    return envelope(
        "zwill survey ingest-matrix",
        "ok",
        {**manifest, "path": str(output), "applied": bool(args.apply)},
        next_steps=[f"zwill commit --survey {args.survey}" if args.apply else f"Review {output / 'manifest.json'}, then rerun with --apply."],
    )
