from __future__ import annotations

from .cli import *  # noqa: F403


def agent_study_question_name(row: dict[str, Any]) -> str | None:
    answer = row.get("answer") or {}
    question_to_attributes = row.get("question_to_attributes") or {}
    for key in question_to_attributes:
        if key in answer:
            return key
    for key in answer:
        if not key.endswith("_comment"):
            return key
    return None


def read_agent_study_manifest() -> list[dict[str, Any]]:
    manifest = read_json(agent_study_manifest_path(), {"runs": []})
    runs = manifest.get("runs", [])
    return runs if isinstance(runs, list) else []


def write_agent_study_manifest(runs: list[dict[str, Any]]) -> None:
    write_json(agent_study_manifest_path(), {"runs": runs})


def upsert_agent_study_manifest(run: dict[str, Any]) -> None:
    runs = [item for item in read_agent_study_manifest() if item.get("job_id") != run.get("job_id")]
    runs.append(run)
    write_agent_study_manifest(sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True))


def agent_study_import_metadata(job_id: str) -> dict[str, Any]:
    return read_json(agent_study_dir(job_id) / "import.json", {})


def cmd_agent_study_import(args: argparse.Namespace) -> dict[str, Any]:
    require_project()
    source = Path(args.input_path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.input_path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    job_id = args.job_id or results.get("zwill", {}).get("agent_study_job_id") or agent_study_job_id_from_results(results)
    jdir = agent_study_dir(job_id)
    if jdir.exists() and not args.replace:
        raise ZwillError("already_exists", f"Agent study results already imported for job id {job_id}.", hint="Use --replace.")
    if jdir.exists():
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)

    existing = [row for row in read_jsonl(agent_study_answers_path()) if row.get("job_id") != job_id]
    extracted = []
    issues = []
    for index, row in enumerate(results.get("data", [])):
        question_name = agent_study_question_name(row)
        answer = row.get("answer") or {}
        if not question_name:
            issues.append({"row": index, "error": "missing_answer_question"})
            continue
        agent = row.get("agent", {})
        model = row.get("model", {})
        question_attributes = (row.get("question_to_attributes") or {}).get(question_name, {})
        raw_model_response = row.get("raw_model_response", {})
        prompt = row.get("prompt", {})
        system_prompt = (prompt.get(f"{question_name}_system_prompt") or {}).get("text")
        user_prompt = (prompt.get(f"{question_name}_user_prompt") or {}).get("text")
        extracted.append(
            {
                "job_id": job_id,
                "row": index,
                "agent_name": agent.get("name"),
                "traits": agent.get("traits", {}),
                "agent_instruction": agent.get("instruction"),
                "instruction_present": bool(agent.get("instruction")),
                "instruction_chars": len(agent.get("instruction") or ""),
                "question_name": question_name,
                "question_text": question_attributes.get("question_text"),
                "question_type": question_attributes.get("question_type"),
                "question_options": question_attributes.get("question_options", []),
                "answer": answer.get(question_name),
                "comment": answer.get(f"{question_name}_comment") or (row.get("comments_dict") or {}).get(f"{question_name}_comment"),
                "model": model.get("model"),
                "service": model.get("inference_service"),
                "model_label": model_label(model.get("inference_service"), model.get("model")),
                "model_parameters": model.get("parameters", {}),
                "scenario": row.get("scenario", {}),
                "prompt": prompt,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "raw_model_response": raw_model_response,
                "generated_tokens": row.get("generated_tokens", {}),
                "comments_dict": row.get("comments_dict", {}),
                "reasoning_summaries_dict": row.get("reasoning_summaries_dict", {}),
                "cache_used_dict": row.get("cache_used_dict", {}),
                "validated_dict": row.get("validated_dict", {}),
                "source_raw": str(stored_raw),
                "imported_at": utc_now(),
            }
        )

    rewrite_jsonl(agent_study_answers_path(), existing + extracted)
    metadata = {
        "job_id": job_id,
        "source_path": str(source),
        "source_hash": sha256(source),
        "stored_path": str(stored_raw),
        "stored_hash": sha256(stored_raw),
        "row_count": len(results.get("data", [])),
        "extracted_count": len(extracted),
        "issue_count": len(issues),
        "issues": issues,
        "question_names": sorted({row.get("question_name") for row in extracted if row.get("question_name")}),
        "models": sorted({row.get("model_label") for row in extracted if row.get("model_label")}),
        "imported_at": utc_now(),
    }
    write_json(jdir / "import.json", metadata)
    upsert_agent_study_manifest(
        {
            "job_id": job_id,
            "status": "imported",
            "created_at": metadata["imported_at"],
            "results_path": str(source),
            "stored_raw": str(stored_raw),
            "row_count": metadata["row_count"],
            "extracted_count": metadata["extracted_count"],
            "issue_count": metadata["issue_count"],
            "question_names": metadata["question_names"],
            "models": metadata["models"],
        }
    )
    return envelope(
        "zwill agent-study import",
        "ok",
        {
            "job_id": job_id,
            "stored_raw": str(stored_raw),
            "row_count": metadata["row_count"],
            "extracted_count": metadata["extracted_count"],
            "issue_count": metadata["issue_count"],
            "issues": issues,
        },
        next_steps=[f"zwill agent-study report --job-id {job_id}"],
    )


def build_agent_study_report(rows: list[dict[str, Any]], job_id: str | None = None) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    distributions: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_model[row.get("model_label", "")].append(row)
        by_question[row.get("question_name", "")].append(row)
        distributions[f"{row.get('question_name', '')}::{row.get('model_label', '')}"][str(row.get("answer"))] += 1
    return {
        "job_id": job_id,
        "rows": rows,
        "summary": {
            "row_count": len(rows),
            "agent_count": len({row.get("agent_name") for row in rows}),
            "question_count": len({row.get("question_name") for row in rows}),
            "model_count": len({row.get("model_label") for row in rows}),
            "by_model": {model: {"rows": len(model_rows)} for model, model_rows in sorted(by_model.items())},
            "by_question": {question: {"rows": len(question_rows)} for question, question_rows in sorted(by_question.items())},
            "answer_distributions": {key: dict(counter) for key, counter in sorted(distributions.items())},
        },
        "health": {"import": agent_study_import_metadata(job_id)} if job_id else {},
    }


def render_agent_study_report_html(payload: dict[str, Any]) -> str:
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    body_rows = "\n".join(
        "<tr>"
        f"<td>{html_escape(row.get('agent_name'))}</td>"
        f"<td>{html_escape(row.get('question_name'))}</td>"
        f"<td>{html_escape(row.get('answer'))}</td>"
        f"<td>{html_escape(row.get('model_label'))}</td>"
        f"<td>{html_escape(row.get('comment') or '')}</td>"
        "</tr>"
        for row in rows
    )
    data = escape_script_text(json.dumps(payload))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Agent Study Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d8dee8; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f6f8; }}
    .summary {{ display: flex; gap: 16px; margin: 20px 0; }}
    .summary div {{ border: 1px solid #d8dee8; padding: 12px; border-radius: 6px; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <h1>Agent Study Report</h1>
  <div class="summary">
    <div><strong>Rows</strong><br>{summary.get('row_count', 0)}</div>
    <div><strong>Agents</strong><br>{summary.get('agent_count', 0)}</div>
    <div><strong>Questions</strong><br>{summary.get('question_count', 0)}</div>
    <div><strong>Models</strong><br>{summary.get('model_count', 0)}</div>
  </div>
  <table>
    <thead><tr><th>Agent</th><th>Question</th><th>Answer</th><th>Model</th><th>Comment</th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
  <script type="application/json" id="agent-study-data">{data}</script>
</body>
</html>
"""


def html_escape(value: Any) -> str:
    import html

    return html.escape(str(value), quote=True)


def cmd_agent_study_report(args: argparse.Namespace) -> None:
    require_project()
    rows = read_jsonl(agent_study_answers_path())
    if args.job_id:
        rows = [row for row in rows if row.get("job_id") == args.job_id]
    if args.model:
        rows = [row for row in rows if row.get("model") == args.model or row.get("model_label") == args.model]
    if not rows:
        raise ZwillError("not_found", "No agent-study answers found for the requested filters.")
    payload = build_agent_study_report(rows, args.job_id)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    fieldnames = ["job_id", "agent_name", "question_name", "answer", "comment", "service", "model", "model_label", "instruction_present", "instruction_chars"]
    if args.format == "csv":
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.path).open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: row.get(key) for key in fieldnames})
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key) for key in fieldnames})
        return
    if args.format == "html":
        output = render_agent_study_report_html(payload)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output)
        else:
            print(output)
        return
    table = Table(title="agent study answers")
    for column in ["agent", "question", "answer", "model"]:
        table.add_column(column)
    for row in rows:
        table.add_row(str(row.get("agent_name", "")), str(row.get("question_name", "")), str(row.get("answer", "")), str(row.get("model_label", "")))
    Console().print(table)


def cmd_agent_study_list(args: argparse.Namespace) -> None:
    require_project()
    runs = read_agent_study_manifest()
    if args.format == "json":
        print(json.dumps({"runs": runs}, indent=2))
        return
    table = Table(title="agent studies")
    for column in ["job_id", "status", "created_at", "rows", "issues", "questions", "models"]:
        table.add_column(column)
    for run in runs:
        table.add_row(
            str(run.get("job_id", "")),
            str(run.get("status", "")),
            str(run.get("created_at", "")),
            str(run.get("extracted_count", "")),
            str(run.get("issue_count", "")),
            ", ".join(run.get("question_names", [])),
            ", ".join(run.get("models", [])),
        )
    Console().print(table)


def cmd_agent_study_show(args: argparse.Namespace) -> dict[str, Any]:
    require_project()
    run = next((item for item in read_agent_study_manifest() if item.get("job_id") == args.job_id), None)
    if run is None:
        metadata = agent_study_import_metadata(args.job_id)
        if not metadata:
            raise ZwillError("not_found", f"No agent study found for job id {args.job_id}.")
        run = {"job_id": args.job_id, "status": "imported", "import": metadata}
    rows = [row for row in read_jsonl(agent_study_answers_path()) if row.get("job_id") == args.job_id]
    data = {"run": run, "import": agent_study_import_metadata(args.job_id), "row_count": len(rows)}
    if args.include_summary and rows:
        data["summary"] = build_agent_study_report(rows, args.job_id)["summary"]
    return envelope("zwill agent-study show", "ok", data)

