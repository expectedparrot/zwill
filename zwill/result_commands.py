from __future__ import annotations

from .cli import *  # noqa: F403


def cmd_twin_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    if results.get("zwill", {}).get("rank_utility_twin_job_id"):
        return cmd_rank_results_import(args)

    job_id = args.job_id or results.get("zwill", {}).get("digital_twin_job_id") or digital_twin_job_id_from_results(results)
    jdir = digital_twin_jobs_dir(sdir) / job_id
    if jdir.exists() and not args.replace:
        raise ZwillError(
            "already_exists",
            f"Digital twin results already imported for job id {job_id}.",
            hint="Use --replace to overwrite this import.",
        )
    if jdir.exists():
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)
    truth_path = sdir / "committed" / "truth_marginals.json"
    truth = read_json(truth_path, {}) if truth_path.exists() else {}

    existing = [row for row in read_jsonl(digital_twin_predictions_path(sdir)) if row.get("job_id") != job_id]
    imported_at = utc_now()
    extracted, issues = extract_twin_prediction_rows(
        results,
        job_id=job_id,
        survey=args.survey,
        stored_raw=str(stored_raw),
        imported_at=imported_at,
        truth=truth,
        allow_missing_actual=getattr(args, "allow_missing_actual", False),
    )

    rewrite_jsonl(digital_twin_predictions_path(sdir), existing + extracted)
    write_json(
        jdir / "import.json",
        {
            "job_id": job_id,
            "survey": args.survey,
            "source_path": str(source),
            "source_hash": sha256(source),
            "stored_path": str(stored_raw),
            "stored_hash": sha256(stored_raw),
            "row_count": len(results.get("data", [])),
            "extracted_count": len(extracted),
            "issue_count": len(issues),
            "issues": issues,
            "imported_at": imported_at,
        },
    )
    upsert_twin_run_manifest(
        sdir,
        {
            "job_id": job_id,
            "survey": args.survey,
            "status": "imported",
            "created_at": imported_at,
            "results_path": str(source),
            "stored_raw": str(stored_raw),
            "row_count": len(results.get("data", [])),
            "extracted_count": len(extracted),
            "issue_count": len(issues),
            "models": sorted({row.get("model_label") or model_label(row.get("service"), row.get("model")) for row in extracted}),
            "heldout_questions": sorted({row.get("heldout_question") for row in extracted if row.get("heldout_question")}),
        },
    )
    return envelope(
        "zwill twin-results import",
        "ok",
        {
            "job_id": job_id,
            "stored_raw": str(stored_raw),
            "row_count": len(results.get("data", [])),
            "extracted_count": len(extracted),
            "issue_count": len(issues),
            "issues": issues,
        },
        next_steps=[
            f"zwill twin-results export --survey {args.survey} --job-id {job_id} --path predictions.csv"
            if getattr(args, "allow_missing_actual", False)
            else f"zwill twin-results report --survey {args.survey} --job-id {job_id}"
        ],
    )


def cmd_rank_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    job_id = args.job_id or results.get("zwill", {}).get("rank_utility_twin_job_id") or rank_job_id_from_results(results)
    jdir = rank_twin_jobs_dir(sdir) / job_id
    if jdir.exists() and not args.replace:
        raise ZwillError("already_exists", f"Rank utility results already imported for job id {job_id}.", hint="Use --replace.")
    if jdir.exists():
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)
    imported_at = utc_now()
    extracted = []
    issues = []
    question_name = getattr(args, "job_question_name", None) or "rank_utility_scores"
    for index, row in enumerate(results.get("data", [])):
        scenario = row.get("scenario", {}) or {}
        model = row.get("model", {}) or {}
        scores, confidence, notes, error = extract_rank_payload(row, question_name=question_name)
        item_ids = [str(item.get("item_id")) for item in scenario.get("rank_items", []) if item.get("item_id")]
        actual_ranks = {str(key): int(value) for key, value in (scenario.get("actual_ranks") or {}).items() if value is not None}
        missing_scores = [item_id for item_id in item_ids if scores is not None and item_id not in scores]
        missing_actual = [item_id for item_id in item_ids if item_id not in actual_ranks]
        if missing_scores:
            error = error or "missing_item_scores"
        if missing_actual and not getattr(args, "allow_missing_actual", False):
            error = error or "missing_actual_ranks"
        if error:
            issues.append(
                {
                    "row": index,
                    "respondent_id": scenario.get("respondent_id"),
                    "rank_task_id": scenario.get("rank_task_id"),
                    "model": model_label(model.get("inference_service"), model.get("model")),
                    "error": error,
                    "missing_scores": missing_scores,
                    "missing_actual": missing_actual,
                }
            )
            continue
        scored_item_ids = [item_id for item_id in item_ids if scores and item_id in scores and item_id in actual_ranks]
        metrics = rank_metrics(actual_ranks, scores or {}, scored_item_ids) if scored_item_ids else {"predicted_ranks": {}}
        extracted.append(
            {
                "job_id": job_id,
                "row": index,
                "survey": args.survey,
                "respondent_id": scenario.get("respondent_id"),
                "rank_task_id": scenario.get("rank_task_id"),
                "rank_task_text": scenario.get("rank_task_text"),
                "rank_direction": scenario.get("rank_direction"),
                "items": scenario.get("rank_items", []),
                "item_count": len(item_ids),
                "actual_ranks": actual_ranks,
                "predicted_scores": scores or {},
                "predicted_ranks": metrics.get("predicted_ranks", {}),
                "model": model.get("model"),
                "service": model.get("inference_service"),
                "model_label": model_label(model.get("inference_service"), model.get("model")),
                "model_parameters": model.get("parameters", {}),
                "observed_answers": scenario.get("observed_answers", []),
                "twin_material": scenario.get("twin_material", []),
                "notes": notes,
                "confidence": confidence,
                "source_raw": str(stored_raw),
                "imported_at": imported_at,
                **{key: value for key, value in metrics.items() if key != "predicted_ranks"},
            }
        )
    existing = [row for row in read_jsonl(rank_twin_predictions_path(sdir)) if row.get("job_id") != job_id]
    rewrite_jsonl(rank_twin_predictions_path(sdir), existing + extracted)
    metadata = {
        "job_id": job_id,
        "survey": args.survey,
        "source_path": str(source),
        "source_hash": sha256(source),
        "stored_path": str(stored_raw),
        "stored_hash": sha256(stored_raw),
        "row_count": len(results.get("data", [])),
        "extracted_count": len(extracted),
        "issue_count": len(issues),
        "issues": issues,
        "rank_task_ids": sorted({row.get("rank_task_id") for row in extracted if row.get("rank_task_id")}),
        "models": sorted({row.get("model_label") for row in extracted if row.get("model_label")}),
        "imported_at": imported_at,
    }
    write_json(jdir / "import.json", metadata)
    return envelope(
        "zwill twin-results import",
        "ok",
        {
            "job_id": job_id,
            "stored_raw": str(stored_raw),
            "row_count": metadata["row_count"],
            "extracted_count": metadata["extracted_count"],
            "issue_count": metadata["issue_count"],
            "issues": issues,
        },
        next_steps=[f"zwill twin-results rank-report --survey {args.survey} --job-id {job_id}"],
    )


def render_rank_report_html(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    model_rows = "".join(
        "<tr>"
        f"<td>{html_escape(model)}</td>"
        f"<td class=\"num\">{values.get('rows', 0)}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_spearman'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_pairwise_order_accuracy'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_top_3_overlap'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_absolute_rank_error'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('top_1_hit_rate'))}</td>"
        "</tr>"
        for model, values in (summary.get("by_model") or {}).items()
    )
    task_rows = "".join(
        "<tr>"
        f"<td>{html_escape(task)}</td>"
        f"<td class=\"num\">{values.get('rows', 0)}</td>"
        f"<td class=\"num\">{values.get('item_count') or ''}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_spearman'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_pairwise_order_accuracy'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_top_3_overlap'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_absolute_rank_error'))}</td>"
        "</tr>"
        for task, values in (summary.get("by_task") or {}).items()
    )
    item_rows = "".join(
        "<tr>"
        f"<td>{html_escape(row.get('rank_task_id'))}</td>"
        f"<td><b>{html_escape(row.get('item_id'))}</b><span>{html_escape(row.get('label'))}</span></td>"
        f"<td class=\"num\">{fmt_optional(row.get('mean_actual_rank'))}</td>"
        f"<td class=\"num\">{fmt_optional(row.get('mean_predicted_rank'))}</td>"
        f"<td class=\"num\">{fmt_optional(row.get('mean_predicted_score'))}</td>"
        f"<td class=\"num\">{fmt_optional(row.get('mean_rank_error'))}</td>"
        "</tr>"
        for row in payload.get("items", [])
    )
    data = escape_script_text(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rank Utility Twin Validation</title>
  <style>
    {EP_REPORT_CSS}
    body {{ max-width:1180px; }}
    section {{ border:1px solid var(--ep-border); border-radius:8px; padding:18px; margin-bottom:18px; background:#fff; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ border-bottom:1px solid #edf0f4; padding:8px; text-align:left; vertical-align:top; }}
    th {{ background:var(--ep-green); color:#fff; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
    td span {{ display:block; color:var(--ep-gray); font-size:12px; margin-top:3px; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <main>
    <h1>Rank Utility Twin Validation</h1>
    <p class="subtle">Joint rank-battery validation using latent utility scores, not independent categorical rank labels.</p>
    <section><h2>Summary</h2><table><tbody>
      <tr><th>Rows</th><td class="num">{summary.get('row_count', 0)}</td><th>Respondents</th><td class="num">{summary.get('respondent_count', 0)}</td></tr>
      <tr><th>Rank tasks</th><td class="num">{summary.get('task_count', 0)}</td><th>Models</th><td class="num">{summary.get('model_count', 0)}</td></tr>
    </tbody></table></section>
    <section><h2>Individual Rank Performance</h2><table><thead><tr><th>Model</th><th class="num">Rows</th><th class="num">Mean Spearman</th><th class="num">Pairwise order accuracy</th><th class="num">Top-3 overlap</th><th class="num">Rank MAE</th><th class="num">Top-1 hit rate</th></tr></thead><tbody>{model_rows}</tbody></table></section>
    <section><h2>Rank Battery Summary</h2><table><thead><tr><th>Rank task</th><th class="num">Rows</th><th class="num">Items</th><th class="num">Mean Spearman</th><th class="num">Pairwise order accuracy</th><th class="num">Top-3 overlap</th><th class="num">Rank MAE</th></tr></thead><tbody>{task_rows}</tbody></table></section>
    <section><h2>Item-Level Diagnostics</h2><table><thead><tr><th>Task</th><th>Item</th><th class="num">Actual avg rank</th><th class="num">Predicted avg rank</th><th class="num">Predicted avg score</th><th class="num">Rank error</th></tr></thead><tbody>{item_rows}</tbody></table></section>
  </main>
  <script type="application/json" id="rank-report-data">{data}</script>
</body>
</html>"""


def fmt_optional(value: Any, precision: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return str(value)


def cmd_rank_results_report(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    rows = read_jsonl(rank_twin_predictions_path(sdir))
    if args.job_id:
        rows = [row for row in rows if row.get("job_id") == args.job_id]
    if args.model:
        rows = [row for row in rows if row.get("model") == args.model or row.get("model_label") == args.model]
    if args.rank_task_id:
        selected = set(args.rank_task_id)
        rows = [row for row in rows if row.get("rank_task_id") in selected]
    if not rows:
        raise ZwillError("not_found", "No rank utility predictions found for the requested filters.")
    payload = build_rank_report(rows, args.job_id)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_rank_report_html(payload)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output)
        else:
            print(output)
        return
    if args.format == "csv":
        fieldnames = ["job_id", "respondent_id", "rank_task_id", "model_label", "spearman", "pairwise_order_accuracy", "top_3_overlap", "mean_absolute_rank_error", "top_1_hit"]
        writer_target = Path(args.path) if args.path else None
        if writer_target:
            writer_target.parent.mkdir(parents=True, exist_ok=True)
            with writer_target.open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows({key: row.get(key) for key in fieldnames} for row in rows)
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows({key: row.get(key) for key in fieldnames} for row in rows)
        return
    table = Table(title=f"{args.survey} rank utility validation")
    for column in ["model", "rows", "spearman", "pairwise", "top-3", "rank mae", "top-1"]:
        table.add_column(column)
    for model, values in payload["summary"]["by_model"].items():
        table.add_row(
            model,
            str(values.get("rows", 0)),
            fmt_optional(values.get("mean_spearman")),
            fmt_optional(values.get("mean_pairwise_order_accuracy")),
            fmt_optional(values.get("mean_top_3_overlap")),
            fmt_optional(values.get("mean_absolute_rank_error")),
            fmt_optional(values.get("top_1_hit_rate")),
        )
    Console().print(table)

