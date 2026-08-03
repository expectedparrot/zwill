from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from zwill.cli import ZwillError
from zwill.stata_extract import extract_stata_batteries


def _source(tmp_path: Path) -> tuple[Path, Path]:
    data = pd.DataFrame({
        "id": [1, 2, 3],
        "weight": [1.0, 2.0, 0.5],
        "party": [1, 2, 2],
        "q1": [1, 2, 1],
        "q2": [2, 1, None],
    })
    data_path = tmp_path / "survey.dta"
    data.to_stata(
        data_path,
        write_index=False,
        variable_labels={"q1": "First source label", "q2": "Second source label"},
        value_labels={
            "party": {1: "Democrat", 2: "Republican"},
            "q1": {1: "Yes", 2: "No"},
            "q2": {1: "Yes", 2: "No"},
        },
    )
    spec = {
        "source": {"title": "Synthetic survey", "release": "test"},
        "respondent_id": "id",
        "weight": "weight",
        "covariates": [{"variable": "party", "name": "Political party"}],
        "batteries": [{
            "id": "Trust Battery",
            "context": "A test battery.",
            "items": [
                {"variable": "q1", "question_text": "Do you agree with the first statement?"},
                {"variable": "q2", "question_text": "Do you agree with the second statement?"},
            ],
        }],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    return data_path, spec_path


def test_extract_stata_batteries_writes_reviewable_matrix_inputs(tmp_path: Path) -> None:
    data_path, spec_path = _source(tmp_path)
    manifest = extract_stata_batteries(data_path, spec_path, tmp_path / "out")

    battery = manifest["batteries"][0]
    assert battery["battery"] == "trust_battery"
    assert battery["questions"] == 2
    assert battery["respondents_total"] == 3
    assert battery["respondents_complete_case"] == 2
    metadata = json.loads((tmp_path / "out" / "trust_battery_metadata.json").read_text())
    assert metadata["items"]["q1"]["item_text"] == "Do you agree with the first statement?"
    assert metadata["items"]["q1"]["option_labels"] == ["Yes", "No"]
    assert metadata["covariates"][0]["labels"] == {"1": "Democrat", "2": "Republican"}
    with (tmp_path / "out" / "trust_battery_respondents.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows[2]["q2"] == ""


def test_extract_stata_batteries_rejects_unknown_variables(tmp_path: Path) -> None:
    data_path, spec_path = _source(tmp_path)
    spec = json.loads(spec_path.read_text())
    spec["batteries"][0]["items"][1]["variable"] = "missing_question"
    spec_path.write_text(json.dumps(spec))
    with pytest.raises(ZwillError) as exc:
        extract_stata_batteries(data_path, spec_path, tmp_path / "out")
    assert exc.value.context["columns"] == ["missing_question"]

