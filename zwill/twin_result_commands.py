from __future__ import annotations

from .cli import *  # noqa: F403


def selected_questions_arg(args: argparse.Namespace) -> set[str]:
    questions = set()
    for value in getattr(args, "question", None) or []:
        questions.add(str(value))
    if getattr(args, "questions", None):
        questions.update(item.strip() for item in str(args.questions).split(",") if item.strip())
    return questions


def default_calibrated_twin_job_id(source_job_id: str, target_kind: str, target_id: str | None, model: str | None) -> str:
    payload = {
        "source_job_id": source_job_id,
        "target_kind": target_kind,
        "target_id": target_id,
        "model": model,
        "method": "kl_ipf",
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
    readable_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", target_id or target_kind).strip("_")[:32]
    return f"{source_job_id}_calibrated_{readable_target}_{digest}"


def probability_job_targets(sdir: Path, job_id: str, model: str | None = None) -> dict[str, dict[str, float]]:
    rows = [row for row in read_jsonl(probability_predictions_path(sdir)) if row.get("job_id") == job_id]
    if model:
        rows = [row for row in rows if row.get("model") == model or row.get("model_label") == model]
    if not rows:
        raise ZwillError("not_found", "No probability predictions found for target job.", context={"target_job_id": job_id})
    targets: dict[str, dict[str, float]] = {}
    duplicates = []
    for row in rows:
        question = row.get("question")
        if not question:
            continue
        if question in targets:
            duplicates.append(question)
            continue
        targets[str(question)] = {str(option): float(value) for option, value in row.get("probabilities", {}).items()}
    if duplicates:
        raise ZwillError(
            "invalid_input",
            "Target probability job has multiple rows for a question; specify --target-model or use a single-model target job.",
            context={"duplicates": sorted(set(duplicates))},
        )
    return targets


def empirical_marginal_targets(sdir: Path) -> dict[str, dict[str, float]]:
    truth_path = sdir / "committed" / "truth_marginals.json"
    truth = read_json(truth_path, {}) if truth_path.exists() else {}
    raw_marginals = truth.get("marginals", {}) if isinstance(truth, dict) else {}
    targets = {}
    for question, values in raw_marginals.items():
        total = sum(float(item.get("weighted_count", item.get("count", 0.0))) for item in values.values())
        if total <= 0:
            continue
        targets[question] = {
            str(option): float(item.get("weighted_count", item.get("count", 0.0))) / total
            for option, item in values.items()
        }
    return targets


def cmd_twin_results_calibrate_marginal(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    source_rows = [row for row in all_rows if row.get("job_id") == args.job_id]
    if args.model:
        source_rows = [
            row for row in source_rows
            if row.get("model") == args.model or row.get("model_label") == args.model
        ]
    selected_questions = selected_questions_arg(args)
    if selected_questions:
        source_rows = [row for row in source_rows if row.get("heldout_question") in selected_questions]
    if not source_rows:
        raise ZwillError("not_found", "No source digital twin predictions found for the requested filters.")

    target_kind = args.target
    target_id = None
    if target_kind == "probability-job":
        target_id = args.target_job_id or args.target_probability_job_id
        if not target_id:
            raise ZwillError("invalid_input", "--target-job-id is required when --target probability-job.")
        targets = probability_job_targets(sdir, target_id, args.target_model)
    elif target_kind == "empirical":
        targets = empirical_marginal_targets(sdir)
    else:
        raise ZwillError("invalid_input", f"Unsupported calibration target: {target_kind}.")

    output_job_id = args.output_job_id or default_calibrated_twin_job_id(args.job_id, target_kind, target_id, args.model)
    jdir = digital_twin_jobs_dir(sdir) / output_job_id
    if jdir.exists() and not args.replace:
        raise ZwillError(
            "already_exists",
            f"Calibrated digital twin result set already exists for job id {output_job_id}.",
            hint="Use --replace to overwrite this derived result set.",
        )
    if jdir.exists():
        shutil.rmtree(jdir)
    jdir.mkdir(parents=True, exist_ok=True)

    truth_path = sdir / "committed" / "truth_marginals.json"
    truth = read_json(truth_path, {}) if truth_path.exists() else {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        label = row.get("model_label") or model_label(row.get("service"), row.get("model"))
        grouped[(str(row.get("heldout_question")), label)].append(row)

    derived_rows = []
    issues = []
    diagnostics = []
    for (question, label), group_rows in sorted(grouped.items()):
        target = targets.get(question)
        if target is None:
            issues.append({"heldout_question": question, "model": label, "error": "missing_target_marginal"})
            continue
        try:
            adjusted_probabilities, calibration = calibrate_probabilities_to_marginal(
                group_rows,
                target,
                max_iter=args.max_iter,
                tolerance=args.tolerance,
            )
        except ValueError as exc:
            issues.append({"heldout_question": question, "model": label, "error": str(exc)})
            continue
        diagnostics.append({"heldout_question": question, "model": label, "rows": len(group_rows), **calibration})
        for source_row, probabilities_by_option in zip(group_rows, adjusted_probabilities):
            row = dict(source_row)
            row["job_id"] = output_job_id
            row["source_job_id"] = args.job_id
            row["source_row"] = source_row.get("row")
            row["probabilities"] = probabilities_by_option
            row["raw_probabilities"] = [probabilities_by_option[option] for option in row.get("option_labels", [])]
            row["raw_probability_sum"] = sum(row["raw_probabilities"])
            row["model_label"] = source_row.get("model_label") or label
            row["calibration"] = {
                "method": "kl_ipf",
                "source_job_id": args.job_id,
                "target": target_kind,
                "target_job_id": target_id,
                "target_model": args.target_model,
            }
            row["notes"] = f"KL/IPF calibrated from {args.job_id} to {target_kind}" + (f" {target_id}" if target_id else "")
            metrics = one_hot_metrics(row.get("option_labels", []), row.get("actual_answer"), probabilities_by_option)
            row.update(metrics)
            marginal_probabilities = true_probabilities_for(question, truth, row.get("option_labels", [])) if truth else {}
            marginal_metrics = one_hot_metrics(row.get("option_labels", []), row.get("actual_answer"), marginal_probabilities) if marginal_probabilities else {}
            row["empirical_marginal_probabilities"] = marginal_probabilities
            row["empirical_marginal_probability_actual"] = marginal_metrics.get("probability_actual")
            row["empirical_marginal_negative_log_likelihood"] = marginal_metrics.get("negative_log_likelihood")
            row["empirical_marginal_brier"] = marginal_metrics.get("brier")
            row["empirical_marginal_top1_correct"] = marginal_metrics.get("top1_correct")
            row["marginal_probabilities"] = marginal_probabilities
            row["marginal_probability_actual"] = marginal_metrics.get("probability_actual")
            row["marginal_negative_log_likelihood"] = marginal_metrics.get("negative_log_likelihood")
            row["marginal_brier"] = marginal_metrics.get("brier")
            row["marginal_top1_correct"] = marginal_metrics.get("top1_correct")
            row["imported_at"] = utc_now()
            derived_rows.append(row)

    if issues and not derived_rows:
        raise ZwillError("invalid_input", "No calibrated rows were produced.", context={"issues": issues})

    predictions_path = digital_twin_predictions_path(sdir)
    with file_lock(predictions_path):
        latest_rows = read_jsonl(predictions_path)
        remaining = [row for row in latest_rows if row.get("job_id") != output_job_id]
        rewrite_jsonl(predictions_path, remaining + derived_rows)
    metadata = {
        "job_id": output_job_id,
        "survey": args.survey,
        "source_job_id": args.job_id,
        "target": target_kind,
        "target_job_id": target_id,
        "target_model": args.target_model,
        "method": "kl_ipf",
        "row_count": len(source_rows),
        "extracted_count": len(derived_rows),
        "issue_count": len(issues),
        "issues": issues,
        "diagnostics": diagnostics,
        "imported_at": utc_now(),
    }
    write_json(jdir / "import.json", metadata)
    upsert_twin_run_manifest(
        sdir,
        {
            "job_id": output_job_id,
            "survey": args.survey,
            "status": "calibrated",
            "created_at": utc_now(),
            "source_job_id": args.job_id,
            "target": target_kind,
            "target_job_id": target_id,
            "row_count": len(source_rows),
            "extracted_count": len(derived_rows),
            "issue_count": len(issues),
            "models": sorted({row.get("model_label") or model_label(row.get("service"), row.get("model")) for row in derived_rows}),
            "heldout_questions": sorted({row.get("heldout_question") for row in derived_rows if row.get("heldout_question")}),
        },
    )
    return envelope(
        "zwill twin-results calibrate-marginal",
        "ok",
        {
            "job_id": output_job_id,
            "source_job_id": args.job_id,
            "target": target_kind,
            "target_job_id": target_id,
            "row_count": len(source_rows),
            "extracted_count": len(derived_rows),
            "issue_count": len(issues),
            "issues": issues,
            "diagnostics": diagnostics,
        },
        next_steps=[f"zwill twin-results report --survey {args.survey} --job-id {output_job_id}"],
    )


def filtered_twin_prediction_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    sdir = require_survey(args.survey)
    rows = read_jsonl(digital_twin_predictions_path(sdir))
    job_ids = set()
    if getattr(args, "manifest", None):
        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            raise ZwillError("not_found", f"Twin results manifest does not exist: {manifest_path}.")
        job_ids.update(job_ids_from_manifest(manifest_path))
    for value in getattr(args, "job_id", None) or []:
        job_ids.add(str(value))
    if getattr(args, "jobs", None):
        job_ids.update(item.strip() for item in str(args.jobs).split(",") if item.strip())
    selected_questions = selected_questions_arg(args)
    return filter_prediction_rows(
        rows,
        job_ids=job_ids or None,
        model=getattr(args, "model", None),
        questions=selected_questions or None,
    )


def cmd_twin_results_export(args: argparse.Namespace) -> None:
    rows = filtered_twin_prediction_rows(args)
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    output_rows = twin_prediction_export_rows(rows, args.format)
    write_csv_rows(Path(args.path) if args.path else None, output_rows)


def cmd_twin_results_package(args: argparse.Namespace) -> dict[str, Any]:
    rows = filtered_twin_prediction_rows(args)
    if not rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    csv_path = Path(args.path)
    output_rows = twin_prediction_export_rows(rows, args.format)
    write_csv_rows(csv_path, output_rows)
    zip_path = Path(args.zip_path) if args.zip_path else csv_path.with_suffix(".zip")
    zip_csv(csv_path, zip_path)
    return envelope(
        "zwill twin-results package",
        "ok",
        {
            "csv_path": str(csv_path),
            "zip_path": str(zip_path),
            "prediction_rows": len(rows),
            "csv_rows": len(output_rows),
            "csv_size_bytes": csv_path.stat().st_size,
            "zip_size_bytes": zip_path.stat().st_size,
        },
    )


def cmd_twin_results_marginal_diagnostics(args: argparse.Namespace) -> None:
    sdir = require_survey(args.survey)
    source_rows = filtered_twin_prediction_rows(args)
    if not source_rows:
        raise ZwillError("not_found", "No digital twin predictions found for the requested filters.")
    if args.target == "probability-job":
        if not args.target_job_id:
            raise ZwillError("invalid_input", "--target-job-id is required when --target probability-job.")
        targets = probability_job_targets(sdir, args.target_job_id, args.target_model)
        target_label = args.target_job_id
    else:
        targets = empirical_marginal_targets(sdir)
        target_label = "empirical"
    respondent_weights = {
        str(row.get("respondent_id")): float(row.get("weight", 1.0))
        for row in read_jsonl(sdir / "respondents.jsonl")
    }
    aggregates = aggregate_twin_marginals(source_rows, respondent_weights)
    summary_rows = []
    option_rows = []
    issues = []
    for (question, label), aggregate in sorted(aggregates.items()):
        target = targets.get(question)
        if target is None:
            issues.append({"heldout_question": question, "model": label, "error": "missing_target_marginal"})
            continue
        predicted = aggregate["probabilities"]
        metrics = distribution_distance_metrics(predicted, target)
        predicted_top, predicted_top_probability = top_prediction(predicted)
        target_top, target_top_probability = top_prediction(target)
        summary_rows.append(
            {
                "survey": args.survey,
                "job_id": ",".join(sorted({str(row.get("job_id")) for row in source_rows if row.get("job_id")})),
                "target": args.target,
                "target_job_id": target_label,
                "heldout_question": question,
                "heldout_question_text": aggregate.get("question_text"),
                "model_label": label,
                "respondent_count": aggregate["respondent_count"],
                "predicted_top_option": predicted_top,
                "predicted_top_probability": predicted_top_probability,
                "target_top_option": target_top,
                "target_top_probability": target_top_probability,
                "top_option_agrees": int(predicted_top == target_top),
                **metrics,
            }
        )
        for option in sorted(set(predicted) | set(target)):
            option_rows.append(
                {
                    "survey": args.survey,
                    "heldout_question": question,
                    "heldout_question_text": aggregate.get("question_text"),
                    "model_label": label,
                    "option_label": option,
                    "predicted_probability": predicted.get(option, 0.0),
                    "target_probability": target.get(option, 0.0),
                    "difference": predicted.get(option, 0.0) - target.get(option, 0.0),
                    "abs_difference": abs(predicted.get(option, 0.0) - target.get(option, 0.0)),
                }
            )
    payload = {"summary": summary_rows, "options": option_rows, "issues": issues}
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).write_text(output + "\n")
        else:
            print(output)
        return
    if args.format == "csv":
        path = Path(args.path) if args.path else None
        fieldnames = list(summary_rows[0].keys()) if summary_rows else [
            "survey",
            "job_id",
            "target",
            "target_job_id",
            "heldout_question",
            "model_label",
            "respondent_count",
            "l1",
            "mae",
            "brier",
            "js_divergence",
        ]
        if path:
            with path.open("w", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(summary_rows)
            if args.option_path:
                option_fieldnames = list(option_rows[0].keys()) if option_rows else [
                    "survey",
                    "heldout_question",
                    "model_label",
                    "option_label",
                    "predicted_probability",
                    "target_probability",
                    "difference",
                    "abs_difference",
                ]
                with Path(args.option_path).open("w", newline="") as output_file:
                    writer = csv.DictWriter(output_file, fieldnames=option_fieldnames)
                    writer.writeheader()
                    writer.writerows(option_rows)
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)
        return
    table = Table(title="Twin Marginal Diagnostics")
    for column in ["question", "model", "n", "target", "L1", "Brier", "JS", "top"]:
        table.add_column(column)
    for row in summary_rows:
        table.add_row(
            str(row["heldout_question"]),
            str(row["model_label"]),
            str(row["respondent_count"]),
            str(row["target_job_id"]),
            f"{row['l1']:.4f}",
            f"{row['brier']:.4f}",
            f"{row['js_divergence']:.4f}",
            "yes" if row["top_option_agrees"] else "no",
        )
    Console().print(table)



def cmd_twin_results_bootstrap(args: argparse.Namespace) -> dict[str, Any]:
    from .twin_bootstrap import bootstrap_summary

    rows = filtered_twin_prediction_rows(args)
    if not rows:
        raise ZwillError("not_found", "No twin prediction rows matched the given filters.")
    baseline_model = getattr(args, "baseline_model", None)
    result = bootstrap_summary(
        rows,
        baseline_model=baseline_model,
        n_boot=int(getattr(args, "n_boot", 1000) or 1000),
        seed=int(getattr(args, "seed", 0) or 0),
        ci=float(getattr(args, "ci", 0.95) or 0.95),
    )
    if getattr(args, "path", None):
        write_json(Path(args.path), result)

    # Compact headline: macro score CIs per model, and macro deltas vs the baseline.
    headline_models = {
        label: block["macro"] for label, block in result["models"].items()
    }
    headline_deltas = {}
    if "deltas_vs_baseline" in result:
        headline_deltas = {
            "baseline_model": result["deltas_vs_baseline"]["baseline_model"],
            "models": {
                label: block["macro"]
                for label, block in result["deltas_vs_baseline"]["models"].items()
            },
        }
    warnings = []
    if baseline_model and baseline_model not in result["models"]:
        warnings.append(
            {"code": "baseline_not_found", "message": f"Baseline model '{baseline_model}' not present in the matched rows."}
        )
    return envelope(
        "zwill twin-results bootstrap",
        "ok",
        {
            "n_boot": result["n_boot"],
            "ci": result["ci"],
            "seed": result["seed"],
            "models": sorted(result["models"]),
            "macro_scores": headline_models,
            "macro_deltas_vs_baseline": headline_deltas,
            "full_result_path": str(Path(args.path)) if getattr(args, "path", None) else None,
        },
        warnings=warnings,
    )


def cmd_twin_results_leakage_audit(args: argparse.Namespace) -> dict[str, Any]:
    from .twin_diagnostics import build_context_leakage_diagnostics

    sdir = require_survey(args.survey)
    questions = read_jsonl(sdir / "questions.jsonl")
    if not questions:
        raise ZwillError("invalid_input", "Survey has no imported questions to audit.")
    answer_by_respondent: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in read_jsonl(sdir / "answers.jsonl"):
        if row.get("answer") is None:
            continue
        answer_by_respondent[row["respondent_id"]][row["question"]] = row["answer"]

    question_names = [str(q["question_name"]) for q in questions]
    targets: list[str] = []
    for value in getattr(args, "target", None) or []:
        targets.append(str(value))
    if getattr(args, "targets", None):
        targets.extend(item.strip() for item in str(args.targets).split(",") if item.strip())
    job_ids = set(getattr(args, "job_id", None) or [])
    if getattr(args, "jobs", None):
        job_ids.update(item.strip() for item in str(args.jobs).split(",") if item.strip())
    if job_ids:
        for row in read_jsonl(digital_twin_predictions_path(sdir)):
            if row.get("job_id") in job_ids and row.get("heldout_question"):
                targets.append(str(row["heldout_question"]))
    if not targets:
        targets = question_names  # audit every question as a potential target
    deduped: list[str] = []
    for target in targets:
        if target not in deduped:
            deduped.append(target)

    diagnostics = build_context_leakage_diagnostics(
        questions,
        answer_by_respondent,
        deduped,
        min_pair_rows=int(getattr(args, "min_pair_rows", 30) or 30),
        warn_threshold=float(getattr(args, "threshold", 0.7) or 0.7),
    )
    if getattr(args, "path", None):
        write_json(Path(args.path), diagnostics)

    warnings = []
    if diagnostics["flagged_count"]:
        top = diagnostics["rows"][0]
        warnings.append(
            {
                "code": "possible_leakage",
                "message": (
                    f"{diagnostics['flagged_count']} context->target pair(s) exceed Cramer's V "
                    f"{diagnostics['warn_threshold']}; strongest: {top['context_question']} -> "
                    f"{top['target_question']} (V={top['cramers_v']:.2f})."
                ),
            }
        )
    return envelope(
        "zwill twin-results leakage-audit",
        "ok",
        {
            "targets_audited": deduped,
            "warn_threshold": diagnostics["warn_threshold"],
            "pair_count": diagnostics["pair_count"],
            "flagged_count": diagnostics["flagged_count"],
            "flagged": [row for row in diagnostics["rows"] if row["warning"]],
            "full_result_path": str(Path(args.path)) if getattr(args, "path", None) else None,
        },
        warnings=warnings,
    )
