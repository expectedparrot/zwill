from __future__ import annotations

from .cli import *  # noqa: F403


def cmd_raw_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists() or not source.is_file():
        raise ZwillError("invalid_input", f"Raw path is not a file: {args.path}.")
    stored_dir = sdir / "raw" / args.id
    stored_dir.mkdir(parents=True, exist_ok=True)
    stored = stored_dir / source.name
    shutil.copy2(source, stored)
    raw_file = {
        "id": args.id,
        "kind": args.kind,
        "title": args.title,
        "source_path": args.path,
        "source_hash": sha256(source),
        "stored_path": str(stored),
        "stored_hash": sha256(stored),
        "added_at": utc_now(),
    }
    raw_files = [r for r in read_json(sdir / "raw_files.json", []) if r["id"] != args.id]
    raw_files.append(raw_file)
    write_json(sdir / "raw_files.json", raw_files)
    return envelope("zwill raw add", "ok", {"raw_file": raw_file})


def cmd_raw_list(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    raw_files = [
        {k: r[k] for k in ["id", "kind", "title", "stored_path", "stored_hash"] if k in r}
        for r in read_json(sdir / "raw_files.json", [])
    ]
    return envelope("zwill raw list", "ok", {"raw_files": raw_files, "raw_file_count": len(raw_files)})


def markdown_from_args(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.path:
        path = Path(args.path)
        if not path.exists() or not path.is_file():
            raise ZwillError("invalid_input", f"Context path is not a file: {args.path}.")
        return path.read_text(), args.path
    return args.text, None


def cmd_context_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    markdown, source_path = markdown_from_args(args)
    path = context_path(sdir)
    existing = path.read_text() if path.exists() else ""
    if existing.strip():
        content = existing.rstrip() + "\n\n" + markdown.strip() + "\n"
    else:
        content = markdown.strip() + "\n"
    path.write_text(content)
    data = {
        "survey": args.survey,
        "path": str(path),
        "source_path": source_path,
        "chars": len(markdown),
        "total_chars": len(content),
        "updated_at": utc_now(),
    }
    return envelope("zwill context add", "ok", {"context": data})


def cmd_context_set(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    markdown, source_path = markdown_from_args(args)
    path = context_path(sdir)
    content = markdown.strip() + "\n"
    path.write_text(content)
    data = {
        "survey": args.survey,
        "path": str(path),
        "source_path": source_path,
        "chars": len(markdown),
        "total_chars": len(content),
        "updated_at": utc_now(),
    }
    return envelope("zwill context set", "ok", {"context": data})


def cmd_context_show(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    path = context_path(sdir)
    markdown = path.read_text() if path.exists() else ""
    return envelope(
        "zwill context show",
        "ok",
        {
            "context": {
                "survey": args.survey,
                "path": str(path),
                "markdown": markdown,
                "chars": len(markdown),
            }
        },
    )


def cmd_question_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    if not args.question_name:
        raise ZwillError("invalid_input", "question_name is required.", hint="Pass --question-name.")
    question = {
        "question_name": args.question_name,
        "question_type": args.question_type,
        "question_text": args.question_text,
        "question_options": args.question_option or [],
        "role": args.role,
        "registered_at": utc_now(),
    }
    option_labels = parse_option_labels(args.option_label)
    if option_labels:
        question["option_labels"] = option_labels
    if args.source_raw or args.source_note:
        question["source"] = {"raw_id": args.source_raw, "note": args.source_note}
    rows = [q for q in read_jsonl(sdir / "questions.jsonl") if q["question_name"] != args.question_name]
    rows.append(question)
    annotated, _rank_tasks = annotate_rank_items(rows)
    rewrite_jsonl(sdir / "questions.jsonl", annotated)
    return envelope("zwill question add", "ok", {"question": question}, next_steps=[f"zwill answer import --survey {args.survey} --path answers.jsonl"])


def cmd_question_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    existing = questions_by_name(sdir)
    imported = []
    for row in read_jsonl(Path(args.path)):
        existing[row["question_name"]] = row
        imported.append(row["question_name"])
    annotated, rank_tasks = annotate_rank_items(list(existing.values()))
    rewrite_jsonl(sdir / "questions.jsonl", annotated)
    return envelope(
        "zwill question import",
        "ok",
        {
            "imported_count": len(imported),
            "skipped_count": 0,
            "question_names": imported,
            "rank_task_count": len(rank_tasks),
            "rank_tasks": rank_tasks,
        },
    )


def cmd_respondent_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    respondent = {
        "respondent_id": args.respondent_id,
        "weight": args.weight,
        "metadata": parse_metadata(args.metadata),
    }
    if args.source_raw or args.source_note:
        respondent["source"] = {"raw_id": args.source_raw, "note": args.source_note}
    rows = [r for r in read_jsonl(sdir / "respondents.jsonl") if r["respondent_id"] != args.respondent_id]
    rows.append(respondent)
    rewrite_jsonl(sdir / "respondents.jsonl", rows)
    return envelope("zwill respondent add", "ok", {"respondent": respondent})


def cmd_respondent_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    existing = respondents_by_id(sdir)
    imported = 0
    for row in read_jsonl(Path(args.path)):
        existing[row["respondent_id"]] = row
        imported += 1
    rewrite_jsonl(sdir / "respondents.jsonl", list(existing.values()))
    return envelope("zwill respondent import", "ok", {"imported_count": imported, "respondent_count": len(existing)})


def material_markdown_from_args(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.path:
        path = Path(args.path)
        if not path.exists() or not path.is_file():
            raise ZwillError("invalid_input", f"Agent material path is not a file: {args.path}.")
        return path.read_text().strip(), args.path
    return args.text.strip(), None


def normalize_tags(values: list[str] | str | None) -> list[str]:
    if values is None:
        return []
    raw_values = values if isinstance(values, list) else [values]
    tags: list[str] = []
    for value in raw_values:
        tags.extend(tag.strip() for tag in value.split(",") if tag.strip())
    return tags


def next_agent_material_id(sdir: Path, respondent_id: str, kind: str) -> str:
    existing = agent_material_rows(sdir)
    prefix = f"{kind}_{respondent_id}_"
    count = sum(1 for row in existing if str(row.get("material_id", "")).startswith(prefix))
    return f"{prefix}{count + 1:03d}"


def validate_agent_material_row(sdir: Path, row: dict[str, Any], line: int | None = None) -> dict[str, Any] | None:
    respondent_id = row.get("respondent_id")
    if not respondent_id:
        return {"code": "invalid_input", "line": line, "message": "respondent_id is required"}
    if respondent_id not in respondents_by_id(sdir):
        return {"code": "unknown_respondent", "line": line, "respondent_id": respondent_id}
    if not row.get("kind"):
        return {"code": "invalid_input", "line": line, "respondent_id": respondent_id, "message": "kind is required"}
    if not row.get("body_markdown"):
        return {"code": "invalid_input", "line": line, "respondent_id": respondent_id, "message": "body_markdown is required"}
    return None


def cmd_agent_material_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    markdown, source_path = material_markdown_from_args(args)
    material = {
        "material_id": args.material_id or next_agent_material_id(sdir, args.respondent_id, args.kind),
        "respondent_id": args.respondent_id,
        "kind": args.kind,
        "title": args.title,
        "body_markdown": markdown,
        "tags": normalize_tags(args.tag),
        "include_by_default": args.include_by_default,
        "source": {
            "path": source_path,
            "raw_id": args.source_raw,
            "note": args.source_note,
        },
        "created_at": utc_now(),
    }
    material["source"] = {key: value for key, value in material["source"].items() if value}
    issue = validate_agent_material_row(sdir, material)
    if issue:
        raise ZwillError(issue["code"], "Agent material failed validation.", context=issue)
    rows = [row for row in agent_material_rows(sdir) if row.get("material_id") != material["material_id"]]
    rows.append(material)
    rewrite_jsonl(agent_material_path(sdir), rows)
    return envelope("zwill agent-material add", "ok", {"material": material})


def cmd_agent_material_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    existing = {row["material_id"]: row for row in agent_material_rows(sdir) if row.get("material_id")}
    imported = 0
    quarantined = 0
    examples = []
    for line, row in enumerate(read_jsonl(Path(args.path)), 1):
        material = dict(row)
        material.setdefault("material_id", next_agent_material_id(sdir, material.get("respondent_id", "unknown"), material.get("kind", "material")))
        material.setdefault("title", material.get("kind", "Agent material"))
        material.setdefault("tags", [])
        material["tags"] = normalize_tags(material.get("tags"))
        material.setdefault("include_by_default", False)
        material.setdefault("created_at", utc_now())
        issue = validate_agent_material_row(sdir, material, line)
        if issue:
            full_issue = add_quarantine_issue(sdir, {"type": "agent_material", **issue})
            quarantined += 1
            if len(examples) < 3:
                examples.append(full_issue)
            continue
        existing[material["material_id"]] = material
        imported += 1
    rewrite_jsonl(agent_material_path(sdir), list(existing.values()))
    data = {
        "imported_count": imported,
        "material_count": len(existing),
        "quarantined_count": quarantined,
    }
    if examples:
        data["quarantine_examples"] = examples
    warnings = [{"code": "partial_import", "message": f"{quarantined} agent material row was quarantined."}] if quarantined else []
    return envelope("zwill agent-material import", "ok", data, warnings=warnings)


def cmd_agent_material_list(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    rows = select_agent_material(sdir, [args.respondent_id] if args.respondent_id else None, args)
    compact = [
        {
            "material_id": row.get("material_id"),
            "respondent_id": row.get("respondent_id"),
            "kind": row.get("kind"),
            "title": row.get("title"),
            "tags": row.get("tags", []),
            "chars": len(row.get("body_markdown", "")),
        }
        for row in rows
    ]
    return envelope("zwill agent-material list", "ok", {"materials": compact, "material_count": len(compact)})


def cmd_agent_material_show(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    for row in agent_material_rows(sdir):
        if row.get("material_id") == args.material_id:
            return envelope("zwill agent-material show", "ok", {"material": row})
    raise ZwillError("not_found", f"Agent material does not exist: {args.material_id}.")


def cmd_answer_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    answer = {"respondent_id": args.respondent_id, "question": args.question}
    if args.missing_code is not None:
        answer["answer"] = None
        answer["missing_code"] = args.missing_code
    else:
        answer["answer"] = args.answer
    issue = validate_answer(sdir, answer)
    if issue:
        add_quarantine_issue(sdir, issue)
        raise ZwillError(issue["code"], "Answer failed validation.", context=issue, next_steps=[f"zwill quarantine list --survey {args.survey}"])
    ensure_implicit_respondent(sdir, args.respondent_id)
    answer["recorded_at"] = utc_now()
    append_jsonl(sdir / "answers.jsonl", answer)
    return envelope("zwill answer add", "ok", {"answer": answer})


def cmd_answer_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    questions = questions_by_name(sdir)
    respondent_ids = set(respondents_by_id(sdir))
    imported = 0
    quarantined = 0
    quarantine_examples = []
    valid_rows = []
    implicit_respondents = []
    for line, row in enumerate(read_jsonl(Path(args.path)), 1):
        question_name = row.get("question")
        issue = None
        if question_name not in questions:
            issue = {"code": "unknown_question", "line": line, "question": question_name}
        elif "answer" in row and row.get("answer") is not None:
            valid_options = questions[question_name].get("question_options", [])
            if valid_options and row["answer"] not in valid_options:
                issue = {
                    "code": "invalid_answer_option",
                    "line": line,
                    "question": question_name,
                    "answer": row["answer"],
                    "valid_options": valid_options,
                }
        elif not row.get("missing_code"):
            issue = {
                "code": "invalid_input",
                "line": line,
                "question": question_name,
                "message": "answer or missing_code is required",
            }
        if issue:
            full_issue = add_quarantine_issue(sdir, issue)
            quarantined += 1
            if len(quarantine_examples) < 3:
                quarantine_examples.append(full_issue)
            continue
        respondent_id = row["respondent_id"]
        if respondent_id not in respondent_ids:
            respondent_ids.add(respondent_id)
            implicit_respondents.append({"respondent_id": respondent_id, "weight": 1.0, "metadata": {}})
        valid_rows.append(row)
        imported += 1
    if implicit_respondents:
        with (sdir / "respondents.jsonl").open("a") as f:
            for respondent in implicit_respondents:
                f.write(json.dumps(respondent, separators=(",", ":")) + "\n")
    if valid_rows:
        with (sdir / "answers.jsonl").open("a") as f:
            for row in valid_rows:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
    warnings = []
    if quarantined:
        warnings.append({"code": "partial_import", "message": f"{quarantined} answer row was quarantined."})
    data = {
        "imported_count": imported,
        "answer_count": len(read_jsonl(sdir / "answers.jsonl")),
        "respondent_count": len(read_jsonl(sdir / "respondents.jsonl")),
        "question_count": len(read_jsonl(sdir / "questions.jsonl")),
        "quarantined_count": quarantined,
    }
    if quarantine_examples:
        data["quarantine_examples"] = quarantine_examples
    return envelope("zwill answer import", "ok", data, warnings=warnings, next_steps=[f"zwill commit --survey {args.survey}"] if not quarantine_examples else [f"zwill quarantine list --survey {args.survey}"])


def cmd_status(_: argparse.Namespace) -> dict[str, Any]:
    project_id = active_project_id()
    surveys = read_json(project_surveys_path(), [])
    summaries = [survey_summary(s["name"]) for s in surveys]
    return envelope(
        "zwill status",
        "ok",
        {"project": project_id, "surveys": summaries},
        next_steps=["zwill commit --survey <survey>"] if summaries else [],
    )


def compute_marginals(sdir: Path) -> dict[str, Any]:
    questions = read_jsonl(sdir / "questions.jsonl")
    respondents = respondents_by_id(sdir)
    answers = read_jsonl(sdir / "answers.jsonl")
    marginals: dict[str, dict[str, dict[str, float | int]]] = {}
    counts: dict[str, Counter] = defaultdict(Counter)
    weighted_counts: dict[str, Counter] = defaultdict(Counter)
    for answer in answers:
        value = answer.get("answer")
        key = "__missing__" if value is None else value
        weight = float(respondents.get(answer["respondent_id"], {}).get("weight", 1.0))
        counts[answer["question"]][key] += 1
        weighted_counts[answer["question"]][key] += weight
    for question in questions:
        qname = question["question_name"]
        keys = list(question.get("question_options", []))
        if counts[qname].get("__missing__"):
            keys.append("__missing__")
        marginals[qname] = {}
        for key in keys:
            marginals[qname][key] = {
                "count": counts[qname].get(key, 0),
                "weighted_count": round(weighted_counts[qname].get(key, 0.0), 10),
            }
    return marginals


def cmd_commit(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    issues = open_quarantine_issues(sdir)
    if issues:
        raise ZwillError(
            "gate_blocked",
            "Survey has open quarantine issues.",
            context={"open_quarantine_issues": len(issues)},
            hint=f"Run `zwill quarantine list --survey {args.survey}`.",
            next_steps=[f"zwill quarantine list --survey {args.survey}"],
        )
    committed = sdir / "committed"
    committed.mkdir(exist_ok=True)
    respondents = read_jsonl(sdir / "respondents.jsonl")
    questions = read_jsonl(sdir / "questions.jsonl")
    answers = read_jsonl(sdir / "answers.jsonl")
    write_json(committed / "respondents.json", respondents)
    write_json(committed / "truth_marginals.json", {"survey": args.survey, "marginals": compute_marginals(sdir)})
    committed_paths = {
        "respondents": str(committed / "respondents.json"),
        "truth_marginals": str(committed / "truth_marginals.json"),
    }
    if context_path(sdir).exists():
        shutil.copy2(context_path(sdir), committed / "context.md")
        committed_paths["context"] = str(committed / "context.md")
    return envelope(
        "zwill commit",
        "ok",
        {
            "survey": args.survey,
            "respondent_count": len(respondents),
            "question_count": len(questions),
            "answer_count": len(answers),
            "truth_marginal_count": len(questions),
            "committed_paths": committed_paths,
        },
    )


def cmd_quarantine_list(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    issues = read_jsonl(sdir / "quarantine.jsonl")
    return envelope("zwill quarantine list", "ok", {"issues": issues, "issue_count": len(issues)})


def cmd_quarantine_resolve(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    rows = read_jsonl(sdir / "quarantine.jsonl")
    for row in rows:
        if row["issue_id"] == args.issue_id:
            row["status"] = "resolved"
            row["resolution"] = {"action": args.action, "note": args.note}
            rewrite_jsonl(sdir / "quarantine.jsonl", rows)
            return envelope("zwill quarantine resolve", "ok", {"issue": row}, next_steps=[f"zwill commit --survey {args.survey}"])
    raise ZwillError("not_found", f"Quarantine issue does not exist: {args.issue_id}.")


def cmd_table(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    questions = read_jsonl(sdir / "questions.jsonl")
    answers = read_jsonl(sdir / "answers.jsonl")
    question_names = [q["question_name"] for q in questions]
    respondent_ids = sorted({a["respondent_id"] for a in answers})
    if args.limit is not None:
        respondent_ids = respondent_ids[: args.limit]
    values = {rid: {q: "" for q in question_names} for rid in respondent_ids}
    for answer in answers:
        if answer["respondent_id"] not in values:
            continue
        value = answer.get("answer")
        if value is None:
            value = f"missing:{answer.get('missing_code', 'unknown')}"
        values[answer["respondent_id"]][answer["question"]] = str(value)

    table = Table(title=f"{args.survey} answers")
    table.add_column("respondent_id", style="bold")
    for question_name in question_names:
        table.add_column(question_name)
    for respondent_id in respondent_ids:
        table.add_row(respondent_id, *(values[respondent_id][name] for name in question_names))
    Console().print(table)

