from __future__ import annotations

import csv
import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .reporting import EP_REPORT_CSS, copy_markdown_control, report_display_title


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def escape_html(value: Any) -> str:
    return html.escape(str(value), quote=True)


def source_note(question: dict[str, Any]) -> str | None:
    source = question.get("source") if isinstance(question.get("source"), dict) else {}
    note = source.get("note") or source.get("raw_id")
    return str(note) if note else None


def known_options(question: dict[str, Any]) -> list[str]:
    options = question.get("question_options") or []
    if options:
        return [str(option) for option in options]
    source = question.get("source") if isinstance(question.get("source"), dict) else {}
    source_options = source.get("known_options") or []
    if isinstance(source_options, list):
        return [str(option) for option in source_options if str(option).strip()]
    return []


def is_checkbox_like(question: dict[str, Any]) -> bool:
    options = known_options(question)
    if not question.get("question_options") and len(options) == 1 and options[0].strip().lower() in {"free text", "freetext", "text"}:
        return False
    return not question.get("question_options") and bool(options)


def is_free_text_question(question: dict[str, Any]) -> bool:
    return str(question.get("question_type")) == "free_text" and not is_checkbox_like(question)


def split_checkbox_answer(value: Any, options: list[str]) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    option_set = set(options)
    if text in option_set:
        return [text]
    parts = [part.strip() for part in text.split(";") if part.strip()]
    return parts if parts else [text]


def answer_value(answer: dict[str, Any]) -> str:
    if answer.get("answer") is None:
        return "__missing__"
    return str(answer.get("answer"))


def compute_draft_marginals(
    questions: list[dict[str, Any]],
    respondents: list[dict[str, Any]],
    answers: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, float | int]]]:
    respondents_by_id = {row["respondent_id"]: row for row in respondents}
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    weighted_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for answer in answers:
        value = answer_value(answer)
        weight = float(respondents_by_id.get(answer.get("respondent_id"), {}).get("weight", 1.0))
        counts[str(answer.get("question"))][value] += 1
        weighted_counts[str(answer.get("question"))][value] += weight
    marginals: dict[str, dict[str, dict[str, float | int]]] = {}
    for question in questions:
        qname = str(question["question_name"])
        keys = list(question.get("question_options") or [])
        extras = [key for key in counts[qname] if key not in keys]
        keys.extend(sorted(extras))
        marginals[qname] = {
            str(key): {
                "count": counts[qname].get(str(key), 0),
                "weighted_count": round(weighted_counts[qname].get(str(key), 0.0), 10),
            }
            for key in keys
        }
    return marginals


def build_survey_report_payload(survey: str, sdir: Path) -> dict[str, Any]:
    questions = read_jsonl(sdir / "questions.jsonl")
    respondents = read_jsonl(sdir / "respondents.jsonl")
    answers = read_jsonl(sdir / "answers.jsonl")
    quarantine = read_jsonl(sdir / "quarantine.jsonl")
    committed_truth = read_json(sdir / "committed" / "truth_marginals.json", {})
    marginals = committed_truth.get("marginals") if isinstance(committed_truth, dict) else None
    marginal_source = "committed" if marginals else "draft"
    if not marginals:
        marginals = compute_draft_marginals(questions, respondents, answers)

    answer_counts: Counter[str] = Counter(str(row.get("question")) for row in answers if row.get("answer") is not None)
    missing_counts: Counter[str] = Counter(str(row.get("question")) for row in answers if row.get("answer") is None)
    respondent_count = len(respondents)
    answer_rows_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for answer in answers:
        answer_rows_by_question[str(answer.get("question"))].append(answer)

    question_rows = []
    option_rows = []
    free_text_rows = []
    issues = []
    total_missing = sum(missing_counts.values())
    for question in questions:
        qname = str(question["question_name"])
        options = known_options(question)
        q_marginal = marginals.get(qname, {}) if isinstance(marginals, dict) else {}
        answered = answer_counts[qname]
        missing = missing_counts[qname]
        response_rate = answered / respondent_count if respondent_count else 0.0
        checkbox_like = is_checkbox_like(question)
        free_text = is_free_text_question(question)
        question_rows.append(
            {
                "question_name": qname,
                "question_text": question.get("question_text"),
                "question_type": "checkbox" if checkbox_like else question.get("question_type"),
                "question_options": options,
                "source_note": source_note(question),
                "answer_count": answered,
                "missing_count": missing,
                "response_rate": response_rate,
                "option_count": len(options),
            }
        )
        if question.get("question_type") == "multiple_choice" and not options:
            issues.append({"severity": "warning", "question": qname, "issue": "multiple_choice_without_options"})
        if free_text:
            responses = [
                str(answer.get("answer")).strip()
                for answer in answer_rows_by_question[qname]
                if answer.get("answer") is not None and str(answer.get("answer")).strip()
            ]
            for index, response in enumerate(responses[:50], start=1):
                free_text_rows.append(
                    {
                        "question_name": qname,
                        "question_text": question.get("question_text"),
                        "sample_index": index,
                        "response": response,
                    }
                )
        elif checkbox_like:
            respondents_by_id = {row["respondent_id"]: row for row in respondents}
            selection_counts: Counter[str] = Counter()
            weighted_selection_counts: Counter[str] = Counter()
            invalid_values = set()
            for answer in answer_rows_by_question[qname]:
                if answer.get("answer") is None:
                    continue
                weight = float(respondents_by_id.get(answer.get("respondent_id"), {}).get("weight", 1.0))
                for selected in split_checkbox_answer(answer.get("answer"), options):
                    if selected not in options:
                        invalid_values.add(selected)
                        continue
                    selection_counts[selected] += 1
                    weighted_selection_counts[selected] += weight
            if invalid_values:
                issues.append({"severity": "error", "question": qname, "issue": "answers_not_in_known_options", "values": sorted(invalid_values)})
            denominator = answered or respondent_count
            weighted_denominator = sum(float(respondent.get("weight", 1.0)) for respondent in respondents) if respondents else 0.0
            for option in options:
                weighted_count = float(weighted_selection_counts.get(option, 0.0))
                option_rows.append(
                    {
                        "question_name": qname,
                        "question_text": question.get("question_text"),
                        "option_label": option,
                        "count": int(selection_counts.get(option, 0)),
                        "weighted_count": weighted_count,
                        "weighted_share": weighted_count / weighted_denominator if weighted_denominator else 0.0,
                        "selection_share": selection_counts.get(option, 0) / denominator if denominator else 0.0,
                        "is_declared_option": True,
                        "is_missing": False,
                    }
                )
        else:
            total_weighted = sum(float(item.get("weighted_count", item.get("count", 0.0))) for item in q_marginal.values())
            observed_values = {answer_value(row) for row in answer_rows_by_question[qname] if row.get("answer") is not None}
            invalid_values = sorted(observed_values - set(options)) if options else []
            if invalid_values:
                issues.append({"severity": "error", "question": qname, "issue": "answers_not_in_options", "values": invalid_values})
            ordered_options = options + sorted(option for option in q_marginal if option not in options)
            for option in ordered_options:
                item = q_marginal.get(option, {})
                weighted_count = float(item.get("weighted_count", item.get("count", 0.0)))
                option_rows.append(
                    {
                        "question_name": qname,
                        "question_text": question.get("question_text"),
                        "option_label": option,
                        "count": int(item.get("count", 0)),
                        "weighted_count": weighted_count,
                        "weighted_share": weighted_count / total_weighted if total_weighted else 0.0,
                        "selection_share": weighted_count / total_weighted if total_weighted else 0.0,
                        "is_declared_option": option in options,
                        "is_missing": option == "__missing__",
                    }
                )

    no_answer_questions = [row["question_name"] for row in question_rows if row["answer_count"] == 0]
    open_quarantine = [row for row in quarantine if row.get("status") == "open"]
    summary = {
        "survey": survey,
        "respondent_count": respondent_count,
        "question_count": len(questions),
        "answer_row_count": len(answers),
        "answered_row_count": sum(answer_counts.values()),
        "missing_answer_count": total_missing,
        "open_quarantine_issue_count": len(open_quarantine),
        "no_answer_question_count": len(no_answer_questions),
        "marginal_source": marginal_source,
    }
    return {
        "survey": survey,
        "summary": summary,
        "questions": question_rows,
        "options": option_rows,
        "free_text_samples": free_text_rows,
        "issues": issues,
        "no_answer_questions": no_answer_questions,
        "open_quarantine_issues": open_quarantine[:50],
    }


def render_issue_values_html(values: Any) -> str:
    if values in (None, "", []):
        return ""
    if not isinstance(values, list):
        return f'<div class="issue-value">{escape_html(values)}</div>'

    rows = []
    for value in values:
        text = str(value)
        if ": " in text:
            main, sub = text.rsplit(": ", 1)
            rows.append(
                f"""
                <li>
                  <span class="issue-value-main">{escape_html(main)}</span>
                  <span class="issue-value-sub">{escape_html(sub)}</span>
                </li>
                """
            )
        else:
            rows.append(f"<li>{escape_html(text)}</li>")
    return f'<ul class="issue-values">{"".join(rows)}</ul>'


def render_issue_html(issue: dict[str, Any]) -> str:
    severity = issue.get("severity", "issue")
    issue_name = issue.get("issue", "unknown_issue")
    values = render_issue_values_html(issue.get("values"))
    return f"""
      <li>
        <div><strong>{escape_html(issue_name)}</strong> <span class="issue-severity">{escape_html(severity)}</span></div>
        {values}
      </li>
    """


def render_survey_report_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    display_title, _raw_title = report_display_title(str(payload["survey"]))
    issues_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in payload.get("issues", []):
        question_name = issue.get("question")
        if question_name:
            issues_by_question[str(question_name)].append(issue)
    question_sections = []
    for question in payload["questions"]:
        qname = question["question_name"]
        options = [row for row in payload["options"] if row["question_name"] == qname]
        free_text_samples = [row for row in payload.get("free_text_samples", []) if row["question_name"] == qname]
        question_issues = issues_by_question.get(qname, [])
        max_share = max((row["weighted_share"] for row in options), default=0.0) or 1.0
        option_rows = []
        for option in options:
            width = 100 * option["weighted_share"] / max_share if max_share else 0
            option_rows.append(
                f"""
                <tr>
                  <td>{escape_html(option['option_label'])}</td>
                  <td class="num">{option['count']}</td>
                  <td class="num">{option['weighted_share']:.1%}</td>
                  <td><div class="bar"><span style="width:{width:.1f}%"></span></div></td>
                </tr>
                """
            )
        if free_text_samples:
            response_rows = "".join(
                f"""
                <li>
                  <div class="sample-index">Response {escape_html(row.get('sample_index'))}</div>
                  <div class="sample-text">{escape_html(row['response'])}</div>
                </li>
                """
                for row in free_text_samples
            )
            body = f"""
              <details>
                <summary>Sample responses ({len(free_text_samples)} shown)</summary>
                <ol class="free-text-samples">{response_rows}</ol>
              </details>
            """
        else:
            body = f"""
              <table>
                <thead><tr><th>Option</th><th>Count</th><th>Weighted share</th><th>Distribution</th></tr></thead>
                <tbody>{''.join(option_rows)}</tbody>
              </table>
            """
        issue_body = ""
        if question_issues:
            issue_rows = "".join(render_issue_html(issue) for issue in question_issues)
            issue_body = f"""
              <details class="question-issues">
                <summary>Data quality issues ({len(question_issues)})</summary>
                <ul>{issue_rows}</ul>
              </details>
            """
        question_sections.append(
            f"""
            <section class="question">
              <h2>{escape_html(qname)} <span>{escape_html(question.get('question_type'))}</span></h2>
              <p class="qtext">{escape_html(question.get('question_text'))}</p>
              <div class="meta">Answers: {question['answer_count']} | Missing: {question['missing_count']} | Response rate: {question['response_rate']:.1%} | Options: {question['option_count']}</div>
              {f"<div class='source'>Source: {escape_html(question.get('source_note'))}</div>" if question.get('source_note') else ""}
              {issue_body}
              {body}
            </section>
            """
        )
    data = escape_html(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{escape_html(display_title)} Survey Report</title>
  <style>
    {EP_REPORT_CSS}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin: 20px 0 28px; }}
    .stat {{ border: 1px solid var(--ep-border); border-radius: 8px; padding: 12px; background: var(--ep-light-gray); }}
    .stat b {{ display:block; font-size: 22px; margin-bottom: 4px; }}
    .question {{ border-top: 1px solid var(--ep-border); padding: 22px 0; }}
    h2 span {{ font-family: var(--font-sans); font-size: 12px; color: var(--ep-gray); font-weight: 500; margin-left: 8px; }}
    .qtext {{ margin: 0 0 8px; }}
    .meta,.source {{ font-size: 13px; margin-bottom: 8px; }}
    .bar {{ height: 12px; background: #edf0f4; border-radius: 4px; overflow: hidden; }}
    .bar span {{ display:block; height:100%; background:#2f6f9f; }}
    .issues {{ background:#fff8e6; border:1px solid #efd38a; padding:14px 18px; border-radius:8px; }}
    .question-issues {{ background:#fff8e6; border:1px solid #efd38a; padding:10px 12px; border-radius:8px; margin:10px 0; }}
    .question-issues ul {{ margin:8px 0 0; padding-left:22px; }}
    .issue-values {{ display:grid; gap:6px; margin-top:8px; }}
    .issue-values li {{ line-height:1.35; }}
    .issue-value-main {{ font-weight:650; }}
    .issue-value-sub {{ color:#637083; margin-left:4px; }}
    .issue-severity {{ color:#637083; font-size:12px; margin-left:6px; text-transform:uppercase; letter-spacing:.03em; }}
    details {{ margin-top: 12px; }}
    summary {{ cursor: pointer; color:#2f4f5a; font-weight: 650; }}
    .free-text-samples {{ list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 10px; }}
    .free-text-samples li {{ border: 1px solid #d8dee6; border-radius: 8px; background: #fbfcfd; padding: 10px 12px; line-height: 1.4; }}
    .sample-index {{ font-size: 12px; font-weight: 700; color: #637083; margin-bottom: 5px; text-transform: uppercase; letter-spacing: .03em; }}
    .sample-text {{ white-space: pre-wrap; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <h1>{escape_html(display_title)} Survey Report</h1>
  <div class="meta">Survey id: <code>{escape_html(payload['survey'])}</code> | Marginal source: {escape_html(summary['marginal_source'])}</div>
  <div class="summary">
    <div class="stat"><b>{summary['respondent_count']}</b>Respondents</div>
    <div class="stat"><b>{summary['question_count']}</b>Questions</div>
    <div class="stat"><b>{summary['answer_row_count']}</b>Answer rows</div>
    <div class="stat"><b>{summary['missing_answer_count']}</b>Missing answers</div>
    <div class="stat"><b>{summary['open_quarantine_issue_count']}</b>Open quarantine issues</div>
    <div class="stat"><b>{summary['no_answer_question_count']}</b>Questions with no answers</div>
  </div>
  {''.join(question_sections)}
  <script type="application/json" id="survey-report-data">{data}</script>
</body>
</html>
"""


def write_survey_report_csvs(payload: dict[str, Any], path: Path) -> dict[str, str]:
    question_path = path.with_name(path.stem + "_questions.csv")
    option_path = path.with_name(path.stem + "_options.csv")
    free_text_path = path.with_name(path.stem + "_free_text_samples.csv")
    question_fields = list(payload["questions"][0].keys()) if payload["questions"] else [
        "question_name",
        "question_text",
        "question_type",
        "answer_count",
        "missing_count",
        "response_rate",
    ]
    option_fields = list(payload["options"][0].keys()) if payload["options"] else [
        "question_name",
        "option_label",
        "count",
        "weighted_count",
        "weighted_share",
    ]
    with question_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=question_fields)
        writer.writeheader()
        writer.writerows(payload["questions"])
    with option_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=option_fields)
        writer.writeheader()
        writer.writerows(payload["options"])
    free_text_samples = payload.get("free_text_samples", [])
    if free_text_samples:
        free_text_fields = list(free_text_samples[0].keys())
        with free_text_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=free_text_fields)
            writer.writeheader()
            writer.writerows(free_text_samples)
        return {"questions_csv": str(question_path), "options_csv": str(option_path), "free_text_samples_csv": str(free_text_path)}
    return {"questions_csv": str(question_path), "options_csv": str(option_path)}
