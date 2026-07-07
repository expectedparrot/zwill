from __future__ import annotations

from .cli import *  # noqa: F403


def paired_twin_response_changes(
    all_rows: list[dict[str, Any]],
    from_job_id: str,
    to_job_id: str,
    *,
    from_label: str | None = None,
    to_label: str | None = None,
    model: str | None = None,
    example_limit: int = 20,
) -> list[dict[str, Any]]:
    def label_for(row: dict[str, Any]) -> str:
        return str(row.get("model_label") or model_label(row.get("service"), row.get("model")))

    def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (str(row.get("respondent_id")), str(row.get("heldout_question")), label_for(row))

    from_rows = [
        row
        for row in all_rows
        if row.get("job_id") == from_job_id and (model is None or label_for(row) == model)
    ]
    to_rows = [
        row
        for row in all_rows
        if row.get("job_id") == to_job_id and (model is None or label_for(row) == model)
    ]
    from_by_key = {row_key(row): row for row in from_rows}
    to_by_key = {row_key(row): row for row in to_rows}
    by_model: dict[str, dict[str, Any]] = {}
    for respondent_id, heldout_question, model_name in sorted(set(from_by_key) & set(to_by_key)):
        before = from_by_key[(respondent_id, heldout_question, model_name)]
        after = to_by_key[(respondent_id, heldout_question, model_name)]
        before_top, before_confidence = twin_top_prediction(before)
        after_top, after_confidence = twin_top_prediction(after)
        before_correct = bool(before.get("top1_correct"))
        after_correct = bool(after.get("top1_correct"))
        changed = before_top != after_top
        probability_actual_delta = float(after.get("probability_actual", 0.0)) - float(before.get("probability_actual", 0.0))
        nll_delta = float(after.get("negative_log_likelihood", 0.0)) - float(before.get("negative_log_likelihood", 0.0))
        bucket = by_model.setdefault(
            model_name,
            {
                "from_job_id": from_job_id,
                "to_job_id": to_job_id,
                "from_label": from_label or from_job_id,
                "to_label": to_label or to_job_id,
                "model": model_name,
                "paired_rows": 0,
                "changed_top_choice": 0,
                "unchanged_top_choice": 0,
                "changed_top_choice_rate": 0.0,
                "corrections": 0,
                "regressions": 0,
                "changed_wrong_to_wrong": 0,
                "changed_correct_to_correct": 0,
                "unchanged_correct": 0,
                "unchanged_wrong": 0,
                "mean_probability_actual_delta": 0.0,
                "mean_nll_delta": 0.0,
                "examples": [],
            },
        )
        bucket["paired_rows"] += 1
        bucket["mean_probability_actual_delta"] += probability_actual_delta
        bucket["mean_nll_delta"] += nll_delta
        if changed:
            bucket["changed_top_choice"] += 1
            if not before_correct and after_correct:
                bucket["corrections"] += 1
            elif before_correct and not after_correct:
                bucket["regressions"] += 1
            elif not before_correct and not after_correct:
                bucket["changed_wrong_to_wrong"] += 1
            else:
                bucket["changed_correct_to_correct"] += 1
            if len(bucket["examples"]) < example_limit:
                bucket["examples"].append(
                    {
                        "respondent_id": respondent_id,
                        "heldout_question": heldout_question,
                        "actual_answer": before.get("actual_answer"),
                        "from_top_choice": before_top,
                        "to_top_choice": after_top,
                        "from_top_confidence": before_confidence,
                        "to_top_confidence": after_confidence,
                        "from_probability_actual": before.get("probability_actual"),
                        "to_probability_actual": after.get("probability_actual"),
                        "probability_actual_delta": probability_actual_delta,
                        "from_correct": before_correct,
                        "to_correct": after_correct,
                    }
                )
        else:
            bucket["unchanged_top_choice"] += 1
            if after_correct:
                bucket["unchanged_correct"] += 1
            else:
                bucket["unchanged_wrong"] += 1
    summaries = []
    for item in by_model.values():
        if item["paired_rows"]:
            item["changed_top_choice_rate"] = item["changed_top_choice"] / item["paired_rows"]
            item["mean_probability_actual_delta"] = item["mean_probability_actual_delta"] / item["paired_rows"]
            item["mean_nll_delta"] = item["mean_nll_delta"] / item["paired_rows"]
        summaries.append(item)
    return sorted(summaries, key=lambda item: (item["model"], item["from_job_id"], item["to_job_id"]))


def paired_twin_response_pair_rows(
    all_rows: list[dict[str, Any]],
    from_job_id: str,
    to_job_id: str,
    *,
    from_label: str | None = None,
    to_label: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    def label_for(row: dict[str, Any]) -> str:
        return str(row.get("model_label") or model_label(row.get("service"), row.get("model")))

    def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (str(row.get("respondent_id")), str(row.get("heldout_question")), label_for(row))

    from_by_key = {
        row_key(row): row
        for row in all_rows
        if row.get("job_id") == from_job_id and (model is None or label_for(row) == model)
    }
    to_by_key = {
        row_key(row): row
        for row in all_rows
        if row.get("job_id") == to_job_id and (model is None or label_for(row) == model)
    }
    pairs = []
    for respondent_id, heldout_question, model_name in sorted(set(from_by_key) & set(to_by_key)):
        before = from_by_key[(respondent_id, heldout_question, model_name)]
        after = to_by_key[(respondent_id, heldout_question, model_name)]
        before_top, before_confidence = twin_top_prediction(before)
        after_top, after_confidence = twin_top_prediction(after)
        before_correct = bool(before.get("top1_correct"))
        after_correct = bool(after.get("top1_correct"))
        changed = before_top != after_top
        if changed and not before_correct and after_correct:
            category = "correction"
        elif changed and before_correct and not after_correct:
            category = "regression"
        elif changed and before_correct and after_correct:
            category = "changed_correct_to_correct"
        elif changed:
            category = "changed_wrong_to_wrong"
        elif after_correct:
            category = "unchanged_correct"
        else:
            category = "unchanged_wrong"
        probability_actual_delta = float(after.get("probability_actual", 0.0)) - float(before.get("probability_actual", 0.0))
        nll_delta = float(after.get("negative_log_likelihood", 0.0)) - float(before.get("negative_log_likelihood", 0.0))
        pairs.append(
            {
                "from_job_id": from_job_id,
                "to_job_id": to_job_id,
                "from_label": from_label or from_job_id,
                "to_label": to_label or to_job_id,
                "respondent_id": respondent_id,
                "heldout_question": heldout_question,
                "model": model_name,
                "actual_answer": before.get("actual_answer"),
                "from_top_choice": before_top,
                "to_top_choice": after_top,
                "from_top_confidence": before_confidence,
                "to_top_confidence": after_confidence,
                "from_probability_actual": before.get("probability_actual"),
                "to_probability_actual": after.get("probability_actual"),
                "probability_actual_delta": probability_actual_delta,
                "from_nll": before.get("negative_log_likelihood"),
                "to_nll": after.get("negative_log_likelihood"),
                "nll_delta": nll_delta,
                "from_correct": before_correct,
                "to_correct": after_correct,
                "changed_top_choice": changed,
                "category": category,
            }
        )
    return pairs


def twin_job_template_and_scenarios(sdir: Path, job_id: str) -> tuple[str | None, dict[tuple[str, str], dict[str, Any]]]:
    run = next((item for item in read_twin_run_manifest(sdir) if item.get("job_id") == job_id), {})
    job_path_text = run.get("job_path")
    if not job_path_text:
        return None, {}
    job_path = Path(str(job_path_text))
    if not job_path.exists():
        return None, {}
    job_dict = read_json(job_path, {})
    questions = job_dict.get("survey", {}).get("questions", [])
    template = questions[0].get("question_text") if questions and isinstance(questions[0], dict) else None
    scenarios = {}
    for scenario in job_dict.get("scenarios", []):
        key = (str(scenario.get("respondent_id")), str(scenario.get("heldout_question_name")))
        scenarios[key] = scenario
    return template, scenarios


def format_probabilities_for_display(probabilities: dict[str, Any] | None) -> str:
    if not probabilities:
        return ""
    return "; ".join(f"{option}: {float(value):.3f}" for option, value in probabilities.items())


def compact_observed_answers(observed_answers: list[dict[str, Any]] | None) -> str:
    if not observed_answers:
        return "No observed answers recorded."
    parts = []
    for item in observed_answers:
        name = item.get("question_name", "")
        text = item.get("question_text", "")
        answer = item.get("answer", "")
        label = f"{name}: " if name else ""
        parts.append(f"{label}{text} -> {answer}")
    return "\n".join(parts)


def paired_twin_microdata_rows(
    sdir: Path,
    all_rows: list[dict[str, Any]],
    pair_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    job_ids = sorted({str(row.get("from_job_id")) for row in pair_rows} | {str(row.get("to_job_id")) for row in pair_rows})
    templates: dict[str, str | None] = {}
    scenarios_by_job: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for job_id in job_ids:
        template, scenarios = twin_job_template_and_scenarios(sdir, job_id)
        templates[job_id] = template
        scenarios_by_job[job_id] = scenarios

    def prediction_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
        label = str(row.get("model_label") or model_label(row.get("service"), row.get("model")))
        return (str(row.get("job_id")), str(row.get("respondent_id")), str(row.get("heldout_question")), label)

    predictions = {prediction_key(row): row for row in all_rows}
    rows = []
    for pair in pair_rows:
        from_job_id = str(pair.get("from_job_id"))
        to_job_id = str(pair.get("to_job_id"))
        respondent_id = str(pair.get("respondent_id"))
        heldout_question = str(pair.get("heldout_question"))
        model_name = str(pair.get("model"))
        before = predictions.get((from_job_id, respondent_id, heldout_question, model_name), {})
        after = predictions.get((to_job_id, respondent_id, heldout_question, model_name), {})
        before_scenario = scenarios_by_job.get(from_job_id, {}).get((respondent_id, heldout_question), {})
        after_scenario = scenarios_by_job.get(to_job_id, {}).get((respondent_id, heldout_question), {})
        scenario = after_scenario or before_scenario
        observed_answers = scenario.get("observed_answers") or after.get("observed_answers") or before.get("observed_answers") or []
        rows.append(
            {
                **pair,
                "heldout_question_text": scenario.get("heldout_question_text") or after.get("heldout_question_text") or before.get("heldout_question_text"),
                "heldout_options": scenario.get("heldout_options") or after.get("option_labels") or before.get("option_labels") or [],
                "observed_answers": observed_answers,
                "observed_answers_text": scenario.get("observed_answers_text") or compact_observed_answers(observed_answers),
                "agent_material_text": scenario.get("agent_material_text"),
                "from_template": templates.get(from_job_id),
                "to_template": templates.get(to_job_id),
                "from_twin_material_text": before_scenario.get("twin_material_text") or before.get("twin_material_text"),
                "to_twin_material_text": after_scenario.get("twin_material_text") or after.get("twin_material_text"),
                "from_probabilities": before.get("probabilities"),
                "to_probabilities": after.get("probabilities"),
                "from_probabilities_text": format_probabilities_for_display(before.get("probabilities")),
                "to_probabilities_text": format_probabilities_for_display(after.get("probabilities")),
                "from_notes": before.get("notes"),
                "to_notes": after.get("notes"),
            }
        )
    metadata = {
        "templates_by_job": templates,
        "row_count": len(rows),
    }
    return rows, metadata


def render_twin_microdata_table_html(rows: list[dict[str, Any]], *, title: str, include_title: bool = True) -> str:
    options = sorted({str(row.get("category")) for row in rows})
    model_options = sorted({str(row.get("model")) for row in rows})
    option_markup = "".join(f'<option value="{html_escape(value)}">{html_escape(value.replace("_", " "))}</option>' for value in options)
    model_markup = "".join(f'<option value="{html_escape(value)}">{html_escape(value)}</option>' for value in model_options)
    body_rows = []
    for row in rows:
        observed_summary = compact_observed_answers(row.get("observed_answers", []))
        template = row.get("to_template") or row.get("from_template") or ""
        material = row.get("to_twin_material_text") or row.get("from_twin_material_text") or ""
        search_blob = " ".join(
            str(value or "")
            for value in [
                row.get("respondent_id"),
                row.get("category"),
                row.get("actual_answer"),
                row.get("from_top_choice"),
                row.get("to_top_choice"),
                observed_summary,
                material,
                row.get("from_notes"),
                row.get("to_notes"),
            ]
        )
        body_rows.append(
            "<tr "
            f"data-category=\"{html_escape(row.get('category'))}\" "
            f"data-model=\"{html_escape(row.get('model'))}\" "
            f"data-search=\"{html_escape(search_blob)}\">"
            f"<td><code>{html_escape(row.get('respondent_id'))}</code><div class=\"muted\">{html_escape(row.get('heldout_question'))}</div></td>"
            f"<td><span class=\"pill {html_escape(row.get('category'))}\">{html_escape(str(row.get('category')).replace('_', ' '))}</span></td>"
            f"<td>{html_escape(row.get('actual_answer'))}</td>"
            f"<td><b>{html_escape(row.get('from_top_choice'))}</b><div class=\"muted\">p(actual) {float(row.get('from_probability_actual') or 0):.3f}</div><div class=\"mono small\">{html_escape(row.get('from_probabilities_text'))}</div></td>"
            f"<td><b>{html_escape(row.get('to_top_choice'))}</b><div class=\"muted\">p(actual) {float(row.get('to_probability_actual') or 0):.3f}</div><div class=\"mono small\">{html_escape(row.get('to_probabilities_text'))}</div></td>"
            f"<td class=\"num\">{float(row.get('probability_actual_delta') or 0):+.3f}</td>"
            f"<td><details><summary>Traits / prompt / response</summary>"
            f"<h4>Observed answer traits</h4><pre>{html_escape(observed_summary)}</pre>"
            f"<h4>Supplemental material</h4><pre>{html_escape(material or 'No supplemental material recorded.')}</pre>"
            f"<h4>Prompt template</h4><pre>{html_escape(template or 'No prompt template recorded.')}</pre>"
            f"<h4>Model notes</h4><pre>{html_escape('Before: ' + str(row.get('from_notes') or '') + chr(10) + 'After: ' + str(row.get('to_notes') or ''))}</pre>"
            "</details></td>"
            "</tr>"
        )
    data_json = escape_script_text(json.dumps(rows, separators=(",", ":")))
    title_markup = f"<h3>{html_escape(title)}</h3>" if include_title else ""
    return f"""<div class="microdata-widget">
  <style>
    .microdata-widget {{ font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#17202a; }}
    .microdata-widget .controls {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:10px 0 14px; }}
    .microdata-widget input,.microdata-widget select {{ border:1px solid #cfd7df; border-radius:6px; padding:7px 9px; font:inherit; background:#fff; }}
    .microdata-widget table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    .microdata-widget th,.microdata-widget td {{ border:1px solid #dfe3e6; padding:7px 8px; vertical-align:top; text-align:left; }}
    .microdata-widget th {{ background:#f0f3f4; }}
    .microdata-widget pre {{ white-space:pre-wrap; max-height:260px; overflow:auto; background:#f7f9fb; border:1px solid #dfe3e6; border-radius:6px; padding:8px; }}
    .microdata-widget .muted {{ color:#607080; font-size:12px; margin-top:3px; }}
    .microdata-widget .mono {{ font-family:SFMono-Regular,Consolas,Menlo,monospace; }}
    .microdata-widget .small {{ font-size:11.5px; }}
    .microdata-widget .num {{ text-align:right; font-family:SFMono-Regular,Consolas,Menlo,monospace; }}
    .microdata-widget .pill {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#eef2f6; font-size:12px; }}
    .microdata-widget .unchanged_correct {{ background:#e9f1fb; color:#244d78; }}
    .microdata-widget .unchanged_wrong {{ background:#eef0f3; color:#4b5563; }}
    .microdata-widget .correction {{ background:#e7f3eb; color:#1f6f43; }}
    .microdata-widget .regression {{ background:#f7e8e6; color:#9b2f24; }}
  </style>
  {title_markup}
  <div class="controls">
    <label>Category <select data-micro-filter="category"><option value="">All</option>{option_markup}</select></label>
    <label>Model <select data-micro-filter="model"><option value="">All</option>{model_markup}</select></label>
    <label>Search <input data-micro-filter="search" type="search" placeholder="respondent, answer, note"></label>
    <span class="muted" data-micro-count>{len(rows)} rows</span>
  </div>
  <div class="table-wrap"><table>
    <thead><tr><th>Respondent</th><th>Category</th><th>Actual</th><th>Before</th><th>After</th><th>Δ p(actual)</th><th>Inspect</th></tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table></div>
  <script type="application/json" class="microdata-json">{data_json}</script>
  <script>
    (function() {{
      const root = document.currentScript.closest(".microdata-widget");
      if (!root) return;
      const rows = Array.from(root.querySelectorAll("tbody tr"));
      const category = root.querySelector('[data-micro-filter="category"]');
      const model = root.querySelector('[data-micro-filter="model"]');
      const search = root.querySelector('[data-micro-filter="search"]');
      const count = root.querySelector("[data-micro-count]");
      function apply() {{
        const q = (search.value || "").toLowerCase();
        let visible = 0;
        for (const row of rows) {{
          const okCategory = !category.value || row.dataset.category === category.value;
          const okModel = !model.value || row.dataset.model === model.value;
          const okSearch = !q || (row.dataset.search || "").toLowerCase().includes(q);
          const show = okCategory && okModel && okSearch;
          row.style.display = show ? "" : "none";
          if (show) visible += 1;
        }}
        count.textContent = visible + " rows";
      }}
      category.addEventListener("change", apply);
      model.addEventListener("change", apply);
      search.addEventListener("input", apply);
    }})();
  </script>
</div>
"""


def experiment_microdata_id(args: argparse.Namespace) -> str:
    payload = {
        "survey": args.survey,
        "metric": args.metric,
        "model": args.model,
        "experiment_id": args.experiment_id,
        "job_id": args.job_id,
        "jobs": args.jobs,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_experiment_microdata_audit(
    sdir: Path,
    experiments: list[dict[str, Any]],
    metric: str,
    model: str | None = None,
) -> dict[str, Any]:
    comparison_rows, metric_info = twin_experiment_comparison_rows(sdir, experiments, metric, model)
    if not comparison_rows:
        raise ZwillError("not_found", "No scored experiment rows found for the requested filters.")
    all_rows = read_jsonl(digital_twin_predictions_path(sdir))
    selected_jobs = {str(row["job_id"]) for row in comparison_rows}
    experiment_by_job = {str(item.get("job_id")): item for item in experiments}
    display_experiments = []
    for row in comparison_rows:
        experiment = experiment_by_job.get(str(row["job_id"]), {})
        display_experiments.append(
            {
                "experiment_id": row.get("experiment_id"),
                "job_id": row.get("job_id"),
                "approach": row.get("approach"),
                "description": row.get("description", ""),
                "rank": row.get("rank"),
                "selected": row.get("selected"),
                "model": row.get("model"),
                "metric": metric,
                "metric_value": row.get("metric_value"),
                "primary_metric": experiment.get("primary_metric"),
            }
        )

    templates: dict[str, str | None] = {}
    scenarios_by_job: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for job_id in selected_jobs:
        template, scenarios = twin_job_template_and_scenarios(sdir, job_id)
        templates[job_id] = template
        scenarios_by_job[job_id] = scenarios

    groups_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    display_job_order = [str(row["job_id"]) for row in comparison_rows]
    for prediction in all_rows:
        job_id = str(prediction.get("job_id"))
        if job_id not in selected_jobs:
            continue
        label = str(prediction.get("model_label") or model_label(prediction.get("service"), prediction.get("model")))
        if model and label != model:
            continue
        respondent_id = str(prediction.get("respondent_id"))
        heldout_question = str(prediction.get("heldout_question"))
        group_key = (respondent_id, heldout_question, label)
        scenario = scenarios_by_job.get(job_id, {}).get((respondent_id, heldout_question), {})
        observed_answers = scenario.get("observed_answers") or prediction.get("observed_answers", [])
        group_id = hashlib.sha256(
            json.dumps(group_key, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:12]
        group = groups_by_key.setdefault(
            group_key,
            {
                "group_id": group_id,
                "respondent_id": respondent_id,
                "heldout_question": heldout_question,
                "heldout_question_text": scenario.get("heldout_question_text") or prediction.get("heldout_question_text"),
                "heldout_options": scenario.get("heldout_options") or prediction.get("option_labels", []),
                "model": label,
                "actual_answer": prediction.get("actual_answer"),
                "observed_answers": observed_answers,
                "observed_answers_text": scenario.get("observed_answers_text") or compact_observed_answers(observed_answers),
                "agent_material_text": scenario.get("agent_material_text"),
                "prediction_rows": {},
            },
        )
        top_choice, top_confidence = twin_top_prediction(prediction)
        experiment = experiment_by_job.get(job_id, {})
        group["prediction_rows"][job_id] = {
            "group_id": group_id,
            "respondent_id": respondent_id,
            "heldout_question": heldout_question,
            "heldout_question_text": group.get("heldout_question_text"),
            "heldout_options": group.get("heldout_options"),
            "model": label,
            "observed_answers": observed_answers,
            "observed_answers_text": scenario.get("observed_answers_text") or compact_observed_answers(observed_answers),
            "agent_material_text": scenario.get("agent_material_text"),
            "experiment_id": experiment.get("experiment_id"),
            "job_id": job_id,
            "approach": experiment.get("approach") or job_id,
            "description": experiment.get("description", ""),
            "top_choice": top_choice,
            "top_confidence": top_confidence,
            "probabilities": prediction.get("probabilities", {}),
            "probabilities_text": format_probabilities_for_display(prediction.get("probabilities")),
            "actual_answer": prediction.get("actual_answer"),
            "probability_actual": prediction.get("probability_actual"),
            "negative_log_likelihood": prediction.get("negative_log_likelihood"),
            "brier": prediction.get("brier"),
            "top1_correct": bool(prediction.get("top1_correct")),
            "notes": prediction.get("notes"),
            "twin_material_text": scenario.get("twin_material_text") or prediction.get("twin_material_text"),
            "prompt_template": templates.get(job_id),
            "source_row": prediction.get("row"),
        }

    groups = []
    prediction_rows = []
    for group in groups_by_key.values():
        rows_by_job = group.pop("prediction_rows")
        visible_rows = [rows_by_job[job_id] for job_id in display_job_order if job_id in rows_by_job]
        correct_values = [bool(item.get("top1_correct")) for item in visible_rows]
        p_values = [
            float(item.get("probability_actual"))
            for item in visible_rows
            if item.get("probability_actual") is not None
        ]
        nll_values = [
            (float(item.get("negative_log_likelihood")), item.get("experiment_id"))
            for item in visible_rows
            if item.get("negative_log_likelihood") is not None
        ]
        top_choices = {item.get("top_choice") for item in visible_rows if item.get("top_choice") is not None}
        if correct_values and all(correct_values):
            category = "all_correct"
        elif correct_values and not any(correct_values):
            category = "all_wrong"
        else:
            category = "mixed_correctness"
        if len(top_choices) > 1:
            category = "top_choice_changed"
        diagnostics = {
            "category": category,
            "top_choice_changed": len(top_choices) > 1,
            "any_correct": any(correct_values) if correct_values else None,
            "all_correct": all(correct_values) if correct_values else None,
            "p_actual_range": max(p_values) - min(p_values) if p_values else None,
            "best_experiment_by_nll": min(nll_values)[1] if nll_values else None,
            "worst_experiment_by_nll": max(nll_values)[1] if nll_values else None,
            "experiment_count": len(visible_rows),
        }
        group["diagnostics"] = diagnostics
        group["prediction_row_count"] = len(visible_rows)
        group["prediction_row_ids"] = [
            f"{item.get('group_id')}::{item.get('job_id')}" for item in visible_rows
        ]
        for item in visible_rows:
            item["row_id"] = f"{item.get('group_id')}::{item.get('job_id')}"
            item["group_diagnostics"] = diagnostics
            prediction_rows.append(item)
        groups.append(group)
    groups.sort(key=lambda row: (str(row.get("heldout_question")), str(row.get("model")), str(row.get("respondent_id"))))
    group_order = {group["group_id"]: index for index, group in enumerate(groups)}
    prediction_rows.sort(
        key=lambda row: (
            group_order.get(row.get("group_id"), 0),
            display_job_order.index(str(row.get("job_id"))) if str(row.get("job_id")) in display_job_order else 999999,
        )
    )
    return {
        "kind": "experiment_microdata_audit",
        "survey": sdir.name,
        "metric": {"name": metric, **metric_info},
        "experiments": display_experiments,
        "groups": groups,
        "prediction_rows": prediction_rows,
        "group_count": len(groups),
        "prediction_row_count": len(prediction_rows),
        "row_count": len(prediction_rows),
        "group_key": ["respondent_id", "heldout_question", "model"],
        "created_at": utc_now(),
    }


def build_experiment_microdata_matrix(
    sdir: Path,
    experiments: list[dict[str, Any]],
    metric: str,
    model: str | None = None,
) -> dict[str, Any]:
    return build_experiment_microdata_audit(sdir, experiments, metric, model)


def render_experiment_microdata_audit_html(payload: dict[str, Any], *, title: str) -> str:
    groups = payload.get("groups", [])
    prediction_rows = payload.get("prediction_rows", [])
    experiments = payload.get("experiments", [])
    questions = sorted({str(row.get("heldout_question")) for row in prediction_rows})
    models = sorted({str(row.get("model")) for row in prediction_rows})
    actuals = sorted({str(row.get("actual_answer")) for row in prediction_rows})
    categories = sorted({str(group.get("diagnostics", {}).get("category")) for group in groups})

    def options(values: list[str], labels: dict[str, str] | None = None) -> str:
        labels = labels or {}
        return "".join(f'<option value="{html_escape(value)}">{html_escape(labels.get(value, value))}</option>' for value in values)

    experiment_toggles = "".join(
        f'<label><input type="checkbox" data-exp-toggle="{html_escape(exp.get("job_id"))}" checked> {html_escape(exp.get("approach") or exp.get("job_id"))}</label>'
        for exp in experiments
    )
    body_rows = []
    rows_by_group: dict[str, list[dict[str, Any]]] = {}
    for row in prediction_rows:
        rows_by_group.setdefault(str(row.get("group_id")), []).append(row)
    for group in groups:
        group_id = str(group.get("group_id"))
        diag = group.get("diagnostics", {})
        observed_text = group.get("observed_answers_text") or compact_observed_answers(group.get("observed_answers", []))
        group_search = " ".join(
            str(value or "")
            for value in [
                group.get("respondent_id"),
                group.get("heldout_question"),
                group.get("heldout_question_text"),
                group.get("actual_answer"),
                observed_text,
                diag.get("category"),
            ]
        )
        body_rows.append(
            "<tr class=\"group-row\" "
            f"data-group-id=\"{html_escape(group_id)}\" "
            f"data-question=\"{html_escape(group.get('heldout_question'))}\" "
            f"data-model=\"{html_escape(group.get('model'))}\" "
            f"data-actual=\"{html_escape(group.get('actual_answer'))}\" "
            f"data-category=\"{html_escape(diag.get('category'))}\" "
            f"data-search=\"{html_escape(group_search)}\">"
            '<td colspan="10">'
            f"<div class=\"group-title\"><code>{html_escape(group.get('respondent_id'))}</code> "
            f"<span>{html_escape(group.get('heldout_question'))}</span> "
            f"<span class=\"muted\">{html_escape(group.get('model'))}</span></div>"
            f"<div class=\"muted\">{html_escape(group.get('heldout_question_text'))}</div>"
            f"<div><span class=\"pill {html_escape(diag.get('category'))}\">{html_escape(str(diag.get('category')).replace('_', ' '))}</span> "
            f"<span class=\"muted\">actual: {html_escape(group.get('actual_answer'))} | p(actual) range {float(diag.get('p_actual_range') or 0):.3f} | best NLL: {html_escape(diag.get('best_experiment_by_nll'))}</span></div>"
            '</td>'
            + "</tr>"
        )
        for row in rows_by_group.get(group_id, []):
            correct_class = "correct" if row.get("top1_correct") else "wrong"
            row_search = " ".join(
                str(value or "")
                for value in [
                    group_search,
                    row.get("approach"),
                    row.get("experiment_id"),
                    row.get("top_choice"),
                    row.get("probabilities_text"),
                    row.get("notes"),
                    row.get("twin_material_text"),
                ]
            )
            body_rows.append(
                "<tr class=\"prediction-row\" "
                f"data-group-id=\"{html_escape(group_id)}\" "
                f"data-job-id=\"{html_escape(row.get('job_id'))}\" "
                f"data-question=\"{html_escape(row.get('heldout_question'))}\" "
                f"data-model=\"{html_escape(row.get('model'))}\" "
                f"data-actual=\"{html_escape(row.get('actual_answer'))}\" "
                f"data-category=\"{html_escape(diag.get('category'))}\" "
                f"data-search=\"{html_escape(row_search)}\">"
                f"<td><code>{html_escape(row.get('respondent_id'))}</code></td>"
                f"<td><b>{html_escape(row.get('approach'))}</b><div class=\"muted\">{html_escape(row.get('experiment_id') or row.get('job_id'))}</div></td>"
                f"<td>{html_escape(row.get('actual_answer'))}</td>"
                f"<td class=\"{correct_class}\"><div class=\"choice\">{html_escape(row.get('top_choice'))}</div><div class=\"muted\">confidence {float(row.get('top_confidence') or 0):.3f}</div></td>"
                f"<td>{float(row.get('probability_actual') or 0):.3f}</td>"
                f"<td>{float(row.get('negative_log_likelihood') or 0):.3f}</td>"
                f"<td>{float(row.get('brier') or 0):.3f}</td>"
                f"<td>{'yes' if row.get('top1_correct') else 'no'}</td>"
                f"<td><div class=\"mono small\">{html_escape(row.get('probabilities_text'))}</div></td>"
                f"<td><button class=\"inspect-button\" type=\"button\" data-inspect-row=\"{html_escape(row.get('row_id'))}\">Inspect</button></td>"
                "</tr>"
            )
    data_json = escape_script_text(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_escape(title)}</title>
  <style>
    :root {{ --ink:#17202a; --muted:#607080; --line:#d8dee6; --bg:#f7f8fa; --panel:#fff; --good:#e7f3eb; --bad:#f7e8e6; --mixed:#fff4d8; --changed:#eaf0ff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ max-width:1500px; margin:0 auto; padding:28px 20px 56px; }}
    h1 {{ margin:0 0 8px; font-size:28px; line-height:1.15; }}
    .subtle,.muted {{ color:var(--muted); }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; margin:14px 0; }}
    .controls {{ display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; }}
    .experiments {{ display:flex; flex-wrap:wrap; gap:8px 14px; margin-top:12px; }}
    input,select {{ border:1px solid #cfd7df; border-radius:6px; padding:7px 9px; font:inherit; background:#fff; }}
    button {{ border:1px solid #c5ced8; border-radius:6px; padding:6px 10px; font:inherit; background:#fff; color:var(--ink); cursor:pointer; }}
    button:hover {{ background:#f2f5f8; }}
    .inspect-button {{ white-space:nowrap; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border:1px solid var(--line); padding:7px 8px; vertical-align:top; text-align:left; }}
    th {{ position:sticky; top:0; background:#f0f3f4; z-index:1; }}
    .table-wrap {{ overflow:auto; max-height:78vh; border:1px solid var(--line); border-radius:8px; background:#fff; }}
    pre {{ white-space:pre-wrap; max-height:260px; overflow:auto; background:#f7f9fb; border:1px solid var(--line); border-radius:6px; padding:8px; }}
    .mono {{ font-family:SFMono-Regular,Consolas,Menlo,monospace; }}
    .small {{ font-size:11.5px; }}
    .choice {{ font-weight:700; margin-bottom:3px; }}
    .group-row td {{ background:#f8fafc; border-top:3px solid #bac6d3; }}
    .group-title {{ display:flex; flex-wrap:wrap; gap:10px; align-items:baseline; font-weight:700; }}
    .prediction-row td:first-child {{ border-left:4px solid #d5dde7; }}
    td.correct {{ background:var(--good); }}
    td.wrong {{ background:var(--bad); }}
    td.missing {{ color:var(--muted); background:#f5f6f8; }}
    .pill {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#eef2f6; font-size:12px; }}
    .all_correct {{ background:var(--good); }}
    .all_wrong {{ background:var(--bad); }}
    .mixed_correctness {{ background:var(--mixed); }}
    .top_choice_changed {{ background:var(--changed); }}
    .modal-backdrop {{ position:fixed; inset:0; display:none; align-items:center; justify-content:center; padding:24px; background:rgba(15,23,42,.45); z-index:10; }}
    .modal-backdrop.open {{ display:flex; }}
    .modal {{ width:min(1120px,96vw); max-height:92vh; overflow:hidden; background:#fff; border:1px solid #cbd5df; border-radius:8px; box-shadow:0 22px 70px rgba(15,23,42,.25); display:flex; flex-direction:column; }}
    .modal-header {{ padding:14px 16px; border-bottom:1px solid var(--line); display:flex; gap:12px; justify-content:space-between; align-items:flex-start; }}
    .modal-title {{ font-size:18px; font-weight:700; }}
    .modal-body {{ padding:14px 16px; overflow:auto; }}
    .modal-actions {{ display:flex; gap:8px; align-items:center; }}
    .tabbar {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }}
    .tabbar button.active {{ background:#17202a; color:#fff; border-color:#17202a; }}
    .tab-panel {{ display:none; }}
    .tab-panel.active {{ display:block; }}
    .summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:10px; }}
    .summary-card {{ border:1px solid var(--line); border-radius:8px; padding:10px; background:#f8fafc; }}
    .summary-card .label {{ color:var(--muted); font-size:12px; }}
    .summary-card .value {{ font-weight:700; margin-top:2px; }}
  </style>
</head>
<body>
{copy_markdown_control()}
<main>
  <h1>{html_escape(title)}</h1>
  <p class="subtle">One audit row per experiment response, grouped by respondent, held-out question, and model. Use Inspect to open the exact observed traits, supplemental material, prompt template, model notes, and source row for that response.</p>
  <section class="panel">
    <div class="controls">
      <label>Question <select data-filter="question"><option value="">All</option>{options(questions)}</select></label>
      <label>Model <select data-filter="model"><option value="">All</option>{options(models)}</select></label>
      <label>Actual <select data-filter="actual"><option value="">All</option>{options(actuals)}</select></label>
      <label>Category <select data-filter="category"><option value="">All</option>{options(categories, {value: value.replace('_', ' ') for value in categories})}</select></label>
      <label>Search <input data-filter="search" type="search" placeholder="respondent, trait, note"></label>
      <span class="muted" data-count>{len(prediction_rows)} rows</span>
    </div>
    <div class="experiments">{experiment_toggles}</div>
  </section>
  <div class="table-wrap"><table>
    <thead><tr><th>Respondent</th><th>Experiment</th><th>Actual</th><th>Top choice</th><th>p(actual)</th><th>NLL</th><th>Brier</th><th>Correct</th><th>Probabilities</th><th>Inspect</th></tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table></div>
</main>
<div class="modal-backdrop" id="inspect-modal" role="dialog" aria-modal="true" aria-labelledby="inspect-modal-title">
  <div class="modal">
    <div class="modal-header">
      <div>
        <div class="modal-title" id="inspect-modal-title">Inspect prediction row</div>
        <div class="muted" id="inspect-modal-subtitle"></div>
      </div>
      <div class="modal-actions">
        <button type="button" data-modal-prev>Previous</button>
        <button type="button" data-modal-next>Next</button>
        <button type="button" data-modal-close>Close</button>
      </div>
    </div>
    <div class="modal-body">
      <div class="tabbar">
        <button type="button" data-tab="summary" class="active">Summary</button>
        <button type="button" data-tab="traits">Traits</button>
        <button type="button" data-tab="material">Material</button>
        <button type="button" data-tab="prompt">Prompt</button>
        <button type="button" data-tab="raw">Raw</button>
      </div>
      <section class="tab-panel active" data-tab-panel="summary"></section>
      <section class="tab-panel" data-tab-panel="traits"></section>
      <section class="tab-panel" data-tab-panel="material"></section>
      <section class="tab-panel" data-tab-panel="prompt"></section>
      <section class="tab-panel" data-tab-panel="raw"></section>
    </div>
  </div>
</div>
<script type="application/json" id="microdata-audit-data">{data_json}</script>
<script>
  const payload = JSON.parse(document.getElementById("microdata-audit-data").textContent);
  const rowById = new Map((payload.prediction_rows || []).map(row => [row.row_id, row]));
  const groupRows = Array.from(document.querySelectorAll("tr.group-row"));
  const predictionRows = Array.from(document.querySelectorAll("tr.prediction-row"));
  let visiblePredictionRows = [];
  let currentRowId = null;
  const filters = {{
    question: document.querySelector('[data-filter="question"]'),
    model: document.querySelector('[data-filter="model"]'),
    actual: document.querySelector('[data-filter="actual"]'),
    category: document.querySelector('[data-filter="category"]'),
    search: document.querySelector('[data-filter="search"]')
  }};
  const count = document.querySelector("[data-count]");
  const modal = document.getElementById("inspect-modal");
  const modalTitle = document.getElementById("inspect-modal-title");
  const modalSubtitle = document.getElementById("inspect-modal-subtitle");
  const panels = new Map(Array.from(document.querySelectorAll("[data-tab-panel]")).map(panel => [panel.dataset.tabPanel, panel]));
  function esc(value) {{
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }}
  function pretty(value) {{
    if (value === undefined || value === null || value === "") return "";
    if (typeof value === "string") return value;
    return JSON.stringify(value, null, 2);
  }}
  function pre(value) {{
    return `<pre>${{esc(pretty(value))}}</pre>`;
  }}
  function metric(value) {{
    return Number(value || 0).toFixed(3);
  }}
  function summaryCard(label, value) {{
    return `<div class="summary-card"><div class="label">${{esc(label)}}</div><div class="value">${{esc(value)}}</div></div>`;
  }}
  function renderModal(rowId) {{
    const row = rowById.get(rowId);
    if (!row) return;
    currentRowId = rowId;
    modalTitle.textContent = `${{row.respondent_id || ""}} | ${{row.approach || row.experiment_id || ""}}`;
    modalSubtitle.textContent = `${{row.heldout_question || ""}} | ${{row.model || ""}}`;
    panels.get("summary").innerHTML = `
      <div class="summary-grid">
        ${{summaryCard("Actual answer", row.actual_answer)}}
        ${{summaryCard("Top choice", row.top_choice)}}
        ${{summaryCard("Correct", row.top1_correct ? "yes" : "no")}}
        ${{summaryCard("p(actual)", metric(row.probability_actual))}}
        ${{summaryCard("NLL", metric(row.negative_log_likelihood))}}
        ${{summaryCard("Brier", metric(row.brier))}}
      </div>
      <h4>Held-out question</h4>
      ${{pre(row.heldout_question_text)}}
      <h4>Probabilities</h4>
      ${{pre(row.probabilities)}}
      <h4>Model notes</h4>
      ${{pre(row.notes || "No model notes recorded.")}}
    `;
    panels.get("traits").innerHTML = `<h4>Observed survey answers used for this twin</h4>${{pre(row.observed_answers_text || row.observed_answers)}}`;
    panels.get("material").innerHTML = `
      <h4>Supplemental twin material</h4>
      ${{pre(row.twin_material_text || "No supplemental twin material recorded.")}}
      <h4>Agent material</h4>
      ${{pre(row.agent_material_text || "No agent material recorded.")}}
    `;
    panels.get("prompt").innerHTML = `<h4>Prompt template</h4>${{pre(row.prompt_template || "No prompt template recorded.")}}`;
    panels.get("raw").innerHTML = `
      <h4>Prediction row</h4>
      ${{pre(row)}}
      <h4>Source row</h4>
      ${{pre(row.source_row)}}
    `;
    modal.classList.add("open");
  }}
  function visibleRowIds() {{
    return visiblePredictionRows.map(row => row.querySelector("[data-inspect-row]")?.dataset.inspectRow).filter(Boolean);
  }}
  function moveModal(delta) {{
    const ids = visibleRowIds();
    if (!ids.length || !currentRowId) return;
    const index = ids.indexOf(currentRowId);
    const nextIndex = index < 0 ? 0 : (index + delta + ids.length) % ids.length;
    renderModal(ids[nextIndex]);
  }}
  function closeModal() {{
    modal.classList.remove("open");
    currentRowId = null;
  }}
  function activeJobIds() {{
    return new Set(Array.from(document.querySelectorAll("[data-exp-toggle]")).filter(t => t.checked).map(t => t.dataset.expToggle));
  }}
  function applyFilters() {{
    const q = (filters.search.value || "").toLowerCase();
    const jobs = activeJobIds();
    let visible = 0;
    const visibleGroups = new Set();
    visiblePredictionRows = [];
    for (const row of predictionRows) {{
      const show = (!filters.question.value || row.dataset.question === filters.question.value)
        && (!filters.model.value || row.dataset.model === filters.model.value)
        && (!filters.actual.value || row.dataset.actual === filters.actual.value)
        && (!filters.category.value || row.dataset.category === filters.category.value)
        && jobs.has(row.dataset.jobId)
        && (!q || (row.dataset.search || "").toLowerCase().includes(q));
      row.style.display = show ? "" : "none";
      if (show) {{
        visible += 1;
        visibleGroups.add(row.dataset.groupId);
        visiblePredictionRows.push(row);
      }}
    }}
    for (const row of groupRows) {{
      const show = visibleGroups.has(row.dataset.groupId);
      row.style.display = show ? "" : "none";
    }}
    count.textContent = visible + " rows";
  }}
  for (const input of Object.values(filters)) input.addEventListener(input.type === "search" ? "input" : "change", applyFilters);
  for (const toggle of document.querySelectorAll("[data-exp-toggle]")) {{
    toggle.addEventListener("change", applyFilters);
  }}
  for (const button of document.querySelectorAll("[data-inspect-row]")) {{
    button.addEventListener("click", () => renderModal(button.dataset.inspectRow));
  }}
  for (const button of document.querySelectorAll("[data-tab]")) {{
    button.addEventListener("click", () => {{
      for (const other of document.querySelectorAll("[data-tab]")) other.classList.toggle("active", other === button);
      for (const panel of document.querySelectorAll("[data-tab-panel]")) panel.classList.toggle("active", panel.dataset.tabPanel === button.dataset.tab);
    }});
  }}
  document.querySelector("[data-modal-prev]").addEventListener("click", () => moveModal(-1));
  document.querySelector("[data-modal-next]").addEventListener("click", () => moveModal(1));
  document.querySelector("[data-modal-close]").addEventListener("click", closeModal);
  modal.addEventListener("click", event => {{
    if (event.target === modal) closeModal();
  }});
  document.addEventListener("keydown", event => {{
    if (!modal.classList.contains("open")) return;
    if (event.key === "Escape") closeModal();
    if (event.key === "ArrowLeft") moveModal(-1);
    if (event.key === "ArrowRight") moveModal(1);
  }});
  applyFilters();
</script>
</body>
</html>
"""


def render_experiment_microdata_matrix_html(payload: dict[str, Any], *, title: str) -> str:
    return render_experiment_microdata_audit_html(payload, title=title)


def write_twin_experiment_microdata(args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    experiments = selected_twin_experiments(args, sdir)
    payload = build_experiment_microdata_audit(sdir, experiments, args.metric, args.model)
    microdata_id = args.microdata_id or experiment_microdata_id(args)
    if args.path:
        html_path = Path(args.path)
        output_dir = html_path.parent
    else:
        output_dir = digital_twin_jobs_dir(sdir) / "microdata" / microdata_id
        html_path = output_dir / "audit.html"
    json_path = Path(args.json_path) if args.json_path else html_path.with_suffix(".json")
    output_dir.mkdir(parents=True, exist_ok=True)
    title = args.title or f"{args.survey} Twin Experiment Microdata"
    html = render_experiment_microdata_audit_html(payload, title=title)
    html_path.write_text(html)
    write_json(json_path, payload)
    return {
        "microdata_id": microdata_id,
        "html_path": str(html_path),
        "json_path": str(json_path),
        "group_count": payload["group_count"],
        "prediction_row_count": payload["prediction_row_count"],
        "row_count": payload["row_count"],
        "experiment_count": len(payload["experiments"]),
    }


def cmd_twin_experiment_microdata(args: argparse.Namespace) -> dict[str, Any]:
    data = write_twin_experiment_microdata(args)
    return envelope(
        "zwill twin-experiment microdata",
        "ok",
        data,
        next_steps=[f"open {data['html_path']}"],
    )

