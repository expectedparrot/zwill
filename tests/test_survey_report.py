from __future__ import annotations

import json
from pathlib import Path

from zwill.survey_report import build_survey_report_payload, render_survey_report_html, write_survey_report_csvs


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))


def test_survey_report_payload_includes_distributions_and_quality_issues(tmp_path: Path) -> None:
    sdir = tmp_path / "survey"
    sdir.mkdir()
    write_jsonl(
        sdir / "questions.jsonl",
        [
            {
                "question_name": "q1",
                "question_type": "multiple_choice",
                "question_text": "Pick one",
                "question_options": ["yes", "no"],
                "source": {"note": "source row 1"},
            },
            {
                "question_name": "q2",
                "question_type": "multiple_choice",
                "question_text": "Broken options",
                "question_options": [],
            },
            {
                "question_name": "q3",
                "question_type": "multiple_choice",
                "question_text": "No answers",
                "question_options": ["A", "B"],
            },
        ],
    )
    write_jsonl(
        sdir / "respondents.jsonl",
        [
            {"respondent_id": "r1", "weight": 1.0},
            {"respondent_id": "r2", "weight": 2.0},
            {"respondent_id": "r3", "weight": 1.0},
        ],
    )
    write_jsonl(
        sdir / "answers.jsonl",
        [
            {"respondent_id": "r1", "question": "q1", "answer": "yes"},
            {"respondent_id": "r2", "question": "q1", "answer": "no"},
            {"respondent_id": "r3", "question": "q1", "answer": "maybe"},
            {"respondent_id": "r1", "question": "q2", "answer": "x"},
        ],
    )
    write_jsonl(sdir / "quarantine.jsonl", [{"issue_id": "i1", "status": "open"}])

    payload = build_survey_report_payload("demo", sdir)
    html = render_survey_report_html(payload)

    assert payload["summary"]["respondent_count"] == 3
    assert payload["summary"]["question_count"] == 3
    assert payload["summary"]["open_quarantine_issue_count"] == 1
    assert payload["summary"]["no_answer_question_count"] == 1
    assert payload["no_answer_questions"] == ["q3"]
    assert {"severity": "warning", "question": "q2", "issue": "multiple_choice_without_options"} in payload["issues"]
    assert {"severity": "error", "question": "q1", "issue": "answers_not_in_options", "values": ["maybe"]} in payload["issues"]
    q1_options = [row for row in payload["options"] if row["question_name"] == "q1"]
    assert [row["option_label"] for row in q1_options] == ["yes", "no", "maybe"]
    assert q1_options[1]["weighted_share"] == 0.5
    assert "Data quality issues (1)" in html
    assert "answers_not_in_options" in html
    assert "<h2>Data Quality Issues</h2>" not in html
    assert "issue-values" in html
    assert "[&#x27;maybe&#x27;]" not in html


def test_survey_report_uses_committed_marginals_when_available(tmp_path: Path) -> None:
    sdir = tmp_path / "survey"
    (sdir / "committed").mkdir(parents=True)
    write_jsonl(
        sdir / "questions.jsonl",
        [{"question_name": "q1", "question_type": "multiple_choice", "question_text": "Pick one", "question_options": ["yes", "no"]}],
    )
    write_jsonl(sdir / "respondents.jsonl", [{"respondent_id": "r1"}])
    write_jsonl(sdir / "answers.jsonl", [{"respondent_id": "r1", "question": "q1", "answer": "yes"}])
    write_jsonl(sdir / "quarantine.jsonl", [])
    (sdir / "committed" / "truth_marginals.json").write_text(
        json.dumps({"survey": "demo", "marginals": {"q1": {"yes": {"count": 10, "weighted_count": 10}, "no": {"count": 5, "weighted_count": 5}}}})
    )

    payload = build_survey_report_payload("demo", sdir)

    assert payload["summary"]["marginal_source"] == "committed"
    assert [row["count"] for row in payload["options"]] == [10, 5]


def test_survey_report_counts_checkbox_like_known_options(tmp_path: Path) -> None:
    sdir = tmp_path / "survey"
    sdir.mkdir()
    write_jsonl(
        sdir / "questions.jsonl",
        [
            {
                "question_name": "q2",
                "question_type": "free_text",
                "question_text": "Which have you heard of?",
                "source": {"known_options": ["AJ Bell", "Vanguard", "None of these"]},
            }
        ],
    )
    write_jsonl(
        sdir / "respondents.jsonl",
        [
            {"respondent_id": "r1", "weight": 1.0},
            {"respondent_id": "r2", "weight": 1.0},
            {"respondent_id": "r3", "weight": 1.0},
        ],
    )
    write_jsonl(
        sdir / "answers.jsonl",
        [
            {"respondent_id": "r1", "question": "q2", "answer": "AJ Bell; Vanguard"},
            {"respondent_id": "r2", "question": "q2", "answer": "Vanguard"},
            {"respondent_id": "r3", "question": "q2", "answer": "None of these"},
        ],
    )
    write_jsonl(sdir / "quarantine.jsonl", [])

    payload = build_survey_report_payload("demo", sdir)

    question = payload["questions"][0]
    options = {row["option_label"]: row for row in payload["options"]}
    assert question["question_type"] == "checkbox"
    assert question["option_count"] == 3
    assert options["AJ Bell"]["count"] == 1
    assert options["Vanguard"]["count"] == 2
    assert options["None of these"]["count"] == 1
    assert options["Vanguard"]["selection_share"] == 2 / 3
    assert payload["issues"] == []


def test_survey_report_treats_free_text_known_option_as_samples(tmp_path: Path) -> None:
    sdir = tmp_path / "survey"
    sdir.mkdir()
    write_jsonl(
        sdir / "questions.jsonl",
        [
            {
                "question_name": "q79",
                "question_type": "free_text",
                "question_text": "Any other comments?",
                "source": {"known_options": ["Free text"]},
            }
        ],
    )
    write_jsonl(sdir / "respondents.jsonl", [{"respondent_id": "r1"}, {"respondent_id": "r2"}])
    write_jsonl(
        sdir / "answers.jsonl",
        [
            {"respondent_id": "r1", "question": "q79", "answer": "I like the emails."},
            {"respondent_id": "r2", "question": "q79", "answer": "No comment"},
        ],
    )
    write_jsonl(sdir / "quarantine.jsonl", [])

    payload = build_survey_report_payload("demo", sdir)
    html = render_survey_report_html(payload)
    csv_paths = write_survey_report_csvs(payload, tmp_path / "survey_report.csv")

    assert payload["questions"][0]["question_type"] == "free_text"
    assert payload["questions"][0]["option_count"] == 1
    assert [row["response"] for row in payload["free_text_samples"]] == ["I like the emails.", "No comment"]
    assert [row for row in payload["options"] if row["question_name"] == "q79"] == []
    assert "Sample responses (2 shown)" in html
    assert "<details open>" not in html
    assert "Response 1" in html
    assert "sample-text" in html
    assert "free_text_samples_csv" in csv_paths


def test_survey_report_structures_list_quality_issue_values() -> None:
    payload = {
        "survey": "demo",
        "summary": {
            "survey": "demo",
            "respondent_count": 1,
            "question_count": 1,
            "answer_row_count": 1,
            "answered_row_count": 1,
            "missing_answer_count": 0,
            "open_quarantine_issue_count": 0,
            "no_answer_question_count": 0,
            "marginal_source": "draft",
        },
        "questions": [
            {
                "question_name": "q78",
                "question_text": "Which providers have you used?",
                "question_type": "checkbox",
                "question_options": [],
                "source_note": None,
                "answer_count": 1,
                "missing_count": 0,
                "response_rate": 1.0,
                "option_count": 0,
            }
        ],
        "options": [],
        "free_text_samples": [],
        "issues": [
            {
                "severity": "error",
                "question": "q78",
                "issue": "answers_not_in_known_options",
                "values": [
                    "Online investment platform (e.g. Interactive Investor, Vanguard): I currently use this",
                    "Private bank (e.g. Coutts, C Hoare and Co): I have never used this",
                ],
            }
        ],
        "no_answer_questions": [],
        "open_quarantine_issues": [],
    }

    html = render_survey_report_html(payload)

    assert "issue-values" in html
    assert "issue-value-main" in html
    assert "issue-value-sub" in html
    assert "Online investment platform" in html
    assert "I currently use this" in html
    assert "[&#x27;" not in html
    assert "['" not in html


def test_survey_report_html_and_csv_outputs(tmp_path: Path) -> None:
    payload = {
        "survey": "demo",
        "summary": {
            "survey": "demo",
            "respondent_count": 1,
            "question_count": 1,
            "answer_row_count": 1,
            "answered_row_count": 1,
            "missing_answer_count": 0,
            "open_quarantine_issue_count": 0,
            "no_answer_question_count": 0,
            "marginal_source": "draft",
        },
        "questions": [
            {
                "question_name": "q1",
                "question_text": "Pick one",
                "question_type": "multiple_choice",
                "question_options": ["yes"],
                "source_note": None,
                "answer_count": 1,
                "missing_count": 0,
                "response_rate": 1.0,
                "option_count": 1,
            }
        ],
        "options": [
            {
                "question_name": "q1",
                "question_text": "Pick one",
                "option_label": "yes",
                "count": 1,
                "weighted_count": 1.0,
                "weighted_share": 1.0,
                "is_declared_option": True,
                "is_missing": False,
            }
        ],
        "issues": [],
        "no_answer_questions": [],
        "open_quarantine_issues": [],
    }

    html = render_survey_report_html(payload)
    csv_paths = write_survey_report_csvs(payload, tmp_path / "survey_report.csv")

    assert "Demo Survey Report" in html
    assert "Survey id:" in html
    assert "Copy as Markdown" in html
    assert 'id="survey-report-data"' in html
    assert Path(csv_paths["questions_csv"]).exists()
    assert Path(csv_paths["options_csv"]).exists()
    assert "question_name" in Path(csv_paths["questions_csv"]).read_text().splitlines()[0]
