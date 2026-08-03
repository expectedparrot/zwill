"""Extract reviewable survey batteries from a labeled Stata data file."""

from __future__ import annotations

import argparse
import csv

from .cli import *  # noqa: F403

_STATA_MISSING_MIN = 2_147_483_620


def _plain(value: Any) -> Any:
    """Convert numpy scalars to JSON/CSV-safe Python values."""
    return value.item() if hasattr(value, "item") else value


def _is_missing(value: Any, *, missing_min: int) -> bool:
    if value is None:
        return True
    try:
        if bool(value != value):  # NaN, including numpy scalars
            return True
    except (TypeError, ValueError):
        pass
    try:
        return float(value) >= missing_min
    except (TypeError, ValueError):
        return False


def _code(value: Any) -> str:
    value = _plain(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def extract_stata_batteries(data_path: Path, spec_path: Path, output: Path) -> dict[str, Any]:
    """Convert declared Stata variables into strict matrix-ingestion inputs."""
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - exercised in minimal installations
        raise ZwillError(
            "missing_dependency",
            "Stata extraction needs pandas; install `zwill[survey-formats]`.",
        ) from exc

    spec = read_json(spec_path, {})
    batteries = spec.get("batteries")
    if not isinstance(batteries, list) or not batteries:
        raise ZwillError("invalid_input", "Stata extraction spec needs a non-empty 'batteries' list.")

    reader = pd.read_stata(data_path, iterator=True, convert_categoricals=False)
    frame = reader.read()
    variable_labels = reader.variable_labels()
    value_labels = reader.value_labels()
    label_sets = dict(zip(reader._varlist, reader._lbllist, strict=True))
    missing_min = int(spec.get("stata_missing_min", _STATA_MISSING_MIN))
    respondent_id = str(spec.get("respondent_id", "id"))
    weight = str(spec.get("weight", "weight"))
    covariates = spec.get("covariates") or []
    covariate_names = [str(c.get("variable", c.get("source"))) for c in covariates]
    declared = {respondent_id, weight, *covariate_names}
    for battery in batteries:
        for item in battery.get("items") or []:
            declared.add(str(item.get("variable")))
    absent = sorted(declared - set(frame.columns))
    if absent:
        raise ZwillError("invalid_input", "Stata data is missing declared variables.", context={"columns": absent})

    output.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, Any]] = []
    for battery in batteries:
        battery_id = slugify(str(battery.get("id", ""))).lower().replace("-", "_")
        items = battery.get("items") or []
        if not battery_id or len(items) < 2:
            raise ZwillError("invalid_input", "Each battery needs an id and at least two items.")
        matrix_path = output / f"{battery_id}_respondents.csv"
        metadata_path = output / f"{battery_id}_metadata.json"
        item_metadata: dict[str, Any] = {}
        item_variables: list[str] = []
        for item in items:
            variable = str(item["variable"])
            item_variables.append(variable)
            configured = item.get("options")
            if configured:
                option_codes = list(configured.keys())
                option_labels = [str(value) for value in configured.values()]
            else:
                labels = value_labels.get(label_sets.get(variable, ""), {})
                usable = [(raw, label) for raw, label in labels.items() if not _is_missing(raw, missing_min=missing_min)]
                option_codes = [_plain(raw) for raw, _label in usable]
                option_labels = [str(label) for _raw, label in usable]
            if not option_codes:
                raise ZwillError("invalid_input", f"{variable}: no usable option labels; declare item.options.")
            item_metadata[variable] = {
                "source_variable": variable,
                "question_stem": str(item.get("question_stem", battery.get("question_stem", ""))),
                "item_text": str(item.get("question_text") or variable_labels.get(variable) or variable),
                "option_codes": option_codes,
                "option_labels": option_labels,
            }

        covariate_metadata: list[dict[str, Any]] = []
        for covariate in covariates:
            variable = str(covariate.get("variable", covariate.get("source")))
            if "labels" in covariate:
                labels = covariate["labels"]
            elif covariate.get("use_value_labels", True):
                labels = value_labels.get(label_sets.get(variable, ""), {})
            else:
                labels = {}
            covariate_metadata.append({
                "source": variable,
                "name": str(covariate.get("name", variable)),
                "labels": {
                    _code(raw): str(label) for raw, label in labels.items()
                    if not _is_missing(raw, missing_min=missing_min)
                },
            })

        columns = [respondent_id, weight, *covariate_names, *item_variables]
        with matrix_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in frame[columns].itertuples(index=False, name=None):
                record = {}
                for column, value in zip(columns, row, strict=True):
                    record[column] = "" if _is_missing(value, missing_min=missing_min) else _plain(value)
                writer.writerow(record)

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
                "Question wording is declared in the reviewed extraction specification; response and covariate "
                "labels are read from the Stata file unless explicitly overridden. Extended missing values are blank."
            ),
        }
        write_json(metadata_path, metadata)
        complete = int(frame[item_variables].apply(
            lambda column: ~column.map(lambda value: _is_missing(value, missing_min=missing_min))
        ).all(axis=1).sum())
        outputs.append({
            "battery": battery_id,
            "metadata": str(metadata_path),
            "respondents": str(matrix_path),
            "questions": len(items),
            "respondents_total": len(frame),
            "respondents_complete_case": complete,
        })

    manifest = {
        "schema_version": 1,
        "command": "zwill survey extract-stata",
        "data_path": str(data_path),
        "spec_path": str(spec_path),
        "batteries": outputs,
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def cmd_survey_extract_stata(args: argparse.Namespace) -> dict[str, Any]:
    output = resolve_output_path(args.out)
    manifest = extract_stata_batteries(Path(args.data), Path(args.spec), output)
    return envelope(
        "zwill survey extract-stata",
        "ok",
        {**manifest, "path": str(output)},
        next_steps=[
            f"Review {output / 'manifest.json'} and generated metadata, then add each matrix with `zwill study add-matrix`."
        ],
    )
