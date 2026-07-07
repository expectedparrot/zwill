from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import zwill.cli as cli
from zwill.cli import build_twin_report, main
from zwill.generated_reports import compact_twin_specific_diagnostics_for_report

FIXTURES = Path(__file__).parent / "fixtures"
DEFAULT_EDSL_TEST_PYTHON = Path("/Users/johnhorton/tools/ep/edsl/.venv/bin/python")


def zwill_project_path(base: Path, project: str = "default") -> Path:
    return base / ".zwill" / "projects" / project


def zwill_survey_path(base: Path, survey: str = "demo", project: str = "default") -> Path:
    return zwill_project_path(base, project) / "surveys" / survey


class FakeAgent:
    def __init__(
        self,
        name: str,
        traits: dict,
        codebook: dict | None = None,
        instruction: str | None = None,
        traits_presentation_template: str | None = None,
    ) -> None:
        self.name = name
        self.traits = traits
        self.codebook = codebook or {}
        self.instruction = instruction
        self.traits_presentation_template = traits_presentation_template


class FakeAgentList:
    def __init__(self, agents: list[FakeAgent]) -> None:
        self.agents = agents

    @staticmethod
    def from_dict(data: dict) -> "FakeAgentList":
        return FakeAgentList(
            [
                FakeAgent(
                    name=agent.get("name"),
                    traits=agent.get("traits", {}),
                    codebook=agent.get("codebook", {}),
                    instruction=agent.get("instruction"),
                    traits_presentation_template=agent.get("traits_presentation_template") or data.get("traits_presentation_template"),
                )
                for agent in data.get("agent_list", [])
            ]
        )

    def to_dict(self) -> dict:
        rows = []
        for agent in self.agents:
            row = {"name": agent.name, "traits": agent.traits, "codebook": agent.codebook}
            if agent.instruction:
                row["instruction"] = agent.instruction
            if agent.traits_presentation_template:
                row["traits_presentation_template"] = agent.traits_presentation_template
            rows.append(row)
        return {"agent_list": rows, "edsl_class_name": "AgentList"}


class FakeQuestionFreeText:
    def __init__(self, question_name: str, question_text: str) -> None:
        self.question_name = question_name
        self.question_text = question_text


class FakeQuestion:
    def __init__(self, question_type: str, **kwargs) -> None:
        self.question_type = question_type
        self.question_name = kwargs["question_name"]
        self.question_text = kwargs["question_text"]
        self.question_options = kwargs.get("question_options", [])


class FakeScenario(dict):
    pass


class FakeScenarioList(list):
    pass


class FakeSurvey:
    def __init__(self, questions: list | None = None) -> None:
        self.questions = questions or []

    def add_question(self, question) -> None:
        self.questions.append(question)

    def to_dict(self) -> dict:
        return {
            "edsl_class_name": "Survey",
            "questions": [
                {
                    "question_name": question.question_name,
                    "question_text": question.question_text,
                    "question_type": getattr(question, "question_type", "free_text"),
                    "question_options": getattr(question, "question_options", []),
                }
                for question in self.questions
            ],
        }


class FakeModel:
    def __init__(self, model_name: str, service_name: str | None = None, **parameters) -> None:
        self.model_name = model_name
        self.service_name = service_name
        self.parameters = parameters


class FakeModelList(list):
    pass


class FakeJobs:
    def __init__(
        self,
        survey: FakeSurvey,
        scenarios: FakeScenarioList | None = None,
        models: FakeModelList | None = None,
        agents: FakeAgentList | None = None,
    ) -> None:
        self.survey = survey
        self.scenarios = scenarios or FakeScenarioList()
        self.models = models or FakeModelList()
        self.agents = agents or FakeAgentList([])

    def to_dict(self) -> dict:
        return {
            "edsl_class_name": "Jobs",
            "survey": {
                "questions": [
                    {
                        "question_name": question.question_name,
                        "question_text": question.question_text,
                        "question_type": getattr(question, "question_type", "free_text"),
                        "question_options": getattr(question, "question_options", []),
                    }
                    for question in self.survey.questions
                ]
            },
            "agents": self.agents.to_dict()["agent_list"],
            "scenarios": list(self.scenarios),
            "models": [
                {
                    "model": model.model_name,
                    "inference_service": model.service_name,
                    "parameters": model.parameters,
                }
                for model in self.models
            ],
        }


def run_cli(*args: str) -> dict:
    rc = main(list(args))
    assert rc == 0
    return {}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))


def default_twin_export_args(**overrides) -> argparse.Namespace:
    values = {
        "heldout_question": ["q1"],
        "heldout_questions": None,
        "balance_actual": False,
        "stratify_actual": False,
        "context_question": None,
        "context_questions": None,
        "exclude_context_question": None,
        "respondent": ["r1"],
        "respondents": None,
        "sample_respondents": None,
        "seed": None,
        "complete_cases": False,
        "limit_respondents": None,
        "context_question_count": 1,
        "model": ["openai:gpt-5.5"],
        "models": None,
        "service_name": None,
        "model_param": None,
        "job_question_name": "response_probabilities",
        "include_agent_material": False,
        "agent_material_kind": None,
        "agent_material_tag": None,
        "max_agent_material_chars": None,
        "twin_material": None,
        "max_twin_material_chars": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def default_agent_list_export_args(**overrides) -> argparse.Namespace:
    values = {
        "question": None,
        "questions": None,
        "exclude_question": None,
        "limit": None,
        "include_survey_context": False,
        "include_agent_material": False,
        "agent_material_kind": None,
        "agent_material_tag": None,
        "max_agent_material_chars": None,
        "traits_presentation_template": None,
        "traits_presentation_template_path": None,
        "no_default_traits_presentation_template": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_installed_skills_are_discoverable(capsys) -> None:
    rc = main(["skills", "path", "digital-twin-practitioner-report"])
    assert rc == 0
    skill_path = Path(capsys.readouterr().out.strip())
    assert skill_path.name == "digital-twin-practitioner-report"
    assert (skill_path / "SKILL.md").exists()
    assert (skill_path / "agents" / "openai.yaml").exists()

    rc = main(["skills", "list", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = {row["name"] for row in payload["data"]["skills"]}
    assert {"digital-twin-study-runner", "digital-twin-practitioner-report"} <= names


def test_workflow_explain_and_run_capture_outputs(tmp_path: Path, capsys) -> None:
    workflow_path = tmp_path / "workflow.json"
    artifacts_dir = tmp_path / "artifacts"
    workflow_path.write_text(
        json.dumps(
            {
                "name": "hello-flow",
                "description": "Tiny workflow test.",
                "vars": {"who": "world"},
                "steps": [
                    {
                        "id": "hello",
                        "name": "Say hello",
                        "run": sys.executable + " -c \"print('hello {{ who }}')\"",
                    },
                    {
                        "id": "stderr",
                        "run": f"{sys.executable} -c \"import sys; print('note', file=sys.stderr)\"",
                    },
                ],
            }
        )
    )

    rc = main(["workflow", "explain", str(workflow_path), "--var", "who=agent"])
    assert rc == 0
    explained = json.loads(capsys.readouterr().out)
    assert explained["data"]["steps"][0]["run"].endswith("print('hello agent')\"")

    rc = main(["workflow", "run", str(workflow_path), "--var", "who=agent", "--artifacts-dir", str(artifacts_dir)])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["manifest_path"] == str(artifacts_dir / "manifest.json")
    manifest = json.loads((artifacts_dir / "manifest.json").read_text())
    assert manifest["status"] == "ok"
    assert len(manifest["steps"]) == 2
    assert Path(manifest["steps"][0]["stdout_path"]).read_text().strip() == "hello agent"
    assert Path(manifest["steps"][1]["stderr_path"]).read_text().strip() == "note"


def test_workflow_run_failure_writes_manifest(tmp_path: Path, capsys) -> None:
    workflow_path = tmp_path / "bad_workflow.json"
    artifacts_dir = tmp_path / "bad_artifacts"
    workflow_path.write_text(
        json.dumps(
            {
                "name": "bad-flow",
                "steps": [
                    {"id": "ok", "run": f"{sys.executable} -c \"print('before')\""},
                    {"id": "fail", "run": f"{sys.executable} -c \"import sys; print('boom', file=sys.stderr); sys.exit(3)\""},
                    {"id": "after", "run": f"{sys.executable} -c \"print('after')\""},
                ],
            }
        )
    )

    rc = main(["workflow", "run", str(workflow_path), "--artifacts-dir", str(artifacts_dir)])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "workflow_step_failed"
    manifest = json.loads((artifacts_dir / "manifest.json").read_text())
    assert manifest["status"] == "error"
    assert [step["id"] for step in manifest["steps"]] == ["ok", "fail"]
    assert Path(manifest["steps"][1]["stderr_path"]).read_text().strip() == "boom"


def test_init_creates_default_project_and_head(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = cli.cmd_init(argparse.Namespace())

    assert result["data"]["active_project"] == "default"
    assert (tmp_path / ".zwill" / "HEAD").read_text().strip() == "default"
    assert (zwill_project_path(tmp_path) / "project.json").exists()
    assert json.loads((zwill_project_path(tmp_path) / "surveys.json").read_text()) == []


def test_project_create_use_and_survey_isolation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    run_cli("survey", "create", "--name", "default_survey")

    create_result = cli.cmd_project_create(argparse.Namespace(project_id="client_a", title="Client A", use=True))
    assert create_result["data"]["project"]["active"] is True
    run_cli("survey", "create", "--name", "client_survey")

    default_surveys = json.loads((zwill_project_path(tmp_path) / "surveys.json").read_text())
    client_surveys = json.loads((zwill_project_path(tmp_path, "client_a") / "surveys.json").read_text())
    assert [row["name"] for row in default_surveys] == ["default_survey"]
    assert [row["name"] for row in client_surveys] == ["client_survey"]

    status = cli.cmd_status(argparse.Namespace())
    assert status["data"]["project"] == "client_a"
    assert [row["name"] for row in status["data"]["surveys"]] == ["client_survey"]

    cli.cmd_project_use(argparse.Namespace(project_id="default"))
    current = cli.cmd_project_current(argparse.Namespace())
    assert current["data"]["project"]["project_id"] == "default"


def create_tiny_binary_survey() -> None:
    run_cli("init")
    run_cli("survey", "create", "--name", "demo")
    run_cli(
        "question",
        "add",
        "--survey",
        "demo",
        "--question-name",
        "q1",
        "--question-type",
        "multiple_choice",
        "--question-text",
        "Pick one",
        "--question-option",
        "yes",
        "--question-option",
        "no",
    )
    run_cli(
        "question",
        "add",
        "--survey",
        "demo",
        "--question-name",
        "q2",
        "--question-type",
        "multiple_choice",
        "--question-text",
        "Pick another",
        "--question-option",
        "left",
        "--question-option",
        "right",
    )
    for respondent_id, q1, q2 in [("r1", "yes", "left"), ("r2", "no", "right")]:
        run_cli("respondent", "add", "--survey", "demo", "--respondent-id", respondent_id)
        run_cli("answer", "add", "--survey", "demo", "--respondent-id", respondent_id, "--question", "q1", "--answer", q1)
        run_cli("answer", "add", "--survey", "demo", "--respondent-id", respondent_id, "--question", "q2", "--answer", q2)
    run_cli("commit", "--survey", "demo")


def test_agent_material_is_stored_and_kept_out_of_table_and_marginals(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()

    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Favorite color",
        "--text",
        "The respondent's favorite color is blue.",
        "--tag",
        "preference",
    )
    payload = cli.cmd_agent_material_list(
        argparse.Namespace(survey="demo", respondent_id="r1", agent_material_kind=None, agent_material_tag=None)
    )
    assert payload["data"]["material_count"] == 1
    assert payload["data"]["materials"][0]["title"] == "Favorite color"

    capsys.readouterr()
    run_cli("table", "--survey", "demo")
    assert "Favorite color" not in capsys.readouterr().out

    marginals = cli.compute_marginals(zwill_survey_path(tmp_path))
    assert "_agent_material_markdown" not in marginals


def test_agent_material_excluded_from_survey_and_probability_exports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("context", "set", "--survey", "demo", "--text", "Survey context should remain separate.")
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Sensitive profile",
        "--text",
        "Sensitive profile material must not leak.",
    )
    monkeypatch.setattr(cli, "load_edsl_classes", lambda: (FakeAgent, FakeAgentList, FakeQuestion, FakeSurvey))
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )

    survey = cli.build_edsl_survey_dict("demo")
    probability_job = cli.build_edsl_probability_job_dict(
        "demo",
        argparse.Namespace(
            question=["q1"],
            questions=None,
            exclude_question=None,
            model=["openai:gpt-5.5"],
            models=None,
            service_name=None,
            model_param=None,
            job_question_name="response_probabilities",
        ),
    )

    survey_text = json.dumps(survey)
    probability_text = json.dumps(probability_job)
    assert "Sensitive profile material" not in survey_text
    assert "Sensitive profile material" not in probability_text
    assert "agent_material" not in probability_text


def test_survey_report_command_writes_json_html_and_csv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("commit", "--survey", "demo")

    json_path = tmp_path / "survey_report.json"
    html_path = tmp_path / "survey_report.html"
    csv_base = tmp_path / "survey_report.csv"
    run_cli("survey", "report", "--survey", "demo", "--format", "json", "--path", str(json_path))
    run_cli("survey", "report", "--survey", "demo", "--format", "html", "--path", str(html_path))
    run_cli("survey", "report", "--survey", "demo", "--format", "csv", "--path", str(csv_base))

    payload = json.loads(json_path.read_text())
    assert payload["summary"]["survey"] == "demo"
    assert payload["summary"]["marginal_source"] == "committed"
    assert payload["questions"][0]["question_name"] == "q1"
    assert "survey-report-data" in html_path.read_text()
    assert (tmp_path / "survey_report_questions.csv").exists()
    assert (tmp_path / "survey_report_options.csv").exists()


def test_report_catalog_lists_readiness_and_commands(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("commit", "--survey", "demo")

    catalog_path = tmp_path / "report_catalog.json"
    run_cli("report", "list", "--survey", "demo", "--format", "json", "--path", str(catalog_path))
    catalog = json.loads(catalog_path.read_text())
    reports = {row["report_id"]: row for row in catalog["reports"]}
    assert reports["survey-profile"]["ready"] is True
    assert reports["twin-run"]["ready"] is False
    assert reports["twin-validation"]["ready"] is False
    assert "zwill report build --survey demo" in reports["survey-profile"]["command"]

    first = json.loads((FIXTURES / "twin_results.json").read_text())
    second = json.loads((FIXTURES / "twin_results.json").read_text())
    second["zwill"]["digital_twin_job_id"] = "fixture-twin-2"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps(first))
    second_path.write_text(json.dumps(second))
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(first_path))
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(second_path))

    ready_catalog = cli.build_report_catalog("demo")
    ready_reports = {row["report_id"]: row for row in ready_catalog["reports"]}
    assert ready_reports["twin-run"]["ready"] is True
    assert ready_reports["twin-job-comparison"]["ready"] is True
    assert "--jobs fixture-twin,fixture-twin-2" in ready_reports["twin-job-comparison"]["command"]
    assert ready_reports["twin-validation"]["ready"] is True
    assert "zwill twin-results executive-summary-export --survey demo" in ready_reports["twin-validation"]["command"]

    executive_path = tmp_path / "executive.html"
    run_cli(
        "twin-results",
        "executive-summary",
        "--survey",
        "demo",
        "--job-id",
        "fixture-twin",
        "--path",
        str(executive_path),
        "--permutations",
        "100",
    )
    assert "Digital Twin AgentList Validation" in executive_path.read_text()
    assert (tmp_path / "executive_actual_answer_lift_histogram.svg").exists()
    assert (tmp_path / "executive_pairwise_order_accuracy.svg").exists()
    assert (tmp_path / "executive_individual_predictive_power_permutation.json").exists()


def test_executive_summary_prompt_asks_for_plain_language_use_examples() -> None:
    prompt = cli.build_executive_summary_report_prompt(
        {
            "survey": "demo",
            "executive_diagnostics": {"individual_signal": {"p_value_mean_p_actual": 0.52}},
            "twin_validation": {"row_count": 10, "summary": {}},
        }
    )

    assert "decision makers get a short, shareable executive version first" in prompt
    assert "Can we use digital twins here?" in prompt
    assert "Do not open with \"Yes,\"" in prompt
    assert "Start with a report-style sentence" in prompt
    assert "Avoid leading with terms such as" in prompt
    assert "## What Digital Twins Are" in prompt
    assert "## Bottom-Line Findings" in prompt
    assert "## What The Twins Are Useful For Now" in prompt
    assert "## What The Twins Should Not Be Used For" in prompt
    assert "## Model Comparison" in prompt
    assert "## Twin-Specific Capabilities" in prompt
    assert "## Next Steps" in prompt
    assert "## Risks And Required Checks Before Scaling" in prompt
    assert "## Appendix A: Detailed Metrics" in prompt
    assert "Copy/Paste Prompt Or Command" in prompt
    assert "not a full replacement for respondent-level twins" in prompt
    assert "reusable categories with brief examples from this survey" in prompt
    assert "State each major recommendation once" in prompt


def test_executive_summary_report_uses_section_prompts() -> None:
    sections = cli.build_executive_summary_report_section_prompts(
        {
            "survey": "demo",
            "executive_diagnostics": {"metrics": {"row_count": 10}},
            "twin_validation": {"summary": {}},
            "twin_specific_diagnostics": {},
        }
    )

    names = [section["question_name"] for section in sections]
    assert names == ["executive_decision_markdown", "validation_evidence_markdown", "next_steps_appendix_markdown"]
    assert "If a decision must be made now" in sections[0]["prompt"]
    assert "Do not create another Recommendation section" in sections[2]["prompt"]
    assert "Appendix A: Detailed Metrics" in sections[2]["prompt"]


def test_executive_summary_context_compacts_twin_specific_diagnostics() -> None:
    bulky_distribution = {f"option_{index:03d}": index / 1000 for index in range(100)}
    diagnostics = {
        "joint_structure": {
            "pair_count": 100,
            "rows": [{"model_label": "m", "left_question": "q1", "right_question": "q2", "joint_l1": 0.1} for _ in range(50)],
        },
        "subgroup_marginals": {
            "cell_count": 100,
            "rows": [
                {
                    "model_label": "m",
                    "heldout_question": "q1",
                    "segment_question": "q2",
                    "segment_value": "A",
                    "rows": 50,
                    "l1": 0.4,
                    "empirical": bulky_distribution,
                    "twin_implied": bulky_distribution,
                }
                for _ in range(50)
            ],
        },
        "conditional_consistency": {
            "cell_count": 100,
            "rows": [
                {
                    "model_label": "m",
                    "condition_question": "q1",
                    "condition_value": "A",
                    "target_question": "q2",
                    "rows": 50,
                    "l1": 0.5,
                    "empirical": bulky_distribution,
                    "twin_implied": bulky_distribution,
                }
                for _ in range(50)
            ],
        },
    }

    compact = compact_twin_specific_diagnostics_for_report(diagnostics, row_limit=3)

    assert compact["subgroup_marginals"]["included_row_count"] == 3
    assert compact["conditional_consistency"]["included_row_count"] == 3
    assert compact["subgroup_marginals"]["omitted_count"] == 47
    assert "empirical" not in compact["subgroup_marginals"]["rows"][0]
    assert len(compact["subgroup_marginals"]["rows"][0]["empirical_top_options"]) == 5
    assert len(json.dumps(compact)) < 10_000


def test_practitioner_report_import_concatenates_multiple_sections(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    report_dir = zwill_project_path(tmp_path) / "practitioner_reports" / "multi-report"
    report_dir.mkdir(parents=True)
    (report_dir / "context.json").write_text(json.dumps({"report_id": "multi-report"}))
    results = {
        "edsl_class_name": "Results",
        "zwill": {
            "practitioner_report_id": "multi-report",
            "practitioner_report_question_names": ["section_a", "section_b"],
        },
        "data": [
            {
                "answer": {
                    "section_a": "## Section A\n\nFirst.",
                    "section_b": "## Section B\n\nSecond.",
                }
            }
        ],
    }
    results_path = tmp_path / "multi_results.json.gz"
    with gzip.open(results_path, "wt") as f:
        json.dump(results, f)

    run_cli("twin-benchmark", "practitioner-report-import", "--report-id", "multi-report", "--path", str(results_path))

    markdown = (report_dir / "report.md").read_text()
    assert "## Section A" in markdown
    assert "## Section B" in markdown
    assert markdown.index("## Section A") < markdown.index("## Section B")


def test_report_build_creates_incremental_bundle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()

    report_dir = tmp_path / "reports" / "demo"
    run_cli("report", "build", "--survey", "demo", "--path", str(report_dir), "--permutations", "100")
    manifest = json.loads((report_dir / "report-manifest.json").read_text())
    stage_manifest = json.loads((report_dir / "stage-manifest.json").read_text())
    pages = {row["page_id"]: row for row in manifest["pages"]}
    assert (report_dir / "index.html").exists()
    assert (report_dir / "report" / "index.html").exists()
    assert (report_dir / "CHECKLIST.md").exists()
    assert (report_dir / "report" / "CHECKLIST.md").exists()
    assert (report_dir / "facts" / "survey-profile.json").exists()
    assert stage_manifest["checklist_path"] == str(report_dir / "CHECKLIST.md")
    assert "zwill report build --survey demo" in " ".join(stage_manifest["canonical_commands"])
    assert {page["page_id"] for page in stage_manifest["pages"]} >= {"survey-profile", "one-shot-marginals", "twin-validation"}
    assert stage_manifest["stages"]["facts"]["status"] == "ready"
    assert stage_manifest["stages"]["analysis"]["status"] == "blocked"
    assert (report_dir / "survey-profile.html").exists()
    assert pages["survey-profile"]["status"] == "ready"
    assert pages["one-shot-marginals"]["status"] == "not_ready"
    assert pages["twin-validation"]["status"] == "not_ready"
    assert "one-shot-coverage" not in pages
    assert "executive-summary" not in pages
    assert "validation-diagnostics" not in pages
    assert "One-Shot Marginals" in (report_dir / "index.html").read_text()

    probability_results = {
        "edsl_class_name": "Results",
        "zwill": {"probability_job_id": "one-shot"},
        "data": [
            {
                "scenario": {
                    "source_question_name": "q1",
                    "source_question_text": "Pick one",
                    "option_labels": ["yes", "no"],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.6,0.4],"notes":"target"}'},
            }
        ],
    }
    probability_path = tmp_path / "probability_results.json"
    probability_path.write_text(json.dumps(probability_results))
    run_cli("prob-results", "import", "--survey", "demo", "--path", str(probability_path))

    twin_results = json.loads((FIXTURES / "twin_results.json").read_text())
    twin_path = tmp_path / "twin_results.json"
    twin_path.write_text(json.dumps(twin_results))
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(twin_path))

    run_cli(
        "report",
        "build",
        "--survey",
        "demo",
        "--path",
        str(report_dir),
        "--job-id",
        "fixture-twin",
        "--permutations",
        "100",
    )
    manifest = json.loads((report_dir / "report-manifest.json").read_text())
    stage_manifest = json.loads((report_dir / "stage-manifest.json").read_text())
    pages = {row["page_id"]: row for row in manifest["pages"]}
    assert pages["one-shot-marginals"]["status"] == "ready"
    assert pages["twin-validation"]["status"] == "ready"
    assert pages["twin-run-audit"]["status"] == "ready"
    assert pages["twin-run-audit"]["primary"] is False
    assert pages["twin-comparison"]["primary"] is False
    assert "one-shot-coverage" not in pages
    assert "executive-summary" not in pages
    assert "validation-diagnostics" not in pages
    assert (report_dir / "one-shot-marginals.html").exists()
    assert (report_dir / "one-shot-coverage.html").exists()
    assert (report_dir / "twin-validation.html").exists()
    assert (report_dir / "executive-summary.html").exists()
    assert (report_dir / "validation-diagnostics.html").exists()
    assert (report_dir / "audit" / "twin-run-fixture-twin.html").exists()
    assert (report_dir / "data" / "one-shot-marginals.json").exists()
    assert (report_dir / "data" / "one-shot-coverage.json").exists()
    assert (report_dir / "data" / "joint-structure.json").exists()
    assert (report_dir / "data" / "subgroup-marginals.json").exists()
    assert (report_dir / "data" / "conditional-consistency.json").exists()
    assert (report_dir / "analysis" / "twin-validation.json").exists()
    assert (report_dir / "analysis" / "joint-structure.json").exists()
    assert (report_dir / "analysis" / "subgroup-marginals.json").exists()
    assert (report_dir / "analysis" / "conditional-consistency.json").exists()
    assert (report_dir / "analysis" / "executive-summary.md").exists()
    assert (report_dir / "report" / "twin-validation.html").exists()
    assert stage_manifest["stages"]["analysis"]["status"] == "ready"
    assert stage_manifest["stages"]["generated_analysis"]["status"] == "blocked"
    assert stage_manifest["stages"]["final_report"]["status"] == "blocked"
    assert "frontier-model one-shot marginal analysis Markdown" in stage_manifest["stages"]["generated_analysis"]["missing"]
    assert "frontier-model executive twin validation Markdown" in stage_manifest["stages"]["generated_analysis"]["missing"]
    assert len(stage_manifest["required_generated_interpretations"]) == 2
    assert main(["report", "render", "--survey", "demo", "--path", str(report_dir), "--job-id", "fixture-twin", "--final"]) != 0
    index_html = (report_dir / "index.html").read_text()
    assert "report-bundle-data" in index_html
    assert "Facts → Analysis → Report" not in index_html
    assert "Copy as Markdown" in index_html
    assert 'href="executive-summary.html"' not in index_html
    assert 'href="audit/twin-run-fixture-twin.html"' in index_html
    assert index_html.count('class="step ready"') + index_html.count('class="step not_ready"') == 3
    checklist = (report_dir / "CHECKLIST.md").read_text()
    assert ".zwill` remains the system of record" in checklist
    assert "Twin Validation (primary): ready" in checklist
    assert "Twin Run Audit (supporting): ready" in checklist
    assert "Joint Structure And Slicing Diagnostics" in (report_dir / "twin-validation.html").read_text()
    twin_data = json.loads((report_dir / "data" / "twin-validation.json").read_text())
    assert twin_data["raw_prediction_rows_included"] is False
    assert "rows" not in twin_data


def test_agent_material_quarantine_blocks_commit_until_resolved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    path = tmp_path / "bad_agent_material.jsonl"
    write_jsonl(
        path,
        [
            {
                "material_id": "bad",
                "respondent_id": "missing",
                "kind": "profile",
                "title": "Bad",
                "body_markdown": "Bad material.",
            }
        ],
    )

    cli.cmd_agent_material_import(argparse.Namespace(survey="demo", path=str(path)))
    try:
        cli.cmd_commit(argparse.Namespace(survey="demo"))
    except cli.ZwillError as exc:
        assert exc.code == "gate_blocked"
    else:
        raise AssertionError("Expected commit to be blocked by open quarantine issue.")

    issue = cli.read_jsonl(zwill_survey_path(tmp_path) / "quarantine.jsonl")[0]
    cli.cmd_quarantine_resolve(
        argparse.Namespace(
            survey="demo",
            issue_id=issue["issue_id"],
            action="accepted_exclusion",
            note="Invalid agent material excluded.",
        )
    )
    result = cli.cmd_commit(argparse.Namespace(survey="demo"))
    assert result["status"] == "ok"


def test_agent_material_import_quarantines_invalid_rows_and_normalizes_tags(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    path = tmp_path / "agent_material.jsonl"
    write_jsonl(
        path,
        [
            {
                "material_id": "profile_r1",
                "respondent_id": "r1",
                "kind": "profile",
                "title": "Profile",
                "body_markdown": "Useful profile.",
                "tags": "profile, useful",
            },
            {
                "material_id": "missing_body",
                "respondent_id": "r1",
                "kind": "profile",
                "title": "Missing body",
            },
            {
                "material_id": "unknown_respondent",
                "respondent_id": "missing",
                "kind": "profile",
                "title": "Unknown",
                "body_markdown": "Should quarantine.",
            },
        ],
    )

    result = cli.cmd_agent_material_import(argparse.Namespace(survey="demo", path=str(path)))
    rows = cli.agent_material_rows(zwill_survey_path(tmp_path))
    issues = cli.read_jsonl(zwill_survey_path(tmp_path) / "quarantine.jsonl")

    assert result["data"]["imported_count"] == 1
    assert result["data"]["quarantined_count"] == 2
    assert rows[0]["tags"] == ["profile", "useful"]
    assert {issue["code"] for issue in issues if issue.get("type") == "agent_material"} == {"invalid_input", "unknown_respondent"}


def test_checkbox_answer_import_validates_each_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    run_cli("survey", "create", "--name", "demo")
    questions = tmp_path / "questions.jsonl"
    write_jsonl(
        questions,
        [
            {
                "question_name": "q_channels",
                "question_type": "checkbox",
                "question_text": "Which channels do you use?",
                "question_options": ["Email", "Phone", "In-person"],
                "option_delimiter": "|",
            }
        ],
    )
    run_cli("question", "import", "--survey", "demo", "--path", str(questions))
    answers = tmp_path / "answers.jsonl"
    write_jsonl(
        answers,
        [
            {"respondent_id": "r1", "question": "q_channels", "answer": "Email|Phone"},
            {"respondent_id": "r2", "question": "q_channels", "answer": "Email|Fax"},
        ],
    )
    result = cli.cmd_answer_import(argparse.Namespace(survey="demo", path=str(answers)))
    assert result["data"]["imported_count"] == 1
    assert result["data"]["quarantined_count"] == 1
    issues = cli.read_jsonl(zwill_survey_path(tmp_path) / "quarantine.jsonl")
    checkbox_issue = next(issue for issue in issues if issue.get("question") == "q_channels")
    assert checkbox_issue["invalid_selections"] == ["Fax"]


def test_agent_material_import_duplicate_material_id_replaces_row(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    write_jsonl(
        first,
        [
            {
                "material_id": "profile_r1",
                "respondent_id": "r1",
                "kind": "profile",
                "title": "Old",
                "body_markdown": "Old body",
            }
        ],
    )
    write_jsonl(
        second,
        [
            {
                "material_id": "profile_r1",
                "respondent_id": "r1",
                "kind": "profile",
                "title": "New",
                "body_markdown": "New body",
            }
        ],
    )

    cli.cmd_agent_material_import(argparse.Namespace(survey="demo", path=str(first)))
    cli.cmd_agent_material_import(argparse.Namespace(survey="demo", path=str(second)))

    rows = cli.agent_material_rows(zwill_survey_path(tmp_path))
    assert len(rows) == 1
    assert rows[0]["title"] == "New"
    assert rows[0]["body_markdown"] == "New body"


def test_agent_material_cli_path_list_filters_and_show(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    body_path = tmp_path / "material.md"
    body_path.write_text("Material from markdown file.")

    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--material-id",
        "profile_r1",
        "--kind",
        "profile",
        "--title",
        "Profile",
        "--path",
        str(body_path),
        "--tag",
        "profile,preference",
    )
    result = cli.cmd_agent_material_list(
        argparse.Namespace(survey="demo", respondent_id=None, agent_material_kind=None, agent_material_tag=["preference"])
    )
    shown = cli.cmd_agent_material_show(argparse.Namespace(survey="demo", material_id="profile_r1"))

    assert result["data"]["material_count"] == 1
    assert result["data"]["materials"][0]["material_id"] == "profile_r1"
    assert shown["data"]["material"]["body_markdown"] == "Material from markdown file."
    assert shown["data"]["material"]["source"]["path"] == str(body_path)


def test_agent_list_export_includes_agent_material_only_when_requested(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("context", "set", "--survey", "demo", "--text", "Demo survey context.")
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Favorite color",
        "--text",
        "The respondent's favorite color is blue.",
    )
    monkeypatch.setattr(cli, "load_edsl_classes", lambda: (FakeAgent, FakeAgentList, object, object))

    base_args = default_agent_list_export_args()
    without_material = cli.build_edsl_agent_list_dict("demo", base_args)
    assert "instruction" not in without_material["agent_list"][0]

    with_material_args = argparse.Namespace(**{**vars(base_args), "include_agent_material": True})
    with_material = cli.build_edsl_agent_list_dict("demo", with_material_args)
    r1 = next(agent for agent in with_material["agent_list"] if agent["name"] == "r1")
    r2 = next(agent for agent in with_material["agent_list"] if agent["name"] == "r2")
    assert "favorite color is blue" in r1["instruction"]
    assert "instruction" not in r2
    assert "_agent_material_markdown" not in with_material["codebook"]
    assert with_material["zwill"]["include_agent_material"] is True

    with_context_args = argparse.Namespace(**{**vars(base_args), "include_survey_context": True})
    with_context = cli.build_edsl_agent_list_dict("demo", with_context_args)
    assert "Demo survey context." in with_context["agent_list"][0]["instruction"]
    assert with_context["zwill"]["include_survey_context"] is True


def test_agent_list_export_filters_limits_and_truncates_material(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Profile",
        "--text",
        "Blue preference material that should be included.",
        "--tag",
        "preference",
    )
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "note",
        "--title",
        "Note",
        "--text",
        "This operational note should be filtered out.",
        "--tag",
        "ops",
    )
    monkeypatch.setattr(cli, "load_edsl_classes", lambda: (FakeAgent, FakeAgentList, object, object))

    exported = cli.build_edsl_agent_list_dict(
        "demo",
        default_agent_list_export_args(
            questions="q1",
            limit=1,
            include_agent_material=True,
            agent_material_tag=["preference"],
            max_agent_material_chars=32,
        ),
    )

    assert exported["zwill"]["agent_count"] == 1
    assert exported["zwill"]["selected_questions"] == ["q1"]
    assert exported["agent_list"][0]["name"] == "r1"
    assert set(exported["agent_list"][0]["traits"]) == {"q1"}
    assert "Blue prefe" in exported["agent_list"][0]["instruction"]
    assert "operational note" not in exported["agent_list"][0]["instruction"]
    assert "Truncated to max agent material characters" in exported["agent_list"][0]["instruction"]


def test_agent_list_export_traits_presentation_template_controls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    monkeypatch.setattr(cli, "load_edsl_classes", lambda: (FakeAgent, FakeAgentList, object, object))

    inline = cli.build_edsl_agent_list_dict(
        "demo",
        default_agent_list_export_args(questions="q1", traits_presentation_template="Inline {{ q1 }}"),
    )
    assert inline["traits_presentation_template"] == "Inline {{ q1 }}"
    assert inline["zwill"]["traits_presentation_template_source"] == "inline"

    path = tmp_path / "traits_template.jinja"
    path.write_text("From file {{ q1 }}")
    from_file = cli.build_edsl_agent_list_dict(
        "demo",
        default_agent_list_export_args(questions="q1", traits_presentation_template_path=str(path)),
    )
    assert from_file["traits_presentation_template"] == "From file {{ q1 }}"
    assert from_file["zwill"]["traits_presentation_template_source"] == "path"

    disabled = cli.build_edsl_agent_list_dict(
        "demo",
        default_agent_list_export_args(questions="q1", no_default_traits_presentation_template=True),
    )
    assert "traits_presentation_template" not in disabled
    assert disabled["zwill"]["traits_presentation_template_source"] == "edsl_default"


def test_edsl_export_agent_list_through_parser_writes_selected_traits_and_instructions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("context", "set", "--survey", "demo", "--text", "Parser context.")
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Profile",
        "--text",
        "Parser profile material.",
    )
    monkeypatch.setattr(cli, "load_edsl_classes", lambda: (FakeAgent, FakeAgentList, object, object))
    path = tmp_path / "agents.json"
    capsys.readouterr()

    run_cli(
        "edsl-export",
        "--survey",
        "demo",
        "--target",
        "agent-list",
        "--questions",
        "q1",
        "--include-survey-context",
        "--include-agent-material",
        "--path",
        str(path),
    )

    stdout_payload = json.loads(capsys.readouterr().out)
    written = json.loads(path.read_text())
    # With --path, stdout is a clean envelope (metadata) and the file holds the job.
    assert stdout_payload["command"] == "zwill edsl-export"
    assert stdout_payload["status"] == "ok"
    assert stdout_payload["data"]["path"] == str(path)
    output = written
    assert output["zwill"]["selected_questions"] == ["q1"]
    r1 = next(agent for agent in output["agent_list"] if agent["name"] == "r1")
    assert set(r1["traits"]) == {"q1"}
    assert "Parser context." in r1["instruction"]
    assert "Parser profile material." in r1["instruction"]


def test_edsl_agent_list_export_round_trips_with_real_edsl(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("edsl")
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("context", "set", "--survey", "demo", "--text", "Round trip context.")
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Profile",
        "--text",
        "Round trip profile.",
    )

    data = cli.build_edsl_agent_list_dict(
        "demo",
        default_agent_list_export_args(
            questions="q1",
            limit=1,
            include_survey_context=True,
            include_agent_material=True,
        ),
    )

    from edsl import AgentList

    agent_list = AgentList.from_dict(data)
    agent = agent_list[0]
    assert agent.name == "r1"
    assert agent.traits == {"q1": "yes"}
    assert "Round trip context." in agent.instruction
    assert "Round trip profile." in agent.instruction


def test_agent_list_inspect_through_parser_writes_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps(
            {
                "edsl_class_name": "AgentList",
                "agent_list": [{"name": "r1", "traits": {"q1": "Yes"}, "instruction": "Profile note"}],
                "codebook": {"q1": "Question one"},
            }
        )
    )

    run_cli("agent-list", "inspect", "--path", str(path), "--format", "json")

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "zwill agent-list inspect"
    assert payload["data"]["agent_count"] == 1
    assert payload["data"]["agents_with_instruction"] == 1


def test_agent_list_inspect_summarizes_traits_and_instructions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps(
            {
                "edsl_class_name": "AgentList",
                "agent_list": [
                    {"name": "r1", "traits": {"q1": "Yes"}, "instruction": "Profile note"},
                    {"name": "r2", "traits": {"q1": "No"}},
                ],
                "codebook": {"q1": "Question one"},
                "zwill": {"selected_questions": ["q1"]},
            }
        )
    )

    summary = cli.cmd_agent_list_inspect(argparse.Namespace(path=str(path), format="json"))["data"]

    assert summary["agent_count"] == 2
    assert summary["trait_keys"] == ["q1"]
    assert summary["agents_with_instruction"] == 1
    assert summary["codebook_keys"] == ["q1"]


def test_agent_list_inspect_failure_cases_and_empty_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    not_agent_list = tmp_path / "not_agent_list.json"
    bad_agent_list = tmp_path / "bad_agent_list.json"
    empty_agent_list = tmp_path / "empty_agent_list.json"
    not_agent_list.write_text(json.dumps({"edsl_class_name": "Jobs", "agents": []}))
    bad_agent_list.write_text(json.dumps({"edsl_class_name": "AgentList", "agent_list": {"name": "r1"}}))
    empty_agent_list.write_text(json.dumps({"edsl_class_name": "AgentList", "agent_list": []}))

    for path in [not_agent_list, bad_agent_list]:
        try:
            cli.cmd_agent_list_inspect(argparse.Namespace(path=str(path), format="json"))
        except cli.ZwillError as exc:
            assert exc.code == "invalid_input"
        else:
            raise AssertionError(f"Expected invalid_input for {path}")

    summary = cli.cmd_agent_list_inspect(argparse.Namespace(path=str(empty_agent_list), format="json"))["data"]
    assert summary["agent_count"] == 0
    assert summary["trait_keys"] == []
    assert summary["sample_agents"] == []


def test_agent_study_export_builds_job_from_agent_list_and_inline_question(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent_list_path = tmp_path / "agents.json"
    agent_list_path.write_text(
        json.dumps(
            {
                "edsl_class_name": "AgentList",
                "agent_list": [
                    {
                        "name": "r1",
                        "traits": {"favorite_color_blue": "Yes"},
                        "instruction": "The respondent's favorite color is blue.",
                    }
                ],
                "codebook": {"favorite_color_blue": "Is this respondent's favorite color blue?"},
            }
        )
    )
    monkeypatch.setattr(
        cli,
        "load_edsl_agent_study_classes",
        lambda: (FakeAgentList, FakeJobs, FakeModel, FakeModelList, FakeQuestion, FakeSurvey),
    )

    job = cli.build_edsl_agent_study_job_dict(
        argparse.Namespace(
            agent_list=str(agent_list_path),
            question_path=None,
            question_name="ask_blue",
            question_type="multiple_choice",
            question_text="Is your favorite color blue?",
            question_option=["Yes", "No"],
            model=["openai:gpt-5.5"],
            models=None,
            service_name=None,
            model_param=None,
        )
    )

    assert job["survey"]["questions"][0]["question_name"] == "ask_blue"
    assert job["agents"][0]["instruction"] == "The respondent's favorite color is blue."
    assert job["zwill"]["agent_count"] == 1
    assert job["zwill"]["agent_study_job_id"]


def test_agent_study_export_rejects_missing_inline_question_fields_and_bad_specs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent_list_path = tmp_path / "agents.json"
    bad_agent_list_path = tmp_path / "bad_agents.json"
    bad_question_path = tmp_path / "bad_question.json"
    agent_list_path.write_text(json.dumps({"edsl_class_name": "AgentList", "agent_list": []}))
    bad_agent_list_path.write_text(json.dumps({"edsl_class_name": "Jobs", "agents": []}))
    bad_question_path.write_text(json.dumps({"question_name": "q"}))
    monkeypatch.setattr(
        cli,
        "load_edsl_agent_study_classes",
        lambda: (FakeAgentList, FakeJobs, FakeModel, FakeModelList, FakeQuestion, FakeSurvey),
    )

    cases = [
        argparse.Namespace(
            agent_list=str(agent_list_path),
            question_path=None,
            question_name="q",
            question_type=None,
            question_text="Text",
            question_option=None,
            model=None,
            models=None,
            service_name=None,
            model_param=None,
        ),
        argparse.Namespace(
            agent_list=str(agent_list_path),
            question_path=None,
            question_name="q",
            question_type="free_text",
            question_text=None,
            question_option=None,
            model=None,
            models=None,
            service_name=None,
            model_param=None,
        ),
        argparse.Namespace(
            agent_list=str(agent_list_path),
            question_path=str(bad_question_path),
            question_name=None,
            question_type=None,
            question_text=None,
            question_option=None,
            model=None,
            models=None,
            service_name=None,
            model_param=None,
        ),
        argparse.Namespace(
            agent_list=str(bad_agent_list_path),
            question_path=None,
            question_name="q",
            question_type="free_text",
            question_text="Text",
            question_option=None,
            model=None,
            models=None,
            service_name=None,
            model_param=None,
        ),
        argparse.Namespace(
            agent_list=str(agent_list_path),
            question_path=None,
            question_name="q",
            question_type="free_text",
            question_text="Text",
            question_option=None,
            model=None,
            models=None,
            service_name=None,
            model_param=["bad:syntax=1"],
        ),
    ]

    for args in cases:
        try:
            cli.build_edsl_agent_study_job_dict(args)
        except cli.ZwillError as exc:
            assert exc.code == "invalid_input"
        else:
            raise AssertionError(f"Expected invalid_input for {args}")


def test_agent_study_export_accepts_question_path_and_parser_args(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    agent_list_path = tmp_path / "agents.json"
    agent_list_path.write_text(
        json.dumps(
            {
                "edsl_class_name": "AgentList",
                "agent_list": [{"name": "r1", "traits": {"q1": "Yes"}, "instruction": "Profile note"}],
                "codebook": {"q1": "Question one"},
                "zwill": {"selected_questions": ["q1"]},
            }
        )
    )
    question_path = tmp_path / "question.json"
    question_path.write_text(
        json.dumps(
            {
                "question_name": "new_question",
                "question_type": "multiple_choice",
                "question_text": "New question?",
                "question_options": ["Yes", "No"],
            }
        )
    )
    job_path = tmp_path / "job.json"
    monkeypatch.setattr(
        cli,
        "load_edsl_agent_study_classes",
        lambda: (FakeAgentList, FakeJobs, FakeModel, FakeModelList, FakeQuestion, FakeSurvey),
    )

    run_cli(
        "agent-study",
        "export",
        "--agent-list",
        str(agent_list_path),
        "--question-path",
        str(question_path),
        "--model",
        "openai:gpt-5.5",
        "--model-param",
        "temperature=0",
        "--path",
        str(job_path),
    )

    stdout_payload = json.loads(capsys.readouterr().out)
    written = json.loads(job_path.read_text())
    assert stdout_payload["command"] == "zwill agent-study export"
    assert stdout_payload["data"]["path"] == str(job_path)
    assert written["zwill"]["question_name"] == "new_question"
    assert written["survey"]["questions"][0]["question_options"] == ["Yes", "No"]
    assert written["models"][0]["parameters"]["temperature"] == 0


def test_agent_list_and_agent_study_exports_have_stable_tiny_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("context", "set", "--survey", "demo", "--text", "Stable context.")
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Profile",
        "--text",
        "Stable profile.",
    )
    monkeypatch.setattr(cli, "load_edsl_classes", lambda: (FakeAgent, FakeAgentList, FakeQuestion, FakeSurvey))
    monkeypatch.setattr(
        cli,
        "load_edsl_agent_study_classes",
        lambda: (FakeAgentList, FakeJobs, FakeModel, FakeModelList, FakeQuestion, FakeSurvey),
    )
    agent_list = cli.build_edsl_agent_list_dict(
        "demo",
        default_agent_list_export_args(
            questions="q1",
            limit=1,
            include_survey_context=True,
            include_agent_material=True,
        ),
    )
    agent_list_path = tmp_path / "agents.json"
    agent_list_path.write_text(json.dumps(agent_list))
    agent_study = cli.build_edsl_agent_study_job_dict(
        argparse.Namespace(
            agent_list=str(agent_list_path),
            question_path=None,
            question_name="new_q",
            question_type="multiple_choice",
            question_text="New question?",
            question_option=["Yes", "No"],
            model=["openai:gpt-5.5"],
            models=None,
            service_name=None,
            model_param=None,
        )
    )

    assert agent_list["edsl_class_name"] == "AgentList"
    assert agent_list["codebook"] == {"q1": "Pick one"}
    assert "Prior survey answers" in agent_list["traits_presentation_template"]
    assert "{{ codebook[question_name]" in agent_list["traits_presentation_template"]
    assert agent_list["agent_list"] == [
        {
            "name": "r1",
            "traits": {"q1": "yes"},
            "instruction": "## Survey context\nStable context.\n\n## Non-survey agent material\n### Profile (profile)\nStable profile.\n",
        }
    ]
    assert agent_study["survey"]["questions"] == [
        {
            "question_name": "new_q",
            "question_text": "New question?",
            "question_type": "multiple_choice",
            "question_options": ["Yes", "No"],
        }
    ]
    assert agent_study["agents"] == [
        {
            "name": "r1",
            "traits": {"q1": "yes"},
            "codebook": {},
            "instruction": agent_list["agent_list"][0]["instruction"],
            "traits_presentation_template": agent_list["traits_presentation_template"],
        }
    ]
    assert agent_study["zwill"]["agent_count"] == 1


def test_agent_study_job_round_trips_with_real_edsl(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("edsl")
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Profile",
        "--text",
        "Real EDSL profile.",
    )
    agent_list = cli.build_edsl_agent_list_dict(
        "demo",
        default_agent_list_export_args(
            questions="q1",
            limit=1,
            include_agent_material=True,
        ),
    )
    agent_list_path = tmp_path / "agents.json"
    agent_list_path.write_text(json.dumps(agent_list))
    job = cli.build_edsl_agent_study_job_dict(
        argparse.Namespace(
            agent_list=str(agent_list_path),
            question_path=None,
            question_name="new_q",
            question_type="multiple_choice",
            question_text="New question?",
            question_option=["Yes", "No"],
            model=["openai:gpt-5.5"],
            models=None,
            service_name=None,
            model_param=None,
        )
    )

    from edsl import Jobs

    loaded = Jobs.from_dict(job)
    assert loaded.survey.questions[0].question_name == "new_q"
    assert loaded.agents[0].name == "r1"
    assert "Real EDSL profile." in loaded.agents[0].instruction


def agent_study_results(job_id: str = "agent-job") -> dict:
    return {
        "edsl_class_name": "Results",
        "zwill": {"agent_study_job_id": job_id},
        "data": [
            {
                "agent": {
                    "name": "r1",
                    "traits": {"q1": "yes"},
                    "instruction": "Profile instruction.",
                },
                "scenario": {},
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {"temperature": 0}},
                "answer": {"new_q": "Yes", "new_q_comment": "Because profile says yes."},
                "question_to_attributes": {
                    "new_q": {
                        "question_text": "New question?",
                        "question_type": "multiple_choice",
                        "question_options": ["Yes", "No"],
                    }
                },
                "raw_model_response": {"new_q_raw_model_response": {"choices": [{"message": {"content": "Yes"}}]}},
            }
        ],
    }


def test_agent_study_import_report_list_show_and_replace(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    results_path = tmp_path / "agent_results.json.gz"
    with gzip.open(results_path, "wt") as f:
        json.dump(agent_study_results("agent-job"), f)

    import_result = cli.cmd_agent_study_import(argparse.Namespace(path=str(results_path), job_id=None, replace=False))
    assert import_result["data"]["job_id"] == "agent-job"
    assert import_result["data"]["extracted_count"] == 1

    try:
        cli.cmd_agent_study_import(argparse.Namespace(path=str(results_path), job_id=None, replace=False))
    except cli.ZwillError as exc:
        assert exc.code == "already_exists"
    else:
        raise AssertionError("Expected duplicate import to require --replace.")
    cli.cmd_agent_study_import(argparse.Namespace(path=str(results_path), job_id=None, replace=True))

    answers = cli.read_jsonl(zwill_project_path(tmp_path) / "agent_studies" / "answers.jsonl")
    assert len(answers) == 1
    assert answers[0]["answer"] == "Yes"
    assert answers[0]["comment"] == "Because profile says yes."
    assert answers[0]["instruction_present"] is True
    assert answers[0]["raw_model_response"]

    json_path = tmp_path / "agent_report.json"
    csv_path = tmp_path / "agent_report.csv"
    html_path = tmp_path / "agent_report.html"
    cli.cmd_agent_study_report(argparse.Namespace(job_id="agent-job", model=None, format="json", path=str(json_path)))
    cli.cmd_agent_study_report(argparse.Namespace(job_id="agent-job", model=None, format="csv", path=str(csv_path)))
    cli.cmd_agent_study_report(argparse.Namespace(job_id="agent-job", model=None, format="html", path=str(html_path)))
    report = json.loads(json_path.read_text())
    assert report["summary"]["row_count"] == 1
    assert report["summary"]["answer_distributions"]["new_q::openai:gpt-5.5"] == {"Yes": 1}
    assert "agent_name,question_name,answer" in csv_path.read_text().splitlines()[0]
    assert "agent-study-data" in html_path.read_text()

    capsys.readouterr()
    cli.cmd_agent_study_list(argparse.Namespace(format="json"))
    listed = json.loads(capsys.readouterr().out)
    assert listed["runs"][0]["job_id"] == "agent-job"
    shown = cli.cmd_agent_study_show(argparse.Namespace(job_id="agent-job", include_summary=True))
    assert shown["data"]["row_count"] == 1
    assert shown["data"]["summary"]["agent_count"] == 1


def test_agent_study_import_records_malformed_rows_as_issues(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    results_path = tmp_path / "bad_agent_results.json"
    results_path.write_text(json.dumps({"edsl_class_name": "Results", "zwill": {"agent_study_job_id": "bad"}, "data": [{"answer": {}}]}))

    result = cli.cmd_agent_study_import(argparse.Namespace(path=str(results_path), job_id=None, replace=False))

    assert result["data"]["extracted_count"] == 0
    assert result["data"]["issue_count"] == 1
    assert result["data"]["issues"][0]["error"] == "missing_answer_question"


def test_twin_job_export_agent_material_changes_scenarios(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Favorite color",
        "--text",
        "The respondent's favorite color is blue.",
    )
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )
    base_args = default_twin_export_args()
    without_material = cli.build_edsl_digital_twin_job_dict("demo", base_args)
    assert without_material["scenarios"][0]["agent_material"] == []
    assert "No non-survey agent material" in without_material["scenarios"][0]["agent_material_text"]

    with_material_args = argparse.Namespace(**{**vars(base_args), "include_agent_material": True})
    with_material = cli.build_edsl_digital_twin_job_dict("demo", with_material_args)
    assert "favorite color is blue" in with_material["scenarios"][0]["agent_material_text"]
    assert with_material["zwill"]["include_agent_material"] is True
    assert with_material["zwill"]["digital_twin_job_id"] != without_material["zwill"]["digital_twin_job_id"]


def test_twin_job_export_includes_supplemental_twin_material(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    material_path = tmp_path / "twin_material.jsonl"
    write_jsonl(
        material_path,
        [
            {
                "material_id": "frontier_prior_q1",
                "kind": "model_prior",
                "title": "Frontier model one-shot prior",
                "question": "q1",
                "body_markdown": "Frontier model prior for q1: yes 0.70, no 0.30.",
            },
            {
                "material_id": "unmatched_q2",
                "kind": "model_prior",
                "title": "Unmatched prior",
                "question": "q2",
                "body_markdown": "This should not be injected for q1.",
            },
        ],
    )
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )

    without_material = cli.build_edsl_digital_twin_job_dict("demo", default_twin_export_args())
    with_material = cli.build_edsl_digital_twin_job_dict(
        "demo",
        default_twin_export_args(twin_material=[str(material_path)]),
    )

    scenario = with_material["scenarios"][0]
    assert "Frontier model prior for q1" in scenario["twin_material_text"]
    assert "This should not be injected" not in scenario["twin_material_text"]
    assert scenario["twin_material"][0]["material_id"] == "frontier_prior_q1"
    assert with_material["zwill"]["twin_material_paths"] == [str(material_path)]
    assert with_material["zwill"]["digital_twin_job_id"] != without_material["zwill"]["digital_twin_job_id"]


def test_twin_job_export_uses_known_options_for_compound_context_questions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    sdir = zwill_survey_path(tmp_path)
    questions = cli.read_jsonl(sdir / "questions.jsonl")
    for question in questions:
        if question["question_name"] == "q2":
            question["question_type"] = "free_text"
            question.pop("question_options", None)
            question["source"] = {
                "raw_id": "source_workbook",
                "note": "Compound checkbox question.",
                "known_options": ["left", "right", "center"],
            }
    cli.rewrite_jsonl(sdir / "questions.jsonl", questions)
    answers = cli.read_jsonl(sdir / "answers.jsonl")
    for answer in answers:
        if answer["respondent_id"] == "r1" and answer["question"] == "q2":
            answer["answer"] = "left; right"
    cli.rewrite_jsonl(sdir / "answers.jsonl", answers)
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )

    job = cli.build_edsl_digital_twin_job_dict(
        "demo",
        default_twin_export_args(context_question=["q2"]),
    )

    observed = job["scenarios"][0]["observed_answers"][0]
    assert observed["question_name"] == "q2"
    assert observed["question_options"] == ["left", "right", "center"]
    assert "Options: left; right; center" in job["scenarios"][0]["observed_answers_text"]
    assert "Respondent answered: left; right" in job["scenarios"][0]["observed_answers_text"]


def test_twin_job_export_applies_target_specific_leakage_exclusions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )

    job = cli.build_edsl_digital_twin_job_dict(
        "demo",
        default_twin_export_args(
            heldout_question=["q1"],
            respondent=["r1"],
            context_question=["q2"],
            leakage_exclusion=["q1:q2"],
        ),
    )

    scenario = job["scenarios"][0]
    assert scenario["heldout_question_name"] == "q1"
    assert scenario["observed_answers"] == []
    assert scenario["leakage_exclusions"] == ["q2"]
    assert "q2" not in scenario["observed_answers_text"]
    assert job["zwill"]["leakage_exclusions"] == {"q1": ["q2"]}


def test_twin_approach_and_experiment_plan_export_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )

    run_cli(
        "twin-approach",
        "add",
        "--survey",
        "demo",
        "--approach-id",
        "baseline",
        "--name",
        "Survey answers only",
        "--description",
        "Use one observed prior survey answer.",
        "--context-question-count",
        "1",
        "--model",
        "openai:gpt-5.5",
    )
    approaches = json.loads((zwill_survey_path(tmp_path) / "digital_twin_jobs" / "approaches.json").read_text())
    assert approaches["approaches"][0]["approach_id"] == "baseline"
    assert approaches["approaches"][0]["construction"]["context_question_count"] == 1
    note_result = cli.cmd_twin_approach_note(
        argparse.Namespace(
            survey="demo",
            approach_id="baseline",
            text="Hypothesis: one prior answer should be enough for this toy survey.",
            path=None,
            clear=False,
        )
    )
    assert "Hypothesis" in note_result["data"]["notes"]

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "plan_id": "demo-plan",
                "survey": "demo",
                "heldout_question": "q1",
                "defaults": {
                    "sample_respondents": 1,
                    "seed": 123,
                    "complete_cases": True,
                },
                "arms": [
                    {"approach_id": "baseline"},
                    {
                        "approach_id": "more-context",
                        "name": "Two observed answers",
                        "description": "Use two observed prior answers.",
                        "construction": {
                            "context_question_count": 2,
                            "model": ["google:gemini-2.5-pro"],
                        },
                    },
                ],
            }
        )
    )
    run_cli("twin-experiment", "approve", "--path", str(plan_path), "--approved-by", "test-user")
    output_dir = tmp_path / "exports"
    run_cli("twin-experiment", "export-plan", "--path", str(plan_path), "--output-dir", str(output_dir))

    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert manifest["kind"] == "twin_experiment_plan_export"
    assert manifest["approval"]["approved"] is True
    assert manifest["experiment_count"] == 2
    assert manifest["prediction_count_estimate"] == 2
    assert manifest["prediction_count_exported"] == 2
    assert manifest["export_count_check"]["requires_reapproval"] is False
    job_paths = [Path(row["job_path"]) for row in manifest["exports"]]
    assert all(path.exists() for path in job_paths)
    first_job = json.loads(job_paths[0].read_text())
    assert first_job["zwill"]["scenario_count"] == 1
    assert first_job["zwill"]["heldout_questions"] == ["q1"]
    assert first_job["zwill"]["approved_validation_plan"]["approval"]["approved"] is True
    assert first_job["zwill"]["approved_validation_plan"]["export_count_check"]["exported_prediction_count"] == 2

    experiments = json.loads((zwill_survey_path(tmp_path) / "digital_twin_jobs" / "experiments.json").read_text())
    by_approach = {row["approach_id"]: row for row in experiments["experiments"]}
    assert set(by_approach) == {"baseline", "more-context"}
    assert by_approach["baseline"]["plan"]["plan_id"] == "demo-plan"
    assert "Hypothesis" in by_approach["baseline"]["notes"]
    assert Path(by_approach["more-context"]["plan"]["job_path"]).exists()
    run_cli(
        "twin-approach",
        "add",
        "--survey",
        "demo",
        "--approach-id",
        "more-context",
        "--name",
        "Two observed answers",
        "--description",
        "Use two observed prior answers.",
        "--context-question-count",
        "2",
        "--model",
        "google:gemini-2.5-pro",
    )

    def write_note(approach_id: str) -> None:
        cli.cmd_twin_approach_note(
            argparse.Namespace(
                survey="demo",
                approach_id=approach_id,
                text=f"Concurrent note for {approach_id}",
                path=None,
                clear=False,
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(write_note, ["baseline", "more-context"]))
    approaches_after_notes = json.loads((zwill_survey_path(tmp_path) / "digital_twin_jobs" / "approaches.json").read_text())
    notes_by_approach = {row["approach_id"]: row.get("notes") for row in approaches_after_notes["approaches"]}
    assert notes_by_approach["baseline"] == "Concurrent note for baseline"
    assert notes_by_approach["more-context"] == "Concurrent note for more-context"

    diff_payload = cli.twin_approach_diff_payload(zwill_survey_path(tmp_path), "baseline", "more-context")
    changed = {row["field"]: row for row in diff_payload["differences"] if row["status"] != "same"}
    assert changed["context_question_count"]["left"] == 1
    assert changed["context_question_count"]["right"] == 2
    diff_html = tmp_path / "approach_diff.html"
    cli.cmd_twin_approach_diff(
        argparse.Namespace(
            survey="demo",
            left="baseline",
            right="more-context",
            format="html",
            path=str(diff_html),
            show_same=False,
        )
    )
    assert "Twin approach diff" in diff_html.read_text()

    package_dir = tmp_path / "package"
    package = cli.cmd_twin_experiment_package(
        argparse.Namespace(
            manifest=str(output_dir / "manifest.json"),
            output_dir=str(package_dir),
            survey=None,
            plan_id=None,
            env_path=str(tmp_path / ".env"),
        )
    )
    assert package["data"]["missing_job_count"] == 0
    package_manifest = json.loads((package_dir / "manifest.json").read_text())
    assert package_manifest["kind"] == "twin_experiment_run_package"
    assert (package_dir / "export_manifest.json").exists()
    assert (package_dir / "plan.json").exists()
    assert (package_dir / "approaches.json").exists()
    assert all(Path(row["package_job_path"]).exists() for row in package_manifest["jobs"])
    runbook = (package_dir / "RUN.md").read_text()
    assert "zwill edsl-run --job" in runbook
    assert f"--env-path {tmp_path / '.env'}" in runbook
    assert "zwill twin-experiment import-plan-results" in runbook


def test_twin_plan_authoring_helpers(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()

    approach_path = tmp_path / "baseline_approach.json"
    result = cli.cmd_twin_approach_scaffold(
        argparse.Namespace(
            survey="demo",
            approach_id="baseline",
            name=None,
            description=None,
            tag=None,
            context_questions="q2",
            context_question_count=1,
            include_agent_material=False,
            twin_material=None,
            model=["openai:gpt-5.5"],
            path=str(approach_path),
        )
    )
    assert result["data"]["path"] == str(approach_path)
    approach = json.loads(approach_path.read_text())
    assert approach["approach_id"] == "baseline"
    assert approach["construction"]["context_questions"] == "q2"

    plan_path = tmp_path / "plan.json"
    result = cli.cmd_twin_experiment_init_plan(
        argparse.Namespace(
            survey="demo",
            plan_id="demo_plan",
            path=str(plan_path),
            heldout_question=["q1"],
            heldout_questions=None,
            approach_id=["baseline"],
            sample_respondents=2,
            seed=456,
            context_question_count=1,
            model=["openai:gpt-5.5"],
            primary_metric="nll",
        )
    )
    assert result["data"]["path"] == str(plan_path)
    plan = json.loads(plan_path.read_text())
    assert plan["heldout_questions"] == "q1"
    assert plan["defaults"]["sample_respondents"] == 2
    assert plan["arms"] == [{"approach_id": "baseline"}]
    assert plan["approval"]["approved"] is False
    approval = cli.cmd_twin_experiment_approve(
        argparse.Namespace(
            path=str(plan_path),
            survey=None,
            approved_by="reviewer",
            note="Looks right.",
            estimated_cost="$1",
            estimated_time="1 minute",
        )
    )
    approved_plan = json.loads(plan_path.read_text())
    assert approval["data"]["approval"]["approved"] is True
    assert approved_plan["approval"]["approved_by"] == "reviewer"
    assert approved_plan["prediction_count_estimate"] == 2

    bundle_manifest = tmp_path / "bundle_manifest.json"
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "selected": {
                    "approach": "Baseline",
                    "metric_value": 0.25,
                }
            }
        )
    )
    bundle_manifest.write_text(
        json.dumps(
            {
                "kind": "twin_experiment_bundle",
                "survey": "demo",
                "plan_id": "demo_plan",
                "metric": "nll",
                "comparison_path": str(comparison),
                "microdata_html_path": "microdata.html",
            }
        )
    )
    capsys.readouterr()
    cli.cmd_twin_experiment_bundle_show(argparse.Namespace(manifest=str(bundle_manifest), format="json"))
    shown = json.loads(capsys.readouterr().out)
    assert shown["selected"]["approach"] == "Baseline"
    assert shown["artifacts"]["microdata_html"] == "microdata.html"

    nested_bundle = tmp_path / "bundle"
    nested_bundle.mkdir()
    nested_comparison = nested_bundle / "comparison.json"
    nested_comparison.write_text(json.dumps({"selected": {"approach": "Nested"}}))
    nested_manifest = nested_bundle / "manifest.json"
    nested_manifest.write_text(
        json.dumps(
            {
                "kind": "twin_experiment_bundle",
                "survey": "demo",
                "plan_id": "demo_plan",
                "metric": "nll",
                "comparison_path": "bundle/comparison.json",
                "microdata_html_path": "bundle/microdata.html",
            }
        )
    )
    capsys.readouterr()
    cli.cmd_twin_experiment_bundle_show(argparse.Namespace(manifest=str(nested_manifest), format="json"))
    nested_shown = json.loads(capsys.readouterr().out)
    assert nested_shown["selected"]["approach"] == "Nested"


def test_twin_experiment_export_requires_approved_plan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    plan_path = tmp_path / "draft_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "plan_id": "draft-plan",
                "survey": "demo",
                "heldout_question": "q1",
                "defaults": {"sample_respondents": 1, "context_question_count": 1},
                "arms": [{"approach_id": "baseline", "name": "Baseline"}],
            }
        )
    )

    with pytest.raises(cli.ZwillError, match="approved"):
        cli.cmd_twin_experiment_export_plan(
            argparse.Namespace(path=str(plan_path), survey=None, output_dir=str(tmp_path / "jobs"), plan_id=None, allow_unapproved=False)
        )


def test_twin_experiment_plan_status_import_and_bundle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "plan_id": "bundle-plan",
                "survey": "demo",
                "heldout_question": "q1",
                "defaults": {"respondent": ["r1", "r2"], "context_question_count": 1},
                "arms": [
                    {"approach_id": "baseline", "name": "Baseline"},
                    {
                        "approach_id": "calibrated",
                        "name": "Calibrated",
                        "construction": {"model": ["google:gemini-2.5-pro"]},
                    },
                ],
            }
        )
    )
    run_cli("twin-experiment", "approve", "--path", str(plan_path))
    output_dir = tmp_path / "jobs"
    run_cli("twin-experiment", "export-plan", "--path", str(plan_path), "--output-dir", str(output_dir))
    manifest = json.loads((output_dir / "manifest.json").read_text())
    status_payload = cli.twin_plan_status_payload(zwill_survey_path(tmp_path), "bundle-plan")
    assert status_payload["imported_count"] == 0
    assert status_payload["ready_for_comparison"] is False

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    fixture = json.loads((FIXTURES / "twin_results.json").read_text())
    for index, exported in enumerate(manifest["exports"], start=1):
        payload = json.loads(json.dumps(fixture))
        payload["zwill"]["digital_twin_job_id"] = exported["job_id"]
        if index == 2:
            payload["data"][1]["answer"]["response_probabilities"] = '{"probabilities":[0.4,0.6],"notes":"corrected"}'
        (results_dir / f"results_{index}.json").write_text(json.dumps(payload))

    import_result = cli.cmd_twin_experiment_import_plan_results(
        argparse.Namespace(
            manifest=str(output_dir / "manifest.json"),
            results_dir=str(results_dir),
            survey=None,
            replace=False,
        )
    )
    assert import_result["data"]["import_count"] == 2
    assert import_result["data"]["missing_jobs"] == []
    status_payload = cli.twin_plan_status_payload(zwill_survey_path(tmp_path), "bundle-plan")
    assert status_payload["imported_count"] == 2
    assert status_payload["ready_for_comparison"] is True
    note_result = cli.cmd_twin_experiment_note(
        argparse.Namespace(
            survey="demo",
            plan_id="bundle-plan",
            text="Compare whether calibrated context improves probability quality.",
            path=None,
            clear=False,
        )
    )
    assert "calibrated context" in note_result["data"]["notes"]

    bundle_dir = tmp_path / "bundle"
    bundle = cli.cmd_twin_experiment_bundle(
        argparse.Namespace(
            survey="demo",
            plan_id="bundle-plan",
            metric="nll",
            model="google:gpt-5.5",
            output_dir=str(bundle_dir),
            report_export=False,
            report_model=None,
            model_param=["max_tokens=12000", "reasoning_effort=high"],
            models=None,
            service_name="openai",
        )
    )
    assert Path(bundle["data"]["comparison_path"]).exists()
    assert Path(bundle["data"]["microdata_html_path"]).exists()
    assert Path(bundle["data"]["plot_manifest_path"]).exists()
    comparison = json.loads((bundle_dir / "comparison.json").read_text())
    assert comparison["plan_id"] == "bundle-plan"
    assert len(comparison["comparisons"]) == 2
    dashboard_path = tmp_path / "dashboard.html"
    dashboard = cli.cmd_twin_experiment_dashboard(
        argparse.Namespace(
            survey="demo",
            plan_id="bundle-plan",
            metric="nll",
            model="google:gpt-5.5",
            bundle_manifest=str(bundle_dir / "manifest.json"),
            path=str(dashboard_path),
            json_path=None,
        )
    )
    assert Path(dashboard["data"]["path"]).exists()
    dashboard_html = dashboard_path.read_text()
    assert "Twin Experiment Dashboard" in dashboard_html
    assert "Plan Status" in dashboard_html
    assert "Performance" in dashboard_html
    assert "Compare whether calibrated context" in dashboard_html
    dashboard_json = json.loads(dashboard_path.with_suffix(".json").read_text())
    assert dashboard_json["selected"]["approach"] == "Calibrated"
    assert "calibrated context" in dashboard_json["plan_notes"]


def test_twin_job_agent_material_filters_truncates_and_affects_job_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "profile",
        "--title",
        "Profile",
        "--text",
        "Included profile preference material.",
        "--tag",
        "preference",
    )
    run_cli(
        "agent-material",
        "add",
        "--survey",
        "demo",
        "--respondent-id",
        "r1",
        "--kind",
        "note",
        "--title",
        "Note",
        "--text",
        "Excluded operational material.",
        "--tag",
        "ops",
    )
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )

    filtered = cli.build_edsl_digital_twin_job_dict(
        "demo",
        default_twin_export_args(
            include_agent_material=True,
            agent_material_tag=["preference"],
            max_agent_material_chars=42,
        ),
    )
    text = filtered["scenarios"][0]["agent_material_text"]
    assert "Included profile" in text
    assert "operational" not in text
    assert "Truncated to max agent material characters" in text

    changed_path = zwill_survey_path(tmp_path) / "agent_material.jsonl"
    rows = cli.read_jsonl(changed_path)
    rows[0]["body_markdown"] = "Different included profile material."
    cli.rewrite_jsonl(changed_path, rows)
    changed = cli.build_edsl_digital_twin_job_dict(
        "demo",
        default_twin_export_args(
            include_agent_material=True,
            agent_material_tag=["preference"],
            max_agent_material_chars=42,
        ),
    )
    assert changed["zwill"]["digital_twin_job_id"] != filtered["zwill"]["digital_twin_job_id"]


def edsl_test_python() -> Path:
    return Path(os.environ.get("ZWILL_TEST_EDSL_PYTHON", str(DEFAULT_EDSL_TEST_PYTHON)))


def test_agent_material_example_script_dry_run_smoke(tmp_path: Path) -> None:
    python_path = edsl_test_python()
    if not python_path.exists():
        pytest.skip("EDSL test Python is not available.")
    script = Path(__file__).resolve().parents[1] / "examples" / "hello_world" / "agent_material_twin.sh"
    workdir = tmp_path / "agent_material_example"
    env = {
        **os.environ,
        "ZWILL_PYTHON": str(python_path),
        "ZWILL_EXAMPLE_DRY_RUN": "1",
        "ZWILL_AGENT_MATERIAL_EXAMPLE_DIR": str(workdir),
    }

    result = subprocess.run([str(script)], cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr + result.stdout
    assert (workdir / "without_material_job.edsl.json").exists()
    assert (workdir / "with_material_job.edsl.json").exists()
    assert not (workdir / "without_material_results.json.gz").exists()
    with_material = json.loads((workdir / "with_material_job.edsl.json").read_text())
    assert with_material["zwill"]["include_agent_material"] is True


def test_agent_list_example_script_dry_run_smoke(tmp_path: Path) -> None:
    python_path = edsl_test_python()
    if not python_path.exists():
        pytest.skip("EDSL test Python is not available.")
    script = Path(__file__).resolve().parents[1] / "examples" / "hello_world" / "agent_list_study.sh"
    workdir = tmp_path / "agent_list_example"
    env = {
        **os.environ,
        "ZWILL_PYTHON": str(python_path),
        "ZWILL_EXAMPLE_DRY_RUN": "1",
        "ZWILL_AGENT_LIST_EXAMPLE_DIR": str(workdir),
    }

    result = subprocess.run([str(script)], cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr + result.stdout
    assert (workdir / "agent_list.edsl.json").exists()
    assert (workdir / "agent_study_job.edsl.json").exists()
    assert not (workdir / "agent_study_results.json.gz").exists()
    agent_list = json.loads((workdir / "agent_list.edsl.json").read_text())
    job = json.loads((workdir / "agent_study_job.edsl.json").read_text())
    assert agent_list["agent_list"][0]["instruction"]
    assert job["zwill"]["agent_study_job_id"]


def test_probability_results_import_and_reports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    run_cli("survey", "create", "--name", "demo")
    run_cli(
        "question",
        "add",
        "--survey",
        "demo",
        "--question-name",
        "q1",
        "--question-type",
        "multiple_choice",
        "--question-text",
        "Pick one",
        "--question-option",
        "yes",
        "--question-option",
        "no",
    )
    run_cli("respondent", "add", "--survey", "demo", "--respondent-id", "r1")
    run_cli("respondent", "add", "--survey", "demo", "--respondent-id", "r2")
    run_cli("answer", "add", "--survey", "demo", "--respondent-id", "r1", "--question", "q1", "--answer", "yes")
    run_cli("answer", "add", "--survey", "demo", "--respondent-id", "r2", "--question", "q1", "--answer", "no")
    run_cli("commit", "--survey", "demo")

    results = {
        "edsl_class_name": "Results",
        "zwill": {"probability_job_id": "job-demo"},
        "data": [
            {
                "scenario": {
                    "source_question_name": "q1",
                    "source_question_text": "Pick one",
                    "option_labels": ["yes", "no"],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '```json\n{"probabilities":[0.6,0.4],"notes":"ok"}\n```'},
            }
        ],
    }
    results_path = tmp_path / "results.json.gz"
    with gzip.open(results_path, "wt") as f:
        json.dump(results, f)

    run_cli("prob-results", "import", "--survey", "demo", "--path", str(results_path))
    predictions = (zwill_survey_path(tmp_path) / "probability_predictions.jsonl").read_text()
    assert '"yes":0.6' in predictions

    json_path = tmp_path / "report.json"
    csv_path = tmp_path / "report.csv"
    html_path = tmp_path / "report.html"
    run_cli("prob-results", "report", "--survey", "demo", "--job-id", "job-demo", "--format", "json", "--path", str(json_path))
    run_cli("prob-results", "report", "--survey", "demo", "--job-id", "job-demo", "--format", "csv", "--path", str(csv_path))
    run_cli("prob-results", "report", "--survey", "demo", "--job-id", "job-demo", "--format", "html", "--path", str(html_path))

    report = json.loads(json_path.read_text())
    assert report["rows"][0]["kl_divergence"] >= 0
    assert "uniform_brier" in csv_path.read_text().splitlines()[0]
    html = html_path.read_text()
    assert "perf-arrow" in html
    assert "report-data" in html
    assert "No generated one-shot analysis has been imported" in html

    def fake_build_one_shot_job(args, report_context):
        assert report_context["report_kind"] == "frontier_generated_one_shot_marginal_analysis"
        assert report_context["survey"] == "demo"
        assert report_context["analysis_target"]["raw_prediction_rows_in_context"] is False
        assert report_context["headline_metrics"]["questions_evaluated"] == 1
        job = {
            "edsl_class_name": "Jobs",
            "survey": {"questions": [{"question_name": "one_shot_analysis_markdown"}]},
            "zwill": {
                "practitioner_report_id": "one-shot-report-demo",
                "practitioner_report_question_name": "one_shot_analysis_markdown",
                "report_kind": "one_shot_marginal_analysis",
            },
        }
        context = {
            "report_id": "one-shot-report-demo",
            "one_shot_analysis_context": report_context,
            "generation": {"mode": "job_exported", "report_id": "one-shot-report-demo", "model": "openai:gpt-5.5"},
        }
        return job, context, "Write a generated one-shot analysis"

    monkeypatch.setattr(cli, "build_edsl_one_shot_analysis_report_job_dict", fake_build_one_shot_job)
    generated_html_path = tmp_path / "one-shot-generated.html"
    run_cli(
        "prob-results",
        "analysis-export",
        "--survey",
        "demo",
        "--job-id",
        "job-demo",
        "--path",
        str(generated_html_path),
        "--model",
        "openai:gpt-5.5",
    )
    report_dir = zwill_project_path(tmp_path) / "practitioner_reports" / "one-shot-report-demo"
    assert (report_dir / "context.json").exists()
    stored_context = json.loads((report_dir / "context.json").read_text())
    assert stored_context["one_shot_analysis_context"]["analysis_target"]["raw_prediction_rows_in_context"] is False

    analysis_results = {
        "edsl_class_name": "Results",
        "zwill": {
            "practitioner_report_id": "one-shot-report-demo",
            "practitioner_report_question_name": "one_shot_analysis_markdown",
        },
        "data": [
            {
                "answer": {
                    "one_shot_analysis_markdown": (
                        "## Analysis\n\n"
                        "The one-shot baseline tracks this aggregate split and should be treated as the deployable baseline."
                    )
                }
            }
        ],
    }
    analysis_results_path = tmp_path / "one_shot_analysis_results.json.gz"
    with gzip.open(analysis_results_path, "wt") as f:
        json.dump(analysis_results, f)

    run_cli("prob-results", "analysis-import", "--report-id", "one-shot-report-demo", "--path", str(analysis_results_path))
    run_cli("prob-results", "analysis-render", "--report-id", "one-shot-report-demo", "--path", str(generated_html_path))
    rendered = generated_html_path.read_text()
    assert "The one-shot baseline tracks this aggregate split" in rendered
    assert rendered.count("<h2>Analysis</h2>") == 1

    bundle_dir = tmp_path / "bundle"
    run_cli("report", "build", "--survey", "demo", "--probability-job-id", "job-demo", "--path", str(bundle_dir))
    bundle_html = (bundle_dir / "one-shot-marginals.html").read_text()
    assert "The one-shot baseline tracks this aggregate split" in bundle_html
    assert (bundle_dir / "data" / "one-shot-analysis.md").exists()

    default_bundle_dir = tmp_path / "bundle-default"
    run_cli("report", "build", "--survey", "demo", "--path", str(default_bundle_dir))
    default_bundle_html = (default_bundle_dir / "one-shot-marginals.html").read_text()
    assert "The one-shot baseline tracks this aggregate split" in default_bundle_html


def test_twin_results_import_and_reports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    run_cli("survey", "create", "--name", "demo")
    run_cli(
        "question",
        "add",
        "--survey",
        "demo",
        "--question-name",
        "q1",
        "--question-type",
        "multiple_choice",
        "--question-text",
        "Pick one",
        "--question-option",
        "yes",
        "--question-option",
        "no",
    )
    run_cli("respondent", "add", "--survey", "demo", "--respondent-id", "r1")
    run_cli("answer", "add", "--survey", "demo", "--respondent-id", "r1", "--question", "q1", "--answer", "yes")
    run_cli("commit", "--survey", "demo")

    results = {
        "edsl_class_name": "Results",
        "zwill": {
            "digital_twin_job_id": "twin-demo",
            "heldout_questions": ["q1"],
            "context_question_count": None,
            "sample_respondents": None,
            "seed": 123,
            "prompt_variant": "answer-commonness-confidence",
            "scenario_count": 1,
            "include_agent_material": False,
            "twin_material_count": 0,
        },
        "data": [
            {
                "scenario": {
                    "respondent_id": "r1",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["yes", "no"],
                    "actual_answer": "yes",
                    "observed_answers": [],
                    "agent_material_text": "",
                    "twin_material_text": "",
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "agent": {"traits": {"segment": "test"}},
                "indices": {"agent": 0, "scenario": 0, "model": 0},
                "interview_hash": "abc123",
                "answer": {"response_probabilities": '```json\n{"probabilities":[0.8,0.2],"notes":"ok"}\n```'},
                "raw_model_response": {
                    "response_probabilities_raw_model_response": {
                        "choices": [{"message": {"content": '{"probabilities":[0.8,0.2],"notes":"ok"}'}}]
                    }
                },
                "prompt": {
                    "response_probabilities_system_prompt": {"text": "You are a survey response model."},
                    "response_probabilities_user_prompt": {"text": "Predict q1 for respondent r1."},
                },
                "question_to_attributes": {
                    "response_probabilities": {
                        "question_text": "Predict {{ heldout_question_name }} for {{ respondent_id }}.",
                        "question_type": "free_text",
                    }
                },
            }
        ],
    }
    results_path = tmp_path / "twin_results.json.gz"
    with gzip.open(results_path, "wt") as f:
        json.dump(results, f)

    run_cli("twin-results", "import", "--survey", "demo", "--path", str(results_path))
    predictions = (zwill_survey_path(tmp_path) / "digital_twin_predictions.jsonl").read_text()
    assert '"probability_actual":0.8' in predictions
    assert '"marginal_probability_actual":1.0' in predictions
    assert '"empirical_marginal_probability_actual":1.0' in predictions
    assert '"model_label":"openai:gpt-5.5"' in predictions

    json_path = tmp_path / "twin_report.json"
    csv_path = tmp_path / "twin_report.csv"
    html_path = tmp_path / "twin_report.html"
    run_report_json_path = tmp_path / "twin_run_report.json"
    run_report_html_path = tmp_path / "twin_run_report.html"
    run_cli("twin-results", "report", "--survey", "demo", "--job-id", "twin-demo", "--format", "json", "--path", str(json_path))
    run_cli("twin-results", "report", "--survey", "demo", "--job-id", "twin-demo", "--format", "csv", "--path", str(csv_path))
    run_cli("twin-results", "report", "--survey", "demo", "--job-id", "twin-demo", "--format", "html", "--path", str(html_path))
    run_cli("twin-results", "run-report", "--survey", "demo", "--job-id", "twin-demo", "--format", "json", "--path", str(run_report_json_path))
    run_cli("twin-results", "run-report", "--survey", "demo", "--job-id", "twin-demo", "--format", "html", "--path", str(run_report_html_path))

    report = json.loads(json_path.read_text())
    assert report["rows"][0]["top1_correct"] == 1
    assert report["rows"][0]["marginal_probability_actual"] == 1.0
    assert report["rows"][0]["empirical_marginal_probability_actual"] == 1.0
    assert report["summary"]["openai:gpt-5.5"]["mean_brier"] < report["summary"]["openai:gpt-5.5"]["mean_uniform_brier"]
    assert "empirical_marginal_probability_actual" in csv_path.read_text().splitlines()[0]
    assert "twin-report-data" in html_path.read_text()
    run_report = json.loads(run_report_json_path.read_text())
    assert run_report["construction"]["prompt_variant"] == "answer-commonness-confidence"
    assert run_report["questions"][0]["question"] == "q1"
    assert run_report["prompt_examples"][0]["prompt_template"] == "Predict {{ heldout_question_name }} for {{ respondent_id }}."
    assert run_report["prompt_examples"][0]["user_prompt"] == "Predict q1 for respondent r1."
    assert run_report["prompt_examples"][0]["twin"]["agent_index"] == 0
    assert run_report["prompt_examples"][0]["twin"]["agent_traits"] == {"segment": "test"}
    assert run_report["prompt_examples"][0]["model_answer"]["response_probabilities"].startswith("```json")
    assert run_report["prompt_examples"][0]["model_response_text"] == '{"probabilities":[0.8,0.2],"notes":"ok"}'
    run_report_html = run_report_html_path.read_text()
    assert "Twin Run Report" in run_report_html
    assert "Prompt Examples" in run_report_html
    assert "Twin identity" in run_report_html
    assert "Model answer" in run_report_html
    assert "Raw model response text" in run_report_html
    assert "Jinja prompt template" in run_report_html
    assert "Scenario inputs" in run_report_html
    assert "twin-run-report-data" in run_report_html


def test_rank_battery_uses_joint_rank_twin_workflow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    run_cli("survey", "create", "--name", "demo")
    for question_name, item in [
        ("q021_c11_bplus_feat_app_1", "Fast shortlist"),
        ("q022_c11_bplus_feat_app_2", "Always-on hiring agent"),
        ("q023_c11_bplus_feat_app_3", "Talent quality guarantee"),
    ]:
        run_cli(
            "question",
            "add",
            "--survey",
            "demo",
            "--question-name",
            question_name,
            "--question-type",
            "multiple_choice",
            "--question-text",
            f"Please rank each from most appealing to least appealing - {item}",
            "--question-option",
            "1",
            "--question-option",
            "2",
            "--question-option",
            "3",
        )
    run_cli(
        "question",
        "add",
        "--survey",
        "demo",
        "--question-name",
        "segment",
        "--question-type",
        "multiple_choice",
        "--question-text",
        "What segment?",
        "--question-option",
        "startup",
        "--question-option",
        "enterprise",
    )
    for respondent_id, ranks in [
        ("r1", {"q021_c11_bplus_feat_app_1": "1", "q022_c11_bplus_feat_app_2": "2", "q023_c11_bplus_feat_app_3": "3", "segment": "startup"}),
        ("r2", {"q021_c11_bplus_feat_app_1": "3", "q022_c11_bplus_feat_app_2": "1", "q023_c11_bplus_feat_app_3": "2", "segment": "enterprise"}),
    ]:
        run_cli("respondent", "add", "--survey", "demo", "--respondent-id", respondent_id)
        for question, answer in ranks.items():
            run_cli("answer", "add", "--survey", "demo", "--respondent-id", respondent_id, "--question", question, "--answer", answer)
    run_cli("commit", "--survey", "demo")

    sdir = zwill_survey_path(tmp_path)
    questions = {row["question_name"]: row for row in cli.read_jsonl(sdir / "questions.jsonl")}
    assert questions["q021_c11_bplus_feat_app_1"]["question_type"] == "rank_item"
    assert questions["q021_c11_bplus_feat_app_1"]["rank_task_id"] == "c11_bplus_feat_app"

    monkeypatch.setattr(cli, "load_edsl_classes", lambda: (FakeAgent, FakeAgentList, FakeQuestion, FakeSurvey))
    survey = cli.build_edsl_survey_dict("demo")
    survey_questions = survey["questions"]
    assert [question["question_name"] for question in survey_questions] == ["c11_bplus_feat_app", "segment"]
    assert survey_questions[0]["question_type"] == "rank"
    assert survey_questions[0]["question_options"] == ["Fast shortlist", "Always-on hiring agent", "Talent quality guarantee"]

    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )
    job = cli.build_edsl_rank_utility_twin_job_dict(
        "demo",
        argparse.Namespace(
            rank_task_id=["c11_bplus_feat_app"],
            rank_task_ids=None,
            heldout_question=None,
            heldout_questions=None,
            context_question=None,
            context_questions=None,
            exclude_context_question=[],
            respondent=None,
            respondents=None,
            sample_respondents=None,
            seed=123,
            limit_respondents=None,
            complete_cases=False,
            allow_missing_actual=False,
            context_question_count=None,
            prompt_variant="raw",
            include_agent_material=False,
            max_agent_material_chars=None,
            twin_material=None,
            max_twin_material_chars=None,
            model=["openai:gpt-5.5"],
            models=None,
            service_name=None,
            model_param=None,
            job_question_name="rank_utility_scores",
        ),
    )
    assert job["zwill"]["rank_utility_twin_job_id"]
    assert job["zwill"]["rank_task_ids"] == ["c11_bplus_feat_app"]
    assert len(job["scenarios"]) == 2
    first_scenario = job["scenarios"][0]
    assert first_scenario["rank_task_id"] == "c11_bplus_feat_app"
    assert len(first_scenario["rank_items"]) == 3
    assert all(answer["question_name"] == "segment" for answer in first_scenario["observed_answers"])
    assert "q021_c11_bplus_feat_app_1" not in first_scenario["observed_answers_text"]

    results = {
        "edsl_class_name": "Results",
        "zwill": job["zwill"],
        "data": [
            {
                "scenario": scenario,
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {
                    "rank_utility_scores": json.dumps(
                        {
                            "scores": {
                                "q021_c11_bplus_feat_app_1": 90 if scenario["respondent_id"] == "r1" else 10,
                                "q022_c11_bplus_feat_app_2": 60 if scenario["respondent_id"] == "r1" else 95,
                                "q023_c11_bplus_feat_app_3": 20 if scenario["respondent_id"] == "r1" else 50,
                            },
                            "confidence": 0.7,
                            "notes": "rank utility",
                        }
                    )
                },
            }
            for scenario in job["scenarios"]
        ],
    }
    results_path = tmp_path / "rank_results.json.gz"
    with gzip.open(results_path, "wt") as f:
        json.dump(results, f)
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(results_path))
    rank_rows = cli.read_jsonl(cli.rank_twin_predictions_path(sdir))
    assert len(rank_rows) == 2
    assert rank_rows[0]["spearman"] == pytest.approx(1.0)
    assert rank_rows[0]["pairwise_order_accuracy"] == pytest.approx(1.0)

    report_path = tmp_path / "rank_report.json"
    html_path = tmp_path / "rank_report.html"
    run_cli("twin-results", "rank-report", "--survey", "demo", "--job-id", job["zwill"]["rank_utility_twin_job_id"], "--format", "json", "--path", str(report_path))
    run_cli("twin-results", "rank-report", "--survey", "demo", "--job-id", job["zwill"]["rank_utility_twin_job_id"], "--format", "html", "--path", str(html_path))
    report = json.loads(report_path.read_text())
    assert report["summary"]["by_model"]["openai:gpt-5.5"]["mean_spearman"] == pytest.approx(1.0)
    assert "Rank Utility Twin Validation" in html_path.read_text()


def test_twin_results_true_holdout_import_export_and_marginal_diagnostics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    run_cli("survey", "create", "--name", "demo")
    run_cli(
        "question",
        "add",
        "--survey",
        "demo",
        "--question-name",
        "q1",
        "--question-type",
        "multiple_choice",
        "--question-text",
        "Pick one",
        "--question-option",
        "yes",
        "--question-option",
        "no",
    )
    run_cli("respondent", "add", "--survey", "demo", "--respondent-id", "r1")
    run_cli("commit", "--survey", "demo")

    twin_results = {
        "edsl_class_name": "Results",
        "zwill": {"digital_twin_job_id": "twin-true"},
        "data": [
            {
                "scenario": {
                    "respondent_id": "r1",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["yes", "no"],
                    "observed_answers": [],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {
                    "response_probabilities": json.dumps(
                        {
                            "probabilities": [0.7, 0.3],
                            "confidence": 0.62,
                            "evidence_summary": "leans yes",
                            "notes": "ok",
                        }
                    )
                },
            }
        ],
    }
    twin_path = tmp_path / "true_holdout_results.json"
    twin_path.write_text(json.dumps(twin_results))
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(twin_path), "--allow-missing-actual")

    predictions = (zwill_survey_path(tmp_path) / "digital_twin_predictions.jsonl").read_text()
    assert '"actual_answer":null' in predictions
    assert '"confidence":0.62' in predictions
    assert '"evidence_summary":"leans yes"' in predictions

    export_path = tmp_path / "predictions.csv"
    run_cli("twin-results", "export", "--survey", "demo", "--job-id", "twin-true", "--path", str(export_path))
    export_text = export_path.read_text()
    assert "option_label,probability" in export_text
    assert "yes,0.7" in export_text
    assert "no,0.3" in export_text

    probability_results = {
        "edsl_class_name": "Results",
        "zwill": {"probability_job_id": "one-shot"},
        "data": [
            {
                "scenario": {
                    "source_question_name": "q1",
                    "source_question_text": "Pick one",
                    "option_labels": ["yes", "no"],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.6,0.4],"notes":"target"}'},
            }
        ],
    }
    probability_path = tmp_path / "probability_results.json"
    probability_path.write_text(json.dumps(probability_results))
    run_cli("prob-results", "import", "--survey", "demo", "--path", str(probability_path))

    summary_path = tmp_path / "marginal_summary.csv"
    option_path = tmp_path / "marginal_options.csv"
    run_cli(
        "twin-results",
        "marginal-diagnostics",
        "--survey",
        "demo",
        "--job-id",
        "twin-true",
        "--target-job-id",
        "one-shot",
        "--format",
        "csv",
        "--path",
        str(summary_path),
        "--option-path",
        str(option_path),
    )
    assert "l1" in summary_path.read_text().splitlines()[0]
    assert ",0.2,0.1,0.020000000000000004," in summary_path.read_text()
    assert "yes,0.7,0.6" in option_path.read_text()


def test_true_holdout_export_import_dir_and_package_workflow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    run_cli("survey", "create", "--name", "demo")
    run_cli(
        "question",
        "add",
        "--survey",
        "demo",
        "--question-name",
        "q1",
        "--question-type",
        "multiple_choice",
        "--question-text",
        "Observed pick",
        "--question-option",
        "yes",
        "--question-option",
        "no",
    )
    run_cli("respondent", "add", "--survey", "demo", "--respondent-id", "r1")
    run_cli("respondent", "add", "--survey", "demo", "--respondent-id", "r2")
    run_cli("answer", "add", "--survey", "demo", "--respondent-id", "r1", "--question", "q1", "--answer", "yes")
    run_cli("answer", "add", "--survey", "demo", "--respondent-id", "r2", "--question", "q1", "--answer", "no")
    run_cli("commit", "--survey", "demo")
    specs_path = tmp_path / "holdout_specs.jsonl"
    specs_path.write_text(
        "\n".join(
            [
                json.dumps({"question_name": "qh1", "question_text": "Holdout one", "question_options": ["A", "B"]}),
                json.dumps({"question_name": "qh2", "question_text": "Holdout two", "question_options": ["C", "D"]}),
            ]
        )
        + "\n"
    )
    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )
    output_dir = tmp_path / "holdout_run"
    result = cli.cmd_twin_study_export_holdout(
        argparse.Namespace(
            survey="demo",
            output_dir=str(output_dir),
            chunk_size=3,
            job_id_prefix="ow_true",
            approved_plan=None,
            allow_unapproved=True,
            question_specs=str(specs_path),
            question_specs_workbook=None,
            question_specs_sheet="Questions",
            question_specs_code_column="Question code",
            question_specs_text_column="Question text",
            question_specs_option_prefix="Answer option ",
            question_specs_labels_column="Answer value labels",
            heldout_question=["qh1", "qh2"],
            heldout_questions=None,
            respondent=None,
            respondents=None,
            sample_respondents=None,
            seed=None,
            complete_cases=False,
            balance_actual=False,
            stratify_actual=False,
            limit_respondents=None,
            context_question=None,
            context_questions=None,
            exclude_context_question=None,
            context_question_count=None,
            include_agent_material=False,
            agent_material_kind=None,
            agent_material_tag=None,
            max_agent_material_chars=None,
            twin_material=None,
            max_twin_material_chars=None,
            prompt_variant="answer-commonness-confidence",
            model=["openai:gpt-5.5"],
            models=None,
            service_name=None,
            model_param=None,
            job_question_name="response_probabilities",
        )
    )
    assert result["data"]["chunk_count"] == 2
    first_job = json.loads((output_dir / "chunk_001_job.edsl.json").read_text())
    assert first_job["zwill"]["digital_twin_job_id"] == "ow_true_chunk_001"
    assert first_job["scenarios"][0]["actual_answer"] is None
    assert "Answer commonness" in first_job["scenarios"][0]["observed_answers_text"]

    for job_path in sorted(output_dir.glob("chunk_*_job.edsl.json")):
        job = json.loads(job_path.read_text())
        rows = []
        for scenario in job["scenarios"]:
            rows.append(
                {
                    "scenario": scenario,
                    "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                    "answer": {"response_probabilities": '{"probabilities":[0.75,0.25],"confidence":0.7,"notes":"ok"}'},
                }
            )
        results = {"edsl_class_name": "Results", "zwill": job["zwill"], "data": rows}
        results_path = output_dir / job_path.name.replace("_job.edsl.json", "_results.json.gz")
        with gzip.open(results_path, "wt") as f:
            json.dump(results, f)

    import_result = cli.cmd_twin_study_import_results_dir(
        argparse.Namespace(
            survey="demo",
            results_dir=str(output_dir),
            job_id_prefix="ow_true",
            pattern=None,
            allow_missing_actual=True,
            replace=False,
        )
    )
    assert import_result["data"]["result_count"] == 2
    assert import_result["data"]["extracted_count"] == 4

    package_path = tmp_path / "ep_ow_predictions.csv"
    package_result = cli.cmd_twin_results_package(
        argparse.Namespace(
            survey="demo",
            manifest=None,
            job_id=None,
            jobs="ow_true_chunk_001,ow_true_chunk_002",
            model=None,
            question=None,
            questions=None,
            format="long",
            path=str(package_path),
            zip_path=None,
        )
    )
    assert package_result["data"]["csv_rows"] == 8
    assert package_path.exists()
    assert package_path.with_suffix(".zip").exists()
    assert "confidence" in package_path.read_text().splitlines()[0]

    manifest_package_path = tmp_path / "ep_ow_predictions_from_manifest.csv"
    manifest_package_result = cli.cmd_twin_results_package(
        argparse.Namespace(
            survey="demo",
            manifest=str(output_dir / "import_results_manifest.json"),
            job_id=None,
            jobs=None,
            model=None,
            question=None,
            questions=None,
            format="long",
            path=str(manifest_package_path),
            zip_path=None,
        )
    )
    assert manifest_package_result["data"]["csv_rows"] == 8
    assert manifest_package_path.with_suffix(".zip").exists()


def test_twin_results_calibrate_marginal_to_probability_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_cli("init")
    run_cli("survey", "create", "--name", "demo")
    run_cli(
        "question",
        "add",
        "--survey",
        "demo",
        "--question-name",
        "q1",
        "--question-type",
        "multiple_choice",
        "--question-text",
        "Pick one",
        "--question-option",
        "yes",
        "--question-option",
        "no",
    )
    run_cli("respondent", "add", "--survey", "demo", "--respondent-id", "r1")
    run_cli("respondent", "add", "--survey", "demo", "--respondent-id", "r2")
    run_cli("answer", "add", "--survey", "demo", "--respondent-id", "r1", "--question", "q1", "--answer", "yes")
    run_cli("answer", "add", "--survey", "demo", "--respondent-id", "r2", "--question", "q1", "--answer", "no")
    run_cli("commit", "--survey", "demo")

    twin_results = {
        "edsl_class_name": "Results",
        "zwill": {"digital_twin_job_id": "twin-source"},
        "data": [
            {
                "scenario": {
                    "respondent_id": "r1",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["yes", "no"],
                    "actual_answer": "yes",
                    "observed_answers": [],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.9,0.1],"notes":"ok"}'},
            },
            {
                "scenario": {
                    "respondent_id": "r2",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["yes", "no"],
                    "actual_answer": "no",
                    "observed_answers": [],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.6,0.4],"notes":"ok"}'},
            },
        ],
    }
    twin_path = tmp_path / "twin_results.json.gz"
    with gzip.open(twin_path, "wt") as f:
        json.dump(twin_results, f)
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(twin_path))

    target_results = {
        "edsl_class_name": "Results",
        "zwill": {"probability_job_id": "target-prob"},
        "data": [
            {
                "scenario": {
                    "source_question_name": "q1",
                    "source_question_text": "Pick one",
                    "option_labels": ["yes", "no"],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.5,0.5],"notes":"target"}'},
            }
        ],
    }
    target_path = tmp_path / "target_results.json.gz"
    with gzip.open(target_path, "wt") as f:
        json.dump(target_results, f)
    run_cli("prob-results", "import", "--survey", "demo", "--path", str(target_path))

    run_cli(
        "twin-results",
        "calibrate-marginal",
        "--survey",
        "demo",
        "--job-id",
        "twin-source",
        "--target-job-id",
        "target-prob",
        "--output-job-id",
        "twin-calibrated",
    )
    metadata = json.loads((zwill_survey_path(tmp_path) / "digital_twin_jobs" / "twin-calibrated" / "import.json").read_text())
    assert metadata["job_id"] == "twin-calibrated"
    assert metadata["extracted_count"] == 2
    assert metadata["issue_count"] == 0
    assert metadata["diagnostics"][0]["converged"] is True

    rows = [
        json.loads(line)
        for line in (zwill_survey_path(tmp_path) / "digital_twin_predictions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    calibrated = [row for row in rows if row["job_id"] == "twin-calibrated"]
    assert len(calibrated) == 2
    mean_yes = sum(row["probabilities"]["yes"] for row in calibrated) / len(calibrated)
    mean_no = sum(row["probabilities"]["no"] for row in calibrated) / len(calibrated)
    assert mean_yes == pytest.approx(0.5)
    assert mean_no == pytest.approx(0.5)
    assert all(row["source_job_id"] == "twin-source" for row in calibrated)
    assert all(row["calibration"]["target_job_id"] == "target-prob" for row in calibrated)

    json_path = tmp_path / "calibrated_report.json"
    run_cli("twin-results", "report", "--survey", "demo", "--job-id", "twin-calibrated", "--format", "json", "--path", str(json_path))
    report = json.loads(json_path.read_text())
    assert report["summary"]["openai:gpt-5.5"]["rows"] == 2
    assert report["health"]["import"]["source_job_id"] == "twin-source"


def test_twin_report_groups_by_provider_qualified_model() -> None:
    rows = [
        {
            "job_id": "twin-demo",
            "survey": "demo",
            "respondent_id": "r1",
            "heldout_question": "q1",
            "heldout_question_text": "Pick one",
            "actual_answer": "yes",
            "model": "same-name",
            "service": "openai",
            "option_labels": ["yes", "no"],
            "probabilities": {"yes": 0.8, "no": 0.2},
            "probability_actual": 0.8,
            "uniform_probability_actual": 0.5,
            "negative_log_likelihood": 0.22,
            "uniform_negative_log_likelihood": 0.69,
            "brier": 0.08,
            "uniform_brier": 0.5,
            "brier_improvement": 0.42,
            "top1_correct": 1,
            "actual_rank": 1,
        },
        {
            "job_id": "twin-demo",
            "survey": "demo",
            "respondent_id": "r1",
            "heldout_question": "q1",
            "heldout_question_text": "Pick one",
            "actual_answer": "yes",
            "model": "same-name",
            "service": "google",
            "option_labels": ["yes", "no"],
            "probabilities": {"yes": 0.7, "no": 0.3},
            "probability_actual": 0.7,
            "uniform_probability_actual": 0.5,
            "negative_log_likelihood": 0.36,
            "uniform_negative_log_likelihood": 0.69,
            "brier": 0.18,
            "uniform_brier": 0.5,
            "brier_improvement": 0.32,
            "top1_correct": 1,
            "actual_rank": 1,
        },
    ]

    report = build_twin_report(rows)

    assert sorted(report["summary"]) == ["google:same-name", "openai:same-name"]


def test_twin_study_run_orchestrates_export_run_import_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_build(survey: str, args: argparse.Namespace) -> dict:
        calls.append(("build", survey, args.heldout_question))
        return {
            "edsl_class_name": "Jobs",
            "models": [{"model": "gpt-5.5", "inference_service": "openai", "parameters": {}}],
            "zwill": {"digital_twin_job_id": "twin-demo", "scenario_count": 1},
        }

    def fake_run(args: argparse.Namespace) -> dict:
        calls.append(("run", args.job, args.path))
        Path(args.path).write_text(json.dumps({"edsl_class_name": "Results", "zwill": {"digital_twin_job_id": "twin-demo"}, "data": []}))
        return {"data": {"results_path": args.path, "digital_twin_job_id": "twin-demo"}}

    def fake_import(args: argparse.Namespace) -> dict:
        calls.append(("import", args.path, args.job_id, args.replace))
        return {"data": {"job_id": args.job_id, "extracted_count": 1}}

    def fake_report(args: argparse.Namespace) -> None:
        calls.append(("report", args.format, args.path))
        Path(args.path).write_text(args.format)

    monkeypatch.setattr(cli, "build_edsl_digital_twin_job_dict", fake_build)
    monkeypatch.setattr(cli, "cmd_edsl_run", fake_run)
    monkeypatch.setattr(cli, "cmd_twin_results_import", fake_import)
    monkeypatch.setattr(cli, "cmd_twin_results_report", fake_report)
    monkeypatch.setattr(cli, "require_survey", lambda survey: zwill_survey_path(tmp_path, survey))

    result = cli.cmd_twin_study_run(
        argparse.Namespace(
            survey="demo",
            output_dir=str(tmp_path / "out"),
            job_path=None,
            results_path=None,
            report_html=None,
            report_json=str(tmp_path / "out" / "report.json"),
            report_csv=None,
            replace=True,
            dry_run=False,
            approved_plan=None,
            allow_unapproved=True,
            heldout_question=["q1"],
            n=None,
            progress_bar=False,
            fresh=False,
            stop_on_exception=False,
            check_api_keys=False,
            verbose=None,
            print_exceptions=None,
            offload_execution=False,
            use_api_proxy=False,
            run_param=None,
        )
    )

    assert result["data"]["job_id"] == "twin-demo"
    assert Path(result["data"]["job_path"]).exists()
    assert Path(result["data"]["report_paths"]["html"]).read_text() == "html"
    assert Path(result["data"]["report_paths"]["json"]).read_text() == "json"
    assert [call[0] for call in calls] == ["build", "run", "import", "report", "report"]


def test_twin_study_run_dry_run_through_parser_preserves_paths_and_model_args(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_build(survey: str, args: argparse.Namespace) -> dict:
        captured.update(
            {
                "survey": survey,
                "heldout_question": args.heldout_question,
                "heldout_questions": args.heldout_questions,
                "sample_respondents": args.sample_respondents,
                "model": args.model,
                "models": args.models,
                "model_param": args.model_param,
            }
        )
        return {
            "edsl_class_name": "Jobs",
            "models": [
                {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                {"model": "gemini-2.5-pro", "inference_service": "google", "parameters": {"temperature": 0}},
            ],
            "zwill": {"digital_twin_job_id": "dryrun-twin", "scenario_count": 3},
        }

    monkeypatch.setattr(cli, "build_edsl_digital_twin_job_dict", fake_build)
    monkeypatch.setattr(cli, "require_survey", lambda survey: zwill_survey_path(tmp_path, survey))

    job_path = tmp_path / "custom" / "job.json"
    run_cli(
        "twin-study",
        "run",
        "--survey",
        "demo",
        "--heldout-question",
        "q1",
        "--heldout-questions",
        "q2,q3",
        "--sample-respondents",
        "12",
        "--model",
        "openai:gpt-5.5",
        "--model",
        "google:gemini-2.5-pro",
        "--models",
        "anthropic:claude-opus-4-1",
        "--model-param",
        "google:gemini-2.5-pro:temperature=0",
        "--job-path",
        str(job_path),
        "--dry-run",
        "--allow-unapproved",
    )

    assert job_path.exists()
    assert json.loads(job_path.read_text())["zwill"]["digital_twin_job_id"] == "dryrun-twin"
    assert captured["survey"] == "demo"
    assert captured["heldout_question"] == ["q1"]
    assert captured["heldout_questions"] == "q2,q3"
    assert captured["sample_respondents"] == 12
    assert captured["model"] == ["openai:gpt-5.5", "google:gemini-2.5-pro"]
    assert captured["models"] == "anthropic:claude-opus-4-1"
    assert captured["model_param"] == ["google:gemini-2.5-pro:temperature=0"]


def test_edsl_run_twin_job_suggests_twin_import(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    job_path = tmp_path / "job.json"
    results_path = tmp_path / "results.json"
    job_path.write_text(
        json.dumps(
            {
                "edsl_class_name": "Jobs",
                "zwill": {"digital_twin_job_id": "twin-demo"},
            }
        )
    )

    class FakeLoadedJobs:
        scenarios = [object()]
        models = [object()]
        survey = argparse.Namespace(questions=[object()])

        def run(self):
            return argparse.Namespace(to_dict=lambda: {"edsl_class_name": "Results", "data": []})

    class FakeJobsLoader:
        @staticmethod
        def from_dict(_data):
            return FakeLoadedJobs()

    monkeypatch.setattr(cli, "load_edsl_runner_classes", lambda: (FakeJobsLoader, object))
    result = cli.cmd_edsl_run(
        argparse.Namespace(
            job=str(job_path),
            path=str(results_path),
            n=None,
            progress_bar=False,
            fresh=False,
            stop_on_exception=False,
            check_api_keys=False,
            verbose=None,
            print_exceptions=None,
            offload_execution=False,
            use_api_proxy=False,
            run_param=None,
            dry_run=False,
        )
    )
    assert result["next_steps"] == [f"zwill twin-results import --survey <survey> --path {results_path}"]


def test_edsl_run_agent_study_job_suggests_results_written(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    job_path = tmp_path / "agent_study_job.json"
    results_path = tmp_path / "agent_study_results.json"
    job_path.write_text(
        json.dumps(
            {
                "edsl_class_name": "Jobs",
                "zwill": {"agent_study_job_id": "agent-study-demo"},
            }
        )
    )

    class FakeLoadedJobs:
        scenarios = []
        models = [object()]
        survey = argparse.Namespace(questions=[object()])

        def run(self):
            return argparse.Namespace(to_dict=lambda: {"edsl_class_name": "Results", "data": []})

    class FakeJobsLoader:
        @staticmethod
        def from_dict(_data):
            return FakeLoadedJobs()

    monkeypatch.setattr(cli, "load_edsl_runner_classes", lambda: (FakeJobsLoader, object))
    result = cli.cmd_edsl_run(
        argparse.Namespace(
            job=str(job_path),
            path=str(results_path),
            n=None,
            progress_bar=False,
            fresh=False,
            stop_on_exception=False,
            check_api_keys=False,
            verbose=None,
            print_exceptions=None,
            offload_execution=False,
            use_api_proxy=False,
            run_param=None,
            dry_run=False,
        )
    )

    assert result["data"]["agent_study_job_id"] == "agent-study-demo"
    assert result["next_steps"] == [f"zwill agent-study import --path {results_path}"]


def test_edsl_run_enforces_approved_validation_count_delta(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    job_path = tmp_path / "approved_twin_job.json"
    results_path = tmp_path / "approved_twin_results.json"
    job_payload = {
        "edsl_class_name": "Jobs",
        "zwill": {
            "digital_twin_job_id": "approved-twin",
            "approved_validation_plan": {
                "plan_id": "plan",
                "approval": {"approved": True},
                "export_count_check": {
                    "approved_prediction_count_estimate": 5,
                    "exported_prediction_count": 4,
                    "delta": -1,
                    "requires_reapproval": True,
                },
            },
        },
    }
    job_path.write_text(json.dumps(job_payload))

    class FakeLoadedJobs:
        scenarios = [object()]
        models = [object()]
        survey = argparse.Namespace(questions=[object()])

        def run(self, **_kwargs):
            return argparse.Namespace(to_dict=lambda: {"edsl_class_name": "Results", "data": []})

    class FakeJobsLoader:
        @staticmethod
        def from_dict(_data):
            return FakeLoadedJobs()

    monkeypatch.setattr(cli, "load_edsl_runner_classes", lambda: (FakeJobsLoader, argparse.Namespace(__dataclass_fields__={})))
    args = argparse.Namespace(
        job=str(job_path),
        path=str(results_path),
        n=None,
        progress_bar=False,
        fresh=False,
        stop_on_exception=False,
        check_api_keys=False,
        verbose=None,
        print_exceptions=None,
        offload_execution=False,
        use_api_proxy=False,
        allow_count_delta=False,
        run_param=None,
        dry_run=True,
    )
    with pytest.raises(cli.ZwillError, match="prediction count"):
        cli.cmd_edsl_run(args)

    job_payload["zwill"]["approved_validation_plan"]["export_count_check"]["requires_reapproval"] = False
    job_path.write_text(json.dumps(job_payload))
    result = cli.cmd_edsl_run(args)
    assert result["data"]["run_parameters"] == {}


def test_edsl_run_dry_run_loads_explicit_env_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    job_path = tmp_path / "job.json"
    results_path = tmp_path / "results.json"
    env_path = tmp_path / "custom.env"
    env_path.write_text("ZWILL_TEST_FAKE_KEY=loaded-from-explicit-env\n")
    job_path.write_text(json.dumps({"edsl_class_name": "Jobs"}))
    monkeypatch.delenv("ZWILL_TEST_FAKE_KEY", raising=False)

    class FakeLoadedJobs:
        scenarios = [object()]
        models = [object()]
        survey = argparse.Namespace(questions=[object()])

    class FakeJobsLoader:
        @staticmethod
        def from_dict(_data):
            return FakeLoadedJobs()

    monkeypatch.setattr(cli, "load_edsl_runner_classes", lambda: (FakeJobsLoader, argparse.Namespace(__dataclass_fields__={})))
    result = cli.cmd_edsl_run(
        argparse.Namespace(
            job=str(job_path),
            path=str(results_path),
            env_path=str(env_path),
            n=None,
            progress_bar=False,
            fresh=False,
            stop_on_exception=False,
            check_api_keys=False,
            verbose=None,
            print_exceptions=None,
            offload_execution=False,
            use_api_proxy=False,
            run_param=None,
            dry_run=True,
        )
    )

    assert result["data"]["loaded_env"]["path"] == str(env_path)
    assert result["data"]["loaded_env"]["loaded_keys"] == ["ZWILL_TEST_FAKE_KEY"]
    assert os.environ["ZWILL_TEST_FAKE_KEY"] == "loaded-from-explicit-env"


def test_twin_results_fixture_generates_golden_reports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()

    run_cli("twin-results", "import", "--survey", "demo", "--path", str(FIXTURES / "twin_results.json"))
    json_path = tmp_path / "fixture_report.json"
    csv_path = tmp_path / "fixture_report.csv"
    html_path = tmp_path / "fixture_report.html"
    run_cli("twin-results", "report", "--survey", "demo", "--job-id", "fixture-twin", "--format", "json", "--path", str(json_path))
    run_cli("twin-results", "report", "--survey", "demo", "--job-id", "fixture-twin", "--format", "csv", "--path", str(csv_path))
    run_cli("twin-results", "report", "--survey", "demo", "--job-id", "fixture-twin", "--format", "html", "--path", str(html_path))

    report = json.loads(json_path.read_text())
    assert sorted(report["summary"]) == ["google:gpt-5.5", "openai:gpt-5.5"]
    assert report["summary"]["openai:gpt-5.5"]["top1_accuracy"] == 1.0
    assert report["summary"]["google:gpt-5.5"]["top1_accuracy"] == 0.0
    assert "expected_calibration_error" in report["summary"]["openai:gpt-5.5"]
    assert "negative_log_likelihood_p95" in report["summary"]["openai:gpt-5.5"]
    assert report["diagnostics"]["overconfident_misses"]
    assert report["diagnostics"]["confusion"]
    assert "model_label" in csv_path.read_text().splitlines()[0]
    html = html_path.read_text()
    assert "openai:gpt-5.5" in html
    assert "google:gpt-5.5" in html
    assert "correct" in html
    assert "wrong" in html


def test_twin_study_list_show_and_compare(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()

    first = json.loads((FIXTURES / "twin_results.json").read_text())
    second = json.loads((FIXTURES / "twin_results.json").read_text())
    second["zwill"]["digital_twin_job_id"] = "fixture-twin-2"
    second["data"][0]["answer"]["response_probabilities"] = '{"probabilities":[0.6,0.4],"notes":"less confident"}'
    second["data"][1]["answer"]["response_probabilities"] = '{"probabilities":[0.4,0.6],"notes":"corrected"}'
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps(first))
    second_path.write_text(json.dumps(second))

    run_cli("twin-results", "import", "--survey", "demo", "--path", str(first_path))
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(second_path))

    runs = cli.read_twin_run_manifest(zwill_survey_path(tmp_path))
    assert {run["job_id"] for run in runs} == {"fixture-twin", "fixture-twin-2"}

    show = cli.cmd_twin_study_show(
        argparse.Namespace(survey="demo", job_id="fixture-twin", include_summary=True)
    )
    assert show["data"]["row_count"] == 2
    assert "summary" in show["data"]

    compare_json = tmp_path / "compare.json"
    rc = main(
        [
            "twin-study",
            "compare",
            "--survey",
            "demo",
            "--job-id",
            "fixture-twin",
            "--job-id",
            "fixture-twin-2",
            "--format",
            "json",
            "--path",
            str(compare_json),
        ]
    )
    assert rc == 0
    comparison = json.loads(compare_json.read_text())
    assert comparison["job_ids"] == ["fixture-twin", "fixture-twin-2"]
    assert {row["job_id"] for row in comparison["comparisons"]} == {"fixture-twin", "fixture-twin-2"}
    changes_by_model = {row["model"]: row for row in comparison["response_changes"]}
    assert changes_by_model["openai:gpt-5.5"]["paired_rows"] == 1
    assert changes_by_model["openai:gpt-5.5"]["changed_top_choice"] == 0
    assert changes_by_model["google:gpt-5.5"]["paired_rows"] == 1
    assert changes_by_model["google:gpt-5.5"]["changed_top_choice"] == 1
    assert changes_by_model["google:gpt-5.5"]["corrections"] == 1
    assert changes_by_model["google:gpt-5.5"]["regressions"] == 0

    comparison_report_json = tmp_path / "comparison_report.json"
    comparison_report_html = tmp_path / "comparison_report.html"
    run_cli(
        "twin-results",
        "compare-report",
        "--survey",
        "demo",
        "--job-id",
        "fixture-twin",
        "--job-id",
        "fixture-twin-2",
        "--format",
        "json",
        "--path",
        str(comparison_report_json),
    )
    run_cli(
        "twin-results",
        "compare-report",
        "--survey",
        "demo",
        "--jobs",
        "fixture-twin,fixture-twin-2",
        "--format",
        "html",
        "--path",
        str(comparison_report_html),
    )
    comparison_report = json.loads(comparison_report_json.read_text())
    assert comparison_report["job_ids"] == ["fixture-twin", "fixture-twin-2"]
    assert "fixture-twin / openai:gpt-5.5" in comparison_report["summary"]
    assert comparison_report["diagnostics"]["marginal_options"]
    comparison_report_html_text = comparison_report_html.read_text()
    assert "Twin Job Comparison" in comparison_report_html_text
    assert "Overall Performance" in comparison_report_html_text
    assert "closest:" in comparison_report_html_text
    assert "twin-job-comparison-data" in comparison_report_html_text


def test_twin_experiment_records_compares_and_selects_approaches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()

    first = json.loads((FIXTURES / "twin_results.json").read_text())
    second = json.loads((FIXTURES / "twin_results.json").read_text())
    second["zwill"]["digital_twin_job_id"] = "fixture-twin-2"
    second["data"][0]["answer"]["response_probabilities"] = '{"probabilities":[0.6,0.4],"notes":"less confident"}'
    second["data"][1]["answer"]["response_probabilities"] = '{"probabilities":[0.4,0.6],"notes":"corrected"}'
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps(first))
    second_path.write_text(json.dumps(second))

    run_cli("twin-results", "import", "--survey", "demo", "--path", str(first_path))
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(second_path))
    run_cli(
        "twin-experiment",
        "record",
        "--survey",
        "demo",
        "--job-id",
        "fixture-twin",
        "--experiment-id",
        "baseline_context",
        "--approach",
        "Baseline context",
        "--description",
        "Use the default held-out setup.",
        "--tag",
        "baseline,context",
    )
    run_cli(
        "twin-experiment",
        "record",
        "--survey",
        "demo",
        "--job-id",
        "fixture-twin-2",
        "--experiment-id",
        "calibrated_context",
        "--approach",
        "Calibrated context",
        "--description",
        "Use a more calibrated prompt.",
        "--primary-metric",
        "nll",
    )

    compare_path = tmp_path / "experiment_compare.json"
    run_cli(
        "twin-experiment",
        "compare",
        "--survey",
        "demo",
        "--metric",
        "nll",
        "--model",
        "google:gpt-5.5",
        "--format",
        "json",
        "--path",
        str(compare_path),
    )
    comparison = json.loads(compare_path.read_text())
    assert comparison["metric"]["direction"] == "lower"
    assert comparison["selected"]["experiment_id"] == "calibrated_context"
    assert comparison["comparisons"][0]["rank"] == 1
    assert comparison["comparisons"][0]["selected"] is True
    assert comparison["response_changes"][0]["from_label"] == "Baseline context"
    assert comparison["response_changes"][0]["to_label"] == "Calibrated context"
    assert comparison["response_changes"][0]["model"] == "google:gpt-5.5"
    assert comparison["response_changes"][0]["paired_rows"] == 1
    assert comparison["response_changes"][0]["changed_top_choice"] == 1
    assert comparison["response_changes"][0]["corrections"] == 1

    microdata_html = tmp_path / "microdata.html"
    microdata_json = tmp_path / "microdata.json"
    run_cli(
        "twin-experiment",
        "microdata",
        "--survey",
        "demo",
        "--metric",
        "nll",
        "--model",
        "google:gpt-5.5",
        "--path",
        str(microdata_html),
        "--json-path",
        str(microdata_json),
    )
    microdata = json.loads(microdata_json.read_text())
    assert microdata["kind"] == "experiment_microdata_audit"
    assert microdata["group_count"] == 1
    assert microdata["prediction_row_count"] == 2
    assert len(microdata["experiments"]) == 2
    assert microdata["groups"][0]["diagnostics"]["top_choice_changed"] is True
    assert {row["approach"] for row in microdata["prediction_rows"]} == {"Baseline context", "Calibrated context"}
    html = microdata_html.read_text()
    assert "Twin Experiment Microdata" in html
    assert "Calibrated context" in html
    assert "Supplemental twin material" in html
    assert "data-inspect-row" in html
    assert "data-tab=\"traits\"" in html

    selected = cli.cmd_twin_experiment_select(
        argparse.Namespace(
            survey="demo",
            experiment_id=None,
            job_id=None,
            jobs=None,
            model="google:gpt-5.5",
            metric="nll",
        )
    )
    assert selected["data"]["selected"]["approach"] == "Calibrated context"
    assert selected["data"]["candidate_count"] == 2


def test_twin_experiment_report_export_import_render(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()

    first = json.loads((FIXTURES / "twin_results.json").read_text())
    second = json.loads((FIXTURES / "twin_results.json").read_text())
    second["zwill"]["digital_twin_job_id"] = "fixture-twin-2"
    second["data"][0]["answer"]["response_probabilities"] = '{"probabilities":[0.6,0.4],"notes":"less confident"}'
    second["data"][1]["answer"]["response_probabilities"] = '{"probabilities":[0.4,0.6],"notes":"corrected"}'
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps(first))
    second_path.write_text(json.dumps(second))
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(first_path))
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(second_path))
    job_with_material = tmp_path / "calibrated_job.edsl.json"
    job_with_material.write_text(
        json.dumps(
            {
                "edsl_class_name": "Jobs",
                "scenarios": [
                    {
                        "respondent_id": "r1",
                        "heldout_question_name": "q1",
                        "twin_material": [
                            {
                                "material_id": "prior_q1",
                                "kind": "model_prior",
                                "title": "One-shot prior",
                                "body_markdown": "Prior says yes 0.60 and no 0.40.",
                            }
                        ],
                        "twin_material_text": "Prior says yes 0.60 and no 0.40.",
                    }
                ],
            }
        )
    )
    cli.upsert_twin_run_manifest(
        zwill_survey_path(tmp_path),
        {
            "job_id": "fixture-twin-2",
            "survey": "demo",
            "status": "ok",
            "created_at": "2026-06-29T00:00:00Z",
            "job_path": str(job_with_material),
            "extracted_count": 2,
            "issue_count": 0,
            "models": ["openai:gpt-5.5", "google:gpt-5.5"],
            "heldout_questions": ["q1"],
        },
    )
    run_cli("twin-experiment", "record", "--survey", "demo", "--job-id", "fixture-twin", "--experiment-id", "baseline", "--approach", "Baseline")
    run_cli("twin-experiment", "record", "--survey", "demo", "--job-id", "fixture-twin-2", "--experiment-id", "calibrated", "--approach", "Calibrated prompt")
    plot_dir = tmp_path / "experiment_plots"
    run_cli(
        "twin-experiment",
        "plots",
        "--survey",
        "demo",
        "--metric",
        "nll",
        "--model",
        "google:gpt-5.5",
        "--path",
        str(plot_dir),
    )
    plot_manifest = plot_dir / "manifest.json"
    manifest = json.loads(plot_manifest.read_text())
    assert manifest["artifact_count"] == 3
    assert (plot_dir / "pair_1_google_gpt-5.5_p_actual_scatter.svg").exists()
    assert (plot_dir / "pair_1_google_gpt-5.5_microdata.html").exists()
    assert manifest["response_changes"][0]["corrections"] == 1
    assert any(artifact["kind"] == "paired_microdata_table" for artifact in manifest["artifacts"])

    monkeypatch.setattr(
        cli,
        "load_edsl_job_classes",
        lambda: (FakeJobs, FakeModel, FakeModelList, FakeQuestionFreeText, FakeScenario, FakeScenarioList, FakeSurvey),
    )
    export_result = cli.cmd_twin_experiment_report_export(
        argparse.Namespace(
            survey="demo",
            experiment_id=None,
            job_id=None,
            jobs=None,
            model="google:gpt-5.5",
            metric="nll",
            job_path=None,
            prompt_path=None,
            context_path=None,
            include_plots=[str(plot_manifest)],
            report_model=["openai:gpt-5.5"],
            models=None,
            service_name="openai",
            model_param=[],
        )
    )
    report_id = export_result["data"]["report_id"]
    report_dir = zwill_project_path(tmp_path) / "practitioner_reports" / report_id
    context = json.loads((report_dir / "context.json").read_text())
    assert context["report_context"]["report_kind"] == "twin_experiment_comparison"
    assert context["report_context"]["selected"]["experiment_id"] == "calibrated"
    assert context["report_context"]["plot_summaries"][0]["response_changes"][0]["corrections"] == 1
    calibrated = next(item for item in context["report_context"]["experiments"] if item["experiment"]["experiment_id"] == "calibrated")
    assert calibrated["scenario_material_examples"][0]["twin_material"][0]["material_id"] == "prior_q1"
    assert "Calibrated prompt" in (report_dir / "prompt.md").read_text()

    results_path = tmp_path / "experiment_report_results.json"
    results_path.write_text(
        json.dumps(
            {
                "edsl_class_name": "Results",
                "zwill": {
                    "practitioner_report_id": report_id,
                    "practitioner_report_question_name": "experiment_report_markdown",
                },
                "data": [{"answer": {"experiment_report_markdown": "## Comparison\n\nCalibrated prompt lowered NLL."}}],
            }
        )
    )
    run_cli("twin-experiment", "report-import", "--path", str(results_path))
    html_path = tmp_path / "experiment_report.html"
    run_cli("twin-experiment", "report-render", "--report-id", report_id, "--path", str(html_path))
    html = html_path.read_text()
    assert "Calibrated prompt lowered NLL" in html
    assert "Study Plots" in html
    assert "Paired probability movement" in html
    assert "Twin microdata" in html
    assert "Traits / prompt / response" in html


def test_twin_benchmark_report_from_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(FIXTURES / "twin_results.json"))

    config = json.loads((FIXTURES / "twin_benchmark.json").read_text())
    config_path = tmp_path / "benchmark.json"
    config_path.write_text(json.dumps(config))
    json_path = tmp_path / "benchmark_report.json"
    csv_path = tmp_path / "benchmark_report.csv"
    html_path = tmp_path / "benchmark_report.html"
    practitioner_path = tmp_path / "benchmark_practitioner_report.html"

    def fake_generate_practitioner_report(args, payload, studies):
        return (
            "# Practitioner Report\n\n"
            "## Executive Summary\n\n"
            "Good Enough To Act for low-stakes decisions.\n\n"
            "## Stakes-Based Recommendation\n\n"
            "- Use more validation as stakes rise.\n",
            {"mode": "test", "model": "fake"},
        )

    monkeypatch.setattr(cli, "generate_practitioner_report_markdown", fake_generate_practitioner_report)

    run_cli("twin-benchmark", "report", "--config", str(config_path), "--format", "json", "--path", str(json_path))
    run_cli("twin-benchmark", "report", "--config", str(config_path), "--format", "csv", "--path", str(csv_path))
    run_cli("twin-benchmark", "report", "--config", str(config_path), "--format", "html", "--path", str(html_path))
    run_cli("twin-benchmark", "practitioner-report", "--config", str(config_path), "--path", str(practitioner_path))

    report = json.loads(json_path.read_text())
    assert report["benchmark"] == "fixture_twin_benchmark"
    assert sorted(report["summary"]) == ["google:gpt-5.5", "openai:gpt-5.5"]
    assert "mean_ece" in report["summary"]["openai:gpt-5.5"]
    assert "nll_vs_empirical" in csv_path.read_text().splitlines()[0]
    html = html_path.read_text()
    assert "Twin Benchmark" in html
    assert "Practical Guidance" in html
    assert "twin-benchmark-data" in html
    practitioner_html = practitioner_path.read_text()
    assert "Survey Digital Twin Report" in practitioner_html
    assert "Practitioner Report" not in practitioner_html
    assert "Copy Markdown" in practitioner_html
    assert "twin-practitioner-data" in practitioner_html
    data_block = practitioner_html.split('id="twin-practitioner-data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(data_block)["benchmark"] == "fixture_twin_benchmark"
    assert "Stakes-Based Recommendation" in practitioner_html
    assert "Good Enough To Act" in practitioner_html


def test_twin_benchmark_practitioner_report_export_import_render(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(FIXTURES / "twin_results.json"))

    config = json.loads((FIXTURES / "twin_benchmark.json").read_text())
    config_path = tmp_path / "benchmark.json"
    config_path.write_text(json.dumps(config))

    def fake_build_report_job(args, payload, studies):
        job = {
            "edsl_class_name": "Jobs",
            "survey": {"questions": [{"question_name": "practitioner_report_markdown"}]},
            "zwill": {
                "practitioner_report_id": "report-demo",
                "practitioner_report_question_name": "practitioner_report_markdown",
            },
        }
        context = {
            "report_id": "report-demo",
            "benchmark_payload": payload,
            "report_context": {"benchmark": payload, "studies": []},
            "studies": studies,
            "prompt": "Write a report",
            "generation": {"mode": "job_exported", "report_id": "report-demo", "model": "openai:gpt-5.5"},
        }
        return job, context, "Write a report"

    monkeypatch.setattr(cli, "build_edsl_practitioner_report_job_dict", fake_build_report_job)

    export_result = cli.cmd_twin_benchmark_practitioner_report_export(
        argparse.Namespace(
            config=str(config_path),
            manifest=None,
            job_path=None,
            prompt_path=None,
            context_path=None,
            model=None,
            models=None,
            service_name="openai",
            model_param=[],
        )
    )
    report_dir = zwill_project_path(tmp_path) / "practitioner_reports" / "report-demo"
    assert Path(export_result["data"]["job_path"]) == Path(".zwill") / "projects" / "default" / "practitioner_reports" / "report-demo" / "job.edsl.json"
    assert (report_dir / "context.json").exists()
    assert (report_dir / "prompt.md").read_text() == "Write a report"

    results = {
        "edsl_class_name": "Results",
        "zwill": {
            "practitioner_report_id": "report-demo",
            "practitioner_report_question_name": "practitioner_report_markdown",
        },
        "data": [{"answer": {"practitioner_report_markdown": "# Practitioner Report\n\nSpecific benchmark findings."}}],
    }
    results_path = tmp_path / "report_results.json.gz"
    with gzip.open(results_path, "wt") as f:
        json.dump(results, f)

    run_cli("twin-benchmark", "practitioner-report-import", "--path", str(results_path))
    assert "# Practitioner Report" in (report_dir / "report.md").read_text()

    html_path = tmp_path / "rendered.html"
    run_cli("twin-benchmark", "practitioner-report-render", "--report-id", "report-demo", "--path", str(html_path))
    html = html_path.read_text()
    assert "Specific benchmark findings" in html
    assert "How to Use This Report" in html
    assert "twin-practitioner-data" in html


def test_twin_study_practitioner_report_export_import_render(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(FIXTURES / "twin_results.json"))

    def fake_build_report_job(args, payload, studies):
        assert payload["report_kind"] == "single_survey_twin_validation"
        assert payload["survey"] == "demo"
        assert studies == [{"survey": "demo", "job_id": "fixture-twin"}]
        job = {
            "edsl_class_name": "Jobs",
            "survey": {"questions": [{"question_name": "practitioner_report_markdown"}]},
            "zwill": {
                "practitioner_report_id": "single-report-demo",
                "practitioner_report_question_name": "practitioner_report_markdown",
            },
        }
        context = {
            "report_id": "single-report-demo",
            "benchmark_payload": payload,
            "report_context": {"benchmark": payload, "report_kind": "single_survey_twin_validation", "studies": []},
            "studies": studies,
            "prompt": "Write a single-survey report",
            "generation": {"mode": "job_exported", "report_id": "single-report-demo", "model": "openai:gpt-5.5"},
        }
        return job, context, "Write a single-survey report"

    monkeypatch.setattr(cli, "build_edsl_practitioner_report_job_dict", fake_build_report_job)

    export_result = cli.cmd_twin_study_practitioner_report_export(
        argparse.Namespace(
            survey="demo",
            job_id="fixture-twin",
            job_path=None,
            prompt_path=None,
            context_path=None,
            model=None,
            models=None,
            service_name="openai",
            model_param=[],
        )
    )
    report_dir = zwill_project_path(tmp_path) / "practitioner_reports" / "single-report-demo"
    assert Path(export_result["data"]["job_path"]) == Path(".zwill") / "projects" / "default" / "practitioner_reports" / "single-report-demo" / "job.edsl.json"
    assert (report_dir / "context.json").exists()
    assert (report_dir / "prompt.md").read_text() == "Write a single-survey report"

    results = {
        "edsl_class_name": "Results",
        "zwill": {
            "practitioner_report_id": "single-report-demo",
            "practitioner_report_question_name": "practitioner_report_markdown",
        },
        "data": [{"answer": {"practitioner_report_markdown": "## Executive Summary\n\nThis survey validation is specific."}}],
    }
    results_path = tmp_path / "single_report_results.json.gz"
    with gzip.open(results_path, "wt") as f:
        json.dump(results, f)

    run_cli("twin-study", "practitioner-report-import", "--path", str(results_path))
    html_path = tmp_path / "single_rendered.html"
    run_cli("twin-study", "practitioner-report-render", "--report-id", "single-report-demo", "--path", str(html_path))
    html = html_path.read_text()
    assert "Demo Digital Twin Validation" in html
    assert "This survey validation is specific" in html
    assert "twin-practitioner-data" in html


def test_twin_results_executive_summary_export_import_render(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    run_cli("twin-results", "import", "--survey", "demo", "--path", str(FIXTURES / "twin_results.json"))
    filter_args = argparse.Namespace(survey="demo", job_id=["fixture-twin"], jobs=None, model=None, question=None, questions=None)
    rows = cli.filtered_twin_prediction_rows(filter_args)
    diagnostics = cli.build_executive_summary(
        rows,
        survey="demo",
        path=tmp_path / "diagnostic.html",
        markdown_path=tmp_path / "diagnostic.md",
        simulations=20,
        seed=123,
    )
    compact_context = cli.build_executive_summary_report_context(
        argparse.Namespace(
            survey="demo",
            job_id=["fixture-twin"],
            jobs=None,
            prediction_model=None,
            question=None,
            questions=None,
        ),
        rows,
        diagnostics,
    )
    assert "rows" not in compact_context
    assert "marginal_options" not in compact_context["twin_validation"]
    assert compact_context["context_size_policy"]["raw_prediction_rows_included"] is False
    assert compact_context["context_size_policy"]["twin_specific_rows_compacted"] is True
    assert "twin_specific_diagnostics" in compact_context
    assert compact_context["source_filters"]["job_id"] == ["fixture-twin"]
    assert len(json.dumps(compact_context)) < 80_000

    def fake_build_report_job(args, report_context):
        assert "reasoning_effort=low" in args.model_param
        assert "max_tokens=16000" in args.model_param
        assert report_context["report_kind"] == "frontier_generated_executive_twin_validation"
        assert report_context["survey"] == "demo"
        assert report_context["executive_diagnostics"]["individual_signal"]["p_value_mean_p_actual"] >= 0
        assert "one_shot_no_persona_baseline" in report_context
        job = {
            "edsl_class_name": "Jobs",
            "survey": {"questions": [{"question_name": "executive_summary_markdown"}]},
            "zwill": {
                "practitioner_report_id": "exec-report-demo",
                "practitioner_report_question_name": "executive_summary_markdown",
                "report_kind": "executive_twin_validation",
            },
        }
        context = {
            "report_id": "exec-report-demo",
            "benchmark_payload": report_context["executive_diagnostics"],
            "executive_report_context": report_context,
            "generation": {"mode": "job_exported", "report_id": "exec-report-demo", "model": "openai:gpt-5.5"},
        }
        return job, context, "Write an evidence-aware executive summary"

    monkeypatch.setattr(cli, "build_edsl_executive_summary_report_job_dict", fake_build_report_job)

    preview_html = tmp_path / "executive.html"
    run_cli(
        "twin-results",
        "executive-summary-export",
        "--survey",
        "demo",
        "--job-id",
        "fixture-twin",
        "--path",
        str(preview_html),
        "--model",
        "openai:gpt-5.5",
    )
    report_dir = zwill_project_path(tmp_path) / "practitioner_reports" / "exec-report-demo"
    assert (report_dir / "context.json").exists()
    assert (report_dir / "prompt.md").read_text() == "Write an evidence-aware executive summary"

    results = {
        "edsl_class_name": "Results",
        "zwill": {
            "practitioner_report_id": "exec-report-demo",
            "practitioner_report_question_name": "executive_summary_markdown",
        },
        "data": [
            {
                "answer": {
                    "executive_summary_markdown": (
                        "## Executive Summary\n\n"
                        "The permutation test is null, so this supports aggregate simulation rather than individual targeting."
                    )
                }
            }
        ],
    }
    results_path = tmp_path / "exec_report_results.json.gz"
    with gzip.open(results_path, "wt") as f:
        json.dump(results, f)

    run_cli("twin-results", "executive-summary-import", "--report-id", "exec-report-demo", "--path", str(results_path))
    rendered_html = tmp_path / "executive_rendered.html"
    run_cli("twin-results", "executive-summary-render", "--report-id", "exec-report-demo", "--path", str(rendered_html))
    html = rendered_html.read_text()
    assert "The permutation test is null" in html
    assert "Generated analysis" in html
    assert "Individual Signal Beyond Marginals" in html

    bundle_dir = tmp_path / "bundle"
    run_cli(
        "report",
        "build",
        "--survey",
        "demo",
        "--job-id",
        "fixture-twin",
        "--path",
        str(bundle_dir),
        "--permutations",
        "20",
    )
    stage_manifest = json.loads((bundle_dir / "stage-manifest.json").read_text())
    assert stage_manifest["stages"]["generated_analysis"]["status"] == "ready"
    assert stage_manifest["stages"]["final_report"]["status"] == "ready"
    provenance_dir = bundle_dir / "analysis" / "generated-reports" / "twin-executive-exec-report-demo"
    assert (provenance_dir / "job.edsl.json").exists()
    assert (provenance_dir / "prompt.md").exists()
    assert (provenance_dir / "context.json").exists()
    assert (provenance_dir / "import.json").exists()
    assert (provenance_dir / "report.md").exists()
    assert (bundle_dir / "report" / "analysis" / "generated-reports" / "twin-executive-exec-report-demo" / "context.json").exists()
    bundle_html = (bundle_dir / "twin-validation.html").read_text()
    assert "The permutation test is null" in bundle_html
    assert "Supporting Diagnostics" in bundle_html
    assert "deterministic fallback" not in bundle_html


def test_twin_benchmark_run_dry_run_writes_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = {
        "name": "dry_benchmark",
        "output_dir": str(tmp_path / "out"),
        "studies": [{"survey": "demo", "heldout_question": "q1"}],
    }
    config_path = tmp_path / "benchmark.json"
    config_path.write_text(json.dumps(config))

    def fake_twin_study_run(args: argparse.Namespace) -> dict:
        return {
            "data": {
                "job_id": "dry-job",
                "job_path": str(tmp_path / "out" / "job.json"),
                "results_path": str(tmp_path / "out" / "results.json.gz"),
                "report_paths": {},
            }
        }

    monkeypatch.setattr(cli, "cmd_twin_study_run", fake_twin_study_run)

    manifest_path = tmp_path / "manifest.json"
    run_cli("twin-benchmark", "run", "--config", str(config_path), "--manifest", str(manifest_path), "--dry-run")

    manifest = json.loads(manifest_path.read_text())
    assert manifest["benchmark"] == "dry_benchmark"
    assert manifest["dry_run"] is True
    assert manifest["runs"][0]["job_id"] == "dry-job"


def test_twin_results_import_records_malformed_row_issues(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_tiny_binary_survey()
    results = {
        "edsl_class_name": "Results",
        "zwill": {"digital_twin_job_id": "bad-twin"},
        "data": [
            {
                "scenario": {
                    "respondent_id": "r1",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["yes", "no"],
                    "actual_answer": "yes",
                    "observed_answers": [],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.9],"notes":"bad length"}'},
            },
            {
                "scenario": {
                    "respondent_id": "r1",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["yes", "no"],
                    "actual_answer": "maybe",
                    "observed_answers": [],
                },
                "model": {"model": "gpt-5.5", "inference_service": "openai", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.5,0.5],"notes":"bad actual"}'},
            },
            {
                "scenario": {
                    "respondent_id": "r2",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["yes", "no"],
                    "actual_answer": "no",
                    "observed_answers": [],
                },
                "model": {"model": "gpt-5.5", "inference_service": "google", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.2,0.8],"notes":"ok"}'},
            },
            {
                "scenario": {
                    "respondent_id": "r2",
                    "heldout_question_name": "q1",
                    "heldout_question_text": "Pick one",
                    "heldout_options": ["yes", "no"],
                    "actual_answer": "no",
                    "observed_answers": [],
                },
                "model": {"model": "gpt-5.5", "inference_service": "google", "parameters": {}},
                "answer": {"response_probabilities": '{"probabilities":[0.2,]'},
            },
        ],
    }
    results_path = tmp_path / "bad_twin_results.json"
    results_path.write_text(json.dumps(results))

    result = cli.cmd_twin_results_import(argparse.Namespace(survey="demo", path=str(results_path), job_id=None, replace=False))

    assert result["data"]["extracted_count"] == 1
    assert result["data"]["issue_count"] == 3
    assert {issue["error"] for issue in result["data"]["issues"]} == {
        "wrong_probability_count",
        "actual_answer_not_in_options",
        "invalid_json",
    }


def test_pew_prepare_imports_expands_codebook_labels(tmp_path: Path) -> None:
    source_dir = write_tiny_pew_source(tmp_path / "source")
    out_dir = tmp_path / "imports"
    script = Path(__file__).parents[1] / "examples" / "pew_w154_diff1" / "prepare_imports.py"
    subprocess.run(
        [sys.executable, str(script), "--source-dir", str(source_dir), "--out-dir", str(out_dir)],
        check=True,
        text=True,
        capture_output=True,
    )

    questions = [json.loads(line) for line in (out_dir / "questions.jsonl").read_text().splitlines()]
    answers = [json.loads(line) for line in (out_dir / "answers.jsonl").read_text().splitlines()]
    assert questions[0]["question_options"] == ["Basically similar", "Basically different"]
    assert {answer["answer"] for answer in answers} == {"Basically similar", "Basically different"}
    assert "Source codes" in questions[0]["source"]["note"]


def test_pew_demo_workflow_without_edsl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source_dir = write_tiny_pew_source(tmp_path / "source")
    workdir = tmp_path / "workdir"

    run_cli("workflow", "pew-demo", "--source-dir", str(source_dir), "--workdir", str(workdir), "--no-edsl")

    truth_path = zwill_survey_path(workdir, "pew_w154_diff1") / "committed" / "truth_marginals.json"
    truth = json.loads(truth_path.read_text())
    assert truth["marginals"]["diff1_a"]["Basically similar"]["weighted_count"] == 1.5
    assert truth["marginals"]["diff1_a"]["Basically different"]["weighted_count"] == 0.5


def write_tiny_pew_source(source_dir: Path) -> Path:
    source_dir.mkdir()
    metadata = {
        "option_codes": [1, 2],
        "option_labels": ["Basically similar", "Basically different"],
        "covariates": ["age"],
        "items": {
            "a": {
                "variable": "item_a",
                "question_stem": "Compare groups.",
                "item_text": "Their hobbies",
            }
        },
    }
    (source_dir / "W154_DIFF1_metadata.json").write_text(json.dumps(metadata))
    (source_dir / "W154_DIFF1_respondents.csv").write_text(
        "respondent_id,weight,age,item_a\n"
        "1,1.5,42,1\n"
        "2,0.5,55,2\n"
    )
    return source_dir
