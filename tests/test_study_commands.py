from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pytest

import zwill.study_commands as study_commands
from zwill.cli import main
from zwill.errors import ZwillError
from zwill.matrix_ingest import convert_matrix


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_study_manifest_is_built_explicitly_and_checked_without_project_writes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    metadata = {
        "battery": "trust", "respondent_id": "caseid", "weight": "wt",
        "items": {"courts": {"source_variable": "Q1", "option_codes": [1, 2],
                               "option_labels": ["Trust", "Do not trust"]}},
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    (tmp_path / "respondents.csv").write_text("caseid,wt,item_courts\n1,1,1\n2,1,2\n")
    manifest = tmp_path / "study.json"
    assert main(["study", "init", "--path", str(manifest)]) == 0
    _payload(capsys)
    assert main(["study", "add-matrix", "--manifest", str(manifest), "--survey", "trust",
                 "--source-id", "TRUST_RELEASE", "--metadata", "metadata.json",
                 "--respondents", "respondents.csv"]) == 0
    _payload(capsys)
    assert main(["study", "set-matrix-defaults", "--manifest", str(manifest),
                 "--data-variable-template", "item_{item}",
                 "--question-name-template", "{battery}_{item}", "--missing-code", "NA"]) == 0
    _payload(capsys)
    assert main(["study", "check", "--manifest", str(manifest)]) == 0
    checked = _payload(capsys)["data"]
    assert checked["surveys"][0]["respondent_count"] == 2
    assert checked["surveys"][0]["question_count"] == 1
    stored = json.loads(manifest.read_text())
    assert stored["surveys"][0]["matrix"]["metadata"] == "metadata.json"
    assert stored["matrix_defaults"]["metadata_overlay"]["data_variable_template"] == "item_{item}"
    assert not (tmp_path / ".zwill").exists()


def test_study_add_matrix_refuses_duplicate_and_check_reports_missing_columns(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "study.yaml"
    assert main(["study", "init", "--path", str(manifest)]) == 0
    _payload(capsys)
    (tmp_path / "metadata.json").write_text(json.dumps({
        "respondent_id": "id", "items": {"q": {"source_variable": "q1"}},
    }))
    (tmp_path / "respondents.csv").write_text("id,weight\n1,1\n")
    command = ["study", "add-matrix", "--manifest", str(manifest), "--survey", "demo",
               "--metadata", "metadata.json", "--respondents", "respondents.csv"]
    assert main(command) == 0
    _payload(capsys)
    assert main(command) == 1
    assert _payload(capsys)["errors"][0]["code"] == "already_exists"
    assert main(["study", "check", "--manifest", str(manifest)]) == 1
    assert "q1" in _payload(capsys)["errors"][0]["message"]


def test_study_semantic_proximity_uses_actual_context_and_battery_fixed_effects(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    surveys = {}
    runs = []
    question_rows = []
    for battery_index, survey in enumerate(("a", "b")):
        sdir = tmp_path / survey
        sdir.mkdir()
        surveys[survey] = sdir
        predictions = []
        for question_index in range(3):
            question = f"q{question_index}"
            job_id = f"job_{survey}_{question}"
            runs.append({"survey": survey, "job_id": job_id, "plan_id": job_id})
            predictions.append({
                "job_id": job_id, "model_label": "model", "heldout_question": question,
                "heldout_question_text": question,
                "observed_answers": [{"question_name": f"c{question_index}",
                                      "question_text": f"c{question_index}"}],
            })
            question_rows.append({
                "survey": survey, "question": question, "model": "model",
                "mean_nll": 1 - .1 * question_index + battery_index,
                "accuracy": .2 + .1 * question_index,
                "nll_delta_vs_conditional": .3 - .1 * question_index,
            })
        (sdir / "predictions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in predictions))
    manifests, question_files = [], []
    for survey in ("a", "b"):
        run_manifest = tmp_path / f"runs-{survey}.json"
        run_manifest.write_text(json.dumps({
            "runs": [row for row in runs if row["survey"] == survey]
        }))
        manifests.append(run_manifest)
        questions = tmp_path / f"questions-{survey}.csv"
        selected = [row for row in question_rows if row["survey"] == survey]
        with questions.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
            writer.writeheader()
            writer.writerows(selected)
        question_files.append(questions)
    vectors = {"q0": [1, 0], "c0": [0, 1], "q1": [1, 0], "c1": [.5, .5],
               "q2": [1, 0], "c2": [1, 0]}
    monkeypatch.setattr(study_commands, "require_survey", lambda survey: surveys[survey])
    monkeypatch.setattr(study_commands, "digital_twin_predictions_path", lambda sdir: sdir / "predictions.jsonl")
    monkeypatch.setattr("zwill.twin_baseline_commands.resolve_baseline_embedder",
                        lambda args, model: lambda texts: [vectors[text] for text in texts])
    assert main(["study", "semantic-proximity",
                 "--manifest", str(manifests[0]), "--manifest", str(manifests[1]),
                 "--questions", str(question_files[0]), "--questions", str(question_files[1]),
                 "--out", str(tmp_path / "out"),
                 "--model", "model", "--embedder", "hashing", "--n-boot", "100"]) == 0
    data = _payload(capsys)["data"]
    estimate = data["estimates"]["mean_context_similarity"]["twin_advantage_vs_xgboost"]
    assert estimate["coefficient"] > 0
    assert data["question_count"] == 6
    assert (tmp_path / "out" / "questions.csv").exists()


def test_ingest_matrix_converts_then_applies_atomically(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    metadata = {
        "battery": "attitudes",
        "respondent_id": "caseid",
        "weight": "wt",
        "missing_codes": ["", "9"],
        "covariates": [{"source": "party", "name": "party", "labels": {"1": "Democrat", "2": "Republican"}}],
        "items": {
            "trust": {
                "source_variable": "q1",
                "question_stem": "How much do you trust",
                "item_text": "the government?",
                "option_codes": [1, 2],
                "option_labels": ["A lot", "Not much"],
            }
        },
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    with (tmp_path / "respondents.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows([["caseid", "wt", "party", "q1"], ["1", "1.5", "1", "1"], ["2", "0.5", "2", "9"]])
    assert main(["init"]) == 0
    _payload(capsys)
    assert main([
        "survey", "ingest-matrix", "--survey", "demo", "--metadata", str(tmp_path / "metadata.json"),
        "--respondents", str(tmp_path / "respondents.csv"), "--out", str(tmp_path / "converted"), "--apply",
    ]) == 0
    data = _payload(capsys)["data"]
    assert data["question_count"] == 1
    assert data["respondent_count"] == 2
    assert data["answer_count"] == 2
    assert data["applied"] is True
    questions = list((tmp_path / ".zwill").rglob("questions.jsonl"))
    imported = [json.loads(line) for line in questions[0].read_text().splitlines()]
    assert imported[0]["question_options"] == ["A lot", "Not much"]
    assert "Source variable q1" in imported[0]["source"]["note"]
    converted_respondents = [
        json.loads(line) for line in (tmp_path / "converted" / "respondents.jsonl").read_text().splitlines()
    ]
    assert converted_respondents[0]["metadata"] == {"party": "Democrat"}
    assert converted_respondents[0]["weight"] == 1.5


@pytest.mark.parametrize(
    ("column", "bad_value", "error"),
    [("q1", "7", "unknown_answer_code"), ("party", "7", "unknown_covariate_code"), ("wt", "bad", "invalid_weight")],
)
def test_ingest_matrix_reports_all_invalid_values_without_writing_import_streams(
    tmp_path: Path, column: str, bad_value: str, error: str
) -> None:
    metadata = {
        "respondent_id": "id", "weight": "wt", "missing_codes": [""],
        "covariates": [{"source": "party", "labels": {"1": "A"}}],
        "items": {"q": {"source_variable": "q1", "option_codes": [1, 2], "option_labels": ["yes", "no"]}},
    }
    metadata_path = tmp_path / "metadata.json"
    matrix_path = tmp_path / "matrix.csv"
    output = tmp_path / "converted"
    metadata_path.write_text(json.dumps(metadata))
    values = {"id": "1", "wt": "1", "party": "1", "q1": "1"}
    values[column] = bad_value
    with matrix_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values))
        writer.writeheader()
        writer.writerow(values)
    with pytest.raises(ZwillError, match="nothing was imported"):
        convert_matrix(metadata_path, matrix_path, output, survey="demo")
    issues = [json.loads(line) for line in (output / "issues.jsonl").read_text().splitlines()]
    assert error in {row["error"] for row in issues}
    assert not (output / "questions.jsonl").exists()
    assert not (output / "respondents.jsonl").exists()
    assert not (output / "answers.jsonl").exists()


def test_ingest_matrix_requires_declared_matrix_columns(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.json"
    matrix_path = tmp_path / "matrix.csv"
    metadata_path.write_text(json.dumps({
        "respondent_id": "id", "weight": "wt",
        "items": {"q": {"source_variable": "missing_q", "option_codes": [1], "option_labels": ["yes"]}},
    }))
    matrix_path.write_text("id,wt\n1,1\n")
    with pytest.raises(ZwillError) as exc:
        convert_matrix(metadata_path, matrix_path, tmp_path / "converted", survey="demo")
    assert exc.value.context["columns"] == ["missing_q"]


def test_study_expand_creates_one_draft_plan_per_question(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    questions = [
        {"question_name": name, "question_type": "multiple_choice", "question_text": name, "question_options": ["yes", "no"]}
        for name in ("q1", "q2", "q3")
    ]
    (tmp_path / "questions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in questions))
    assert main(["init"]) == 0
    _payload(capsys)
    assert main(["survey", "create", "--name", "demo"]) == 0
    _payload(capsys)
    assert main(["question", "import", "--survey", "demo", "--input-path", str(tmp_path / "questions.jsonl")]) == 0
    _payload(capsys)
    manifest = {"design": {"sample_respondents": 200, "seed": 7, "models": ["openai:gpt-5.5"]},
                "surveys": [{"survey": "demo", "heldout_questions": ["q1", "q2"]}]}
    (tmp_path / "study.json").write_text(json.dumps(manifest))
    assert main(["study", "expand", "--manifest", str(tmp_path / "study.json"), "--out", str(tmp_path / "plans")]) == 0
    data = _payload(capsys)["data"]
    assert data["plan_count"] == 2
    expanded = json.loads((tmp_path / "plans" / "manifest.json").read_text())
    assert [row["heldout_questions"] for row in expanded["plans"]] == [["q1"], ["q2"]]
    assert all(Path(row["path"]).is_absolute() for row in expanded["plans"])
    assert all(json.loads(Path(row["path"]).read_text())["approval"]["status"] == "draft" for row in expanded["plans"])

    first_path = Path(expanded["plans"][0]["path"])
    first = json.loads(first_path.read_text())
    first["approval"] = {"status": "approved", "approved_by": "reviewer"}
    first_path.write_text(json.dumps(first))
    assert main(["study", "expand", "--manifest", str(tmp_path / "study.json"), "--out", str(tmp_path / "plans")]) == 0
    rerun = _payload(capsys)["data"]
    assert rerun["plans"][0]["existing"] is True
    assert json.loads(first_path.read_text())["approval"]["status"] == "approved"


def test_study_prepare_composes_reviewed_artifacts(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(study_commands, "resolve_output_path", lambda value: Path(value))
    monkeypatch.setattr(study_commands, "cmd_study_expand", lambda args: {
        "data": {"manifest": str(tmp_path / "run" / "plans" / "manifest.json"), "plan_count": 2}
    })
    monkeypatch.setattr(study_commands, "cmd_study_approve", lambda args: {
        "data": {"plan_count": 2, "reviewer": args.approved_by}
    })
    def export(args):
        calls.append(args)
        return {"data": {"path": str(tmp_path / "run" / "jobs" / "run-manifest.json"),
                         "job_count": 2, "prediction_count": 400}}
    monkeypatch.setattr(study_commands, "cmd_study_export", export)
    result = study_commands.cmd_study_prepare(argparse.Namespace(
        manifest="study.json", out=str(tmp_path / "run"), results_dir="results",
        approved_by="reviewer", note="reviewed", mode="by-question", plan_suffix="v1",
        approach_id=None, sample_respondents=200, seed=7, context_question_count=8,
        comparison=None, survey=None, model=None,
    ))
    assert result["data"]["prediction_count"] == 400
    assert result["data"]["approval"]["reviewer"] == "reviewer"
    assert calls[0].results_dir == "results"
    assert result["next_steps"] == [f"ep run-manifest {result['data']['run_manifest']} --execute"]


def test_study_finish_stops_before_audit_when_results_are_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(study_commands, "cmd_study_import", lambda args: {
        "status": "warning", "data": {"missing_jobs": ["job-1"]}
    })
    monkeypatch.setattr(study_commands, "cmd_study_audit", lambda args: pytest.fail("audit must not run"))
    args = argparse.Namespace(manifest="runs.json", results_dir="results", replace=True,
                              audit_out="audits", out="reports/{survey}", include_manifest=None,
                              embedder="hashing", embedding_model="model", env_path=None,
                              seed=0, n_boot=10, ci=.95, probability_jobs=None)
    with pytest.raises(ZwillError, match="incomplete"):
        study_commands.cmd_study_finish(args)


def test_study_finish_composes_import_audit_and_validation(monkeypatch) -> None:
    monkeypatch.setattr(study_commands, "cmd_study_import", lambda args: {
        "status": "ok", "data": {"import_count": 2}
    })
    monkeypatch.setattr(study_commands, "cmd_study_audit", lambda args: {
        "data": {"report_count": 2, "path": args.out}
    })
    monkeypatch.setattr(study_commands, "cmd_study_validate", lambda args, embedder=None: {
        "data": {"survey_count": 1, "path": args.out}
    })
    args = argparse.Namespace(manifest="runs.json", results_dir="results", replace=True,
                              audit_out="audits", out="reports/{survey}", include_manifest=None,
                              embedder="hashing", embedding_model="model", env_path=None,
                              seed=0, n_boot=10, ci=.95, probability_jobs=None)
    result = study_commands.cmd_study_finish(args)
    assert result["status"] == "ok"
    assert result["data"]["import"]["import_count"] == 2
    assert result["data"]["audit"]["report_count"] == 2


def test_study_expand_simultaneous_filters_surveys_and_carries_leakage_exclusions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    questions = [
        {"question_name": name, "question_type": "multiple_choice", "question_text": name, "question_options": ["yes", "no"]}
        for name in ("q1", "q2", "followup")
    ]
    (tmp_path / "questions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in questions))
    assert main(["init"]) == 0
    _payload(capsys)
    for survey in ("keep", "skip"):
        assert main(["survey", "create", "--name", survey]) == 0
        _payload(capsys)
        assert main(["question", "import", "--survey", survey, "--input-path", str(tmp_path / "questions.jsonl")]) == 0
        _payload(capsys)
    manifest = {
        "design": {"models": ["openai:gpt-5.5"]},
        "surveys": [
            {"survey": "keep", "plan_id": "keep_existing_id", "heldout_questions": ["q1", "q2"],
             "by_question_plan_ids": {"q1": "keep_q1_historical"},
             "leakage_exclusions": {"q1": ["followup"]}},
            {"survey": "skip", "heldout_questions": ["q1"]},
        ],
    }
    (tmp_path / "study.json").write_text(json.dumps(manifest))
    assert main([
        "study", "expand", "--manifest", str(tmp_path / "study.json"), "--mode", "simultaneous",
        "--survey", "keep", "--out", str(tmp_path / "plans"),
    ]) == 0
    plan_record = _payload(capsys)["data"]["plans"][0]
    assert plan_record["plan_id"] == "keep_existing_id"
    assert plan_record["heldout_questions"] == ["q1", "q2"]
    plan = json.loads(Path(plan_record["path"]).read_text())
    assert "q1:followup" in plan["defaults"]["leakage_exclusion"]

    assert main([
        "study", "expand", "--manifest", str(tmp_path / "study.json"), "--mode", "by-question",
        "--survey", "keep", "--out", str(tmp_path / "by-question"),
    ]) == 0
    by_question = _payload(capsys)["data"]["plans"]
    assert by_question[0]["plan_id"] == "keep_q1_historical"
    assert by_question[1]["plan_id"].startswith("keep_byq_q2_")


def test_study_expand_named_comparison_builds_model_question_matrix(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    questions = [{"question_name": q, "question_type": "multiple_choice", "question_text": q,
                  "question_options": ["yes", "no"]} for q in ("q1", "q2")]
    (tmp_path / "questions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in questions))
    assert main(["init"]) == 0
    _payload(capsys)
    assert main(["survey", "create", "--name", "demo"]) == 0
    _payload(capsys)
    assert main(["question", "import", "--survey", "demo", "--input-path", str(tmp_path / "questions.jsonl")]) == 0
    _payload(capsys)
    manifest = {
        "design": {"sample_respondents": 200}, "surveys": [{"survey": "demo", "heldout_questions": ["q1", "q2"]}],
        "comparisons": {"providers": {
            "mode": "by-question", "surveys": ["demo"],
            "plan_id_template": "{survey}_model_{model_slug}_{question}_v2",
            "models": [{"model": "anthropic:claude", "approach_id": "claude_context"},
                       {"model": "google:gemini", "approach_id": "gemini_context"}],
        }},
    }
    (tmp_path / "study.json").write_text(json.dumps(manifest))
    assert main(["study", "expand", "--manifest", str(tmp_path / "study.json"), "--comparison", "providers",
                 "--out", str(tmp_path / "plans")]) == 0
    plans = _payload(capsys)["data"]["plans"]
    assert len(plans) == 4
    assert {tuple(row["models"]) for row in plans} == {("anthropic:claude",), ("google:gemini",)}
    assert all(row["comparison"] == "providers" for row in plans)
    first = json.loads(Path(plans[0]["path"]).read_text())
    assert first["arms"][0]["approach_id"] in {"claude_context", "gemini_context"}


def test_study_summarize_writes_tidy_explicit_benchmarks(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "reports" / "demo" / "validation"
    bundle.mkdir(parents=True)
    report = {
        "design": {"respondent_count": 200},
        "skill_scores": {"openai:gpt-5.5": {"macro": {"questions": 1, "mean_nll": .8, "uniform_mean_nll": 1.1,
            "marginal_mean_nll": .9, "nll_delta_vs_uniform": -.3, "nll_delta_vs_marginal": -.1},
            "questions": {"q1": {"mean_nll": .8, "uniform_mean_nll": 1.1, "marginal_mean_nll": .9}}}},
        "marginal_options": [{"model_label": "openai:gpt-5.5", "heldout_question": "q1", "option_label": "yes",
                              "predicted_probability": .6, "target_probability": .55}],
    }
    (bundle / "report_data.json").write_text(json.dumps(report))
    (bundle / "bootstrap.json").write_text(json.dumps({"deltas_vs_baseline": {"models": {"openai:gpt-5.5": {
        "macro": {"negative_log_likelihood": {"delta": -.02, "lo": -.04, "hi": .01}},
        "questions": {"q1": {"negative_log_likelihood": {"delta": -.02, "lo": -.05, "hi": .02}}}}}}}))
    (bundle / "leakage_audit.json").write_text(json.dumps({"flagged_count": 0}))
    (tmp_path / "study.json").write_text(json.dumps({"surveys": [{
        "survey": "demo", "source_id": "source", "exclude_from_claims": True, "exclusion_reason": "audit flag",
    }]}))
    assert main(["study", "summarize", "--manifest", str(tmp_path / "study.json"),
                 "--bundle-template", "reports/{survey}/validation", "--out", str(tmp_path / "summary")]) == 0
    data = _payload(capsys)["data"]
    assert data["survey_count"] == 1
    rows = list(csv.DictReader((tmp_path / "summary" / "surveys.csv").open()))
    assert float(rows[0]["uniform_mean_nll"]) == 1.1
    assert float(rows[0]["nll_delta_vs_conditional_lo"]) == -.04
    assert rows[0]["conditional_gate"] == "inconclusive"
    assert rows[0]["excluded_from_claims"] == "True"
    aggregates = json.loads((tmp_path / "summary" / "aggregates.json").read_text())
    model = aggregates["models"]["openai:gpt-5.5"]
    assert model["benchmarks"]["uniform"]["questions_better"] == 1
    assert model["conditional_gate_counts"]["inconclusive"] == 1


def test_study_summarize_normalizes_legacy_scores_and_exports_one_shot_and_marginals(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "reports" / "demo" / "validation"
    bundle.mkdir(parents=True)
    report = {
        "skill_scores": {"model": {
            "macro": {"questions": 1, "mean_nll": 0.8, "nll_skill_vs_uniform": 0.2, "nll_skill_vs_marginal": -0.6},
            "questions": {"q1": {"mean_nll": 0.8, "nll_skill_vs_uniform": 0.2, "nll_skill_vs_marginal": -0.6}},
        }},
        "marginals": [{"model_label": "model", "heldout_question": "q1", "total_variation": 0.1}],
        "marginal_options": [{"model_label": "model", "heldout_question": "q1", "option_label": "yes"}],
        "calibration": {"expected_calibration_error": {"model": 0.04}},
    }
    (bundle / "report_data.json").write_text(json.dumps(report))
    (bundle / "bootstrap-one-shot.json").write_text(json.dumps({"deltas_vs_baseline": {"models": {"model": {
        "macro": {"negative_log_likelihood": {"delta": -0.1, "lo": -0.2, "hi": 0.0}},
        "questions": {"q1": {"negative_log_likelihood": {"delta": -0.1, "lo": -0.25, "hi": 0.05}}},
    }}}}))
    (tmp_path / "study.json").write_text(json.dumps({"surveys": [{"survey": "demo"}, {"survey": "missing"}]}))
    assert main([
        "study", "summarize", "--manifest", str(tmp_path / "study.json"),
        "--bundle-template", "reports/{survey}/validation", "--out", str(tmp_path / "summary"), "--allow-missing",
    ]) == 0
    data = _payload(capsys)["data"]
    survey = next(csv.DictReader((tmp_path / "summary" / "surveys.csv").open()))
    assert float(survey["uniform_mean_nll"]) == pytest.approx(1.0)
    assert float(survey["marginal_mean_nll"]) == pytest.approx(0.5)
    assert float(survey["nll_delta_vs_uniform"]) == pytest.approx(-0.2)
    assert float(survey["nll_delta_vs_one_shot_lo"]) == pytest.approx(-0.2)
    assert float(survey["expected_calibration_error"]) == pytest.approx(0.04)
    assert data["marginal_rows"] == 1 and data["option_rows"] == 1
    assert len(data["missing_bundles"]) == 1
    assert len(list(csv.DictReader((tmp_path / "summary" / "marginals.csv").open()))) == 1


def test_study_summarize_rejects_missing_bundle_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "study.json").write_text(json.dumps({"surveys": ["missing"]}))
    with pytest.raises(ZwillError, match="validation bundles are missing"):
        study_commands.cmd_study_summarize(argparse.Namespace(
            manifest=str(tmp_path / "study.json"), bundle_template="reports/{survey}",
            out=str(tmp_path / "summary"), allow_missing=False,
        ))


def test_study_costs_rolls_up_result_packages(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.ep"
    second = tmp_path / "second.ep"
    first.touch()
    second.touch()
    monkeypatch.setattr(study_commands, "read_edsl_results", lambda path: {
        "data": [{"model": {"model": path.stem}, "raw_model_response": {"q_cost": 1.25}}]
    })
    result = study_commands.cmd_study_costs(argparse.Namespace(result=[str(tmp_path / "*.ep")], manifest=None, path=None))
    assert result["data"]["file_count"] == 2
    assert result["data"]["total_usd"] == 2.5
    assert result["data"]["call_count"] == 2


def test_study_costs_reads_manifest_deduplicates_paths_and_groups_models(tmp_path: Path, monkeypatch) -> None:
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    result_path = result_dir / "one.ep"
    result_path.touch()
    manifest = tmp_path / "study.json"
    manifest.write_text(json.dumps({"result_paths": ["results/*.ep", "results/one.ep"]}))
    monkeypatch.setattr(study_commands, "read_edsl_results", lambda _path: {
        "data": [
            {"model": {"model": "a"}, "raw_model_response": {"q_cost": 1.0}},
            {"model": {"model": "b"}, "raw_model_response": {"q_cost": 2.0}},
        ]
    })
    result = study_commands.cmd_study_costs(argparse.Namespace(result=[], manifest=str(manifest), path=None))
    assert result["data"]["file_count"] == 1
    assert result["data"]["total_usd"] == 3.0
    assert {row["model"] for row in result["data"]["by_model"]} == {"a", "b"}


def test_study_costs_rejects_empty_glob(tmp_path: Path) -> None:
    with pytest.raises(ZwillError, match="No Results packages matched"):
        study_commands.cmd_study_costs(argparse.Namespace(result=[str(tmp_path / "*.ep")], manifest=None, path=None))


def test_study_export_writes_run_manifest_and_ep_commands(tmp_path: Path, monkeypatch) -> None:
    import zwill.twin_experiments as experiments

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"plan_id": "p1", "survey": "demo", "approval": {"approved": True}}))
    expanded = tmp_path / "expanded.json"
    expanded.write_text(json.dumps({"plans": [{"plan_id": "p1", "path": str(plan)}]}))
    monkeypatch.setattr(experiments, "cmd_twin_experiment_validate", lambda _args: {"status": "ok"})
    monkeypatch.setattr(experiments, "cmd_twin_experiment_export_plan", lambda args: {
        "data": {"survey": "demo", "exports": [{"job_id": "job1", "job_path": str(Path(args.output_dir) / "a_jobs.ep"),
                                                    "prediction_count_exported": 200}]}
    })
    result = study_commands.cmd_study_export(argparse.Namespace(
        manifest=str(expanded), out=str(tmp_path / "run"), results_dir="results",
    ))
    run = result["data"]["runs"][0]
    assert run["ep_command"].endswith("--output results/p1/a_results.ep")
    assert result["data"]["prediction_count"] == 200
    assert (tmp_path / "run" / "run-manifest.json").exists()


def test_study_import_groups_jobs_by_plan(tmp_path: Path, monkeypatch) -> None:
    import zwill.twin_experiments as experiments

    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps({"runs": [
        {"plan_id": "p1", "survey": "s", "export_manifest": "exports/p1/manifest.json", "job_id": "j1"},
        {"plan_id": "p1", "survey": "s", "export_manifest": "exports/p1/manifest.json", "job_id": "j2"},
    ]}))
    calls = []
    monkeypatch.setattr(experiments, "cmd_twin_experiment_import_plan_results", lambda args: (
        calls.append(args) or {"data": {"import_count": 2, "missing_jobs": []}}
    ))
    result = study_commands.cmd_study_import(argparse.Namespace(
        manifest=str(manifest), results_dir=str(tmp_path / "results"), replace=True,
    ))
    assert len(calls) == 1
    assert calls[0].results_dir == str(tmp_path / "results" / "p1")
    assert result["data"]["import_count"] == 2


def test_study_audit_renders_each_job(tmp_path: Path, monkeypatch) -> None:
    import zwill.twin_validation_commands as validation

    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps({"runs": [
        {"plan_id": "p1", "survey": "s", "job_id": "j1"},
        {"plan_id": "p2", "survey": "s", "job_id": "j2"},
    ]}))
    paths = []
    monkeypatch.setattr(validation, "cmd_twin_results_run_report", lambda args: (
        paths.append(args.path) or {"data": {"issue_count": 0}}
    ))
    result = study_commands.cmd_study_audit(argparse.Namespace(manifest=str(manifest), out=str(tmp_path / "audits")))
    assert result["data"]["report_count"] == 2
    assert paths == [str(tmp_path / "audits" / "s" / "j1.html"), str(tmp_path / "audits" / "s" / "j2.html")]


def test_study_validate_groups_jobs_and_attaches_probability_baselines(tmp_path: Path, monkeypatch) -> None:
    import zwill.twin_validation_workflow as workflow

    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps({"runs": [
        {"plan_id": "p1", "survey": "s", "job_id": "j1"},
        {"plan_id": "p2", "survey": "s", "job_id": "j2"},
    ]}))
    probability = tmp_path / "probability.json"
    probability.write_text(json.dumps({"s": "prior1"}))
    calls = []
    monkeypatch.setattr(workflow, "cmd_twin_validate", lambda args, embedder=None: (
        calls.append(args) or {"data": {"manifest_path": str(Path(args.out) / "manifest.json")}}
    ))
    result = study_commands.cmd_study_validate(argparse.Namespace(
        manifest=str(manifest), out=str(tmp_path / "validation"), embedder="hashing",
        embedding_model="fake", env_path=None, seed=7, n_boot=100, ci=.95,
        probability_jobs=str(probability),
    ))
    assert result["data"]["survey_count"] == 1
    assert calls[0].jobs == "j1,j2"
    assert calls[0].probability_baseline_job_id == "prior1"


def test_study_one_shot_build_creates_independent_survey_jobs(tmp_path: Path, monkeypatch) -> None:
    import zwill.edsl_integration as edsl

    manifest = tmp_path / "study.json"
    manifest.write_text(json.dumps({
        "design": {"models": ["openai:m"]},
        "surveys": [{"survey": "a", "heldout_questions": ["q1", "q2"]}, {"survey": "b", "questions": ["q3"]}],
    }))
    calls = []
    monkeypatch.setattr(edsl, "cmd_edsl_build", lambda args: (
        calls.append(args) or {"data": {"path": args.path, "scenario_count": len(args.questions.split(",")), "model_count": 1}}
    ))
    result = study_commands.cmd_study_one_shot_build(argparse.Namespace(
        manifest=str(manifest), out=str(tmp_path / "jobs"), model=None, model_param=None,
    ))
    assert result["data"]["survey_count"] == 2
    assert calls[0].questions == "q1,q2"
    assert calls[1].model == ["openai:m"]
    assert result["data"]["runs"][0]["result_path"] == "results/a/one_shot_results.ep"


def test_study_one_shot_import_writes_probability_job_mapping(tmp_path: Path, monkeypatch) -> None:
    import zwill.probability_commands as probability

    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps({"runs": [{"survey": "a"}, {"survey": "b"}]}))
    monkeypatch.setattr(probability, "cmd_probability_results_import", lambda args: {
        "data": {"job_id": f"prior-{args.survey}", "issue_count": 0}
    })
    result = study_commands.cmd_study_one_shot_import(argparse.Namespace(
        manifest=str(manifest), results_dir=str(tmp_path / "results"), path=str(tmp_path / "mapping.json"), replace=True,
    ))
    assert result["data"]["probability_jobs"] == {"a": "prior-a", "b": "prior-b"}
    assert json.loads((tmp_path / "mapping.json").read_text()) == {"a": "prior-a", "b": "prior-b"}


def test_study_plot_renders_reusable_svg_artifacts(tmp_path: Path) -> None:
    summary = tmp_path / "summary"
    summary.mkdir()
    study_commands._write_csv(summary / "surveys.csv", [{
        "survey": "s", "source_id": "Source", "model": "model", "mean_nll": .7,
        "nll_delta_vs_conditional": -.1, "nll_delta_vs_conditional_lo": -.2,
        "nll_delta_vs_conditional_hi": -.01, "expected_calibration_error": .05,
    }])
    study_commands._write_csv(summary / "questions.csv", [{
        "survey": "s", "question": "q", "model": "model", "nll_delta_vs_uniform": -.2,
        "nll_delta_vs_one_shot": -.1, "nll_delta_vs_one_shot_lo": -.15, "nll_delta_vs_one_shot_hi": -.02,
    }])
    study_commands._write_csv(summary / "options.csv", [{
        "survey": "s", "model_label": "model", "target_probability": .4, "predicted_probability": .5,
    }])
    result = study_commands.cmd_study_plot(argparse.Namespace(
        summary_dir=str(summary), out=str(tmp_path / "plots"), model="model", kind=None,
    ))
    assert len(result["data"]["artifacts"]) == 6
    for artifact in result["data"]["artifacts"]:
        assert Path(artifact["path"]).read_text().startswith("<svg")


def test_study_ingest_applies_metadata_overlay_to_clean_batch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    metadata = {"respondent_id": "id", "weight": "wt", "items": {
        "q": {"source_variable": "q", "option_codes": [1, 2], "option_labels": ["yes", "no"]},
    }}
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    (tmp_path / "matrix.csv").write_text("id,wt,q,party\n1,1,1,1\n")
    manifest = {"surveys": [{"survey": "demo", "matrix": {
        "metadata": "metadata.json", "respondents": "matrix.csv",
        "metadata_overlay": {"covariates": [{"source": "party", "name": "party_label", "labels": {"1": "A"}}]},
    }}]}
    (tmp_path / "study.json").write_text(json.dumps(manifest))
    result = study_commands.cmd_study_ingest(argparse.Namespace(
        manifest=str(tmp_path / "study.json"), out=str(tmp_path / "imports"), apply=False,
    ))
    assert result["data"]["survey_count"] == 1
    respondent = json.loads((tmp_path / "imports" / "demo" / "respondents.jsonl").read_text())
    assert respondent["metadata"] == {"party_label": "A"}
    merged = json.loads((tmp_path / "imports" / "demo" / "conversion_metadata.json").read_text())
    assert merged["covariates"][0]["name"] == "party_label"


def test_study_ingest_supports_templates_and_shared_covariate_codebook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "release_metadata.json").write_text(json.dumps({
        "battery": "battery", "respondent_id": "id", "weight": "wt", "covariates": ["party"],
        "items": {"a": {"source_variable": "Q1", "option_codes": [1], "option_labels": ["yes"]}},
    }))
    (source / "release_respondents.csv").write_text("id,wt,item_a,party\n1,1,1,1\n")
    (tmp_path / "codebook.json").write_text(json.dumps({"party": {"name": "party_label", "labels": {"1": "A"}}}))
    (tmp_path / "study.json").write_text(json.dumps({
        "matrix_defaults": {
            "metadata": "source/{source_id}_metadata.json", "respondents": "source/{source_id}_respondents.csv",
            "covariate_codebook": "codebook.json",
            "metadata_overlay": {"data_variable_template": "item_{item}", "question_name_template": "{battery}_{item}"},
        },
        "surveys": [{"survey": "demo", "source_id": "release"}],
    }))
    study_commands.cmd_study_ingest(argparse.Namespace(
        manifest=str(tmp_path / "study.json"), out=str(tmp_path / "imports"), apply=False,
    ))
    question = json.loads((tmp_path / "imports" / "demo" / "questions.jsonl").read_text())
    respondent = json.loads((tmp_path / "imports" / "demo" / "respondents.jsonl").read_text())
    assert question["question_name"] == "battery_a"
    assert respondent["metadata"] == {"party_label": "A"}


def test_study_ingest_does_not_apply_any_survey_when_later_conversion_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    metadata = {"respondent_id": "id", "weight": "wt", "items": {
        "q": {"source_variable": "q", "option_codes": [1], "option_labels": ["yes"]},
    }}
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    (tmp_path / "good.csv").write_text("id,wt,q\n1,1,1\n")
    (tmp_path / "bad.csv").write_text("id,wt,q\n1,1,7\n")
    (tmp_path / "study.json").write_text(json.dumps({"surveys": [
        {"survey": "good", "matrix": {"metadata": "metadata.json", "respondents": "good.csv"}},
        {"survey": "bad", "matrix": {"metadata": "metadata.json", "respondents": "bad.csv"}},
    ]}))
    with pytest.raises(ZwillError):
        study_commands.cmd_study_ingest(argparse.Namespace(
            manifest=str(tmp_path / "study.json"), out=str(tmp_path / "imports"), apply=True,
        ))
    assert not (tmp_path / ".zwill").exists()


def test_study_approve_validates_all_plans_and_preserves_existing_approval(tmp_path: Path, monkeypatch) -> None:
    import zwill.twin_experiments as experiments

    draft = tmp_path / "draft.json"
    approved = tmp_path / "approved.json"
    draft.write_text(json.dumps({"plan_id": "draft", "approval": {"approved": False}}))
    approved.write_text(json.dumps({"plan_id": "approved", "approved": True, "approval": {"approved": True}}))
    manifest = tmp_path / "expanded.json"
    manifest.write_text(json.dumps({"plans": [
        {"plan_id": "draft", "path": str(draft)}, {"plan_id": "approved", "path": str(approved)},
    ]}))
    validated = []
    monkeypatch.setattr(
        experiments,
        "cmd_twin_experiment_validate",
        lambda args: validated.append((args.input_path, args.plan_id)) or {},
    )
    monkeypatch.setattr(experiments, "cmd_twin_experiment_approve", lambda args: {
        "data": {"approval": {"approved": True, "approved_by": args.approved_by}}
    })
    result = study_commands.cmd_study_approve(argparse.Namespace(
        manifest=str(manifest), approved_by="Reviewer", note="Reviewed",
    ))
    assert len(validated) == 2
    assert validated[0] == (str(draft), None)
    assert result["data"]["plans"][0]["approval"]["approved_by"] == "Reviewer"
    assert result["data"]["plans"][1]["existing"] is True
