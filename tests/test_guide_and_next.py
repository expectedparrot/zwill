from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import zwill.cli as cli
from zwill.cli import main
from zwill.errors import ZwillError
from zwill.guide_commands import GUIDES, cmd_guide_list, cmd_guide_show, cmd_next, guide_path


def test_bundled_guides_exist_and_nonempty() -> None:
    for name in GUIDES:
        path = guide_path(name)
        assert path.exists(), name
        assert path.read_text().strip()


def test_guide_show_prints_known_guide(capsys) -> None:
    result = cmd_guide_show(argparse.Namespace(name="agent-workflow"))
    assert result["status"] == "ok"
    out = capsys.readouterr().out
    assert "zwill agent workflow" in out


def test_guide_show_defaults_to_walkthrough(capsys) -> None:
    cmd_guide_show(argparse.Namespace(name=None))
    assert "survey data" in capsys.readouterr().out


def test_guide_show_unknown_raises() -> None:
    with pytest.raises(ZwillError):
        cmd_guide_show(argparse.Namespace(name="does-not-exist"))


def test_guide_list_returns_all_bundled(capsys) -> None:
    result = cmd_guide_list(argparse.Namespace(format="json"))
    names = {g["name"] for g in result["data"]["guides"]}
    assert names == set(GUIDES)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))


def _stage(survey: str | None = None) -> str:
    return cmd_next(argparse.Namespace(survey=survey))["data"]["stage"]


def _survey_dir(tmp_path: Path) -> Path:
    return next((tmp_path / ".zwill").rglob("questions.jsonl")).parent


def test_next_walks_the_pipeline(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    # Stage 0: no workspace.
    assert _stage() == "init"

    assert main(["init"]) == 0
    assert _stage() == "create_survey"

    assert main(["survey", "create", "--name", "demo"]) == 0
    assert _stage() == "import_data"

    _write_jsonl(
        tmp_path / "q.jsonl",
        [
            {"question_name": f"q{i}", "question_type": "multiple_choice",
             "question_text": f"Q{i}", "question_options": ["a", "b"]}
            for i in (1, 2)
        ],
    )
    _write_jsonl(tmp_path / "r.jsonl", [{"respondent_id": f"r{i}", "weight": 1.0, "metadata": {}} for i in range(4)])
    _write_jsonl(
        tmp_path / "a.jsonl",
        [{"respondent_id": f"r{i}", "question": q, "answer": "a" if i % 2 else "b"}
         for i in range(4) for q in ("q1", "q2")],
    )
    assert main(["question", "import", "--survey", "demo", "--path", str(tmp_path / "q.jsonl")]) == 0
    assert main(["respondent", "import", "--survey", "demo", "--path", str(tmp_path / "r.jsonl")]) == 0
    assert main(["answer", "import", "--survey", "demo", "--path", str(tmp_path / "a.jsonl")]) == 0
    assert _stage() == "commit"

    assert main(["commit", "--survey", "demo"]) == 0
    assert _stage() == "run_twins"

    sdir = _survey_dir(tmp_path)
    predictions = sdir / "digital_twin_predictions.jsonl"

    # Twin job present, no baseline -> validate.
    _write_jsonl(predictions, [
        {"job_id": "twinjob", "model_label": "openai:gpt-5.5", "heldout_question": "q1",
         "respondent_id": "r0", "actual_answer": "a", "option_labels": ["a", "b"],
         "probabilities": {"a": 0.7, "b": 0.3}},
    ])
    result = cmd_next(argparse.Namespace(survey=None))["data"]
    assert result["stage"] == "validate"
    assert "twinjob" in result["next_command"]

    # Baseline present -> build_report.
    _write_jsonl(predictions, [
        {"job_id": "twinjob", "model_label": "openai:gpt-5.5", "heldout_question": "q1",
         "respondent_id": "r0", "actual_answer": "a", "option_labels": ["a", "b"],
         "probabilities": {"a": 0.7, "b": 0.3}},
        {"job_id": "baseline_x", "model_label": "baseline:conditional-embedding", "heldout_question": "q1",
         "respondent_id": "r0", "actual_answer": "a", "option_labels": ["a", "b"],
         "probabilities": {"a": 0.6, "b": 0.4}},
    ])
    assert _stage() == "build_report"

    report_dir = tmp_path / "demo_report"
    report_dir.mkdir()
    (report_dir / "stage-manifest.json").write_text(json.dumps({
        "survey": "demo",
        "output_dir": "demo_report",
        "stages": {
            "generated_analysis": {
                "status": "blocked",
                "missing": ["frontier-model one-shot marginal analysis Markdown"],
                "next_step": "zwill prob-results analysis-export --survey demo --path demo_report/one-shot-marginals.html",
            },
            "final_report": {
                "status": "blocked",
                "missing": ["frontier-model one-shot marginal analysis Markdown"],
                "next_step": "zwill prob-results analysis-export --survey demo --path demo_report/one-shot-marginals.html",
            },
        },
        "pages": [],
    }))
    result = cmd_next(argparse.Namespace(survey=None))["data"]
    assert result["stage"] == "final_report"
    assert "prob-results analysis-export" in result["next_command"]


def test_next_prompts_to_choose_when_multiple_surveys(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert main(["survey", "create", "--name", "one"]) == 0
    assert main(["survey", "create", "--name", "two"]) == 0
    result = cmd_next(argparse.Namespace(survey=None))["data"]
    assert result["stage"] == "choose_survey"
    assert set(result["surveys"]) == {"one", "two"}


def test_init_next_steps_point_at_guide(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli.cmd_init(argparse.Namespace())
    assert any("zwill guide" in step for step in result["next_steps"])
