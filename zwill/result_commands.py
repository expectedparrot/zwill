from __future__ import annotations

from .cli import *  # noqa: F403
from .costs import results_cost_summary


def cmd_twin_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.input_path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.input_path}.")
    results = read_json_or_gzip(source)
    if not isinstance(results, dict) or results.get("edsl_class_name") != "Results":
        raise ZwillError("invalid_input", "Expected an EDSL Results serialization.")
    if results.get("zwill", {}).get("rank_utility_twin_job_id"):
        return cmd_rank_results_import(args)

    job_id = args.job_id or results.get("zwill", {}).get("digital_twin_job_id") or digital_twin_job_id_from_results(results)
    merge = getattr(args, "merge", False)
    jdir = digital_twin_jobs_dir(sdir) / job_id
    if jdir.exists() and not args.replace and not merge:
        raise ZwillError(
            "already_exists",
            f"Digital twin results already imported for job id {job_id}.",
            hint="Use --replace to overwrite this import, or --merge to add recovered rows (e.g. after retry-malformed).",
        )
    if jdir.exists() and not merge:
        shutil.rmtree(jdir)
    raw_dir = jdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stored_raw = raw_dir / source.name
    shutil.copy2(source, stored_raw)
    truth_path = sdir / "committed" / "truth_marginals.json"
    truth = read_json(truth_path, {}) if truth_path.exists() else {}

    weight_by_respondent = {
        str(row["respondent_id"]): float(row.get("weight", 1.0))
        for row in read_jsonl(sdir / "respondents.jsonl")
        if row.get("respondent_id") is not None
    }
    imported_at = utc_now()
    extracted, issues = extract_twin_prediction_rows(
        results,
        job_id=job_id,
        survey=args.survey,
        stored_raw=str(stored_raw),
        imported_at=imported_at,
        truth=truth,
        allow_missing_actual=getattr(args, "allow_missing_actual", False),
        weight_by_respondent=weight_by_respondent,
    )

    def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (row.get("job_id"), row.get("respondent_id"), row.get("heldout_question"), row.get("model_label"))

    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    if merge:
        # Upsert: keep every existing row except those the recovered rows replace
        # (same job/respondent/heldout/model), so re-importing a small retry batch
        # doesn't wipe the rows that scored fine the first time.
        recovered_keys = {_row_key(row) for row in extracted}
        kept = [row for row in all_rows if _row_key(row) not in recovered_keys]
        rewrite_jsonl(digital_twin_predictions_path(sdir), kept + extracted)
    else:
        existing = [row for row in all_rows if row.get("job_id") != job_id]
        rewrite_jsonl(digital_twin_predictions_path(sdir), existing + extracted)
    report_issues = issues
    if merge:
        # Carry forward prior issues, dropping any now resolved by the recovered
        # rows, so import.json reflects what is still outstanding after the retry.
        resolved = {(row.get("respondent_id"), row.get("heldout_question"), row.get("model_label")) for row in extracted}
        prior = read_json(jdir / "import.json", {}).get("issues", []) if (jdir / "import.json").exists() else []
        carried = [
            issue for issue in prior
            if (issue.get("respondent_id"), issue.get("heldout_question"), issue.get("model")) not in resolved
        ]
        report_issues = carried + issues
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
            "issue_count": len(report_issues),
            "issues": report_issues,
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
            "issue_count": len(report_issues),
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
            "issue_count": len(report_issues),
            "issues": report_issues,
            "recovered_count": len(extracted) if merge else None,
            "cost": results_cost_summary(results),
        },
        next_steps=[
            f"zwill twin-results export --survey {args.survey} --job-id {job_id} --path predictions.csv"
            if getattr(args, "allow_missing_actual", False)
            else f"zwill twin-results report --survey {args.survey} --job-id {job_id}"
        ],
    )


def _scenario_matches_pairs(scenario: dict[str, Any], pairs: set[tuple[Any, Any]]) -> bool:
    return (scenario.get("respondent_id"), scenario.get("heldout_question_name")) in pairs


def filter_retry_scenarios(job_dict: dict[str, Any], failed_pairs: set[tuple[Any, Any]]) -> tuple[dict[str, Any], int]:
    """Return a copy of an exported twin job with only the scenarios whose
    (respondent, held-out question) failed, plus the kept count. Uses the original
    job so re-run prompts are byte-for-byte identical."""
    retry = json.loads(json.dumps(job_dict))
    scenarios = retry.get("scenarios")
    if isinstance(scenarios, dict) and isinstance(scenarios.get("scenarios"), list):
        filtered = [s for s in scenarios["scenarios"] if _scenario_matches_pairs(s, failed_pairs)]
        scenarios["scenarios"] = filtered
        return retry, len(filtered)
    if isinstance(scenarios, list):
        filtered = [s for s in scenarios if _scenario_matches_pairs(s, failed_pairs)]
        retry["scenarios"] = filtered
        return retry, len(filtered)
    return retry, 0


def cmd_twin_results_retry_malformed(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    job_id = args.job_id
    jdir = digital_twin_jobs_dir(sdir) / job_id
    import_path = jdir / "import.json"
    if not import_path.exists():
        raise ZwillError(
            "not_found",
            f"No import record found for twin job id {job_id}.",
            hint=f"Import the results first: `zwill twin-results import --survey {args.survey} --input-path <results>`.",
        )
    issues = read_json(import_path, {}).get("issues", [])
    if not issues:
        return envelope(
            "zwill twin-results retry-malformed",
            "ok",
            {"job_id": job_id, "malformed_count": 0, "retry_scenario_count": 0},
            next_steps=[f"zwill twin-results report --survey {args.survey} --job-id {job_id}"],
        )
    job_path = Path(args.job)
    if not job_path.exists():
        raise ZwillError("not_found", f"Original EDSL job file does not exist: {args.job}.")
    job_dict = read_json_or_gzip(job_path)
    failed_pairs = {(issue.get("respondent_id"), issue.get("heldout_question")) for issue in issues}
    retry_dict, kept = filter_retry_scenarios(job_dict, failed_pairs)
    if kept == 0:
        raise ZwillError(
            "invalid_input",
            "No scenarios in the job matched the malformed rows.",
            context={"failed_pairs": sorted(f"{r}:{q}" for r, q in failed_pairs)[:10]},
            hint="Pass the original job file that produced these results.",
        )
    out_path = resolve_output_path(args.path) if getattr(args, "path", None) else jdir / "retry.edsl.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, retry_dict)
    results_path = jdir / "retry_results.json.gz"
    return envelope(
        "zwill twin-results retry-malformed",
        "ok",
        {
            "job_id": job_id,
            "malformed_count": len(issues),
            "retry_scenario_count": kept,
            "retry_job_path": str(out_path),
        },
        next_steps=[
            f"zwill edsl-run --job {out_path} --path {results_path}",
            f"zwill twin-results import --survey {args.survey} --job-id {job_id} --merge --input-path {results_path}",
        ],
    )


def cmd_rank_results_import(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    source = Path(args.input_path)
    if not source.exists():
        raise ZwillError("not_found", f"Results file does not exist: {args.input_path}.")
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
    weight_by_respondent = {
        str(row["respondent_id"]): float(row.get("weight", 1.0))
        for row in read_jsonl(sdir / "respondents.jsonl")
        if row.get("respondent_id") is not None
    }
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
        # scored_item_ids is the ranked subset (internal-ordering metrics); item_ids is the
        # full battery, used for top-K identification (did the twin pick the right items?).
        metrics = rank_metrics(actual_ranks, scores or {}, scored_item_ids, full_item_ids=item_ids) if scored_item_ids else {"predicted_ranks": {}}
        extracted.append(
            {
                "job_id": job_id,
                "row": index,
                "survey": args.survey,
                "respondent_id": scenario.get("respondent_id"),
                "weight": weight_by_respondent.get(str(scenario.get("respondent_id")), 1.0),
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
    warnings = []
    missing_actual_dropped = sum(1 for issue in issues if issue.get("error") == "missing_actual_ranks")
    if missing_actual_dropped and not getattr(args, "allow_missing_actual", False):
        warnings.append(
            {
                "code": "partial_rankings_dropped",
                "message": (
                    f"Dropped {missing_actual_dropped} rows because respondents ranked only some items "
                    "(top-N / partial rankings, e.g. 'pick your top 3'). Re-import with --allow-missing-actual "
                    "to score them on the items each respondent actually ranked."
                ),
            }
        )
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
        warnings=warnings or None,
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
        f"<td class=\"num\">{fmt_optional(values.get('mean_top_k_identification'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_top_k_identification_chance'))}</td>"
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
        f"<td class=\"num\">{fmt_optional(values.get('mean_top_k_identification'))}</td>"
        f"<td class=\"num\">{fmt_optional(values.get('mean_top_k_identification_chance'))}</td>"
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
    <section><h2>Individual Rank Performance</h2><table><thead><tr><th>Model</th><th class="num">Rows</th><th class="num">Mean Spearman</th><th class="num">Pairwise order accuracy</th><th class="num">Top-3 overlap</th><th class="num">Top-K identification</th><th class="num">Chance</th><th class="num">Rank MAE</th><th class="num">Top-1 hit rate</th></tr></thead><tbody>{model_rows}</tbody></table>
      <p class="subtle">Top-K identification: for a top-N battery, the share of each respondent's stated top-K items that the twin's predicted top-K (over the whole battery) also picked. Unlike Spearman/pairwise it does not presume you already know which items the respondent chose. <b>Chance</b> is K/N (picking K of N items at random) &mdash; identification above chance is real item-identification signal. Blank for full rankings.</p></section>
    <section><h2>Rank Battery Summary</h2><table><thead><tr><th>Rank task</th><th class="num">Rows</th><th class="num">Items</th><th class="num">Mean Spearman</th><th class="num">Pairwise order accuracy</th><th class="num">Top-3 overlap</th><th class="num">Top-K identification</th><th class="num">Chance</th><th class="num">Rank MAE</th></tr></thead><tbody>{task_rows}</tbody></table></section>
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
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output + "\n")
        print(output)
        return
    if args.format == "html":
        output = render_rank_report_html(payload)
        if args.path:
            resolve_output_path(args.path).parent.mkdir(parents=True, exist_ok=True)
            resolve_output_path(args.path).write_text(output)
        else:
            print(output)
        return
    if args.format == "csv":
        fieldnames = ["job_id", "respondent_id", "rank_task_id", "model_label", "spearman", "pairwise_order_accuracy", "top_3_overlap", "top_k_identification", "top_k_identification_chance", "mean_absolute_rank_error", "top_1_hit"]
        writer_target = resolve_output_path(args.path) if args.path else None
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
    for column in ["model", "rows", "spearman", "pairwise", "top-3", "top-K id", "chance", "rank mae", "top-1"]:
        table.add_column(column)
    for model, values in payload["summary"]["by_model"].items():
        table.add_row(
            model,
            str(values.get("rows", 0)),
            fmt_optional(values.get("mean_spearman")),
            fmt_optional(values.get("mean_pairwise_order_accuracy")),
            fmt_optional(values.get("mean_top_3_overlap")),
            fmt_optional(values.get("mean_top_k_identification")),
            fmt_optional(values.get("mean_top_k_identification_chance")),
            fmt_optional(values.get("mean_absolute_rank_error")),
            fmt_optional(values.get("top_1_hit_rate")),
        )
    Console().print(table)

