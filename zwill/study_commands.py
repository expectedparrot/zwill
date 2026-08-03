"""Cross-survey study utilities.

These commands deliberately stop at zwill's artifact boundary: they expand
approved-plan inputs, summarize validation bundles, and account for completed
Results packages. Expected Parrot still executes exported jobs.
"""

from __future__ import annotations

import argparse
import csv
import glob
import html
import math
import random
import statistics

from .cli import *  # noqa: F403
from .costs import results_cost_summary


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ZwillError("not_found", f"Study input does not exist: {path}.")
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        value = yaml.safe_load(path.read_text())
    else:
        value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ZwillError("invalid_input", f"Study input must be an object: {path}.")
    return value


def _study_surveys(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = manifest.get("surveys") or []
    if not isinstance(rows, list):
        raise ZwillError("invalid_input", "Study manifest 'surveys' must be a list.")
    normalized = []
    for row in rows:
        if isinstance(row, str):
            row = {"survey": row}
        if not isinstance(row, dict) or not row.get("survey"):
            raise ZwillError("invalid_input", "Every study survey needs a 'survey' name.")
        normalized.append(dict(row))
    return normalized


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bootstrap_mean(values: list[float], *, seed: int = 0, n_boot: int = 10000) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "lo": None, "hi": None}
    rng = random.Random(seed)
    draws = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(n_boot))
    return {
        "n": len(values), "mean": sum(values) / len(values),
        "lo": draws[int(.025 * (n_boot - 1))], "hi": draws[int(.975 * (n_boot - 1))],
    }


def _study_aggregates(
    survey_rows: list[dict[str, Any]], question_rows: list[dict[str, Any]],
    option_rows: list[dict[str, Any]], marginal_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    models = sorted({str(row.get("model")) for row in question_rows if row.get("model")})
    result: dict[str, Any] = {"models": {}}
    for model in models:
        questions = [row for row in question_rows if row.get("model") == model]
        surveys = [row for row in survey_rows if row.get("model") == model]
        claim_surveys = [
            row for row in surveys
            if not row.get("excluded_from_claims") and (_numeric(row.get("leakage_flags")) or 0) == 0
        ]
        benchmark = {}
        for name in ("uniform", "marginal", "conditional", "one_shot"):
            values = [value for row in questions if (value := _numeric(row.get(f"nll_delta_vs_{name}"))) is not None]
            benchmark[name] = {
                **_bootstrap_mean(values), "questions_better": sum(value < 0 for value in values),
                "surveys_better": sum(
                    (_numeric(row.get(f"nll_delta_vs_{name}")) or 0) < 0
                    for row in surveys if _numeric(row.get(f"nll_delta_vs_{name}")) is not None
                ),
            }
            brier_values = [
                value for row in questions
                if (value := _numeric(row.get(f"brier_delta_vs_{name}"))) is not None
            ]
            benchmark[name]["brier"] = {
                **_bootstrap_mean(brier_values),
                "questions_better": sum(value < 0 for value in brier_values),
            }
        option_values = [
            row for row in option_rows
            if str(row.get("model_label", "")) == model or str(row.get("model_label", "")).endswith(f" / {model}")
        ]
        pairs = [
            (_numeric(row.get("target_probability")), _numeric(row.get("predicted_probability")))
            for row in option_values
        ]
        pairs = [(actual, predicted) for actual, predicted in pairs if actual is not None and predicted is not None]
        correlation = None
        if len(pairs) > 1:
            actual_mean = sum(a for a, _p in pairs) / len(pairs)
            predicted_mean = sum(p for _a, p in pairs) / len(pairs)
            numerator = sum((a - actual_mean) * (p - predicted_mean) for a, p in pairs)
            denominator = math.sqrt(
                sum((a - actual_mean) ** 2 for a, _p in pairs)
                * sum((p - predicted_mean) ** 2 for _a, p in pairs)
            )
            correlation = numerator / denominator if denominator else None
        marginal_values = [
            row for row in marginal_rows
            if str(row.get("model_label", "")) == model or str(row.get("model_label", "")).endswith(f" / {model}")
        ]
        result["models"][model] = {
            "questions": len(questions), "surveys": len(surveys), "benchmarks": benchmark,
            "conditional_gate_counts": {
                status: sum(row.get("conditional_gate") == status for row in surveys)
                for status in ("pass", "fail", "inconclusive", "not_available")
            },
            "claim_conditional_gate_counts": {
                status: sum(row.get("conditional_gate") == status for row in claim_surveys)
                for status in ("pass", "fail", "inconclusive", "not_available")
            },
            "excluded_surveys": len(surveys) - len(claim_surveys),
            "calibration": {
                "mean_ece": (
                    sum(values) / len(values)
                    if (values := [value for row in surveys if (value := _numeric(row.get("expected_calibration_error"))) is not None])
                    else None
                )
            },
            "marginal_fit": {
                "option_count": len(pairs),
                "option_mae": sum(abs(a - p) for a, p in pairs) / len(pairs) if pairs else None,
                "option_correlation": correlation,
                "mean_question_mae": (
                    sum(values) / len(values)
                    if (values := [value for row in marginal_values if (value := _numeric(row.get("mae"))) is not None])
                    else None
                ),
                "median_question_mae": (
                    statistics.median(values)
                    if (values := [value for row in marginal_values if (value := _numeric(row.get("mae"))) is not None])
                    else None
                ),
                "mean_js_divergence": (
                    sum(values) / len(values)
                    if (values := [value for row in marginal_values if (value := _numeric(row.get("js_divergence"))) is not None])
                    else None
                ),
                "top_option_agreement": (
                    sum(str(row.get("top_option_agrees", "")).lower() in {"true", "1"} for row in marginal_values)
                    / len(marginal_values) if marginal_values else None
                ),
            },
        }
    return result


def _bootstrap_delta(bootstrap: dict[str, Any], model: str, question: str | None = None) -> dict[str, Any]:
    block = (bootstrap.get("deltas_vs_baseline") or {}).get("models", {}).get(model, {})
    if question is None:
        block = block.get("macro", {})
    else:
        block = (block.get("questions") or {}).get(question, {})
    return block.get("negative_log_likelihood") or {}


def _explicit_benchmarks(scores: dict[str, Any]) -> dict[str, Any]:
    """Normalize v1 skill-only bundles to the explicit v2 benchmark schema."""
    result = dict(scores)
    model_nll = result.get("mean_nll")
    model_brier = result.get("mean_brier")
    for name in ("uniform", "marginal"):
        for metric, model_value in (("nll", model_nll), ("brier", model_brier)):
            mean_key = f"{name}_mean_{metric}"
            delta_key = f"{metric}_delta_vs_{name}"
            skill = result.get(f"{metric}_skill_vs_{name}")
            if result.get(mean_key) is None and model_value is not None and skill is not None and skill != 1:
                result[mean_key] = float(model_value) / (1.0 - float(skill))
            if result.get(delta_key) is None and model_value is not None and result.get(mean_key) is not None:
                result[delta_key] = float(model_value) - float(result[mean_key])
    return result


def cmd_study_summarize(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest)
    manifest = _load(manifest_path)
    root = manifest_path.parent
    out = resolve_output_path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    survey_rows: list[dict[str, Any]] = []
    question_rows: list[dict[str, Any]] = []
    option_rows: list[dict[str, Any]] = []
    marginal_rows: list[dict[str, Any]] = []
    missing: list[str] = []

    for record in _study_surveys(manifest):
        survey = str(record["survey"])
        rendered = str(args.bundle_template).format(survey=survey, source_id=record.get("source_id", survey))
        bundle = Path(rendered)
        if not bundle.is_absolute():
            bundle = root / bundle
        report_path = bundle / "report_data.json"
        if not report_path.exists():
            missing.append(str(report_path))
            continue
        report = read_json(report_path, {})
        bootstrap = read_json(bundle / "bootstrap.json", report.get("bootstrap") or {})
        one_shot_bootstrap = read_json(bundle / "bootstrap-one-shot.json", {})
        leakage = read_json(bundle / "leakage_audit.json", report.get("leakage") or {})
        ece = ((report.get("calibration") or {}).get("expected_calibration_error") or {})
        for model, model_data in sorted((report.get("skill_scores") or {}).items()):
            macro = _explicit_benchmarks(model_data.get("macro") or {})
            delta = _bootstrap_delta(bootstrap, model)
            one_shot_delta = _bootstrap_delta(one_shot_bootstrap, model)
            survey_rows.append(
                {
                    "survey": survey,
                    "source_id": record.get("source_id", survey),
                    "model": model,
                    "respondents": (report.get("design") or {}).get("respondent_count"),
                    "questions": macro.get("questions"),
                    "mean_nll": macro.get("mean_nll"),
                    "uniform_mean_nll": macro.get("uniform_mean_nll"),
                    "marginal_mean_nll": macro.get("marginal_mean_nll"),
                    "nll_delta_vs_uniform": macro.get("nll_delta_vs_uniform"),
                    "nll_delta_vs_marginal": macro.get("nll_delta_vs_marginal"),
                    "nll_delta_vs_conditional": delta.get("delta"),
                    "nll_delta_vs_conditional_lo": delta.get("lo"),
                    "nll_delta_vs_conditional_hi": delta.get("hi"),
                    "nll_delta_vs_one_shot": one_shot_delta.get("delta"),
                    "nll_delta_vs_one_shot_lo": one_shot_delta.get("lo"),
                    "nll_delta_vs_one_shot_hi": one_shot_delta.get("hi"),
                    "leakage_flags": leakage.get("flagged_count", 0),
                    "expected_calibration_error": (
                        sum(float(value) for key, value in ece.items() if key == model or key.endswith(f" / {model}"))
                        / len([key for key in ece if key == model or key.endswith(f" / {model}")])
                        if any(key == model or key.endswith(f" / {model}") for key in ece) else None
                    ),
                    "conditional_gate": (
                        "not_available" if delta.get("lo") is None or delta.get("hi") is None
                        else "pass" if float(delta["hi"]) < 0
                        else "fail" if float(delta["lo"]) > 0
                        else "inconclusive"
                    ),
                    "excluded_from_claims": bool(record.get("exclude_from_claims", False)),
                    "exclusion_reason": record.get("exclusion_reason"),
                    "bundle": str(bundle),
                }
            )
            for question, raw_scores in sorted((model_data.get("questions") or {}).items()):
                scores = _explicit_benchmarks(raw_scores)
                q_delta = _bootstrap_delta(bootstrap, model, question)
                q_one_shot_delta = _bootstrap_delta(one_shot_bootstrap, model, question)
                question_rows.append(
                    {
                        "survey": survey,
                        "source_id": record.get("source_id", survey),
                        "model": model,
                        "question": question,
                        **scores,
                        "nll_delta_vs_conditional": q_delta.get("delta"),
                        "nll_delta_vs_conditional_lo": q_delta.get("lo"),
                        "nll_delta_vs_conditional_hi": q_delta.get("hi"),
                        "nll_delta_vs_one_shot": q_one_shot_delta.get("delta"),
                        "nll_delta_vs_one_shot_lo": q_one_shot_delta.get("lo"),
                        "nll_delta_vs_one_shot_hi": q_one_shot_delta.get("hi"),
                    }
                )
        for row in report.get("marginal_options") or []:
            option_rows.append({"survey": survey, "source_id": record.get("source_id", survey), **row})
        for row in report.get("marginals") or []:
            marginal_rows.append({"survey": survey, "source_id": record.get("source_id", survey), **row})

    if missing and not getattr(args, "allow_missing", False):
        raise ZwillError(
            "not_found",
            "One or more study validation bundles are missing.",
            context={"missing": missing},
            hint="Run each survey's twin-validate stage, or pass --allow-missing for a partial summary.",
        )
    _write_csv(out / "surveys.csv", survey_rows)
    _write_csv(out / "questions.csv", question_rows)
    _write_csv(out / "options.csv", option_rows)
    _write_csv(out / "marginals.csv", marginal_rows)
    aggregates = _study_aggregates(survey_rows, question_rows, option_rows, marginal_rows)
    write_json(out / "aggregates.json", {"schema_version": 1, **aggregates})
    summary = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "bundle_template": args.bundle_template,
        "survey_count": len({row["survey"] for row in survey_rows}),
        "model_count": len({row["model"] for row in survey_rows}),
        "question_rows": len(question_rows),
        "option_rows": len(option_rows),
        "marginal_rows": len(marginal_rows),
        "missing_bundles": missing,
        "artifacts": {
            "surveys": str(out / "surveys.csv"),
            "questions": str(out / "questions.csv"),
            "options": str(out / "options.csv"),
            "marginals": str(out / "marginals.csv"),
            "aggregates": str(out / "aggregates.json"),
        },
        "aggregates": aggregates,
    }
    write_json(out / "summary.json", summary)
    return envelope("zwill study summarize", "ok", {**summary, "path": str(out)})


def cmd_study_expand(args: argparse.Namespace) -> dict[str, Any]:
    from .twin_experiments import cmd_twin_experiment_init_plan

    source = Path(args.manifest)
    manifest = _load(source)
    defaults = dict(manifest.get("design") or {})
    comparison_name = getattr(args, "comparison", None)
    comparison = {}
    if comparison_name:
        comparison = (manifest.get("comparisons") or {}).get(comparison_name) or {}
        if not comparison:
            raise ZwillError("not_found", f"Unknown study comparison: {comparison_name}.")
        defaults = _merge_objects(defaults, comparison.get("design") or {})
        args.mode = comparison.get("mode", args.mode)
    models = list(args.model or defaults.get("models") or ["openai:gpt-5.5"])
    output = resolve_output_path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    plans = []
    selected_surveys = set(args.survey or comparison.get("surveys") or [])
    comparison_models = comparison.get("models") or []
    for record in _study_surveys(manifest):
        survey = str(record["survey"])
        if selected_surveys and survey not in selected_surveys:
            continue
        targets = record.get("heldout_questions") or record.get("questions") or []
        if isinstance(targets, str):
            targets = [item.strip() for item in targets.split(",") if item.strip()]
        groups = [[target] for target in targets] if args.mode == "by-question" else [list(targets)]
        for target_group in groups:
            suffix = slugify(target_group[0]).lower() if len(target_group) == 1 else "simultaneous"
            variants = comparison_models or [{"models": models}]
            for raw_variant in variants:
                variant = {"model": raw_variant} if isinstance(raw_variant, str) else dict(raw_variant)
                variant_models = list(variant.get("models") or ([variant["model"]] if variant.get("model") else models))
                declared_plan_id = record.get("plan_id") if args.mode == "simultaneous" else (
                    (record.get("by_question_plan_ids") or {}).get(target_group[0])
                )
                if comparison_name:
                    model_slug = "_".join("".join(char if char.isalnum() else "_" for char in model).strip("_") for model in variant_models)
                    template = comparison.get("plan_id_template", "{survey}_{comparison}_{model_slug}_{question}_v1")
                    declared_plan_id = template.format(survey=survey, comparison=comparison_name,
                                                       model_slug=model_slug, question=suffix)
                derived_plan_id = (
                    f"{survey}_byq_{suffix}_{args.plan_suffix}"
                    if args.mode == "by-question" else f"{survey}_{suffix}_{args.plan_suffix}"
                )
                plan_id = str(declared_plan_id or derived_plan_id)
                path = output / f"{plan_id}.json"
                if path.exists():
                    existing = _load(path)
                    plans.append({
                        "plan_id": plan_id, "survey": survey, "heldout_questions": target_group,
                        "models": existing.get("defaults", {}).get("model", variant_models), "path": str(path.resolve()),
                        "prediction_count_estimate": existing.get("prediction_count_estimate"), "existing": True,
                        "comparison": comparison_name,
                    })
                    continue
                leakage = []
                exclusions = record.get("leakage_exclusions") or {}
                for target in target_group:
                    for context in exclusions.get(target, []):
                        leakage.append(f"{target}:{context}")
                approach = variant.get("approach_id") or defaults.get("approach_id", "battery_context")
                ns = argparse.Namespace(
                    survey=survey, plan_id=plan_id, path=str(path.resolve()), heldout_question=target_group,
                    heldout_questions=None, approach_id=list(args.approach_id or [approach]),
                    sample_respondents=args.sample_respondents or defaults.get("sample_respondents"),
                    seed=args.seed if args.seed is not None else defaults.get("seed", 123),
                    stratify_actual=bool(defaults.get("stratify_actual", False)),
                    context_question_count=args.context_question_count or defaults.get("context_question_count", 5),
                    context_question=None, context_questions=None, exclude_context_question=None,
                    leakage_exclusion=leakage or None, model=variant_models,
                    model_param=variant.get("model_param") or defaults.get("model_param"),
                    primary_metric=defaults.get("primary_metric", "nll"),
                )
                result = cmd_twin_experiment_init_plan(ns)
                plans.append({
                    "plan_id": plan_id, "survey": survey, "heldout_questions": target_group,
                    "models": variant_models, "path": str(path.resolve()),
                    "prediction_count_estimate": result["data"]["plan"].get("prediction_count_estimate"),
                    "comparison": comparison_name,
                })
    expanded = {"schema_version": 1, "source_manifest": str(source), "mode": args.mode, "plans": plans}
    write_json(output / "manifest.json", expanded)
    return envelope(
        "zwill study expand",
        "ok",
        {"path": str(output), "manifest": str(output / "manifest.json"), "plan_count": len(plans), "plans": plans},
        next_steps=[f"Review and approve the plans listed in {output / 'manifest.json'} before export."],
    )


def cmd_study_prepare(args: argparse.Namespace) -> dict[str, Any]:
    """Expand, approve, and export a study as one inspectable lifecycle step."""
    output = resolve_output_path(args.out)
    plans_dir = output / "plans"
    jobs_dir = output / "jobs"
    expanded = cmd_study_expand(argparse.Namespace(
        manifest=args.manifest, mode=args.mode, out=str(plans_dir),
        plan_suffix=args.plan_suffix, approach_id=args.approach_id,
        sample_respondents=args.sample_respondents, seed=args.seed,
        context_question_count=args.context_question_count, comparison=args.comparison,
        survey=args.survey, model=args.model,
    ))["data"]
    plans_manifest = str(expanded["manifest"])
    approval = cmd_study_approve(argparse.Namespace(
        manifest=plans_manifest, approved_by=args.approved_by, note=args.note,
    ))["data"]
    exported = cmd_study_export(argparse.Namespace(
        manifest=plans_manifest, out=str(jobs_dir), results_dir=args.results_dir,
    ))["data"]
    return envelope(
        "zwill study prepare", "ok",
        {
            "path": str(output), "plans_manifest": plans_manifest,
            "run_manifest": exported["path"], "plan_count": expanded["plan_count"],
            "job_count": exported["job_count"],
            "prediction_count": exported["prediction_count"], "approval": approval,
        },
        next_steps=[f"ep run-manifest {exported['path']} --execute"],
    )


def cmd_study_costs(args: argparse.Namespace) -> dict[str, Any]:
    patterns = list(args.result or [])
    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest = _load(manifest_path)
        for value in manifest.get("result_paths") or manifest.get("results") or []:
            path = Path(str(value))
            patterns.append(str(path if path.is_absolute() else manifest_path.parent / path))
    paths: list[Path] = []
    for pattern in patterns:
        matches = [Path(item) for item in glob.glob(str(pattern), recursive=True)]
        paths.extend(matches or [Path(pattern)])
    paths = sorted(dict.fromkeys(path for path in paths if path.is_file()))
    if not paths:
        raise ZwillError("not_found", "No Results packages matched the requested paths.")
    files = []
    total = 0.0
    calls = 0
    by_model: dict[str, dict[str, Any]] = {}
    for path in paths:
        result = read_edsl_results(path)
        cost = results_cost_summary(result)
        files.append({"path": str(path), **cost})
        total += float(cost["total_usd"])
        calls += int(cost["call_count"])
        for row in cost["by_model"]:
            bucket = by_model.setdefault(row["model"], {"model": row["model"], "cost_usd": 0.0, "call_count": 0})
            bucket["cost_usd"] += float(row["cost_usd"])
            bucket["call_count"] += int(row["call_count"])
    data = {
        "file_count": len(files),
        "total_usd": round(total, 4),
        "call_count": calls,
        "by_model": sorted(by_model.values(), key=lambda row: row["model"]),
        "files": files,
    }
    if args.path:
        write_json(resolve_output_path(args.path), data)
        data["path"] = str(resolve_output_path(args.path))
    return envelope("zwill study costs", "ok", data)


def _manifest_records(path: Path, *, key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _load(path)
    rows = payload.get(key) or []
    if not isinstance(rows, list) or not rows:
        raise ZwillError("invalid_input", f"Study manifest needs a non-empty '{key}' list.")
    return payload, [dict(row) for row in rows]


def cmd_study_export(args: argparse.Namespace) -> dict[str, Any]:
    """Export every approved plan and write an execution-neutral run manifest."""
    from .twin_experiments import cmd_twin_experiment_export_plan, cmd_twin_experiment_validate

    source = Path(args.manifest)
    _payload, plans = _manifest_records(source, key="plans")
    output = resolve_output_path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    runs = []
    for record in plans:
        plan_path = Path(str(record.get("path", "")))
        if not plan_path.is_absolute():
            plan_path = source.parent / plan_path
        cmd_twin_experiment_validate(argparse.Namespace(input_path=str(plan_path), survey=None, plan_id=None))
        plan_id = str(record.get("plan_id") or plan_path.stem)
        export_dir = output / plan_id
        existing_manifest = export_dir / "manifest.json"
        result = _load(existing_manifest) if existing_manifest.exists() else cmd_twin_experiment_export_plan(
            argparse.Namespace(input_path=str(plan_path), survey=None, plan_id=None,
                               output_dir=str(export_dir), allow_unapproved=False)
        )["data"]
        for exported in result.get("exports") or []:
            job_path = Path(str(exported["job_path"]))
            result_path = Path(args.results_dir) / plan_id / f"{job_path.stem.removesuffix('_jobs')}_results.ep"
            runs.append({
                "plan_id": plan_id, "survey": result["survey"], "plan_path": str(plan_path),
                "export_manifest": str(export_dir / "manifest.json"), "job_id": exported["job_id"],
                "job_path": str(job_path), "result_path": str(result_path),
                "ep_command": f"ep run {job_path} --output {result_path}",
                "prediction_count": exported.get("prediction_count_exported"),
            })
    manifest = {
        "schema_version": 1, "kind": "zwill_study_run", "source_manifest": str(source),
        "runs": runs, "plan_count": len(plans), "job_count": len(runs),
        "prediction_count": sum(int(row.get("prediction_count") or 0) for row in runs),
    }
    manifest_path = output / "run-manifest.json"
    write_json(manifest_path, manifest)
    return envelope(
        "zwill study export", "ok", {**manifest, "path": str(manifest_path)},
        next_steps=[f"Run each ep_command in {manifest_path}; then zwill study import --manifest {manifest_path} --results-dir results"],
    )


def cmd_study_import(args: argparse.Namespace) -> dict[str, Any]:
    """Import result directories named by plan id from a study run manifest."""
    from .twin_experiments import cmd_twin_experiment_import_plan_results

    source = Path(args.manifest)
    _payload, runs = _manifest_records(source, key="runs")
    results_root = Path(args.results_dir)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        groups.setdefault((str(run["plan_id"]), str(run["export_manifest"])), run)
    imports = []
    for (plan_id, export_manifest), run in sorted(groups.items()):
        manifest_path = Path(export_manifest)
        if not manifest_path.is_absolute():
            manifest_path = source.parent / manifest_path
        result_dir = results_root / plan_id
        result = cmd_twin_experiment_import_plan_results(argparse.Namespace(
            manifest=str(manifest_path), survey=run.get("survey"), results_dir=str(result_dir),
            replace=bool(args.replace),
        ))
        imports.append({"plan_id": plan_id, **result["data"]})
    missing = [job for row in imports for job in row.get("missing_jobs") or []]
    return envelope(
        "zwill study import", "ok" if not missing else "warning",
        {"plan_count": len(imports), "import_count": sum(row["import_count"] for row in imports),
         "missing_jobs": missing, "plans": imports},
    )


def cmd_study_audit(args: argparse.Namespace) -> dict[str, Any]:
    from .twin_validation_commands import cmd_twin_results_run_report

    source = Path(args.manifest)
    _payload, runs = _manifest_records(source, key="runs")
    output = resolve_output_path(args.out)
    reports = []
    for run in runs:
        path = output / str(run["survey"]) / f"{run['job_id']}.html"
        result = cmd_twin_results_run_report(argparse.Namespace(
            survey=str(run["survey"]), job_id=str(run["job_id"]), format="html",
            path=str(path), example_limit=6,
        ))
        reports.append({"survey": run["survey"], "job_id": run["job_id"], "path": str(path),
                        "issue_count": (result.get("data") or {}).get("issue_count")})
    return envelope("zwill study audit", "ok", {"report_count": len(reports), "reports": reports, "path": str(output)})


def cmd_study_validate(args: argparse.Namespace, *, embedder=None) -> dict[str, Any]:
    from .twin_validation_workflow import cmd_twin_validate

    source = Path(args.manifest)
    _payload, runs = _manifest_records(source, key="runs")
    for extra in getattr(args, "include_manifest", None) or []:
        _extra_payload, extra_runs = _manifest_records(Path(extra), key="runs")
        runs.extend(extra_runs)
    output_template = str(args.out)
    output = resolve_output_path(output_template) if "{survey}" not in output_template else None
    probability_jobs = read_json(Path(args.probability_jobs), {}) if args.probability_jobs else {}
    by_survey: dict[str, list[str]] = {}
    for run in runs:
        by_survey.setdefault(str(run["survey"]), []).append(str(run["job_id"]))
    validations = []
    for survey, job_ids in sorted(by_survey.items()):
        path = (resolve_output_path(output_template.format(survey=survey))
                if "{survey}" in output_template else output / survey)
        ns = argparse.Namespace(
            survey=survey, job_id=None, jobs=",".join(dict.fromkeys(job_ids)), out=str(path), view="full",
            skip_baseline=False, require_baseline=True, skip_leakage_audit=False, skip_bootstrap=False,
            embedding_model=args.embedding_model, embedder=args.embedder, l2=1.0, leakage_threshold=0.7,
            min_pair_rows=30, n_boot=args.n_boot, ci=args.ci, seed=args.seed, env_path=args.env_path,
            probability_baseline_job_id=probability_jobs.get(survey), probability_baseline_model=None,
        )
        result = cmd_twin_validate(ns, embedder=embedder)
        validations.append({"survey": survey, "jobs": list(dict.fromkeys(job_ids)), "path": str(path),
                            "manifest": result["data"].get("manifest_path")})
    return envelope(
        "zwill study validate", "ok",
        {"survey_count": len(validations), "validations": validations,
         "path": output_template if "{survey}" in output_template else str(output)},
    )


def cmd_study_finish(args: argparse.Namespace, *, embedder=None) -> dict[str, Any]:
    """Import completed runs, render audits, and validate every study survey."""
    imported = cmd_study_import(argparse.Namespace(
        manifest=args.manifest, results_dir=args.results_dir, replace=args.replace,
    ))
    if imported["status"] != "ok":
        raise ZwillError(
            "incomplete_results",
            "Study results are incomplete; finish the run manifest before importing.",
        )
    audited = cmd_study_audit(argparse.Namespace(manifest=args.manifest, out=args.audit_out))["data"]
    validated = cmd_study_validate(argparse.Namespace(
        manifest=args.manifest, include_manifest=args.include_manifest, out=args.out,
        embedder=args.embedder, embedding_model=args.embedding_model,
        env_path=args.env_path, seed=args.seed, n_boot=args.n_boot, ci=args.ci,
        probability_jobs=args.probability_jobs,
    ), embedder=embedder)["data"]
    return envelope(
        "zwill study finish", "ok",
        {
            "import": imported["data"], "audit": audited, "validation": validated,
            "path": validated["path"],
        },
        next_steps=["Review the generated prompt audits before interpreting validation results."],
    )


def cmd_study_one_shot_build(args: argparse.Namespace) -> dict[str, Any]:
    """Build one independently runnable probability job for each survey."""
    from .edsl_integration import cmd_edsl_build

    source = Path(args.manifest)
    manifest = _load(source)
    defaults = manifest.get("design") or {}
    models = list(args.model or defaults.get("models") or ["openai:gpt-5.5"])
    output = resolve_output_path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    runs = []
    for record in _study_surveys(manifest):
        survey = str(record["survey"])
        questions = record.get("heldout_questions") or record.get("questions") or []
        if isinstance(questions, str):
            questions = [value.strip() for value in questions.split(",") if value.strip()]
        path = output / survey / "one_shot_jobs.ep"
        ns = argparse.Namespace(
            survey=survey, path=str(path), target="probability-job", question=None,
            questions=",".join(questions), exclude_question=None, limit=None, model=models,
            models=None, service_name=None, model_param=args.model_param or defaults.get("model_param"),
            job_question_name="response_probabilities",
        )
        result = cmd_edsl_build(ns)["data"]
        result_path = Path("results") / survey / "one_shot_results.ep"
        runs.append({
            "survey": survey, "source_id": record.get("source_id", survey), "questions": list(questions),
            "job_path": result["path"], "result_path": str(result_path),
            "ep_command": f"ep run {result['path']} --output {result_path}",
            "scenario_count": result.get("scenario_count"), "model_count": result.get("model_count"),
        })
    payload = {"schema_version": 1, "kind": "zwill_study_one_shot_run", "source_manifest": str(source), "runs": runs}
    path = output / "run-manifest.json"
    write_json(path, payload)
    return envelope(
        "zwill study one-shot-build", "ok",
        {**payload, "survey_count": len(runs), "path": str(path)},
        next_steps=[f"Run each ep_command in {path}; then zwill study one-shot-import --manifest {path} --results-dir results --path probability-jobs.json"],
    )


def cmd_study_one_shot_import(args: argparse.Namespace) -> dict[str, Any]:
    from .probability_commands import cmd_probability_results_import

    source = Path(args.manifest)
    _payload, runs = _manifest_records(source, key="runs")
    root = Path(args.results_dir)
    imported = []
    mapping = {}
    for run in runs:
        survey = str(run["survey"])
        result_path = root / survey / "one_shot_results.ep"
        result = cmd_probability_results_import(argparse.Namespace(
            survey=survey, input_path=str(result_path), job_id=None, replace=bool(args.replace),
        ))["data"]
        mapping[survey] = result["job_id"]
        imported.append({"survey": survey, "result_path": str(result_path), "job_id": result["job_id"],
                         "issue_count": result.get("issue_count", 0), "cost": result.get("cost")})
    path = resolve_output_path(args.path)
    write_json(path, mapping)
    return envelope(
        "zwill study one-shot-import", "ok",
        {"survey_count": len(imported), "imports": imported, "probability_jobs": mapping, "path": str(path)},
    )


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _svg_scatter(
    rows: list[dict[str, Any]], *, title: str, x_label: str, y_label: str,
    x_key: str, y_key: str, identity: bool = False,
) -> str:
    width, height = 760, 520
    left, right, top, bottom = 85, 25, 55, 70
    pairs = [(_numeric(row.get(x_key)), _numeric(row.get(y_key))) for row in rows]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    values = [value for pair in pairs for value in pair]
    low, high = (0.0, 1.0) if identity else ((min(values), max(values)) if values else (0.0, 1.0))
    if high <= low:
        high = low + 1.0
    pad = 0 if identity else .08 * (high - low)
    low, high = low - pad, high + pad
    def sx(value: float) -> float:
        return left + (value - low) / (high - low) * (width - left - right)

    def sy(value: float) -> float:
        return height - bottom - (value - low) / (high - low) * (height - top - bottom)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             '<style>text{font-family:system-ui,sans-serif;fill:#222}.title{font-size:19px;font-weight:600}.axis{font-size:13px}.grid{stroke:#ddd}.point{fill:#2878b5;fill-opacity:.55}</style>',
             f'<text class="title" x="{left}" y="30">{html.escape(title)}</text>',
             f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#333"/>',
             f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#333"/>']
    for index in range(6):
        value = low + index * (high - low) / 5
        x, y = sx(value), sy(value)
        parts += [f'<line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}"/>',
                  f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}"/>',
                  f'<text class="axis" x="{x:.1f}" y="{height-bottom+22}" text-anchor="middle">{value:.2f}</text>',
                  f'<text class="axis" x="{left-10}" y="{y+4:.1f}" text-anchor="end">{value:.2f}</text>']
    if identity:
        parts.append(f'<line x1="{sx(low):.1f}" y1="{sy(low):.1f}" x2="{sx(high):.1f}" y2="{sy(high):.1f}" stroke="#333" stroke-dasharray="5 4"/>')
    parts.extend(f'<circle class="point" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4"/>' for x, y in pairs)
    parts += [f'<text class="axis" x="{(left+width-right)/2:.1f}" y="{height-18}" text-anchor="middle">{html.escape(x_label)}</text>',
              f'<text class="axis" transform="translate(20 {(top+height-bottom)/2:.1f}) rotate(-90)" text-anchor="middle">{html.escape(y_label)}</text>', "</svg>"]
    return "".join(parts)


def _svg_forest(rows: list[dict[str, Any]], *, title: str, value_key: str, lo_key: str, hi_key: str) -> str:
    usable = [row for row in rows if _numeric(row.get(value_key)) is not None]
    width, row_height = 820, 28
    height = 95 + row_height * max(1, len(usable))
    left, right, top = 245, 30, 50
    bounds = [value for row in usable for key in (lo_key, hi_key, value_key) if (value := _numeric(row.get(key))) is not None]
    low, high = (min(bounds), max(bounds)) if bounds else (-1.0, 1.0)
    if high <= low:
        high = low + 1
    pad = .08 * (high - low)
    low, high = low - pad, high + pad
    def sx(value: float) -> float:
        return left + (value - low) / (high - low) * (width - left - right)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             '<style>text{font-family:system-ui,sans-serif;fill:#222}.title{font-size:19px;font-weight:600}.label{font-size:12px}.ci{stroke:#2878b5;stroke-width:2}.point{fill:#2878b5}</style>',
             f'<text class="title" x="20" y="28">{html.escape(title)}</text>']
    if low <= 0 <= high:
        parts.append(f'<line x1="{sx(0):.1f}" y1="{top-12}" x2="{sx(0):.1f}" y2="{height-35}" stroke="#555" stroke-dasharray="4 4"/>')
    for index, row in enumerate(usable):
        y = top + index * row_height
        value = _numeric(row.get(value_key))
        lo = _numeric(row.get(lo_key)) or value
        hi = _numeric(row.get(hi_key)) or value
        label = str(row.get("source_id") or row.get("survey") or row.get("question") or "")
        parts += [f'<text class="label" x="{left-10}" y="{y+4}" text-anchor="end">{html.escape(label)}</text>',
                  f'<line class="ci" x1="{sx(lo):.1f}" y1="{y}" x2="{sx(hi):.1f}" y2="{y}"/>',
                  f'<circle class="point" cx="{sx(value):.1f}" cy="{y}" r="4"/>']
    parts += [f'<text class="label" x="{left}" y="{height-12}">{low:.3f}</text>',
              f'<text class="label" x="{width-right}" y="{height-12}" text-anchor="end">{high:.3f}</text>', "</svg>"]
    return "".join(parts)


def cmd_study_plot(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.summary_dir)
    surveys = _read_csv_rows(source / "surveys.csv")
    questions = _read_csv_rows(source / "questions.csv")
    options = _read_csv_rows(source / "options.csv")
    model = args.model or next((row["model"] for row in surveys if not row.get("model", "").startswith("baseline:")), None)
    if not model:
        raise ZwillError("invalid_input", "No non-baseline model is available to plot.")
    kinds = args.kind or ["forest", "marginals", "benchmark", "calibration", "models", "one-shot"]
    output = resolve_output_path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for kind in kinds:
        svg = ""
        if kind == "forest":
            rows = [row for row in surveys if row.get("model") == model]
            svg = _svg_forest(rows, title="Twin minus conditional baseline NLL", value_key="nll_delta_vs_conditional",
                              lo_key="nll_delta_vs_conditional_lo", hi_key="nll_delta_vs_conditional_hi")
        elif kind == "marginals":
            rows = [row for row in options if row.get("model_label") == model or row.get("model_label", "").endswith(f" / {model}")]
            svg = _svg_scatter(rows, title="Observed and implied option shares", x_label="Observed option share",
                               y_label="Mean predicted probability", x_key="target_probability", y_key="predicted_probability", identity=True)
        elif kind in {"benchmark", "one-shot"}:
            key = "nll_delta_vs_one_shot" if kind == "one-shot" else "nll_delta_vs_uniform"
            rows = [row for row in questions if row.get("model") == model and _numeric(row.get(key)) is not None]
            svg = _svg_forest(rows, title=("Twin minus one-shot NLL" if kind == "one-shot" else "Twin minus uniform NLL"),
                              value_key=key, lo_key=f"{key}_lo", hi_key=f"{key}_hi")
        elif kind == "calibration":
            rows = [{"x": index, "y": row.get("expected_calibration_error")} for index, row in enumerate(surveys) if row.get("model") == model]
            svg = _svg_scatter(rows, title="Calibration by survey", x_label="Survey index", y_label="Expected calibration error", x_key="x", y_key="y")
        elif kind == "models":
            rows = [{"x": index, "y": row.get("mean_nll")} for index, row in enumerate(surveys) if not row.get("model", "").startswith("baseline:")]
            svg = _svg_scatter(rows, title="Mean NLL across study models", x_label="Survey-model row", y_label="Mean NLL", x_key="x", y_key="y")
        path = output / f"{kind}.svg"
        path.write_text(svg + "\n")
        artifacts.append({"kind": kind, "path": str(path)})
    manifest = {"schema_version": 1, "summary_dir": str(source), "model": model, "artifacts": artifacts}
    write_json(output / "manifest.json", manifest)
    return envelope("zwill study plot", "ok", {**manifest, "path": str(output / "manifest.json")})


def _merge_objects(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_objects(result[key], value)
        else:
            result[key] = value
    return result


def _write_study_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml
        path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    else:
        write_json(path, manifest)


def cmd_study_init(args: argparse.Namespace) -> dict[str, Any]:
    """Create an intentionally minimal manifest; never infer source files."""
    path = Path(args.path)
    if path.exists():
        raise ZwillError("already_exists", f"Refusing to replace existing study manifest: {path}.")
    manifest = {"schema_version": 1, "matrix_defaults": {}, "surveys": []}
    _write_study_manifest(path, manifest)
    return envelope(
        "zwill study init", "ok", {"path": str(path), **manifest},
        next_steps=[f"zwill study add-matrix --manifest {path} --survey NAME --metadata METADATA.json --respondents RESPONDENTS.csv"],
    )


def cmd_study_add_matrix(args: argparse.Namespace) -> dict[str, Any]:
    """Append one explicit source pair without guessing from filenames."""
    path = Path(args.manifest)
    manifest = _load(path)
    surveys = _study_surveys(manifest)
    if any(str(row.get("survey")) == args.survey for row in surveys):
        raise ZwillError("already_exists", f"Survey '{args.survey}' is already declared in {path}.")
    matrix = {"metadata": args.metadata, "respondents": args.respondents}
    if args.raw_metadata:
        matrix["raw_metadata"] = args.raw_metadata
    record = {"survey": args.survey, "source_id": args.source_id or args.survey, "matrix": matrix}
    manifest["surveys"] = [*surveys, record]
    _write_study_manifest(path, manifest)
    return envelope(
        "zwill study add-matrix", "ok",
        {"path": str(path), "survey_count": len(manifest["surveys"]), "survey": record},
        next_steps=[f"zwill study check --manifest {path}"],
    )


def cmd_study_set_matrix_defaults(args: argparse.Namespace) -> dict[str, Any]:
    """Record only defaults explicitly supplied by the operator."""
    path = Path(args.manifest)
    manifest = _load(path)
    defaults = dict(manifest.get("matrix_defaults") or {})
    if args.covariate_codebook is not None:
        defaults["covariate_codebook"] = args.covariate_codebook
    overlay = dict(defaults.get("metadata_overlay") or {})
    for key in ("data_variable_template", "question_name_template"):
        value = getattr(args, key)
        if value is not None:
            overlay[key] = value
    if args.missing_code is not None:
        overlay["missing_codes"] = args.missing_code
    if overlay:
        defaults["metadata_overlay"] = overlay
    manifest["matrix_defaults"] = defaults
    _write_study_manifest(path, manifest)
    return envelope(
        "zwill study set-matrix-defaults", "ok", {"path": str(path), "matrix_defaults": defaults},
        next_steps=[f"zwill study check --manifest {path}"],
    )


def cmd_study_check(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve and inventory every declared input without writing conversion state."""
    source = Path(args.manifest)
    manifest = _load(source)
    root = source.parent
    defaults = manifest.get("matrix_defaults") or {}
    inventory = []
    seen: set[str] = set()
    for record in _study_surveys(manifest):
        survey = str(record.get("survey", ""))
        if not survey or survey in seen:
            raise ZwillError("invalid_input", f"Study survey ids must be non-empty and unique: {survey!r}.")
        seen.add(survey)
        matrix = _merge_objects(defaults, record.get("matrix") or record.get("ingest") or {})
        variables = {"survey": survey, "source_id": record.get("source_id", survey)}
        resolved: dict[str, Path] = {}
        for key in ("metadata", "respondents"):
            if not matrix.get(key):
                raise ZwillError("invalid_input", f"{survey}: matrix.{key} is required.")
            candidate = Path(str(matrix[key]).format(**variables))
            resolved[key] = candidate if candidate.is_absolute() else root / candidate
            if not resolved[key].is_file():
                raise ZwillError("not_found", f"{survey}: {key} file does not exist: {resolved[key]}.")
        metadata = _load(resolved["metadata"])
        overlay = matrix.get("metadata_overlay") or {}
        if isinstance(overlay, str):
            overlay_path = Path(overlay)
            overlay_path = overlay_path if overlay_path.is_absolute() else root / overlay_path
            overlay = _load(overlay_path)
        metadata = _merge_objects(metadata, overlay)
        codebook = matrix.get("covariate_codebook")
        if codebook:
            codebook_path = Path(str(codebook).format(**variables))
            codebook_path = codebook_path if codebook_path.is_absolute() else root / codebook_path
            _load(codebook_path)
        with resolved["respondents"].open(newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            respondent_count = sum(1 for _row in reader)
        required = {str(metadata.get("respondent_id", "respondent_id")), str(metadata.get("weight", "weight"))}
        for item, spec in (metadata.get("items") or {}).items():
            source_variable = str(spec.get("source_variable", item))
            template = metadata.get("data_variable_template")
            required.add(str(template).format(item=item, source_variable=source_variable) if template else
                         str(spec.get("data_variable", source_variable)))
        missing = sorted(required - set(header))
        if missing:
            raise ZwillError("invalid_input", f"{survey}: respondent matrix is missing columns: {', '.join(missing)}.")
        inventory.append({
            "survey": survey, "source_id": variables["source_id"],
            "metadata": str(resolved["metadata"]), "respondents": str(resolved["respondents"]),
            "respondent_count": respondent_count, "question_count": len(metadata.get("items") or {}),
            "column_count": len(header), "required_columns": sorted(required),
        })
    return envelope(
        "zwill study check", "ok",
        {"path": str(source), "survey_count": len(inventory), "surveys": inventory},
        next_steps=[f"zwill study ingest --manifest {source} --out imports"],
    )


def _fixed_effect_slope(rows: list[dict[str, Any]], x_key: str, y_key: str) -> tuple[float | None, list[dict[str, float]]]:
    usable = [row for row in rows if _numeric(row.get(x_key)) is not None and _numeric(row.get(y_key)) is not None]
    residuals = []
    for survey in sorted({str(row["survey"]) for row in usable}):
        group = [row for row in usable if row["survey"] == survey]
        x_mean = sum(float(row[x_key]) for row in group) / len(group)
        y_mean = sum(float(row[y_key]) for row in group) / len(group)
        residuals.extend({"x": float(row[x_key]) - x_mean, "y": float(row[y_key]) - y_mean} for row in group)
    denominator = sum(row["x"] ** 2 for row in residuals)
    return ((sum(row["x"] * row["y"] for row in residuals) / denominator) if denominator else None, residuals)


def _loess(x: Any, y: Any, grid: Any, *, span: float = .75) -> list[float]:
    """Small dependency-free local-linear tricube smoother."""
    import numpy as np

    x, y, grid = np.asarray(x, dtype=float), np.asarray(y, dtype=float), np.asarray(grid, dtype=float)
    neighbors = max(3, min(len(x), int(math.ceil(span * len(x)))))
    fitted = []
    for point in grid:
        distance = np.abs(x - point)
        bandwidth = np.partition(distance, neighbors - 1)[neighbors - 1]
        if bandwidth <= 0:
            positive = distance[distance > 0]
            bandwidth = float(positive.min()) if len(positive) else 1.0
        weights = np.maximum(0, 1 - (distance / bandwidth) ** 3) ** 3
        centered = x - point
        design = np.column_stack([np.ones(len(x)), centered])
        matrix = design.T @ (weights[:, None] * design)
        target = design.T @ (weights * y)
        try:
            fitted.append(float(np.linalg.solve(matrix, target)[0]))
        except np.linalg.LinAlgError:
            fitted.append(float(np.average(y, weights=weights)) if weights.sum() else float(y.mean()))
    return fitted


def cmd_study_semantic_proximity(args: argparse.Namespace) -> dict[str, Any]:
    """Estimate exploratory within-battery proximity/performance relationships."""
    import numpy as np

    from .twin_baseline_commands import resolve_baseline_embedder

    sources = [Path(value) for value in args.manifest]
    runs = []
    for source in sources:
        _payload, source_runs = _manifest_records(source, key="runs")
        runs.extend(source_runs)
    performance = [
        row
        for value in args.questions
        for row in _read_csv_rows(Path(value))
        if row.get("model") == args.model
    ]
    performance_by_key = {(row["survey"], row["question"]): row for row in performance}
    texts: dict[tuple[str, str], str] = {}
    contexts: dict[tuple[str, str], list[list[str]]] = {}
    for run in runs:
        survey, job_id = str(run["survey"]), str(run["job_id"])
        sdir = require_survey(survey)
        for row in read_jsonl(digital_twin_predictions_path(sdir)):
            if str(row.get("job_id")) != job_id or str(row.get("model_label")) != args.model:
                continue
            target = str(row.get("heldout_question"))
            texts[(survey, target)] = str(row.get("heldout_question_text") or target)
            names = []
            for observed in row.get("observed_answers") or []:
                name = str(observed.get("question_name"))
                texts[(survey, name)] = str(observed.get("question_text") or name)
                names.append(name)
            if names:
                contexts.setdefault((survey, target), []).append(names)
    unique_texts = list(dict.fromkeys(texts.values()))
    embedder = resolve_baseline_embedder(args, args.embedding_model)
    vectors = embedder(unique_texts)
    index = {text_value: np.asarray(vector, dtype=float) for text_value, vector in zip(unique_texts, vectors)}
    for vector in index.values():
        norm = np.linalg.norm(vector)
        if norm:
            vector /= norm
    rows = []
    for key, case_contexts in sorted(contexts.items()):
        if key not in performance_by_key:
            continue
        target_vector = index[texts[key]]
        case_means, case_maxima = [], []
        for names in case_contexts:
            similarities = [float(target_vector @ index[texts[(key[0], name)]]) for name in names]
            case_means.append(sum(similarities) / len(similarities))
            case_maxima.append(max(similarities))
        outcome = performance_by_key[key]
        rows.append({
            "survey": key[0], "question": key[1], "respondent_cases": len(case_contexts),
            "mean_context_similarity": sum(case_means) / len(case_means),
            "max_context_similarity": sum(case_maxima) / len(case_maxima),
            "mean_nll": _numeric(outcome.get("mean_nll")), "accuracy": _numeric(outcome.get("accuracy")),
            "twin_advantage_vs_xgboost": -float(outcome["nll_delta_vs_conditional"]),
        })
    if len(rows) < 3:
        raise ZwillError("invalid_input", "Semantic-proximity analysis needs at least three matched questions.")
    outcomes = ["mean_nll", "accuracy", "twin_advantage_vs_xgboost"]
    estimates = {}
    smooth_rows = []
    surveys = sorted({row["survey"] for row in rows})
    rng = random.Random(args.seed)
    for x_key in ("mean_context_similarity", "max_context_similarity"):
        estimates[x_key] = {}
        for y_key in outcomes:
            slope, residuals = _fixed_effect_slope(rows, x_key, y_key)
            draws = []
            for _ in range(args.n_boot):
                sampled = [rng.choice(surveys) for _survey in surveys]
                boot_rows = [dict(row, survey=f"{index}:{survey}") for index, survey in enumerate(sampled)
                             for row in rows if row["survey"] == survey]
                value, _unused = _fixed_effect_slope(boot_rows, x_key, y_key)
                if value is not None:
                    draws.append(value)
            draws.sort()
            estimates[x_key][y_key] = {
                "coefficient": slope, "lo": draws[int(.025 * (len(draws) - 1))],
                "hi": draws[int(.975 * (len(draws) - 1))], "questions": len(residuals),
                "batteries": len(surveys), "interpretation": "Change in outcome per one-unit cosine similarity, with battery fixed effects.",
            }
            if x_key == "mean_context_similarity" and y_key == "twin_advantage_vs_xgboost":
                residual_plot = [{"similarity_residual": row["x"], "advantage_residual": row["y"]} for row in residuals]
    # A descriptive LOESS curve plus battery-cluster bootstrap band for the two
    # outcomes shown in the paper. The black fixed-effect line remains the
    # estimand; this smoother reveals departures from linearity.
    for y_key in ("mean_nll", "twin_advantage_vs_xgboost"):
        _slope, residuals = _fixed_effect_slope(rows, "mean_context_similarity", y_key)
        x = np.asarray([row["x"] for row in residuals])
        y = np.asarray([row["y"] for row in residuals])
        grid = np.linspace(float(x.min()), float(x.max()), 61)
        fit = _loess(x, y, grid)
        smooth_draws = []
        for _ in range(min(args.n_boot, 2000)):
            sampled = [rng.choice(surveys) for _survey in surveys]
            boot_rows = [dict(row, survey=f"{index}:{survey}") for index, survey in enumerate(sampled)
                         for row in rows if row["survey"] == survey]
            _boot_slope, boot_residuals = _fixed_effect_slope(
                boot_rows, "mean_context_similarity", y_key
            )
            smooth_draws.append(_loess(
                [row["x"] for row in boot_residuals], [row["y"] for row in boot_residuals], grid
            ))
        draw_array = np.asarray(smooth_draws)
        lo, hi = np.quantile(draw_array, [.025, .975], axis=0)
        smooth_rows.extend({
            "outcome": y_key, "similarity_residual": float(point), "fit": fitted,
            "lo": float(lower), "hi": float(upper), "span": .75,
            "bootstrap_clusters": min(args.n_boot, 2000),
        } for point, fitted, lower, upper in zip(grid, fit, lo, hi))
    output = resolve_output_path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "questions.csv", rows)
    _write_csv(output / "residuals.csv", residual_plot)
    _write_csv(output / "smooth.csv", smooth_rows)
    result = {
        "schema_version": 1, "model": args.model, "embedder": args.embedder,
        "embedding_model": args.embedding_model, "question_count": len(rows),
        "battery_count": len(surveys), "estimates": estimates,
        "scope": "Exploratory question-level association; battery fixed effects; battery-cluster bootstrap; no causal interpretation.",
        "artifacts": {"questions": str(output / "questions.csv"), "residuals": str(output / "residuals.csv"),
                      "smooth": str(output / "smooth.csv")},
    }
    write_json(output / "analysis.json", result)
    return envelope("zwill study semantic-proximity", "ok", {**result, "path": str(output / "analysis.json")})


def cmd_study_ingest(args: argparse.Namespace) -> dict[str, Any]:
    """Convert every declared matrix first, then optionally apply the clean batch."""
    from .matrix_ingest import cmd_survey_ingest_matrix, convert_matrix

    source = Path(args.manifest)
    manifest = _load(source)
    root = source.parent
    matrix_defaults = manifest.get("matrix_defaults") or {}
    output = resolve_output_path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    prepared = []
    for record in _study_surveys(manifest):
        matrix = _merge_objects(matrix_defaults, record.get("matrix") or record.get("ingest") or {})
        if not isinstance(matrix, dict):
            raise ZwillError("invalid_input", f"{record['survey']}: study ingest needs a matrix object.")
        variables = {"survey": record["survey"], "source_id": record.get("source_id", record["survey"])}
        metadata_path = Path(str(matrix.get("metadata", "")).format(**variables))
        respondents_path = Path(str(matrix.get("respondents", "")).format(**variables))
        if not metadata_path.is_absolute():
            metadata_path = root / metadata_path
        if not respondents_path.is_absolute():
            respondents_path = root / respondents_path
        metadata = _load(metadata_path)
        overlay = matrix.get("metadata_overlay") or {}
        if isinstance(overlay, str):
            overlay_path = Path(overlay)
            if not overlay_path.is_absolute():
                overlay_path = root / overlay_path
            overlay = _load(overlay_path)
        if not isinstance(overlay, dict):
            raise ZwillError("invalid_input", f"{record['survey']}: metadata_overlay must be an object or file path.")
        merged = _merge_objects(metadata, overlay)
        codebook_path = matrix.get("covariate_codebook")
        if codebook_path:
            codebook_path = Path(str(codebook_path).format(**variables))
            if not codebook_path.is_absolute():
                codebook_path = root / codebook_path
            merged["covariate_codebook"] = _load(codebook_path)
        survey_out = output / str(record["survey"])
        spec_path = survey_out / "conversion_metadata.json"
        write_json(spec_path, merged)
        conversion = convert_matrix(metadata_path=spec_path, respondents_path=respondents_path,
                                    output=survey_out, survey=str(record["survey"]))
        raw_metadata = matrix.get("raw_metadata") or matrix.get("metadata")
        raw_path = Path(str(raw_metadata).format(**variables))
        if not raw_path.is_absolute():
            raw_path = root / raw_path
        prepared.append({
            "survey": str(record["survey"]), "metadata": str(spec_path), "raw_metadata": str(raw_path),
            "respondents": str(respondents_path), "out": str(survey_out), **conversion,
        })
    if args.apply:
        for row in prepared:
            cmd_survey_ingest_matrix(argparse.Namespace(
                survey=row["survey"], metadata=row["metadata"], raw_metadata=row["raw_metadata"],
                respondents=row["respondents"], out=row["out"], apply=True,
            ))
            row["applied"] = True
    payload = {"schema_version": 1, "source_manifest": str(source), "survey_count": len(prepared),
               "applied": bool(args.apply), "surveys": prepared}
    path = output / "manifest.json"
    write_json(path, payload)
    return envelope(
        "zwill study ingest", "ok", {**payload, "path": str(path)},
        next_steps=([f"zwill commit --survey {row['survey']}" for row in prepared] if args.apply
                    else [f"Review {path}, then rerun with --apply."]),
    )


def cmd_study_approve(args: argparse.Namespace) -> dict[str, Any]:
    from .twin_experiments import cmd_twin_experiment_approve, cmd_twin_experiment_validate

    source = Path(args.manifest)
    _payload, plans = _manifest_records(source, key="plans")
    approvals = []
    for record in plans:
        path = Path(str(record["path"]))
        if not path.is_absolute():
            path = source.parent / path
        cmd_twin_experiment_validate(argparse.Namespace(input_path=str(path), survey=None, plan_id=None))
        plan = _load(path)
        if plan.get("approved") is True or (plan.get("approval") or {}).get("approved") is True:
            approvals.append({"plan_id": record["plan_id"], "path": str(path), "existing": True})
            continue
        result = cmd_twin_experiment_approve(argparse.Namespace(
            input_path=str(path), survey=None, approved_by=args.approved_by, note=args.note,
            estimated_cost=None, estimated_time=None,
        ))["data"]
        approvals.append({"plan_id": record["plan_id"], "path": str(path), "approval": result["approval"]})
    return envelope("zwill study approve", "ok", {"plan_count": len(approvals), "plans": approvals})
