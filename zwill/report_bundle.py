from __future__ import annotations

from .cli import *  # noqa: F403


def report_catalog_entry(
    *,
    report_id: str,
    stage: str,
    name: str,
    purpose: str,
    ready: bool,
    inputs: str,
    available: str,
    command: str,
    path: str,
    notes: str = "",
    primary: bool = True,
) -> dict[str, Any]:
    return {
        "report_id": report_id,
        "stage": stage,
        "name": name,
        "purpose": purpose,
        "ready": bool(ready),
        "inputs": inputs,
        "available": available,
        "command": command,
        "suggested_path": path,
        "path_exists": Path(path).exists(),
        "notes": notes,
        "primary": primary,
        "role": "primary" if primary else "supporting",
    }


def build_report_catalog(survey: str) -> dict[str, Any]:
    sdir = require_survey(survey)
    questions = read_jsonl(sdir / "questions.jsonl")
    answers = read_jsonl(sdir / "answers.jsonl")
    respondents = read_jsonl(sdir / "respondents.jsonl")
    probability_rows = read_jsonl(probability_predictions_path(sdir))
    twin_rows = read_jsonl(digital_twin_predictions_path(sdir))
    twin_job_ids = sorted({str(row.get("job_id")) for row in twin_rows if row.get("job_id")})
    probability_job_ids = sorted({str(row.get("job_id")) for row in probability_rows if row.get("job_id")})
    twin_runs = read_twin_run_manifest(sdir)
    ordered_twin_job_ids = []
    for run in twin_runs:
        job_id = str(run.get("job_id"))
        if job_id in twin_job_ids and job_id not in ordered_twin_job_ids:
            ordered_twin_job_ids.append(job_id)
    for job_id in twin_job_ids:
        if job_id not in ordered_twin_job_ids:
            ordered_twin_job_ids.append(job_id)
    questions_by_twin_job: dict[str, set[str]] = defaultdict(set)
    rows_by_twin_job: Counter[str] = Counter()
    for row in twin_rows:
        job_id = str(row.get("job_id"))
        if not job_id:
            continue
        rows_by_twin_job[job_id] += 1
        if row.get("heldout_question"):
            questions_by_twin_job[job_id].add(str(row.get("heldout_question")))
    twin_experiments = read_twin_experiments(sdir)
    recorded_experiment_jobs = [str(row.get("job_id")) for row in twin_experiments if row.get("job_id")]
    practitioner_reports = list(practitioner_reports_dir().glob("*/report.html")) if practitioner_reports_dir().exists() else []
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", survey).strip("_") or "survey"
    bundle_command = f"zwill report build --survey {survey} --path {base}_report/"
    latest_twin_job = ordered_twin_job_ids[0] if ordered_twin_job_ids else "<job_id>"
    comparison_pair = ordered_twin_job_ids[:2]
    best_pair_score = (-1, -1, -1)
    for left_index, left in enumerate(ordered_twin_job_ids):
        for right_index, right in enumerate(ordered_twin_job_ids[left_index + 1 :], left_index + 1):
            overlap = len(questions_by_twin_job.get(left, set()) & questions_by_twin_job.get(right, set()))
            score = (overlap, min(rows_by_twin_job[left], rows_by_twin_job[right]), -(left_index + right_index))
            if score > best_pair_score:
                best_pair_score = score
                comparison_pair = [left, right]
    compare_jobs = ",".join(comparison_pair) if len(comparison_pair) >= 2 else "<job1>,<job2>"
    experiment_jobs = ",".join(recorded_experiment_jobs[:2]) if len(recorded_experiment_jobs) >= 2 else "<recorded_job1>,<recorded_job2>"
    executive_summary_path = Path("artifacts") / f"{base}_executive_summary.html"
    if not executive_summary_path.exists():
        executive_summary_path = Path(f"{base}_executive_summary.html")

    entries = [
        report_catalog_entry(
            report_id="survey-profile",
            stage="survey",
            name="Survey Profile Report",
            purpose="Question text, options, response distributions, and survey data-quality issues before any twin work.",
            ready=bool(questions),
            inputs="Survey questions and answers.",
            available=f"{len(questions)} questions, {len(answers)} answers, {len(respondents)} respondents",
            command=bundle_command,
            path=f"{base}_report/survey-profile.html",
        ),
        report_catalog_entry(
            report_id="probability-results",
            stage="one-shot",
            name="One-Shot Marginals Report",
            purpose="Frontier-model marginal predictions compared with committed empirical survey marginals. Export a frontier-model one-shot analysis job before treating the page as interpreted.",
            ready=bool(probability_rows),
            inputs="Imported probability-job results.",
            available=f"{len(probability_rows)} prediction rows across {len(probability_job_ids)} job ids",
            command=f"zwill prob-results analysis-export --survey {survey} --path {base}_report/one-shot-marginals.html",
            path=f"{base}_report/one-shot-marginals.html",
            notes="The report bundle renders diagnostics. The analysis section should be generated from compact one-shot summary statistics by a report-writing model.",
        ),
        report_catalog_entry(
            report_id="twin-run",
            stage="audit",
            name="Twin Run Report",
            purpose="Inspect one twin job's construction metadata, prompt template, rendered prompts, twin identity, and raw model response.",
            ready=bool(twin_job_ids),
            inputs="One imported digital twin result job.",
            available=f"{len(twin_job_ids)} twin job ids, {len(twin_runs)} run/import records",
            command=f"{bundle_command} --audit-job-id {latest_twin_job}",
            path=f"{base}_report/audit/twin-run-{latest_twin_job}.html",
            primary=False,
        ),
        report_catalog_entry(
            report_id="twin-validation",
            stage="validation",
            name="Twin Validation Report",
            purpose="Main twin validation page: held-out performance, generated interpretation, recommendation, deterministic diagnostics, lift distribution, individual predictive-power test, and rank-order evidence.",
            ready=bool(twin_rows),
            inputs="Imported digital twin predictions with observed held-out answers.",
            available=(str(executive_summary_path) if executive_summary_path.exists() else f"{len(twin_rows)} twin prediction rows available"),
            command=f"zwill twin-results executive-summary-export --survey {survey} --jobs {compare_jobs} --path {base}_report/executive-summary.html",
            path=f"{base}_report/twin-validation.html",
            notes="The report bundle folds deterministic diagnostics and generated executive interpretation into the main twin validation page.",
        ),
        report_catalog_entry(
            report_id="twin-job-comparison",
            stage="comparison",
            name="Twin Job Comparison Report",
            purpose="Side-by-side comparison of two or more imported twin jobs, including actual vs twin-implied marginals and option-level winners.",
            ready=len(twin_job_ids) >= 2,
            inputs="At least two imported digital twin result jobs.",
            available=f"{len(twin_job_ids)} twin job ids",
            command=f"{bundle_command} --jobs {compare_jobs}",
            path=f"{base}_report/twin-comparison.html",
            primary=False,
        ),
        report_catalog_entry(
            report_id="twin-experiment-microdata",
            stage="experiment",
            name="Twin Experiment Microdata Audit",
            purpose="Row-level audit of changes across recorded twin experiment approaches, with observed traits, material, prompts, and predictions.",
            ready=len(recorded_experiment_jobs) >= 2,
            inputs="At least two recorded twin experiments.",
            available=f"{len(twin_experiments)} recorded experiments",
            command=f"zwill twin-experiment microdata --survey {survey} --jobs {experiment_jobs} --path {base}_report/audit/twin-experiment-microdata.html",
            path=f"{base}_report/audit/twin-experiment-microdata.html",
            notes="Record approaches first with `zwill twin-experiment record` if this is not ready.",
            primary=False,
        ),
        report_catalog_entry(
            report_id="practitioner-narrative",
            stage="final",
            name="Practitioner Narrative Report",
            purpose="Model-authored final narrative report over a twin study or benchmark, using recorded artifacts as context.",
            ready=bool(twin_job_ids),
            inputs="One imported twin study job plus report-generation model access.",
            available=f"{len(practitioner_reports)} rendered practitioner report files currently under the project report store",
            command=f"zwill twin-study practitioner-report --survey {survey} --job-id {latest_twin_job} --path {base}_practitioner_report.html",
            path=f"{base}_practitioner_report.html",
        ),
    ]
    return {
        "survey": survey,
        "summary": {
            "questions": len(questions),
            "answers": len(answers),
            "respondents": len(respondents),
            "open_quarantine_issues": len(open_quarantine_issues(sdir)),
            "probability_prediction_rows": len(probability_rows),
            "probability_job_count": len(probability_job_ids),
            "twin_prediction_rows": len(twin_rows),
            "twin_job_count": len(twin_job_ids),
            "twin_experiment_count": len(twin_experiments),
            "ready_report_count": sum(1 for entry in entries if entry["ready"]),
        },
        "reports": entries,
    }


def cmd_report_list(args: argparse.Namespace) -> None:
    payload = build_report_catalog(args.survey)
    if args.format == "json":
        output = json.dumps(payload, indent=2)
        if args.path:
            Path(args.path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.path).write_text(output + "\n")
        print(output)
        return
    table = Table(title=f"{args.survey} report catalog")
    for column in ["stage", "report", "ready", "available", "suggested command"]:
        table.add_column(column)
    for entry in payload["reports"]:
        command = entry["command"]
        if len(command) > 96:
            command = command[:93] + "..."
        table.add_row(
            str(entry["stage"]),
            str(entry["name"]),
            "yes" if entry["ready"] else "no",
            str(entry["available"]),
            command,
        )
    Console().print(table)


def report_bundle_default_dir(survey: str) -> Path:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", survey).strip("_") or "survey"
    return Path(f"{base}_report")


def bundle_rel_link(path: str | Path, base: Path) -> str:
    return os.path.relpath(Path(path).resolve(), start=base.resolve()).replace(os.sep, "/")


def report_bundle_page(
    *,
    page_id: str,
    title: str,
    stage: str,
    status: str,
    description: str,
    path: Path | None = None,
    data_path: Path | None = None,
    inputs: str = "",
    next_step: str = "",
    notes: str = "",
    generated_files: list[Path] | None = None,
    primary: bool = True,
) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "title": title,
        "stage": stage,
        "status": status,
        "description": description,
        "path": str(path) if path else None,
        "data_path": str(data_path) if data_path else None,
        "inputs": inputs,
        "next_step": next_step,
        "notes": notes,
        "generated_files": [str(p) for p in (generated_files or [])],
        "primary": primary,
    }


def write_bundle_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def compact_twin_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact = dict(payload)
    rows = compact.pop("rows", [])
    compact["row_count"] = len(rows) if isinstance(rows, list) else 0
    compact["raw_prediction_rows_included"] = False
    return compact


def copy_bundle_file(source: Path, destination: Path) -> str | None:
    if not source.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(destination)


def copy_generated_report_provenance(generation: dict[str, Any] | None, output_dir: Path, *, prefix: str) -> list[Path]:
    if not generation or generation.get("mode") != "imported_results":
        return []
    report_id = str(generation.get("report_id") or "").strip()
    if not report_id:
        return []
    paths = default_practitioner_report_paths(report_id)
    destination_dir = output_dir / "analysis" / "generated-reports" / f"{prefix}-{report_id}"
    copied: list[Path] = []
    for name, source in [
        ("job.edsl.json", paths["job"]),
        ("prompt.md", paths["prompt"]),
        ("context.json", paths["context"]),
        ("import.json", paths["import"]),
        ("report.md", paths["markdown"]),
    ]:
        copied_path = copy_bundle_file(source, destination_dir / name)
        if copied_path:
            copied.append(Path(copied_path))
    return copied


def report_stage_status(*, ready: bool, label: str, files: list[str], missing: list[str] | None = None, next_step: str = "") -> dict[str, Any]:
    return {
        "status": "ready" if ready else "blocked",
        "label": label,
        "files": files,
        "missing": missing or [],
        "next_step": next_step,
    }


def render_report_bundle_checklist(stage_manifest: dict[str, Any]) -> str:
    survey = str(stage_manifest.get("survey") or "")
    lines = [
        f"# {survey} Report Bundle Checklist" if survey else "# Report Bundle Checklist",
        "",
        "This file is a read-only workflow view. `.zwill` remains the system of record for survey state, approvals, imports, generated report metadata, and result manifests.",
        "",
        "## Stages",
        "",
    ]
    for stage_id, stage in (stage_manifest.get("stages") or {}).items():
        status = str(stage.get("status") or "")
        label = str(stage.get("label") or stage_id)
        lines.append(f"- [{'x' if status == 'ready' else ' '}] {label} (`{stage_id}`): {status}")
        missing = stage.get("missing") or []
        if missing:
            lines.append(f"  - Missing: {', '.join(str(item) for item in missing)}")
        next_step = stage.get("next_step")
        if next_step:
            lines.append(f"  - Next: `{next_step}`")
    pages = stage_manifest.get("pages") or []
    if pages:
        lines.extend(["", "## Pages", ""])
        for page in pages:
            role = "primary" if page.get("primary", True) else "supporting"
            status = str(page.get("status") or "")
            title = str(page.get("title") or page.get("page_id") or "")
            path = page.get("path")
            lines.append(f"- [{'x' if status == 'ready' else ' '}] {title} ({role}): {status}")
            if path:
                lines.append(f"  - Path: `{path}`")
            if page.get("next_step"):
                lines.append(f"  - Next: `{page['next_step']}`")
    commands = stage_manifest.get("canonical_commands") or []
    if commands:
        lines.extend(["", "## Canonical Commands", ""])
        for command in commands:
            lines.append(f"- `{command}`")
    lines.append("")
    return "\n".join(lines)


def page_is_ready(manifest: dict[str, Any], page_id: str) -> bool:
    return any(page.get("page_id") == page_id and page.get("status") == "ready" for page in manifest.get("pages", []))


def imported_generation_ready(generation: dict[str, Any] | None) -> bool:
    return bool(generation) and generation.get("mode") == "imported_results"


def write_report_stage_artifacts(output_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    facts_dir = output_dir / "facts"
    analysis_dir = output_dir / "analysis"
    rendered_dir = output_dir / "report"
    data_dir = output_dir / "data"
    facts_files = [
        path
        for path in [
            copy_bundle_file(data_dir / "report-catalog.json", facts_dir / "report-catalog.json"),
            copy_bundle_file(data_dir / "survey-profile.json", facts_dir / "survey-profile.json"),
        ]
        if path
    ]
    analysis_files = []
    for name in [
        "one-shot-marginals.json",
        "one-shot-analysis.md",
        "one-shot-coverage.json",
        "twin-validation.json",
        "joint-structure.json",
        "subgroup-marginals.json",
        "conditional-consistency.json",
        "executive-summary.md",
        "validation-diagnostics.json",
        "twin-comparison.json",
    ]:
        copied = copy_bundle_file(data_dir / name, analysis_dir / name)
        if copied:
            analysis_files.append(copied)
    for path in sorted(output_dir.glob("executive-summary_*")):
        if path.is_file():
            copied = copy_bundle_file(path, analysis_dir / path.name)
            if copied:
                analysis_files.append(copied)
    report_files = []
    for path in sorted(output_dir.glob("*.html")):
        copied = copy_bundle_file(path, rendered_dir / path.name)
        if copied:
            report_files.append(copied)
    for page in manifest.get("pages", []):
        for raw_path in page.get("generated_files", []):
            source = Path(str(raw_path))
            if not source.is_absolute():
                source = output_dir / source
            try:
                relative = source.relative_to(output_dir)
            except ValueError:
                continue
            if not source.exists() or source.is_dir() or relative.parts[:1] == ("report",):
                continue
            if source.suffix.lower() == ".html":
                continue
            copied = copy_bundle_file(source, rendered_dir / relative)
            if copied:
                report_files.append(copied)
    for path in sorted((output_dir / "audit").glob("*.html")) if (output_dir / "audit").exists() else []:
        copied = copy_bundle_file(path, rendered_dir / "audit" / path.name)
        if copied:
            report_files.append(copied)
    survey = str(manifest.get("survey", ""))
    one_shot_page_ready = page_is_ready(manifest, "one-shot-marginals")
    twin_pages_ready = page_is_ready(manifest, "twin-validation")
    one_shot_generation = manifest.get("one_shot_analysis_generation") or {}
    executive_generation = (manifest.get("executive_summary") or {}).get("generation") or {}
    one_shot_generated_ready = imported_generation_ready(one_shot_generation)
    executive_generated_ready = imported_generation_ready(executive_generation)
    one_shot_generated_file = analysis_dir / "one-shot-analysis.md"
    executive_generated_file = analysis_dir / "executive-summary.md"
    one_shot_generated_next = (
        f"zwill prob-results analysis-export --survey {survey} --path {output_dir / 'one-shot-marginals.html'}"
        if survey
        else "Run `zwill prob-results analysis-export`, then run/import/render the report-writing job."
    )
    executive_generated_next = (
        f"zwill twin-results executive-summary-export --survey {survey} --path {output_dir / 'executive-summary.html'}"
        if survey
        else "Run `zwill twin-results executive-summary-export`, then run/import/render the report-writing job."
    )
    required_generated = []
    if one_shot_page_ready:
        required_generated.append(
            {
                "id": "one-shot-analysis",
                "label": "Generated One-Shot Analysis",
                "ready": one_shot_generated_ready,
                "file": str(one_shot_generated_file),
                "missing": "frontier-model one-shot marginal analysis Markdown",
                "next_step": "" if one_shot_generated_ready else one_shot_generated_next,
            }
        )
    if twin_pages_ready:
        required_generated.append(
            {
                "id": "executive-twin-validation",
                "label": "Generated Executive Twin Validation",
                "ready": executive_generated_ready,
                "file": str(executive_generated_file),
                "missing": "frontier-model executive twin validation Markdown",
                "next_step": "" if executive_generated_ready else executive_generated_next,
            }
        )
    missing_generated = [item["missing"] for item in required_generated if not item["ready"]]
    generated_files = [
        item["file"]
        for item in required_generated
        if item["ready"] and Path(item["file"]).exists()
    ]
    generated_analysis_ready = not missing_generated
    first_generated_next = next((item["next_step"] for item in required_generated if not item["ready"] and item.get("next_step")), "")
    stages = {
        "facts": report_stage_status(
            ready=bool(facts_files),
            label="Facts",
            files=facts_files,
            missing=[] if facts_files else ["survey-profile facts"],
            next_step="Review facts/survey-profile.json and survey-profile.html.",
        ),
        "analysis": report_stage_status(
            ready=bool(analysis_files),
            label="Deterministic Analysis",
            files=analysis_files,
            missing=[] if analysis_files else ["deterministic diagnostics"],
            next_step="Import one-shot or twin results, then rerun `zwill report analyze`.",
        ),
        "generated_analysis": report_stage_status(
            ready=generated_analysis_ready,
            label="Generated Interpretations",
            files=generated_files if generated_analysis_ready else generated_files,
            missing=missing_generated,
            next_step="" if generated_analysis_ready else first_generated_next,
        ),
        "report_preview": report_stage_status(
            ready=bool(report_files),
            label="Report Preview",
            files=report_files,
            missing=[] if report_files else ["rendered report HTML"],
            next_step="Open report/index.html or index.html.",
        ),
        "final_report": report_stage_status(
            ready=bool(report_files) and generated_analysis_ready,
            label="Final Report",
            files=report_files if generated_analysis_ready else [],
            missing=missing_generated,
            next_step="Ready." if generated_analysis_ready else first_generated_next,
        ),
    }
    page_views = [
        {
            "page_id": page.get("page_id"),
            "title": page.get("title"),
            "stage": page.get("stage"),
            "status": page.get("status"),
            "primary": page.get("primary", True),
            "path": page.get("path"),
            "data_path": page.get("data_path"),
            "next_step": page.get("next_step"),
        }
        for page in manifest.get("pages", [])
    ]
    canonical_commands = []
    if survey:
        canonical_commands.extend(
            [
                f"zwill report list --survey {survey}",
                f"zwill report build --survey {survey} --path {output_dir}",
                f"zwill report render --survey {survey} --path {output_dir} --final",
            ]
        )
    stage_manifest = {
        "survey": manifest.get("survey"),
        "generated_at": manifest.get("generated_at"),
        "output_dir": str(output_dir),
        "facts_dir": str(facts_dir),
        "analysis_dir": str(analysis_dir),
        "report_dir": str(rendered_dir),
        "report_catalog_path": str(data_dir / "report-catalog.json"),
        "report_manifest_path": str(output_dir / "report-manifest.json"),
        "checklist_path": str(output_dir / "CHECKLIST.md"),
        "stages": stages,
        "pages": page_views,
        "canonical_commands": canonical_commands,
        "required_generated_interpretations": required_generated,
    }
    checklist_markdown = render_report_bundle_checklist(stage_manifest)
    (output_dir / "CHECKLIST.md").write_text(checklist_markdown)
    (rendered_dir / "CHECKLIST.md").parent.mkdir(parents=True, exist_ok=True)
    (rendered_dir / "CHECKLIST.md").write_text(checklist_markdown)
    write_bundle_json(output_dir / "stage-manifest.json", stage_manifest)
    write_bundle_json(facts_dir / "facts-manifest.json", stage_manifest)
    write_bundle_json(analysis_dir / "analysis-manifest.json", stage_manifest)
    return stage_manifest


def render_report_bundle_index(payload: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        import html

        return html.escape(str(value), quote=True)

    output_dir = Path(payload["output_dir"])
    generated_at = esc(payload.get("generated_at", ""))
    survey_id = str(payload.get("survey", ""))
    survey_title, _raw_title = report_display_title(survey_id)
    survey = esc(survey_title)
    summary = payload.get("summary", {})
    executive = payload.get("executive_summary") or {}
    executive_block = ""
    if executive:
        metrics = executive.get("metrics") or {}
        lift = executive.get("lift") or {}
        individual = executive.get("individual_signal") or {}
        pairwise = executive.get("pairwise_ordering") or {}
        executive_page = next((page for page in payload.get("pages", []) if page.get("page_id") == "executive-summary"), {})
        executive_href = bundle_rel_link(executive_page.get("path"), output_dir) if executive_page.get("path") else ""
        validation_page = next((page for page in payload.get("pages", []) if page.get("page_id") == "twin-validation"), {})
        validation_href = bundle_rel_link(validation_page.get("path"), output_dir) if validation_page.get("path") else ""
        executive_block = f"""
    <section class="decision-summary">
      <div class="stage">Executive Summary</div>
      <h2>Digital Twin Validation Readout</h2>
      <p>This bundle has held-out validation results. Use twins for exploratory and directional work when the validation target family matches the intended use; use fresh validation for exact estimates or respondent-level action.</p>
      <table>
        <tbody>
          <tr><th>Validation rows</th><td class="num">{esc(int(metrics.get("row_count", 0)))}</td><th>Held-out questions</th><td class="num">{esc(int(metrics.get("question_count", 0)))}</td></tr>
          <tr><th>Mean p(actual)</th><td class="num">{float(metrics.get("mean_probability_actual", 0.0)):.1%}</td><th>Uniform p(actual)</th><td class="num">{float(metrics.get("mean_uniform_probability_actual", 0.0)):.1%}</td></tr>
          <tr><th>Rows above uniform</th><td class="num">{float(lift.get("share_above_1", 0.0)):.0%}</td><th>Mean lift vs uniform</th><td class="num">{float(lift.get("mean_lift", 0.0)):.2f}x</td></tr>
          <tr><th>Individual-signal p-value</th><td class="num">{float(individual.get("p_value_mean_p_actual", 0.0)):.5f}</td><th>Option-pair ordering accuracy</th><td class="num">{float((pairwise.get("summary") or {}).get("pairwise_order_accuracy", 0.0)):.0%}</td></tr>
        </tbody>
      </table>
      <p>{f'<a class="button" href="{esc(executive_href)}">Open full executive summary</a>' if executive_href else ''} {f'<a class="button secondary" href="{esc(validation_href)}">Open technical validation</a>' if validation_href else ''}</p>
    </section>"""
    primary_pages = [page for page in payload.get("pages", []) if page.get("primary", True)]
    secondary_pages = [page for page in payload.get("pages", []) if not page.get("primary", True)]
    items = []
    for index, page in enumerate(primary_pages, 1):
        status = str(page.get("status", "not_ready"))
        status_label = status.replace("_", " ").title()
        path = page.get("path")
        href = bundle_rel_link(path, output_dir) if path else ""
        title = esc(page.get("title", page.get("page_id", "")))
        description = esc(page.get("description", ""))
        stage = esc(page.get("stage", ""))
        inputs = esc(page.get("inputs", ""))
        next_step = esc(page.get("next_step", ""))
        notes = esc(page.get("notes", ""))
        link = f'<a class="button" href="{esc(href)}">Open</a>' if path else ""
        items.append(
            f"""
      <li class="step {esc(status)}">
        <div class="step-number">{index}</div>
        <div class="step-body">
          <div class="step-head">
          <div>
            <div class="stage">{stage}</div>
            <h2>{title}</h2>
          </div>
          <span class="status">{esc(status_label)}</span>
        </div>
        <p>{description}</p>
        <dl>
          <dt>Inputs</dt><dd>{inputs or "Available survey state"}</dd>
          <dt>Next</dt><dd>{next_step or ("Open the page." if path else "No action needed.")}</dd>
        </dl>
        {f'<p class="notes">{notes}</p>' if notes else ''}
        {link}
        </div>
      </li>"""
        )
    secondary_items = []
    for page in secondary_pages:
        status = str(page.get("status", "not_ready"))
        path = page.get("path")
        href = bundle_rel_link(path, output_dir) if path else ""
        secondary_items.append(
            f"""
      <li class="{esc(status)}">
        <div>
          <b>{esc(page.get("title", page.get("page_id", "")))}</b>
          <span>{esc(page.get("description", ""))}</span>
        </div>
        {f'<a class="button secondary" href="{esc(href)}">Open</a>' if path else f'<span class="status">{esc(status.replace("_", " ").title())}</span>'}
      </li>"""
        )
    secondary_block = ""
    if secondary_items:
        secondary_block = f"""
    <section class="panel secondary-reports">
      <div class="stage">Supporting Artifacts</div>
      <h2>Linked From The Main Reports</h2>
      <p>These are generated for auditability and comparisons, but they are not separate top-level reports.</p>
      <ul>{''.join(secondary_items)}</ul>
    </section>"""
    stale_items = [f"<li>{esc(bundle_rel_link(path, output_dir))}</li>" for path in payload.get("stale_files", [])]
    stale_block = ""
    if stale_items:
        stale_block = f"""
      <section class="panel">
        <h2>Stale Generated Files</h2>
        <p>These files were generated by an earlier bundle build but were not regenerated in this run. They were left in place.</p>
        <ul>{''.join(stale_items)}</ul>
      </section>"""
    data = escape_script_text(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{survey} Report Bundle</title>
  <style>
    {EP_REPORT_CSS}
    header {{ margin-bottom: 1.5rem; }}
    main {{ padding-bottom: 2rem; }}
    .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:18px; }}
    .stat {{ border:1px solid var(--ep-border); border-radius:8px; padding:12px; background:var(--ep-light-gray); }}
    .stat strong {{ display:block; font-size:22px; }}
    .workflow {{ list-style:none; padding:0; margin:1.5rem 0; display:grid; gap:12px; }}
    .step,.panel,.decision-summary {{ background:#fff; border:1px solid var(--ep-border); border-radius:8px; padding:16px; }}
    .decision-summary {{ border-left:4px solid var(--ep-green); margin-bottom:1.5rem; }}
    .decision-summary h2 {{ margin-top:0; }}
    .step {{ display:grid; grid-template-columns:34px 1fr; gap:12px; }}
    .step-number {{ width:28px; height:28px; border-radius:999px; background:var(--ep-green); display:flex; align-items:center; justify-content:center; font-weight:700; color:#fff; }}
    .step-head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }}
    .step h2 {{ margin:0; border-bottom:0; padding-bottom:0; color:var(--ep-dark); font-size:1.35rem; }}
    .stage {{ color:var(--ep-gray); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }}
    .status {{ border:1px solid var(--ep-border); border-radius:999px; padding:3px 8px; font-size:12px; white-space:nowrap; }}
    .ready .status {{ color:#12643b; border-color:#9ac6ad; background:#edf8f1; }}
    .not_ready .status {{ color:#8a5a00; border-color:#dbc17f; background:#fff8e6; }}
    .stale .status {{ color:#7a322b; border-color:#dda39e; background:#fff0ef; }}
    dl {{ display:grid; grid-template-columns:58px 1fr; gap:4px 10px; margin:12px 0; }}
    dt {{ color:var(--ep-gray); }}
    dd {{ margin:0; }}
    .button {{ display:inline-block; border:1px solid var(--ep-green); color:var(--ep-green); background:#fff; border-radius:6px; padding:6px 10px; text-decoration:none; }}
    .button.secondary {{ border-color:var(--ep-border); color:var(--ep-dark); }}
    .notes {{ color:var(--ep-gray); font-size:13px; }}
    .secondary-reports ul {{ list-style:none; margin:12px 0 0; display:grid; gap:10px; }}
    .secondary-reports li {{ border:1px solid var(--ep-border); border-radius:8px; padding:12px; display:flex; justify-content:space-between; gap:14px; align-items:center; }}
    .secondary-reports span {{ display:block; color:var(--ep-gray); }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    @media (max-width: 760px) {{ .step-head {{ display:block; }} .status {{ display:inline-block; margin-top:8px; }} }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <header>
    <h1>{survey} Report Bundle</h1>
    <div class="subtle">Survey id: <code>{esc(survey_id)}</code></div>
    <div class="subtle">Generated {generated_at}. Open this page first; it links to each report page that is ready.</div>
    <div class="stats">
      <div class="stat"><span>Questions</span><strong>{esc(summary.get("questions", 0))}</strong></div>
      <div class="stat"><span>Respondents</span><strong>{esc(summary.get("respondents", 0))}</strong></div>
      <div class="stat"><span>Open quarantine</span><strong>{esc(summary.get("open_quarantine_issues", 0))}</strong></div>
      <div class="stat"><span>One-shot rows</span><strong>{esc(summary.get("probability_prediction_rows", 0))}</strong></div>
      <div class="stat"><span>Twin rows</span><strong>{esc(summary.get("twin_prediction_rows", 0))}</strong></div>
    </div>
  </header>
  <main>
    {executive_block}
    <ol class="workflow">
      {''.join(items)}
    </ol>
    {secondary_block}
    {stale_block}
  </main>
  <script type="application/json" id="report-bundle-data">{data}</script>
</body>
</html>
"""


def build_report_bundle(args: argparse.Namespace) -> dict[str, Any]:
    survey = args.survey
    sdir = require_survey(survey)
    output_dir = Path(args.path) if args.path else report_bundle_default_dir(survey)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    audit_dir = output_dir / "audit"
    manifest_path = output_dir / "report-manifest.json"
    previous_manifest = read_json(manifest_path, {})
    previous_files = set()
    for page in previous_manifest.get("pages", []):
        for raw_path in page.get("generated_files", []):
            previous_path = Path(str(raw_path))
            if not previous_path.is_absolute():
                previous_path = output_dir / previous_path
            previous_files.add(str(previous_path))

    catalog = build_report_catalog(survey)
    catalog_path = data_dir / "report-catalog.json"
    write_bundle_json(catalog_path, catalog)
    pages: list[dict[str, Any]] = []
    generated: set[str] = {str(catalog_path)}
    one_shot_analysis_generation: dict[str, Any] | None = None

    def add_page(page: dict[str, Any]) -> None:
        pages.append(page)
        for path in page.get("generated_files", []):
            generated.add(str(path))

    survey_payload = build_survey_report_payload(survey, sdir)
    survey_html_path = output_dir / "survey-profile.html"
    survey_data_path = data_dir / "survey-profile.json"
    survey_html = render_survey_report_html(survey_payload).replace("Survey Report", "Survey Profile")
    survey_html_path.write_text(survey_html)
    write_bundle_json(survey_data_path, survey_payload)
    add_page(
        report_bundle_page(
            page_id="survey-profile",
            title="Survey Profile",
            stage="survey",
            status="ready",
            description="Question text, response options, observed distributions, missingness, respondent counts, and data-quality issues.",
            path=survey_html_path,
            data_path=survey_data_path,
            inputs=f"{catalog['summary']['questions']} questions, {catalog['summary']['answers']} answers",
            next_step="Review this page before running model-based analyses.",
            generated_files=[survey_html_path, survey_data_path],
        )
    )

    truth_path = sdir / "committed" / "truth_marginals.json"
    probability_rows = read_jsonl(probability_predictions_path(sdir))
    if getattr(args, "probability_job_id", None):
        probability_rows = [row for row in probability_rows if row.get("job_id") == args.probability_job_id]
    if getattr(args, "probability_model", None):
        probability_rows = [
            row
            for row in probability_rows
            if row.get("model") == args.probability_model or row.get("model_label") == args.probability_model
        ]
    if probability_rows and truth_path.exists():
        probability_payload = build_probability_report(probability_rows, read_json(truth_path, {}))
        probability_html_path = output_dir / "one-shot-marginals.html"
        probability_data_path = data_dir / "one-shot-marginals.json"
        probability_markdown_path = data_dir / "one-shot-analysis.md"
        generated_one_shot = find_imported_one_shot_analysis_report(
            survey=survey,
            job_id=getattr(args, "probability_job_id", None),
            model=getattr(args, "probability_model", None),
            questions=sorted({str(row.get("question")) for row in probability_payload["rows"] if row.get("question")}),
        )
        if generated_one_shot:
            one_shot_analysis_generation = generated_one_shot.get("generation") or {}
            probability_markdown_path.write_text(generated_one_shot["markdown"].strip() + "\n")
        coverage_payload = build_probability_coverage_payload(sdir, probability_rows)
        coverage_html_path = output_dir / "one-shot-coverage.html"
        coverage_data_path = data_dir / "one-shot-coverage.json"
        probability_html = render_probability_report_html(
            survey,
            probability_payload["rows"],
            probability_payload["summary"],
            generated_analysis_markdown=generated_one_shot.get("markdown") if generated_one_shot else None,
            generation=generated_one_shot.get("generation") if generated_one_shot else None,
        )
        probability_html = insert_before_main_close(probability_html, render_probability_coverage_section(coverage_payload))
        probability_html_path.write_text(probability_html)
        write_bundle_json(probability_data_path, probability_payload)
        coverage_html_path.write_text(render_probability_coverage_html(coverage_payload))
        write_bundle_json(coverage_data_path, coverage_payload)
        generated_files = [probability_html_path, probability_data_path, coverage_html_path, coverage_data_path]
        if generated_one_shot:
            generated_files.append(probability_markdown_path)
            generated_files.extend(
                copy_generated_report_provenance(
                    generated_one_shot.get("generation"),
                    output_dir,
                    prefix="one-shot",
                )
            )
        add_page(
            report_bundle_page(
                page_id="one-shot-marginals",
                title="One-Shot Marginals",
                stage="one-shot",
                status="ready",
                description="Frontier-model aggregate marginal predictions compared with committed empirical survey marginals.",
                path=probability_html_path,
                data_path=probability_data_path,
                inputs=f"{len(probability_rows)} imported probability prediction rows",
                next_step=(
                    "Use this as an aggregate baseline before individual twin validation."
                    if generated_one_shot
                    else f"Run `zwill prob-results analysis-export --survey {survey} --path {probability_html_path}`, then run/import/render the report-writing job."
                ),
                notes="Generated one-shot analysis imported." if generated_one_shot else "Generated one-shot analysis has not been imported yet.",
                generated_files=generated_files,
            )
        )
    else:
        missing = "Imported probability-job results"
        if probability_rows and not truth_path.exists():
            missing = "Committed truth marginals"
        add_page(
            report_bundle_page(
                page_id="one-shot-marginals",
                title="One-Shot Marginals",
                stage="one-shot",
                status="not_ready",
                description="Frontier-model aggregate marginal predictions compared with empirical survey marginals.",
                inputs=missing,
                next_step=f"Run/import one-shot probability results, then rerun `zwill report build --survey {survey} --path {output_dir}`.",
            )
        )

    twin_rows = read_jsonl(digital_twin_predictions_path(sdir))
    selected_job_ids = selected_twin_result_job_ids(args)
    if selected_job_ids:
        selected_job_set = set(selected_job_ids)
        twin_rows = [row for row in twin_rows if row.get("job_id") in selected_job_set]
    if getattr(args, "model", None):
        twin_rows = [row for row in twin_rows if row.get("model") == args.model or row.get("model_label") == args.model]
    twin_job_ids = sorted({str(row.get("job_id")) for row in twin_rows if row.get("job_id")})
    if twin_rows:
        executive_result = {}
        twin_payload = build_twin_report(twin_rows)
        attach_twin_set_descriptions(sdir, twin_payload, twin_rows)
        twin_payload["health"] = {"job_ids": twin_job_ids}
        twin_html_path = output_dir / "twin-validation.html"
        twin_data_path = data_dir / "twin-validation.json"

        executive_path = output_dir / "executive-summary.html"
        executive_markdown_path = data_dir / "executive-summary.md"
        heldout_questions_for_report = sorted({str(row.get("heldout_question")) for row in twin_rows if row.get("heldout_question")})
        generated_executive = find_imported_executive_summary_report(
            survey=survey,
            job_ids=twin_job_ids,
            model=getattr(args, "model", None),
            questions=heldout_questions_for_report,
        )
        executive_result = build_executive_summary(
            twin_rows,
            survey=survey,
            path=executive_path,
            markdown_path=executive_markdown_path,
            simulations=getattr(args, "permutations", DEFAULT_REPORT_PERMUTATIONS),
            seed=getattr(args, "seed", 20260701),
            generated_markdown=generated_executive.get("markdown") if generated_executive else None,
            generation=generated_executive.get("generation") if generated_executive else None,
        )
        executive_generated = [Path(path) for path in executive_result.get("artifacts", {}).values()]
        executive_generated.extend([executive_path, executive_markdown_path])
        executive_generated.extend(
            copy_generated_report_provenance(
                generated_executive.get("generation") if generated_executive else None,
                output_dir,
                prefix="twin-executive",
            )
        )
        diagnostics_html_path = output_dir / "validation-diagnostics.html"
        diagnostics_data_path = data_dir / "validation-diagnostics.json"
        diagnostics_payload = {"survey": survey, "artifacts": executive_result.get("artifacts", {})}
        diagnostics_html_path.write_text(
            render_validation_diagnostics_html(
                survey=survey,
                artifacts=executive_result.get("artifacts", {}),
                output_dir=output_dir,
            )
        )
        write_bundle_json(diagnostics_data_path, diagnostics_payload)
        joint_structure_path = data_dir / "joint-structure.json"
        subgroup_marginals_path = data_dir / "subgroup-marginals.json"
        conditional_consistency_path = data_dir / "conditional-consistency.json"
        write_bundle_json(joint_structure_path, twin_payload.get("diagnostics", {}).get("joint_structure", {}))
        write_bundle_json(subgroup_marginals_path, twin_payload.get("diagnostics", {}).get("subgroup_marginals", {}))
        write_bundle_json(conditional_consistency_path, twin_payload.get("diagnostics", {}).get("conditional_consistency", {}))
        twin_html = render_twin_summary_report_html(
            survey,
            twin_payload["rows"],
            twin_payload["summary"],
            twin_payload.get("diagnostics"),
            twin_payload.get("health"),
        )
        twin_html = insert_after_main_open(twin_html, render_generated_executive_interpretation_section(generated_executive))
        twin_html = insert_before_main_close(
            twin_html,
            render_validation_diagnostics_section(
                survey=survey,
                artifacts=executive_result.get("artifacts", {}),
                output_dir=output_dir,
            ),
        )
        twin_html = insert_before_main_close(
            twin_html,
            render_twin_value_diagnostics_section(twin_payload.get("diagnostics", {})),
        )
        twin_html_path.write_text(twin_html)
        write_bundle_json(twin_data_path, compact_twin_report_payload(twin_payload))
        twin_generated_files = [
            twin_html_path,
            twin_data_path,
            diagnostics_html_path,
            diagnostics_data_path,
            joint_structure_path,
            subgroup_marginals_path,
            conditional_consistency_path,
            *executive_generated,
        ]
        add_page(
            report_bundle_page(
                page_id="twin-validation",
                title="Twin Validation",
                stage="validation",
                status="ready",
                description="Held-out twin performance, generated interpretation, uniform baseline comparisons, calibration diagnostics, marginal fit, and supporting validation diagnostics.",
                path=twin_html_path,
                data_path=twin_data_path,
                inputs=f"{len(twin_rows)} twin prediction rows across {len(twin_job_ids)} job ids",
                next_step=(
                    "Use this as the main technical validation and decision-readout page."
                    if generated_executive
                    else f"Run `zwill twin-results executive-summary-export --survey {survey} --jobs {','.join(twin_job_ids)} --path {executive_path}`, then run/import/render the report-writing job."
                ),
                notes="Generated twin validation interpretation imported." if generated_executive else "Generated twin validation interpretation has not been imported yet.",
                generated_files=twin_generated_files,
            )
        )

        audit_job_id = getattr(args, "audit_job_id", None) or (selected_job_ids[0] if selected_job_ids else twin_job_ids[0])
        try:
            audit_payload = build_twin_run_report_payload(sdir, survey, audit_job_id, example_limit=getattr(args, "example_limit", 6))
            audit_html_path = audit_dir / f"twin-run-{audit_job_id}.html"
            audit_data_path = data_dir / f"twin-run-{audit_job_id}.json"
            audit_html_path.parent.mkdir(parents=True, exist_ok=True)
            audit_html_path.write_text(render_twin_run_report_html(audit_payload))
            write_bundle_json(audit_data_path, audit_payload)
            add_page(
                report_bundle_page(
                    page_id="twin-run-audit",
                    title="Twin Run Audit",
                    stage="audit",
                    status="ready",
                    description="Construction metadata, held-out questions, prompt templates, rendered prompt examples, twin identity, and raw model responses.",
                    path=audit_html_path,
                    data_path=audit_data_path,
                    inputs=f"Imported twin job {audit_job_id}",
                    next_step="Use this to inspect leakage and prompt construction details.",
                    generated_files=[audit_html_path, audit_data_path],
                    primary=False,
                )
            )
        except ZwillError as exc:
            add_page(
                report_bundle_page(
                    page_id="twin-run-audit",
                    title="Twin Run Audit",
                    stage="audit",
                    status="not_ready",
                    description="Construction metadata, prompt templates, rendered prompt examples, and raw model responses.",
                    inputs=f"Twin run metadata for {audit_job_id}",
                    next_step="Import or rerun the twin job with stored raw Results, then rebuild the report bundle.",
                    notes=exc.message,
                    primary=False,
                )
            )

        if len(twin_job_ids) >= 2:
            comparison_payload = build_twin_job_comparison_report_payload(sdir, survey, twin_job_ids, model=getattr(args, "model", None))
            comparison_html_path = output_dir / "twin-comparison.html"
            comparison_data_path = data_dir / "twin-comparison.json"
            comparison_html_path.write_text(render_twin_job_comparison_report_html(comparison_payload))
            write_bundle_json(comparison_data_path, comparison_payload)
            add_page(
                report_bundle_page(
                    page_id="twin-comparison",
                    title="Twin Comparison",
                    stage="comparison",
                    status="ready",
                    description="Side-by-side comparison of twin jobs, including actual versus twin-implied marginals and option-level winners.",
                    path=comparison_html_path,
                    data_path=comparison_data_path,
                    inputs=f"{len(twin_job_ids)} imported twin job ids",
                    next_step="Use this when choosing between construction approaches.",
                    generated_files=[comparison_html_path, comparison_data_path],
                    primary=False,
                )
            )
        else:
            add_page(
                report_bundle_page(
                    page_id="twin-comparison",
                    title="Twin Comparison",
                    stage="comparison",
                    status="not_ready",
                    description="Side-by-side comparison of two or more twin jobs.",
                    inputs="At least two imported twin jobs",
                    next_step="Import another twin job or record another approach, then rebuild the report bundle.",
                    primary=False,
                )
            )
        supporting_section = render_twin_supporting_artifacts_section(pages, output_dir)
        if supporting_section and twin_html_path.exists():
            twin_html_path.write_text(insert_before_main_close(twin_html_path.read_text(), supporting_section))
    else:
        for page_id, title, stage, description in [
            ("twin-validation", "Twin Validation", "validation", "Held-out twin performance, calibration diagnostics, and marginal fit."),
            ("twin-run-audit", "Twin Run Audit", "audit", "Prompt, construction, and raw-response audit for one twin job."),
            ("twin-comparison", "Twin Comparison", "comparison", "Side-by-side comparison of two or more twin jobs."),
        ]:
            add_page(
                report_bundle_page(
                    page_id=page_id,
                    title=title,
                    stage=stage,
                    status="not_ready",
                    description=description,
                    inputs="Imported digital twin predictions",
                    next_step=f"Run/import twin results, then rerun `zwill report build --survey {survey} --path {output_dir}`.",
                    primary=page_id == "twin-validation",
                )
            )

    manifest_generated = set(generated)
    stale_files = sorted(path for path in previous_files - manifest_generated if Path(path).exists())
    bundle_summary = {
        **catalog["summary"],
        "ready_report_count": sum(1 for page in pages if page.get("status") == "ready"),
        "top_level_page_count": sum(1 for page in pages if page.get("primary", True)),
        "secondary_page_count": sum(1 for page in pages if not page.get("primary", True)),
    }
    manifest = {
        "survey": survey,
        "generated_at": utc_now(),
        "output_dir": str(output_dir),
        "index_path": str(output_dir / "index.html"),
        "summary": bundle_summary,
        "pages": pages,
        "one_shot_analysis_generation": one_shot_analysis_generation,
        "executive_summary": executive_result if twin_rows else None,
        "stale_files": stale_files,
        "generated_files": sorted(manifest_generated | {str(output_dir / "index.html"), str(manifest_path)}),
    }
    stage_manifest = write_report_stage_artifacts(output_dir, manifest)
    manifest["stages"] = stage_manifest
    (output_dir / "index.html").write_text(render_report_bundle_index(manifest))
    write_bundle_json(manifest_path, manifest)
    copied_index = copy_bundle_file(output_dir / "index.html", output_dir / "report" / "index.html")
    if copied_index:
        if copied_index not in stage_manifest["stages"]["report_preview"]["files"]:
            stage_manifest["stages"]["report_preview"]["files"].append(copied_index)
        if stage_manifest["stages"]["final_report"]["status"] == "ready" and copied_index not in stage_manifest["stages"]["final_report"]["files"]:
            stage_manifest["stages"]["final_report"]["files"].append(copied_index)
        manifest["stages"] = stage_manifest
        write_bundle_json(output_dir / "stage-manifest.json", stage_manifest)
        write_bundle_json(output_dir / "facts" / "facts-manifest.json", stage_manifest)
        write_bundle_json(output_dir / "analysis" / "analysis-manifest.json", stage_manifest)
        write_bundle_json(manifest_path, manifest)
    return manifest


def cmd_report_build(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_report_bundle(args)
    ready_count = sum(1 for page in manifest["pages"] if page["status"] == "ready")
    return envelope(
        "zwill report build",
        "ok",
        {
            "survey": args.survey,
            "path": manifest["output_dir"],
            "index_path": manifest["index_path"],
            "ready_pages": ready_count,
            "page_count": len(manifest["pages"]),
            "stale_file_count": len(manifest.get("stale_files", [])),
        },
        next_steps=[f"open {manifest['index_path']}"],
    )


def report_stage_envelope(command: str, manifest: dict[str, Any], stage_name: str) -> dict[str, Any]:
    stages = manifest.get("stages", {}).get("stages", {})
    stage = stages.get(stage_name, {})
    return envelope(
        command,
        "ok",
        {
            "survey": manifest.get("survey"),
            "path": manifest["output_dir"],
            "stage": stage_name,
            "stage_status": stage.get("status"),
            "stage_detail": stage,
            "stage_manifest": str(Path(manifest["output_dir"]) / "stage-manifest.json"),
        },
        next_steps=[stage.get("next_step") or f"open {manifest['index_path']}"],
    )


def find_imported_executive_summary_report(
    *,
    survey: str,
    job_ids: list[str],
    model: str | None,
    questions: list[str],
) -> dict[str, Any] | None:
    reports_root = practitioner_reports_dir()
    if not reports_root.exists():
        return None
    expected_jobs = set(job_ids)
    expected_questions = set(questions)
    candidates: list[dict[str, Any]] = []
    for context_path_candidate in sorted(reports_root.glob("*/context.json")):
        report_dir = context_path_candidate.parent
        markdown_path = report_dir / "report.md"
        import_path = report_dir / "import.json"
        if not markdown_path.exists() or not import_path.exists():
            continue
        context = read_json(context_path_candidate, {})
        report_context = context.get("executive_report_context", {})
        if report_context.get("report_kind") != "frontier_generated_executive_twin_validation":
            continue
        if report_context.get("survey") != survey:
            continue
        filters = report_context.get("filters", {})
        context_jobs = set(str(job) for job in filters.get("job_ids", []) if job)
        context_questions = set(str(question) for question in filters.get("questions", []) if question)
        context_model = filters.get("model")
        if expected_jobs and context_jobs != expected_jobs:
            continue
        if expected_questions and context_questions != expected_questions:
            continue
        if (model or None) != (context_model or None):
            continue
        imported = read_json(import_path, {})
        generation = {
            **context.get("generation", {}),
            "mode": "imported_results",
            "report_id": context.get("report_id") or report_dir.name,
            "context_path": str(context_path_candidate),
            "markdown_path": str(markdown_path),
            "import_path": str(import_path),
            "imported_at": imported.get("imported_at"),
        }
        candidates.append(
            {
                "report_id": generation["report_id"],
                "markdown": markdown_path.read_text(),
                "generation": generation,
                "imported_at": imported.get("imported_at") or "",
            }
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.get("imported_at") or "", item.get("report_id") or ""))[-1]


def find_imported_one_shot_analysis_report(
    *,
    survey: str,
    job_id: str | None,
    model: str | None,
    questions: list[str],
) -> dict[str, Any] | None:
    reports_root = practitioner_reports_dir()
    if not reports_root.exists():
        return None
    expected_questions = set(questions)
    candidates: list[dict[str, Any]] = []
    for context_path_candidate in sorted(reports_root.glob("*/context.json")):
        report_dir = context_path_candidate.parent
        markdown_path = report_dir / "report.md"
        import_path = report_dir / "import.json"
        if not markdown_path.exists() or not import_path.exists():
            continue
        context = read_json(context_path_candidate, {})
        report_context = context.get("one_shot_analysis_context", {})
        if report_context.get("report_kind") != "frontier_generated_one_shot_marginal_analysis":
            continue
        if report_context.get("survey") != survey:
            continue
        filters = report_context.get("filters", {})
        context_questions = set(str(question) for question in filters.get("questions", []) if question)
        if expected_questions and context_questions != expected_questions:
            continue
        if job_id is not None and (job_id or None) != (filters.get("job_id") or None):
            continue
        if model is not None and (model or None) != (filters.get("model") or None):
            continue
        imported = read_json(import_path, {})
        generation = {
            **context.get("generation", {}),
            "mode": "imported_results",
            "report_id": context.get("report_id") or report_dir.name,
            "context_path": str(context_path_candidate),
            "markdown_path": str(markdown_path),
            "import_path": str(import_path),
            "imported_at": imported.get("imported_at"),
        }
        candidates.append(
            {
                "report_id": generation["report_id"],
                "markdown": markdown_path.read_text(),
                "generation": generation,
                "imported_at": imported.get("imported_at") or "",
            }
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.get("imported_at") or "", item.get("report_id") or ""))[-1]


def cmd_report_facts(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_report_bundle(args)
    return report_stage_envelope("zwill report facts", manifest, "facts")


def cmd_report_analyze(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_report_bundle(args)
    return report_stage_envelope("zwill report analyze", manifest, "analysis")


def cmd_report_render(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_report_bundle(args)
    final_stage = manifest.get("stages", {}).get("stages", {}).get("final_report", {})
    if args.final and final_stage.get("status") != "ready":
        raise ZwillError(
            "blocked",
            "Final report is blocked because required generated interpretations are missing.",
            hint=final_stage.get("next_step") or "Run the relevant export/run/import/render generated-interpretation flow.",
            context={"stage_manifest": str(Path(manifest["output_dir"]) / "stage-manifest.json"), "missing": final_stage.get("missing", [])},
        )
    return report_stage_envelope("zwill report render", manifest, "final_report" if args.final else "report_preview")


def read_probability_imports(sdir: Path) -> list[dict[str, Any]]:
    imports = []
    jobs_dir = probability_jobs_dir(sdir)
    if not jobs_dir.exists():
        return imports
    for import_path in sorted(jobs_dir.glob("*/import.json")):
        metadata = read_json(import_path, {})
        if metadata:
            imports.append(metadata)
    return imports


def build_probability_coverage_payload(sdir: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    imports = read_probability_imports(sdir)
    imported_by_job_model: Counter[tuple[str, str]] = Counter()
    for row in rows:
        imported_by_job_model[(str(row.get("job_id")), model_label(row.get("service"), row.get("model")))] += 1
    issue_model_rows: list[tuple[str, str]] = []
    requested_by_job_model: Counter[tuple[str, str]] = Counter()
    job_rows = []
    for metadata in imports:
        job_id = str(metadata.get("job_id"))
        for issue in metadata.get("issues", []):
            issue_model_rows.append((job_id, str(issue.get("model") or "")))
        stored_path = metadata.get("stored_path")
        if stored_path and Path(stored_path).exists():
            try:
                results = read_json_or_gzip(Path(stored_path))
            except Exception:
                results = {}
            for result_row in results.get("data", []) if isinstance(results, dict) else []:
                model = result_row.get("model", {})
                requested_by_job_model[(job_id, model_label(model.get("inference_service"), model.get("model")))] += 1
        job_rows.append(
            {
                "job_id": job_id,
                "source_path": metadata.get("source_path"),
                "requested_rows": metadata.get("row_count", 0),
                "imported_rows": metadata.get("extracted_count", 0),
                "issue_count": metadata.get("issue_count", 0),
                "imported_at": metadata.get("imported_at"),
            }
        )
    labels_by_job: dict[str, set[str]] = defaultdict(set)
    for job_id, label in set(requested_by_job_model) | set(imported_by_job_model):
        labels_by_job[job_id].add(label)
    issue_by_job_model: Counter[tuple[str, str]] = Counter()
    for job_id, raw_label in issue_model_rows:
        label = raw_label
        if raw_label not in labels_by_job.get(job_id, set()):
            suffix_matches = sorted(label for label in labels_by_job.get(job_id, set()) if label.split(":", 1)[-1] == raw_label)
            if len(suffix_matches) == 1:
                label = suffix_matches[0]
        issue_by_job_model[(job_id, label)] += 1
    model_rows = []
    keys = sorted(set(requested_by_job_model) | set(imported_by_job_model) | set(issue_by_job_model))
    for job_id, model in keys:
        imported = imported_by_job_model.get((job_id, model), 0)
        issues = issue_by_job_model.get((job_id, model), 0)
        model_rows.append(
            {
                "job_id": job_id,
                "model": model,
                "requested_rows": requested_by_job_model.get((job_id, model), 0),
                "imported_rows": imported,
                "issue_count": issues,
                "missing_or_malformed_rows": max(0, requested_by_job_model.get((job_id, model), 0) - imported),
            }
        )
    return {"jobs": job_rows, "models": model_rows}


def render_probability_coverage_html(payload: dict[str, Any]) -> str:
    section = render_probability_coverage_section(payload)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>One-Shot Import Coverage</title>
  <style>
    {EP_REPORT_CSS}
    section {{ border:1px solid var(--ep-border); border-radius:8px; padding:18px; margin-bottom:16px; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <main>
    <h1>One-Shot Import Coverage</h1>
    <p>Requested rows come from the stored raw EDSL Results object when available. Imported rows are rows that passed probability parsing and normalization.</p>
    {section}
  </main>
</body>
</html>"""


def render_probability_coverage_section(payload: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        import html

        return html.escape(str(value), quote=True)

    job_rows = "".join(
        "<tr>"
        f"<td><code>{esc(row['job_id'])}</code></td>"
        f"<td class=\"num\">{esc(row['requested_rows'])}</td>"
        f"<td class=\"num\">{esc(row['imported_rows'])}</td>"
        f"<td class=\"num\">{esc(row['issue_count'])}</td>"
        f"<td>{esc(row.get('imported_at') or '')}</td>"
        "</tr>"
        for row in payload.get("jobs", [])
    )
    model_rows = "".join(
        "<tr>"
        f"<td><code>{esc(row['job_id'])}</code></td>"
        f"<td>{esc(row['model'])}</td>"
        f"<td class=\"num\">{esc(row['requested_rows'])}</td>"
        f"<td class=\"num\">{esc(row['imported_rows'])}</td>"
        f"<td class=\"num\">{esc(row['missing_or_malformed_rows'])}</td>"
        f"<td class=\"num\">{esc(row['issue_count'])}</td>"
        "</tr>"
        for row in payload.get("models", [])
    )
    if not job_rows:
        job_rows = "<tr><td colspan=\"5\">No imported one-shot jobs found.</td></tr>"
    if not model_rows:
        model_rows = "<tr><td colspan=\"6\">No model-level coverage rows found.</td></tr>"
    return f"""
    <section class="analysis-card">
      <h2>Coverage and Import Quality</h2>
      <p>Requested rows come from the stored raw EDSL Results object when available. Imported rows are rows that passed probability parsing and normalization.</p>
      <h3>Jobs</h3>
      <table><thead><tr><th>Job</th><th class="num">Requested rows</th><th class="num">Imported rows</th><th class="num">Issues</th><th>Imported at</th></tr></thead><tbody>{job_rows}</tbody></table>
      <h3>Models</h3>
      <table><thead><tr><th>Job</th><th>Model</th><th class="num">Requested rows</th><th class="num">Imported rows</th><th class="num">Missing/malformed</th><th class="num">Issues</th></tr></thead><tbody>{model_rows}</tbody></table>
    </section>"""


def insert_before_main_close(html_text: str, section: str) -> str:
    if not section:
        return html_text
    if "</main>" in html_text:
        return html_text.replace("</main>", f"{section}\n  </main>", 1)
    return html_text + section


def insert_after_main_open(html_text: str, section: str) -> str:
    if not section:
        return html_text
    if "<main>" in html_text:
        return html_text.replace("<main>", f"<main>\n{section}", 1)
    return section + html_text


def render_generated_executive_interpretation_section(generated: dict[str, Any] | None) -> str:
    if not generated:
        return """
    <section class="summary-card">
      <h2>Interpretation and Recommendation</h2>
      <p>No generated twin-validation interpretation has been imported yet. Run the executive summary export, then run/import/render the report-writing job before circulating this report.</p>
    </section>"""
    body = markdown_to_html(remove_leading_executive_summary_heading(generated.get("markdown", "")))
    generation = generated.get("generation") or {}
    model = generation.get("model") or generation.get("model_label") or "unknown model"
    return f"""
    <section class="summary-card generated-analysis">
      <h2>Interpretation and Recommendation</h2>
      {body}
      <p class="subtle">Generated by {model} from compact twin-validation artifacts.</p>
    </section>"""


def render_validation_diagnostics_html(*, survey: str, artifacts: dict[str, str], output_dir: Path) -> str:
    display_title, _raw_title = report_display_title(str(survey))
    section = render_validation_diagnostics_section(survey=survey, artifacts=artifacts, output_dir=output_dir)
    import html

    esc = lambda value: html.escape(str(value), quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(display_title)} Validation Diagnostics</title>
  <style>
    {EP_REPORT_CSS}
    section {{ border:1px solid var(--ep-border); border-radius:8px; padding:18px; margin-bottom:16px; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <main>
    <h1>Validation Diagnostics</h1>
    <p class="subtle">{esc(display_title)} · Survey id: <code>{esc(survey)}</code></p>
    <p>Supporting plot and data artifacts generated for the executive summary and twin validation readout.</p>
    {section}
  </main>
</body>
</html>"""


def render_validation_diagnostics_section(*, survey: str, artifacts: dict[str, str], output_dir: Path) -> str:
    def esc(value: Any) -> str:
        import html

        return html.escape(str(value), quote=True)

    labels = {
        "lift_histogram": "Lift vs uniform histogram",
        "empirical_lift_histogram": "Lift vs empirical marginal oracle histogram",
        "pairwise_order_accuracy": "Pairwise option-order accuracy",
        "spearman_rank_order": "Spearman rank-order JSON",
        "spearman_rank_order_csv": "Spearman rank-order CSV",
        "individual_predictive_power": "Overall and per-question permutation JSON",
        "individual_predictive_power_by_question": "Per-question permutation CSV",
        "pairwise_order_accuracy_csv": "Pairwise option-order CSV",
    }
    rows = []
    image_blocks = []
    for key, path in artifacts.items():
        href = bundle_rel_link(path, output_dir)
        label = labels.get(key, key.replace("_", " ").title())
        rows.append(f"<tr><td>{esc(label)}</td><td><a href=\"{esc(href)}\">{esc(href)}</a></td></tr>")
        if str(path).endswith(".svg"):
            image_blocks.append(f"<section><h3>{esc(label)}</h3><img src=\"{esc(href)}\" alt=\"{esc(label)}\"></section>")
    if not rows:
        rows.append("<tr><td colspan=\"2\">No diagnostic artifacts were generated.</td></tr>")
    return f"""
    <section class="summary-card">
      <h2 id="definitions">Supporting Diagnostics</h2>
      <p>Links and previews for lift histograms, rank-order diagnostics, pairwise option ordering, and permutation tests.</p>
      <table><thead><tr><th>Artifact</th><th>File</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
      {''.join(image_blocks)}
    </section>"""

