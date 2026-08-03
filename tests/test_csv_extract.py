from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from zwill.cli import ZwillError
from zwill.csv_extract import extract_csv_batteries


def _source(tmp_path: Path) -> tuple[Path, Path]:
    data_path = tmp_path / "survey.csv"
    data_path.write_text(
        "id,region,q1,q2\n"
        "a,1,1,0\n"
        "b,2,,1\n"
        "c,1,0,1\n",
        encoding="utf-8",
    )
    spec = {
        "source": {"title": "Synthetic CSV survey"},
        "respondent_id": "id",
        "weight_value": 1,
        "covariates": [{"variable": "region", "labels": {"1": "North", "2": "South"}}],
        "batteries": [{
            "id": "Practice Battery",
            "items": [
                {
                    "variable": "q1",
                    "question_text": "Have you used the first practice?",
                    "options": {"0": "No", "1": "Yes"},
                    "recode": {"": "0", "0": "0", "1": "1"},
                },
                {
                    "variable": "q2",
                    "question_text": "Have you used the second practice?",
                    "options": {"0": "No", "1": "Yes"},
                },
            ],
        }],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return data_path, spec_path


def test_extract_csv_writes_labeled_matrices_and_audits_recodes(tmp_path: Path) -> None:
    data_path, spec_path = _source(tmp_path)
    manifest = extract_csv_batteries(data_path, spec_path, tmp_path / "out")

    battery = manifest["batteries"][0]
    assert battery == {
        "battery": "practice_battery",
        "metadata": str(tmp_path / "out" / "practice_battery_metadata.json"),
        "respondents": str(tmp_path / "out" / "practice_battery_respondents.csv"),
        "questions": 2,
        "respondents_total": 3,
        "respondents_complete_case": 3,
        "transformed_cells": 1,
    }
    metadata = json.loads((tmp_path / "out" / "practice_battery_metadata.json").read_text())
    assert metadata["items"]["q1"]["option_labels"] == ["No", "Yes"]
    assert metadata["covariates"][0]["labels"] == {"1": "North", "2": "South"}
    with (tmp_path / "out" / "practice_battery_respondents.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows[1]["q1"] == "0"
    assert rows[1]["weight"] == "1"


def test_extract_csv_requires_explicit_uniform_weight(tmp_path: Path) -> None:
    data_path, spec_path = _source(tmp_path)
    spec = json.loads(spec_path.read_text())
    del spec["weight_value"]
    spec_path.write_text(json.dumps(spec))
    with pytest.raises(ZwillError) as exc:
        extract_csv_batteries(data_path, spec_path, tmp_path / "out")
    assert exc.value.context["columns"] == ["weight"]


def test_extract_csv_recode_rejects_undeclared_source_codes(tmp_path: Path) -> None:
    data_path, spec_path = _source(tmp_path)
    data_path.write_text(data_path.read_text().replace("a,1,1,0", "a,1,9,0"))
    with pytest.raises(ZwillError) as exc:
        extract_csv_batteries(data_path, spec_path, tmp_path / "out")
    assert exc.value.context == {"row": 2, "code": "9"}


def test_extract_csv_rejects_duplicate_respondent_ids(tmp_path: Path) -> None:
    data_path, spec_path = _source(tmp_path)
    data_path.write_text(data_path.read_text().replace("c,1,0,1", "a,1,0,1"))
    with pytest.raises(ZwillError) as exc:
        extract_csv_batteries(data_path, spec_path, tmp_path / "out")
    assert exc.value.context["duplicate_ids"] == 1
