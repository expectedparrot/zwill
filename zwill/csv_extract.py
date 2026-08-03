"""Extract reviewable survey batteries from respondent-by-variable CSV files."""

from __future__ import annotations

import argparse
import csv

from .cli import *  # noqa: F403


def _code(value: Any) -> str:
    """Normalize JSON and CSV codes without changing ordinary text values."""
    text = "" if value is None else str(value).strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return str(int(number)) if number.is_integer() else str(number)


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ZwillError("invalid_input", f"{field} must be a non-empty object.")
    return {_code(raw): output for raw, output in value.items()}


def extract_csv_batteries(data_path: Path, spec_path: Path, output: Path) -> dict[str, Any]:
    """Convert declared CSV variables into strict matrix-ingestion inputs."""
    spec = read_json(spec_path, {})
    batteries = spec.get("batteries")
    if not isinstance(batteries, list) or not batteries:
        raise ZwillError("invalid_input", "CSV extraction spec needs a non-empty 'batteries' list.")
    respondent_id = str(spec.get("respondent_id", "")).strip()
    if not respondent_id:
        raise ZwillError("invalid_input", "CSV extraction spec needs 'respondent_id'.")
    weight = str(spec.get("weight", "weight")).strip()
    weight_value = spec.get("weight_value")
    if weight_value is not None:
        try:
            if float(weight_value) <= 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ZwillError("invalid_input", "weight_value must be a positive number.") from exc

    with data_path.open(newline="", encoding=str(spec.get("encoding", "utf-8-sig"))) as handle:
        reader = csv.DictReader(handle)
        source_columns = reader.fieldnames or []
        rows = list(reader)
    if not source_columns:
        raise ZwillError("invalid_input", "CSV source has no header row.")
    if respondent_id not in source_columns:
        raise ZwillError("invalid_input", "CSV data is missing the respondent id.", context={"columns": [respondent_id]})
    if weight not in source_columns and weight_value is None:
        raise ZwillError(
            "invalid_input",
            "CSV data is missing the declared weight; set weight_value explicitly for uniform weights.",
            context={"columns": [weight]},
        )
    ids = [str(row.get(respondent_id, "")).strip() for row in rows]
    blank_ids = sum(not value for value in ids)
    duplicate_ids = len(ids) - len(set(ids))
    if blank_ids or duplicate_ids:
        raise ZwillError(
            "invalid_input",
            "Respondent ids must be non-blank and unique.",
            context={"blank_ids": blank_ids, "duplicate_ids": duplicate_ids},
        )

    covariates = spec.get("covariates") or []
    if not isinstance(covariates, list):
        raise ZwillError("invalid_input", "covariates must be a list.")
    covariate_names = [str(c.get("variable", c.get("source"))) if isinstance(c, dict) else str(c) for c in covariates]
    declared = {respondent_id, *covariate_names}
    if weight in source_columns:
        declared.add(weight)
    for battery in batteries:
        for item in battery.get("items") or []:
            declared.add(str(item.get("variable")))
    absent = sorted(declared - set(source_columns))
    if absent:
        raise ZwillError("invalid_input", "CSV data is missing declared variables.", context={"columns": absent})

    output.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, Any]] = []
    total_transformed = 0
    for battery in batteries:
        battery_id = slugify(str(battery.get("id", ""))).lower().replace("-", "_")
        items = battery.get("items") or []
        if not battery_id or len(items) < 2:
            raise ZwillError("invalid_input", "Each battery needs an id and at least two items.")

        item_metadata: dict[str, Any] = {}
        item_variables: list[str] = []
        item_rules: dict[str, tuple[dict[str, Any] | None, set[str], set[str]]] = {}
        for item in items:
            variable = str(item["variable"])
            key = str(item.get("id", variable))
            if key in item_metadata:
                raise ZwillError("invalid_input", f"{battery_id}: duplicate item id {key!r}.")
            options = _mapping(item.get("options"), field=f"{variable}.options")
            option_codes = list(options.keys())
            recode = item.get("recode")
            recode_map = _mapping(recode, field=f"{variable}.recode") if recode is not None else None
            missing = {_code(value) for value in item.get("missing_codes", spec.get("missing_codes", [""]))}
            item_rules[variable] = (recode_map, missing, set(option_codes))
            item_variables.append(variable)
            item_metadata[key] = {
                "source_variable": variable,
                "data_variable": variable,
                "question_name": str(item.get("question_name", key)),
                "question_stem": str(item.get("question_stem", battery.get("question_stem", ""))),
                "item_text": str(item.get("question_text", "")).strip(),
                "option_codes": option_codes,
                "option_labels": [str(options[code]) for code in option_codes],
            }
            if not item_metadata[key]["item_text"]:
                raise ZwillError("invalid_input", f"{variable}: question_text is required for CSV extraction.")

        covariate_metadata: list[dict[str, Any]] = []
        covariate_rules: dict[str, tuple[dict[str, Any] | None, set[str] | None]] = {}
        for covariate in covariates:
            if isinstance(covariate, str):
                covariate = {"variable": covariate}
            variable = str(covariate.get("variable", covariate.get("source")))
            labels = covariate.get("labels")
            label_map = _mapping(labels, field=f"{variable}.labels") if labels is not None else None
            recode = covariate.get("recode")
            recode_map = _mapping(recode, field=f"{variable}.recode") if recode is not None else None
            covariate_rules[variable] = (recode_map, set(label_map) if label_map is not None else None)
            record = {"source": variable, "name": str(covariate.get("name", variable))}
            if label_map is not None:
                record["labels"] = {code: str(label) for code, label in label_map.items()}
            covariate_metadata.append(record)

        transformed = 0
        complete = 0
        output_rows: list[dict[str, Any]] = []
        for row_number, source_row in enumerate(rows, start=2):
            out_row = {respondent_id: source_row[respondent_id]}
            out_row[weight] = source_row[weight] if weight in source_columns else weight_value
            for variable in covariate_names:
                raw = _code(source_row.get(variable, ""))
                recode_map, allowed = covariate_rules[variable]
                if recode_map is not None:
                    if raw not in recode_map:
                        raise ZwillError("invalid_input", f"{variable}: unmapped CSV code.", context={"row": row_number, "code": raw})
                    value = _code(recode_map[raw])
                    transformed += int(value != raw)
                else:
                    value = raw
                if value and allowed is not None and value not in allowed:
                    raise ZwillError("invalid_input", f"{variable}: code has no covariate label.", context={"row": row_number, "code": value})
                out_row[variable] = value
            row_complete = True
            for variable in item_variables:
                raw = _code(source_row.get(variable, ""))
                recode_map, missing, allowed = item_rules[variable]
                if recode_map is not None:
                    if raw not in recode_map:
                        raise ZwillError("invalid_input", f"{variable}: unmapped CSV code.", context={"row": row_number, "code": raw})
                    value = _code(recode_map[raw])
                    transformed += int(value != raw)
                else:
                    value = raw
                if value in missing:
                    value = ""
                    row_complete = False
                elif value not in allowed:
                    raise ZwillError("invalid_input", f"{variable}: code has no option label.", context={"row": row_number, "code": value})
                out_row[variable] = value
            complete += int(row_complete)
            output_rows.append(out_row)

        matrix_path = output / f"{battery_id}_respondents.csv"
        metadata_path = output / f"{battery_id}_metadata.json"
        columns = [respondent_id, weight, *covariate_names, *item_variables]
        with matrix_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(output_rows)
        metadata = {
            "source": spec.get("source", {}),
            "battery": battery_id,
            "context": battery.get("context", ""),
            "respondent_id": respondent_id,
            "weight": weight,
            "missing_codes": [""],
            "covariates": covariate_metadata,
            "items": item_metadata,
            "extraction_note": (
                "Question wording, response labels, and any recodes are declared in the reviewed CSV extraction "
                "specification. Recodes reject undeclared source values; the manifest reports transformed cells."
            ),
        }
        write_json(metadata_path, metadata)
        total_transformed += transformed
        outputs.append({
            "battery": battery_id,
            "metadata": str(metadata_path),
            "respondents": str(matrix_path),
            "questions": len(items),
            "respondents_total": len(rows),
            "respondents_complete_case": complete,
            "transformed_cells": transformed,
        })

    manifest = {
        "schema_version": 1,
        "command": "zwill survey extract-csv",
        "data_path": str(data_path),
        "spec_path": str(spec_path),
        "batteries": outputs,
        "transformed_cells": total_transformed,
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def cmd_survey_extract_csv(args: argparse.Namespace) -> dict[str, Any]:
    output = resolve_output_path(args.out)
    manifest = extract_csv_batteries(Path(args.data), Path(args.spec), output)
    return envelope(
        "zwill survey extract-csv",
        "ok",
        {**manifest, "path": str(output)},
        next_steps=[
            f"Review {output / 'manifest.json'} and generated metadata, then add each matrix with `zwill study add-matrix`."
        ],
    )
