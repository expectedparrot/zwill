#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COMMANDS = [
    {
        "stem": "00_pew_workflow",
        "title": "Import and commit the PEW project from source files",
        "command": "zwill workflow pew-demo --fresh --no-edsl --workdir examples/pew_w154_diff1/workdir",
        "note": "Converts the normalized PEW metadata and respondent CSV into JSONL imports, initializes a fresh zwill project, imports questions/respondents/answers, expands response codes to human-readable labels, and commits the survey.",
    },
    {
        "stem": "01_agent_list_export",
        "title": "Export a sampled EDSL AgentList",
        "command": (
            "zwill edsl-export --survey pew_w154_diff1 --target agent-list "
            "--questions diff1_a,diff1_b,diff1_c,diff1_d,diff1_e --limit 30 "
            "--include-survey-context --path pew_w154_diff1_agent_list.edsl.json"
        ),
        "note": "Creates 30 EDSL agents. Each agent receives the five DIFF1 answers as traits, with question text stored in the AgentList codebook.",
    },
    {
        "stem": "02_agent_list_inspect",
        "title": "Inspect the AgentList",
        "command": "zwill agent-list inspect --path pew_w154_diff1_agent_list.edsl.json --format json",
        "note": "Checks the number of agents, trait keys, instruction coverage, and a small preview before asking a new question.",
    },
    {
        "stem": "03_agent_study_export_leadership",
        "title": "Export a multiple-choice AgentStudy job",
        "command": (
            "zwill agent-study export --agent-list pew_w154_diff1_agent_list.edsl.json "
            "--question-name gender_political_leadership_similarity --question-type multiple_choice "
            '--question-text "In general, when it comes to being effective leaders in politics, are men and women basically similar or basically different?" '
            '--question-option "Men and women are basically similar" '
            '--question-option "Men and women are basically different" '
            "--model openai:gpt-5.5 --model-param temperature=0 --path pew_w154_diff1_agent_study_leadership_job.edsl.json"
        ),
        "note": "Asks a new, related binary gender-attitudes question that was not in the original DIFF1 battery.",
    },
    {
        "stem": "04_agent_study_export_gender_roles",
        "title": "Export a free-text AgentStudy job",
        "command": (
            "zwill agent-study export --agent-list pew_w154_diff1_agent_list.edsl.json "
            "--question-name gender_roles_views --question-type free_text "
            '--question-text "Given this respondent\'s prior answers, briefly describe this respondent\'s likely views on gender roles in society. Mention the evidence from their prior survey answers." '
            "--model openai:gpt-5.5 --model-param temperature=0 --path pew_w154_diff1_agent_study_gender_roles_job.edsl.json"
        ),
        "note": "Asks a qualitative follow-up question about likely views on gender roles, using the same constructed agents.",
    },
    {
        "stem": "05_edsl_run_leadership",
        "title": "Run the multiple-choice EDSL job",
        "command": "zwill edsl-run --job pew_w154_diff1_agent_study_leadership_job.edsl.json --path pew_w154_diff1_agent_study_leadership_results.json.gz",
        "note": "Runs the serialized multiple-choice EDSL job and writes a serialized EDSL Results object.",
    },
    {
        "stem": "06_agent_study_import_leadership",
        "title": "Import the multiple-choice Results object",
        "command": "zwill agent-study import --path pew_w154_diff1_agent_study_leadership_results.json.gz --replace",
        "note": "Stores the raw Results object and extracts one row per agent/model/question answer for reporting.",
    },
    {
        "stem": "07_edsl_run_gender_roles",
        "title": "Run the free-text EDSL job",
        "command": "zwill edsl-run --job pew_w154_diff1_agent_study_gender_roles_job.edsl.json --path pew_w154_diff1_agent_study_gender_roles_results.json.gz",
        "note": "Runs the serialized free-text EDSL job and writes a second serialized EDSL Results object.",
    },
    {
        "stem": "08_agent_study_import_gender_roles",
        "title": "Import the free-text Results object",
        "command": "zwill agent-study import --path pew_w154_diff1_agent_study_gender_roles_results.json.gz --replace",
        "note": "Stores the raw free-text Results object and extracts one row per agent/model/question answer.",
    },
    {
        "stem": "09_agent_study_report",
        "title": "Export combined analysis data",
        "command": "zwill agent-study report --format json --path pew_w154_diff1_agent_study_report.json",
        "note": "Writes machine-readable rows and summary counts for both AgentStudy jobs.",
    },
]

def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def script_text(value: str) -> str:
    return value.replace("<", "\\u003c").replace("</script", "<\\/script")


def pct(numerator: int, denominator: int) -> str:
    return "0.0%" if denominator == 0 else f"{100 * numerator / denominator:.1f}%"


def answer_distribution(rows: list[dict[str, Any]]) -> Counter:
    return Counter(str(row.get("answer")) for row in rows)


def trait_key(row: dict[str, Any]) -> str:
    traits = row.get("traits") or {}
    values = [str(traits.get(key, "")) for key in sorted(traits)]
    similar = sum(1 for value in values if "similar" in value.lower())
    different = sum(1 for value in values if "different" in value.lower())
    if different > similar:
        return "More original answers said basically different"
    if similar > different:
        return "More original answers said basically similar"
    return "Split original answers"


def raw_text(row: dict[str, Any]) -> str:
    raw = row.get("raw_model_response")
    if not raw:
        return ""
    return json.dumps(raw, indent=2)[:1200]


def rows_for_question(rows: list[dict[str, Any]], question_name: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("question_name") == question_name]


def render_table(headers: list[str], rows: list[list[Any]]) -> str:
    thead = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"


def read_text_if_exists(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def render_captured_output(label: str, text: str) -> str:
    if not text:
        return f"<p class=\"small\">No {esc(label)} output.</p>"
    return (
        f"<details><summary>{esc(label)} output</summary>"
        f"<pre><code>{esc(text)}</code></pre>"
        "</details>"
    )


def render_commands(artifacts_dir: Path | None) -> str:
    parts = []
    for item in COMMANDS:
        stdout = ""
        stderr = ""
        if artifacts_dir is not None:
            stdout = read_text_if_exists(artifacts_dir / f"{item['stem']}.stdout.txt")
            stderr = read_text_if_exists(artifacts_dir / f"{item['stem']}.stderr.txt")
        parts.append(
            f"<section class=\"step\"><h3>{esc(item['title'])}</h3><p>{esc(item['note'])}</p>"
            f"<pre><code>{esc(item['command'])}</code></pre>"
            f"{render_captured_output('stdout', stdout)}"
            f"{render_captured_output('stderr', stderr)}"
            "</section>"
        )
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-list-inspect", type=Path, required=True)
    parser.add_argument("--leadership-job", type=Path, required=True)
    parser.add_argument("--gender-roles-job", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()

    inspect_payload = read_json(args.agent_list_inspect)
    leadership_job_payload = read_json(args.leadership_job)
    gender_roles_job_payload = read_json(args.gender_roles_job)
    report_payload = read_json(args.report_json)
    rows = report_payload.get("rows", [])
    summary = report_payload.get("summary", {})
    leadership_job_id = leadership_job_payload.get("zwill", {}).get("agent_study_job_id")
    gender_roles_job_id = gender_roles_job_payload.get("zwill", {}).get("agent_study_job_id")
    leadership_rows = rows_for_question(rows, "gender_political_leadership_similarity")
    gender_roles_rows = rows_for_question(rows, "gender_roles_views")

    distribution = answer_distribution(leadership_rows)
    by_trait = defaultdict(Counter)
    for row in leadership_rows:
        by_trait[trait_key(row)][str(row.get("answer"))] += 1

    dist_rows = [
        [answer, count, pct(count, len(leadership_rows))]
        for answer, count in distribution.most_common()
    ]
    trait_rows = []
    for group, counts in sorted(by_trait.items()):
        total = sum(counts.values())
        trait_rows.append(
            [
                group,
                total,
                counts.get("Men and women are basically similar", 0),
                counts.get("Men and women are basically different", 0),
            ]
        )

    example_rows = []
    for row in leadership_rows[:8]:
        traits = row.get("traits") or {}
        original = "; ".join(f"{key}: {value}" for key, value in sorted(traits.items()))
        cache_used = (row.get("cache_used_dict") or {}).get(row.get("question_name"))
        example_rows.append(
            [
                row.get("agent_name"),
                original,
                row.get("answer"),
                "yes" if row.get("comment") else "no",
                cache_used,
            ]
        )

    free_text_rows = []
    for row in gender_roles_rows[:10]:
        free_text_rows.append(
            [
                row.get("agent_name"),
                trait_key(row),
                row.get("answer"),
                (row.get("cache_used_dict") or {}).get(row.get("question_name")),
            ]
        )

    prompt_rows = []
    for row in (leadership_rows[:2] + gender_roles_rows[:2]):
        prompt_rows.append(
            [
                row.get("agent_name"),
                row.get("question_name"),
                row.get("system_prompt") or row.get("agent_instruction") or "",
                row.get("user_prompt") or "",
            ]
        )

    raw_example = raw_text(leadership_rows[0] if leadership_rows else rows[0]) if rows else ""
    markdown_summary = f"""# PEW W154 DIFF1 AgentList Study

This example used `zwill` to export 30 PEW W154 DIFF1 respondents as EDSL agents, ask a new related binary gender-attitudes question and a free-text question about gender roles, import the EDSL Results objects, and analyze the extracted answers.

Multiple-choice job id: `{leadership_job_id}`
Free-text job id: `{gender_roles_job_id}`

The new multiple-choice question was:

> In general, when it comes to being effective leaders in politics, are men and women basically similar or basically different?

The free-text question asked the model to briefly describe the respondent's likely views on gender roles in society, citing prior survey-answer evidence.

The model answered `{len(leadership_rows)}` multiple-choice rows and `{len(gender_roles_rows)}` free-text rows. The most common multiple-choice answer was `{distribution.most_common(1)[0][0] if leadership_rows else "n/a"}`.
"""

    html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>PEW AgentList Study Report</title>
  <style>
    :root {{
      --ink: #17202a;
      --muted: #5d6d7e;
      --line: #d8dee8;
      --panel: #f7f9fb;
      --accent: #2f6f4e;
      --code: #111827;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #ffffff;
      line-height: 1.5;
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 40px 28px 64px;
    }}
    .brand {{
      color: var(--accent);
      font-weight: 800;
      letter-spacing: .02em;
      margin-bottom: 28px;
    }}
    h1 {{
      font-size: 40px;
      line-height: 1.08;
      margin: 0 0 12px;
    }}
    h2 {{
      margin-top: 42px;
      border-top: 1px solid var(--line);
      padding-top: 26px;
    }}
    h3 {{
      margin-bottom: 8px;
    }}
    .lede {{
      font-size: 18px;
      color: var(--muted);
      max-width: 860px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 28px 0;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 14px;
      background: var(--panel);
    }}
    .metric strong {{
      display: block;
      font-size: 24px;
    }}
    .metric span {{
      color: var(--muted);
      font-size: 13px;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      margin: 16px 0 24px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      padding: 10px;
    }}
    th {{
      background: var(--panel);
    }}
    pre {{
      background: var(--code);
      color: #f8fafc;
      border-radius: 6px;
      padding: 14px;
      overflow-x: auto;
      white-space: pre-wrap;
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
    }}
    .step {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 16px;
      margin: 14px 0;
    }}
    .note {{
      background: #fff8d6;
      border: 1px solid #eadc8b;
      border-radius: 6px;
      padding: 14px;
    }}
    .small {{
      color: var(--muted);
      font-size: 13px;
    }}
    button {{
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 6px;
      padding: 9px 12px;
      font-weight: 700;
      cursor: pointer;
    }}
    @media (max-width: 760px) {{
      .metrics {{ grid-template-columns: 1fr 1fr; }}
      h1 {{ font-size: 31px; }}
    }}
  </style>
</head>
<body>
<main>
  <div class="brand">E[🦜] EXPECTED PARROT</div>
  <h1>PEW W154 AgentList Study: Asking Survey-Built Agents New Gender-Attitudes Questions</h1>
  <p class="lede">This short research report is a literate `zwill` example. It starts from the normalized PEW W154 DIFF1 source files, imports and commits the survey, exports respondents as EDSL agents, asks one binary question and one free-text question, imports the returned EDSL Results objects, and summarizes what the constructed agents answered.</p>
  <button id="copy-md">Copy Markdown summary</button>

  <div class="metrics">
    <div class="metric"><strong>{esc(summary.get("agent_count", 0))}</strong><span>agents analyzed</span></div>
    <div class="metric"><strong>{esc(len(leadership_rows))}</strong><span>binary answer rows</span></div>
    <div class="metric"><strong>{esc(len(gender_roles_rows))}</strong><span>free-text answer rows</span></div>
    <div class="metric"><strong>{esc(summary.get("model_count", 0))}</strong><span>model count</span></div>
  </div>

  <h2>Research Design</h2>
  <p>The source survey is the PEW W154 DIFF1 battery: five binary items asking whether men and women are basically similar or basically different in hobbies and interests, physical abilities, parenting, emotional expression, and workplace abilities. The example uses those five answers as respondent traits. The first new question asks about effective leadership in politics, which is deliberately related to the original battery but not one of the measured items. The second asks for a short free-text description of the respondent's likely views on gender roles in society, citing evidence from the prior survey answers.</p>
  <p class="note">This example first runs `zwill workflow pew-demo --fresh --no-edsl`, so the visible workflow includes the PEW import step. It then uses `--limit 30`, which takes the first 30 respondents from the imported PEW file. That makes the model run cheap and inspectable, but it is not a representative estimate of the full PEW population. Treat the result as a demonstration of the AgentList and AgentStudy workflow.</p>

  <h2>Literate `zwill` Workflow</h2>
  {render_commands(args.artifacts_dir)}

  <h2>AgentList Check</h2>
  <p>The AgentList inspection reported {esc(inspect_payload.get("agent_count"))} agents with trait keys: {esc(", ".join(inspect_payload.get("trait_keys", [])))}. The shared codebook keeps each trait tied to its human-readable question text.</p>

  <h2>Result Summary</h2>
  <p>The constructed agents answered one binary question with the same two response options used in the original DIFF1 battery.</p>
  {render_table(["Answer", "Count", "Share"], dist_rows)}

  <h2>Relationship to Original DIFF1 Pattern</h2>
  <p>This descriptive split groups agents by whether their five original answers leaned toward “basically similar” or “basically different,” then reports their answer to the new political-leadership question.</p>
  {render_table(["Original DIFF1 pattern", "Agents", "New answer: similar", "New answer: different"], trait_rows)}

  <h2>Example Agent Rows</h2>
  <p>The table below shows the first eight extracted rows from `zwill agent-study report`. These are the raw unit of analysis: one constructed agent, one new question, one model answer. The comment column is diagnostic: this run used an EDSL multiple-choice question, and the model returned only an option string, so no separate comment was parsed.</p>
  {render_table(["Agent", "Original DIFF1 traits", "New answer", "Comment present?", "Cache used?"], example_rows)}

  <h2>Free-Text Gender Roles Responses</h2>
  <p>The second AgentStudy job asks each constructed agent for a short qualitative description of the respondent's likely views on gender roles in society. These answers are not scored against ground truth; they are useful for inspecting how the agent applies prior survey-answer evidence.</p>
  {render_table(["Agent", "Original DIFF1 pattern", "Free-text answer", "Cache used?"], free_text_rows)}

  <h2>Agent Prompts</h2>
  <p>The table below shows rendered prompts extracted from the EDSL Results objects. The system prompt contains the survey context plus the prior survey question-answer pairs; the user prompt contains the new question.</p>
  {render_table(["Agent", "Question", "System prompt", "User prompt"], prompt_rows)}

  <h2>Interpretation</h2>
  <p>For this small run, the useful output is not a population estimate. The practical value is that the workflow makes it easy to instantiate respondents from real survey answers, ask a related question, and inspect whether the answers move in a coherent direction relative to the original traits. If this were being used for a real study, the next step would be to run a larger, randomized respondent sample and compare alternative models or prompts.</p>
  <p>Because this is an AgentStudy rather than a held-out validation study, there is no real respondent answer for either new question. The analysis is therefore descriptive: answer distributions, relationship to observed traits, free-text responses, prompts, and raw model responses. To measure accuracy, use a digital-twin hold-out design where the target question was actually answered by respondents.</p>

  <h2>Representative Raw Model Response</h2>
  <pre><code>{esc(raw_example)}</code></pre>

  <p class="small">Generated {esc(datetime.now(timezone.utc).isoformat())}. Workdir: {esc(args.workdir)}.</p>
  <script type="application/json" id="agent-study-data">{script_text(json.dumps(report_payload))}</script>
  <script type="text/markdown" id="markdown-summary">{script_text(markdown_summary)}</script>
  <script>
    document.getElementById("copy-md").addEventListener("click", async () => {{
      const text = document.getElementById("markdown-summary").textContent;
      await navigator.clipboard.writeText(text);
      document.getElementById("copy-md").textContent = "Copied";
    }});
  </script>
</main>
</body>
</html>
"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_doc)
    print(args.output)


if __name__ == "__main__":
    main()
