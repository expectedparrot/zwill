from __future__ import annotations

from .cli import *  # noqa: F403


def twin_approaches_path(sdir: Path) -> Path:
    return digital_twin_jobs_dir(sdir) / "approaches.json"


def read_twin_approaches(sdir: Path) -> list[dict[str, Any]]:
    payload = read_json(twin_approaches_path(sdir), {"approaches": []})
    return payload.get("approaches", [])


def write_twin_approaches(sdir: Path, approaches: list[dict[str, Any]]) -> None:
    approaches = sorted(approaches, key=lambda item: item.get("updated_at", item.get("created_at", "")), reverse=True)
    write_json(twin_approaches_path(sdir), {"approaches": approaches})


def update_twin_approaches(sdir: Path, updater) -> list[dict[str, Any]]:
    path = twin_approaches_path(sdir)
    with file_lock(path):
        approaches = read_twin_approaches(sdir)
        updated = updater(approaches)
        write_twin_approaches(sdir, updated)
        return updated


def twin_approach_id(value: str) -> str:
    return slugify(value).lower()


def normalize_twin_approach_record(record: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
    name = str(record.get("name") or record.get("approach") or record.get("approach_id") or "")
    if not name:
        raise ZwillError("invalid_input", "Twin approach needs a name or approach_id.", context={"source": source})
    construction = dict(record.get("construction") or {})
    for key in TWIN_APPROACH_CONSTRUCTION_KEYS:
        if key in record:
            construction[key] = record[key]
    now = utc_now()
    return {
        "approach_id": twin_approach_id(str(record.get("approach_id") or name)),
        "name": name,
        "description": str(record.get("description") or "").strip(),
        "notes": str(record.get("notes") or record.get("note") or "").strip(),
        "tags": sorted(set(normalize_tags(record.get("tags") or record.get("tag")))),
        "construction": construction,
        "source": source,
        "created_at": str(record.get("created_at") or now),
        "updated_at": now,
    }


def twin_approach_from_args(args: argparse.Namespace) -> dict[str, Any]:
    construction: dict[str, Any] = {}
    for key in sorted(TWIN_APPROACH_CONSTRUCTION_KEYS):
        if not hasattr(args, key):
            continue
        value = getattr(args, key)
        if isinstance(value, bool) and value is False:
            continue
        if value is not None and value != []:
            construction[key] = value
    name = args.name or args.approach_id
    if not name:
        raise ZwillError("invalid_input", "Pass --approach-id or --name when adding an inline twin approach.")
    return normalize_twin_approach_record(
        {
            "approach_id": args.approach_id,
            "name": name,
            "description": twin_experiment_description(args),
            "tags": normalize_tags(args.tag),
            "construction": construction,
        }
    )


def upsert_twin_approach(sdir: Path, approach: dict[str, Any]) -> None:
    def updater(approaches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered = [item for item in approaches if item.get("approach_id") != approach.get("approach_id")]
        filtered.append(approach)
        return filtered

    update_twin_approaches(sdir, updater)


def markdown_from_note_args(args: argparse.Namespace) -> str | None:
    if getattr(args, "clear", False):
        return ""
    if getattr(args, "input_path", None):
        return Path(args.input_path).read_text().strip()
    if getattr(args, "text", None) is not None:
        return str(args.text).strip()
    return None


def cmd_twin_approach_add(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    if args.input_path:
        approach = normalize_twin_approach_record(load_object_file(Path(args.input_path), kind="Twin approach"), source=str(args.input_path))
    else:
        approach = twin_approach_from_args(args)
    upsert_twin_approach(sdir, approach)
    return envelope(
        "zwill twin-approach add",
        "ok",
        {"approach": approach},
        next_steps=[f"zwill twin-approach list --survey {args.survey}"],
    )


def cmd_twin_approach_note(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    approach = next((item for item in read_twin_approaches(sdir) if item.get("approach_id") == args.approach_id), None)
    if not approach:
        raise ZwillError("not_found", f"Twin approach not found: {args.approach_id}.")
    note = markdown_from_note_args(args)
    if note is None:
        return envelope("zwill twin-approach note", "ok", {"approach_id": args.approach_id, "notes": approach.get("notes", "")})

    def updater(approaches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matched = False
        for item in approaches:
            if item.get("approach_id") != args.approach_id:
                continue
            matched = True
            item["notes"] = note
            item["updated_at"] = utc_now()
        if not matched:
            raise ZwillError("not_found", f"Twin approach not found: {args.approach_id}.")
        return approaches

    update_twin_approaches(sdir, updater)
    return envelope(
        "zwill twin-approach note",
        "ok",
        {"approach_id": args.approach_id, "notes": note},
        next_steps=[f"zwill twin-approach show --survey {args.survey} --approach-id {args.approach_id}"],
    )


def cmd_twin_approach_list(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    approaches = read_twin_approaches(sdir)
    if args.format == "json":
        print(json.dumps({"survey": args.survey, "approaches": approaches}, indent=2))
        return
    table = Table(title=f"{args.survey} twin approaches")
    for column in ["approach_id", "name", "tags", "construction", "updated_at"]:
        table.add_column(column)
    for approach in approaches:
        construction = approach.get("construction", {})
        table.add_row(
            approach.get("approach_id", ""),
            approach.get("name", ""),
            ", ".join(approach.get("tags", [])),
            ", ".join(sorted(construction))[:90],
            approach.get("updated_at", ""),
        )
    Console().print(table)


def cmd_twin_approach_show(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    approach = next((item for item in read_twin_approaches(sdir) if item.get("approach_id") == args.approach_id), None)
    if not approach:
        raise ZwillError("not_found", f"Twin approach not found: {args.approach_id}.")
    return envelope("zwill twin-approach show", "ok", {"approach": approach})


def cmd_twin_approach_scaffold(args: argparse.Namespace) -> dict[str, Any]:
    require_survey(args.survey)
    construction: dict[str, Any] = {
        "context_question_count": args.context_question_count,
        "complete_cases": True,
        "model": list_or_none(args.model) or ["openai:gpt-5.5"],
    }
    if args.context_questions:
        construction["context_questions"] = args.context_questions
    if args.include_agent_material:
        construction["include_agent_material"] = True
    if args.twin_material:
        construction["twin_material"] = list_or_none(args.twin_material)
    approach = normalize_twin_approach_record(
        {
            "approach_id": args.approach_id,
            "name": args.name or args.approach_id.replace("_", " ").replace("-", " ").title(),
            "description": args.description or "Describe what information this construction approach gives each digital twin.",
            "tags": normalize_tags(args.tag),
            "construction": construction,
        }
    )
    path = Path(args.path or f"{approach['approach_id']}_approach.json")
    write_json(path, approach)
    return envelope(
        "zwill twin-approach scaffold",
        "ok",
        {"path": str(path), "approach": approach},
        next_steps=[f"zwill twin-approach add --survey {args.survey} --input-path {path}"],
    )


def find_twin_approach_record(sdir: Path, identifier: str) -> dict[str, Any] | None:
    normalized = twin_approach_id(identifier)
    for approach in read_twin_approaches(sdir):
        if approach.get("approach_id") == normalized or approach.get("name") == identifier:
            return approach
    for experiment in read_twin_experiments(sdir):
        if identifier in {
            str(experiment.get("approach_id")),
            str(experiment.get("experiment_id")),
            str(experiment.get("job_id")),
            str(experiment.get("approach")),
        }:
            return normalize_twin_approach_record(
                {
                    "approach_id": experiment.get("approach_id") or experiment.get("experiment_id") or experiment.get("job_id"),
                    "name": experiment.get("approach") or experiment.get("approach_id") or experiment.get("experiment_id"),
                    "description": experiment.get("description", ""),
                    "tags": experiment.get("tags", []),
                    "construction": experiment.get("plan", {}).get("construction", {}),
                    "created_at": experiment.get("created_at"),
                    "updated_at": experiment.get("created_at"),
                }
            )
    return None


def diff_values(left: Any, right: Any) -> str:
    if left == right:
        return "same"
    if left in (None, "", [], {}):
        return "added"
    if right in (None, "", [], {}):
        return "removed"
    return "changed"


def twin_approach_diff_payload(sdir: Path, left_id: str, right_id: str) -> dict[str, Any]:
    left = find_twin_approach_record(sdir, left_id)
    right = find_twin_approach_record(sdir, right_id)
    if not left:
        raise ZwillError("not_found", f"Twin approach not found: {left_id}.")
    if not right:
        raise ZwillError("not_found", f"Twin approach not found: {right_id}.")
    construction_fields = set(left.get("construction", {}).keys()) | set(right.get("construction", {}).keys())
    metadata_fields = (set(left.keys()) | set(right.keys())) - {"construction"}
    rows = []
    for field in sorted(metadata_fields):
        left_value = left.get(field)
        right_value = right.get(field)
        rows.append(
            {
                "field": field,
                "location": "metadata",
                "status": diff_values(left_value, right_value),
                "left": left_value,
                "right": right_value,
            }
        )
    for field in sorted(construction_fields):
        left_value = left.get("construction", {}).get(field)
        right_value = right.get("construction", {}).get(field)
        rows.append(
            {
                "field": field,
                "location": "construction",
                "status": diff_values(left_value, right_value),
                "left": left_value,
                "right": right_value,
            }
        )
    rows.sort(key=lambda row: (row["status"] == "same", row["location"], row["field"]))
    return {
        "survey": sdir.name,
        "left": {"id": left_id, "approach": left},
        "right": {"id": right_id, "approach": right},
        "differences": rows,
        "changed_count": sum(1 for row in rows if row["status"] != "same"),
    }


def render_twin_approach_diff_html(payload: dict[str, Any]) -> str:
    def pretty(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, sort_keys=True)
        return "" if value is None else str(value)

    rows = []
    for row in payload["differences"]:
        cls = row["status"]
        rows.append(
            "<tr>"
            f"<td><code>{html_escape(row['field'])}</code><div class=\"muted\">{html_escape(row['location'])}</div></td>"
            f"<td><span class=\"pill {cls}\">{html_escape(cls)}</span></td>"
            f"<td><pre>{html_escape(pretty(row['left']))}</pre></td>"
            f"<td><pre>{html_escape(pretty(row['right']))}</pre></td>"
            "</tr>"
        )
    left_name = payload["left"]["approach"].get("name")
    right_name = payload["right"]["approach"].get("name")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Twin approach diff</title>
  <style>
    body {{ font: 15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin: 32px; color:#17202a; background:#f7f8fa; }}
    main {{ max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin: 0 0 6px; font-size: 34px; }}
    .muted {{ color:#64748b; }}
    .card {{ background:#fff; border:1px solid #d8dee6; border-radius:8px; padding:20px; margin:18px 0; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; }}
    th,td {{ border:1px solid #dfe3e6; padding:10px; text-align:left; vertical-align:top; }}
    th {{ background:#f0f3f4; }}
    pre {{ white-space:pre-wrap; margin:0; font:12px/1.4 SFMono-Regular,Consolas,Menlo,monospace; max-height:220px; overflow:auto; }}
    .pill {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#eef2f6; font-size:12px; }}
    .changed,.added,.removed {{ background:#fff4cc; color:#6f4e00; }}
    .same {{ background:#edf2f7; color:#4a5568; }}
  </style>
</head>
<body>
{copy_markdown_control()}
<main>
  <h1>Twin approach diff</h1>
  <div class="muted">{html_escape(payload['survey'])}: {html_escape(left_name)} vs {html_escape(right_name)}</div>
  <section class="card"><b>{payload['changed_count']}</b> changed fields. Metadata and construction settings are compared separately.</section>
  <table>
    <thead><tr><th>Field</th><th>Status</th><th>{html_escape(left_name)}</th><th>{html_escape(right_name)}</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</main>
</body>
</html>
"""


def cmd_twin_approach_diff(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    payload = twin_approach_diff_payload(sdir, args.left, args.right)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_twin_approach_diff_html(payload)
        path = Path(args.path or f"{args.left}_vs_{args.right}_approach_diff.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
        print(str(path))
        return
    table = Table(title=f"{args.survey} twin approach diff")
    for column in ["field", "location", "status", args.left, args.right]:
        table.add_column(column)
    for row in payload["differences"]:
        if row["status"] == "same" and not args.show_same:
            continue
        table.add_row(
            str(row["field"]),
            str(row["location"]),
            str(row["status"]),
            json.dumps(row["left"], sort_keys=True) if isinstance(row["left"], (dict, list)) else str(row["left"]),
            json.dumps(row["right"], sort_keys=True) if isinstance(row["right"], (dict, list)) else str(row["right"]),
        )
    Console().print(table)

