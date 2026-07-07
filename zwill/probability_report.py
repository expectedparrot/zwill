from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .probability import probability_metrics, true_probabilities_for
from .report_common import *  # noqa: F403


def build_probability_report(rows: list[dict[str, Any]], truth: dict[str, Any]) -> dict[str, Any]:
    report_rows = []
    for row in rows:
        options = row.get("option_labels", [])
        actual = true_probabilities_for(row["question"], truth, options)
        predicted = {option: float(row.get("probabilities", {}).get(option, 0.0)) for option in options}
        uniform = {option: 1.0 / len(options) for option in options}
        actual_values = [actual[option] for option in options]
        predicted_values = [predicted[option] for option in options]
        uniform_values = [uniform[option] for option in options]
        metrics = probability_metrics(actual_values, predicted_values)
        baseline_metrics = probability_metrics(actual_values, uniform_values)
        report_rows.append(
            {
                "job_id": row["job_id"],
                "question": row["question"],
                "question_text": row.get("question_text"),
                "model": row["model"],
                "service": row.get("service"),
                "actual": actual,
                "predicted": predicted,
                "uniform": uniform,
                "mae": metrics["mae"],
                "brier": metrics["brier"],
                "kl_divergence": metrics["kl_divergence"],
                "uniform_mae": baseline_metrics["mae"],
                "uniform_brier": baseline_metrics["brier"],
                "uniform_kl_divergence": baseline_metrics["kl_divergence"],
                "brier_improvement": baseline_metrics["brier"] - metrics["brier"],
                "kl_improvement": baseline_metrics["kl_divergence"] - metrics["kl_divergence"],
                "brier_percent_improvement": (
                    (baseline_metrics["brier"] - metrics["brier"]) / baseline_metrics["brier"] * 100.0
                    if baseline_metrics["brier"]
                    else 0.0
                ),
                "kl_percent_improvement": (
                    (baseline_metrics["kl_divergence"] - metrics["kl_divergence"]) / baseline_metrics["kl_divergence"] * 100.0
                    if baseline_metrics["kl_divergence"]
                    else 0.0
                ),
            }
        )

    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report_rows:
        by_model[row["model"]].append(row)
    summary = {}
    for model, model_rows in by_model.items():
        summary[model] = {
            "rows": len(model_rows),
            "mean_mae": sum(row["mae"] for row in model_rows) / len(model_rows),
            "mean_brier": sum(row["brier"] for row in model_rows) / len(model_rows),
            "mean_kl_divergence": sum(row["kl_divergence"] for row in model_rows) / len(model_rows),
            "mean_uniform_brier": sum(row["uniform_brier"] for row in model_rows) / len(model_rows),
            "mean_uniform_kl_divergence": sum(row["uniform_kl_divergence"] for row in model_rows) / len(model_rows),
            "mean_brier_improvement": sum(row["brier_improvement"] for row in model_rows) / len(model_rows),
            "mean_kl_improvement": sum(row["kl_improvement"] for row in model_rows) / len(model_rows),
        }
        summary[model]["mean_brier_percent_improvement"] = (
            summary[model]["mean_brier_improvement"] / summary[model]["mean_uniform_brier"] * 100.0
            if summary[model]["mean_uniform_brier"]
            else 0.0
        )
        summary[model]["mean_kl_percent_improvement"] = (
            summary[model]["mean_kl_improvement"] / summary[model]["mean_uniform_kl_divergence"] * 100.0
            if summary[model]["mean_uniform_kl_divergence"]
            else 0.0
        )
    return {"rows": report_rows, "summary": summary}


def render_probability_report_html(
    survey: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    generated_analysis_markdown: str | None = None,
    generation: dict[str, Any] | None = None,
) -> str:
    display_title, _raw_title = report_display_title(survey)

    def pct(value: float) -> str:
        return f"{max(0.0, min(100.0, value * 100.0)):.2f}%"

    def series_id(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()

    score_rows = []
    best_brier = min((values["mean_brier"] for values in summary.values()), default=0.0)
    for model, values in summary.items():
        best_badge = '<b class="best">best Brier</b>' if values["mean_brier"] == best_brier else ""
        score_rows.append(
            "<tr>"
            f"<td>{escape_html(model)} {best_badge}</td>"
            f"<td class=\"num\">{values.get('questions', values.get('rows', ''))}</td>"
            f"<td class=\"num\">{values['mean_brier']:.4f}</td>"
            f"<td class=\"num\">{values['mean_uniform_brier']:.4f}</td>"
            f"<td class=\"num {'good' if values['mean_brier_improvement'] >= 0 else 'bad'}\">{values['mean_brier_improvement']:+.4f}</td>"
            f"<td class=\"num {'good' if values['mean_brier_percent_improvement'] >= 0 else 'bad'}\">{values['mean_brier_percent_improvement']:+.1f}%</td>"
            f"<td class=\"num\">{values['mean_mae']:.4f}</td>"
            f"<td class=\"num\">{values['mean_kl_divergence']:.4f}</td>"
            "</tr>"
        )
    score_table = (
        "<section class=\"model-comparison\"><h2>Model Comparisons</h2>"
        "<table><thead><tr><th>Model</th><th class=\"num\">Rows</th><th class=\"num\">Mean Brier</th><th class=\"num\">Uniform Brier</th><th class=\"num\">Delta</th><th class=\"num\">Delta %</th><th class=\"num\">MAE</th><th class=\"num\">KL divergence</th></tr></thead>"
        f"<tbody>{''.join(score_rows)}</tbody></table></section>"
    )

    if generated_analysis_markdown:
        analysis_body = markdown_to_html(remove_leading_one_shot_analysis_heading(generated_analysis_markdown))
        generation_note = ""
        if generation:
            report_model = generation.get("model") or ", ".join(generation.get("models", []) or [])
            if report_model:
                generation_note = f"<p class=\"generated-note\">Generated analysis: {escape_html(str(report_model))}</p>"
        analysis_section = f"""
    <section class="analysis-card generated-analysis">
      <h2>Analysis</h2>
      {analysis_body}
      {generation_note}
    </section>"""
    else:
        analysis_section = """
    <section class="analysis-card generated-analysis missing">
      <h2>Analysis</h2>
      <p>No generated one-shot analysis has been imported for this report yet. Export a one-shot analysis job, run it with a report-writing model, import the results, and rebuild the report bundle.</p>
    </section>"""

    model_series = []
    for row in rows:
        key = f"{row['service']}:{row['model']}"
        if key not in [item[0] for item in model_series]:
            model_series.append((key, series_id(key)))

    metric_options = [
        ("brier", "Brier score (lower is better)"),
        ("mae", "MAE (lower is better)"),
        ("kl", "KL divergence (lower is better)"),
    ]

    controls = [
        '<label class="select-control">Performance metric <select id="metric-select">' + ''.join(
            f'<option value="{value}">{label}</option>' for value, label in metric_options
        ) + '</select></label>',
        '<label><input type="checkbox" data-toggle-series="actual" checked> Actual</label>',
        *[
            f'<label><input type="checkbox" data-toggle-series="{escape_html(sid)}" checked> {escape_html(label)}</label>'
            for label, sid in model_series
        ],
        '<label><input type="checkbox" data-toggle-series="uniform" checked> Uniform</label>',
    ]

    rows_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_question[row["question"]].append(row)

    max_metric = {
        "brier": max([*(row["brier"] for row in rows), *(row["uniform_brier"] for row in rows)], default=1.0) or 1.0,
        "mae": max([*(row["mae"] for row in rows), *(row["uniform_mae"] for row in rows)], default=1.0) or 1.0,
        "kl": max([*(row["kl_divergence"] for row in rows), *(row["uniform_kl_divergence"] for row in rows)], default=1.0) or 1.0,
    }
    performance_rows = []
    for question, question_rows in rows_by_question.items():
        cells = []
        for row in question_rows:
            cells.append(
                "<div class=\"perf-cell\" "
                f"data-brier=\"{row['brier']:.6f}\" "
                f"data-mae=\"{row['mae']:.6f}\" "
                f"data-kl=\"{row['kl_divergence']:.6f}\" "
                f"data-max-brier=\"{max_metric['brier']:.6f}\" "
                f"data-max-mae=\"{max_metric['mae']:.6f}\" "
                f"data-max-kl=\"{max_metric['kl']:.6f}\">"
                f"<div class=\"perf-model\">{escape_html(row['model'])}</div>"
                "<div class=\"perf-track\"><div class=\"perf-fill\"></div><div class=\"perf-arrow\"></div></div>"
                "<div class=\"perf-value\"></div>"
                "</div>"
            )
        first_perf = question_rows[0]
        performance_rows.append(
            "<div class=\"perf-row\">"
            f"<div class=\"perf-question\"><b>{escape_html(question)}</b><span>{escape_html(question_rows[0].get('question_text') or question)}</span></div>"
            "<div class=\"perf-cells\" "
            f"data-uniform-brier=\"{first_perf['uniform_brier']:.6f}\" "
            f"data-uniform-mae=\"{first_perf['uniform_mae']:.6f}\" "
            f"data-uniform-kl=\"{first_perf['uniform_kl_divergence']:.6f}\" "
            f"data-max-brier=\"{max_metric['brier']:.6f}\" "
            f"data-max-mae=\"{max_metric['mae']:.6f}\" "
            f"data-max-kl=\"{max_metric['kl']:.6f}\">"
            f"{''.join(cells)}<div class=\"perf-row-baseline\"><span></span></div></div>"
            "</div>"
        )

    question_sections = []
    for question, question_rows in rows_by_question.items():
        first = question_rows[0]
        options = list(first["actual"].keys())
        option_blocks = []
        for option in options:
            bars = [
                "<div class=\"bar-line\" data-series=\"actual\">"
                "<div class=\"bar-series actual-dot\">Actual</div>"
                "<div class=\"track\"><div class=\"fill actual\" style=\"width:"
                f"{pct(first['actual'][option])}\"></div></div>"
                f"<div class=\"bar-value\">{first['actual'][option]:.3f}</div>"
                "</div>"
            ]
            for row in question_rows:
                label = f"{row['service']}:{row['model']}"
                sid = series_id(label)
                bars.append(
                    f"<div class=\"bar-line\" data-series=\"{escape_html(sid)}\">"
                    f"<div class=\"bar-series predicted-dot\">{escape_html(label)}</div>"
                    "<div class=\"track\"><div class=\"fill predicted\" style=\"width:"
                    f"{pct(row['predicted'][option])}\"></div></div>"
                    f"<div class=\"bar-value\">{row['predicted'][option]:.3f}</div>"
                    "</div>"
                )
            bars.append(
                "<div class=\"bar-line\" data-series=\"uniform\">"
                "<div class=\"bar-series uniform-dot\">Uniform</div>"
                "<div class=\"track\"><div class=\"fill uniform\" style=\"width:"
                f"{pct(first['uniform'][option])}\"></div></div>"
                f"<div class=\"bar-value\">{first['uniform'][option]:.3f}</div>"
                "</div>"
            )
            option_blocks.append(
                "<div class=\"option-block\">"
                f"<div class=\"option-label\">{escape_html(option)}</div>"
                f"<div class=\"bar-stack\">{''.join(bars)}</div>"
                "</div>"
            )

        model_metrics = []
        for row in question_rows:
            model_metrics.append(
                "<div class=\"metric-pill\">"
                f"<b>{escape_html(row['model'])}</b>"
                "<span class=\"metric-value\" "
                f"data-brier=\"Brier {row['brier']:.4f}\" "
                f"data-mae=\"MAE {row['mae']:.4f}\" "
                f"data-kl=\"KL {row['kl_divergence']:.4f}\">Brier {row['brier']:.4f}</span>"
                "</div>"
            )

        question_sections.append(
            "<section class=\"question-card\">"
            f"<div class=\"question-meta\"><span>{escape_html(question)}</span><span>{len(options)} options</span></div>"
            f"<h2>{escape_html(first.get('question_text') or question)}</h2>"
            f"<div class=\"metric-strip\">{''.join(model_metrics)}</div>"
            f"<div class=\"option-chart\">{''.join(option_blocks)}</div>"
            "</section>"
        )

    report_data = escape_html(json.dumps({"rows": rows, "summary": summary}, separators=(",", ":")))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(display_title)} One-Shot Marginals</title>
  <style>
    {EP_REPORT_CSS}
    :root {{ --ink:var(--ep-dark); --muted:var(--ep-gray); --line:var(--ep-border); --panel:#ffffff; --good:#0b7a3b; --bad:#b42318; --actual:#2563eb; --predicted:var(--ep-green); --uniform:#9ca3af; --metric:#475569; }}
    body {{ max-width:1180px; }}
    header {{ margin-bottom:1.5rem; }}
    main {{ padding-bottom:2rem; }}
    h2 {{ line-height:1.35; max-width:980px; }}
    .model-comparison {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; margin-bottom:22px; }}
    .model-comparison h2 {{ margin-top:0; }}
    .analysis-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; margin-bottom:22px; }}
    .analysis-card h2 {{ margin-top:0; }}
    .generated-analysis.missing {{ border-style:dashed; color:var(--muted); }}
    .generated-note {{ color:var(--muted); font-size:12px; border-top:1px solid var(--line); margin-top:18px; padding-top:10px; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .best {{ color:var(--good); background:#e7f6ed; border:1px solid #b7e0c6; border-radius:999px; padding:2px 7px; font-size:12px; margin-left:6px; }}
    label {{ display:block; color:var(--muted); font-size:12px; margin-bottom:4px; }}
    strong {{ font-variant-numeric:tabular-nums; }}
    .controls {{ display:flex; gap:10px 18px; flex-wrap:wrap; align-items:center; background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin:0 0 18px; }}
    .controls strong {{ margin-right:4px; }}
    .controls label {{ display:flex; align-items:center; gap:6px; margin:0; color:#334155; cursor:pointer; }}
    .controls input {{ margin:0; }}
    .controls select {{ min-height:30px; border:1px solid var(--line); border-radius:6px; background:#fff; color:var(--ink); padding:3px 8px; }}
    .select-control {{ font-size:12px; color:var(--muted); }}
    .performance-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; margin:0 0 18px; }}
    .performance-card h2 {{ margin-top:0; }}
    .metric-note {{ margin:4px 0 14px; color:var(--muted); font-size:13px; }}
    .metric-note b {{ color:var(--ink); }}
    .perf-row {{ display:grid; grid-template-columns:minmax(220px,0.7fr) minmax(420px,1.5fr); gap:18px; padding:12px 0; border-top:1px solid var(--line); }}
    .perf-row:first-of-type {{ border-top:0; }}
    .perf-question b {{ display:block; margin-bottom:4px; }}
    .perf-question span {{ display:block; color:var(--muted); font-size:12px; line-height:1.35; }}
    .perf-cells {{ display:grid; gap:8px; position:relative; }}
    .perf-cell {{ display:grid; grid-template-columns:minmax(120px,0.45fr) minmax(160px,1fr) 72px; gap:10px; align-items:center; }}
    .perf-model {{ color:#334155; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .perf-track {{ position:relative; height:16px; border-radius:999px; background:#e8edf3; overflow:hidden; }}
    .perf-row-baseline {{ position:absolute; top:0; height:0; width:0; border-left:3px dashed #eab308; z-index:3; pointer-events:none; }}
    .perf-row-baseline span {{ position:absolute; top:-24px; left:0; transform:translateX(-50%); padding:2px 6px; border-radius:4px; border:1px solid #eab308; background:#fef3c7; color:#111827; font-size:10px; font-weight:700; line-height:1.2; white-space:nowrap; box-shadow:0 1px 2px rgba(0,0,0,.12); }}
    .perf-fill {{ position:absolute; left:0; top:0; height:100%; width:0%; border-radius:999px; background:var(--metric); }}
    .perf-arrow {{ position:absolute; top:50%; height:0; border-top:2px solid currentColor; color:var(--good); z-index:2; transform:translateY(-50%); pointer-events:none; }}
    .perf-arrow::before, .perf-arrow::after {{ content:""; position:absolute; top:-5px; width:0; height:0; border-top:4px solid transparent; border-bottom:4px solid transparent; }}
    .perf-arrow::before {{ left:-1px; border-right:6px solid currentColor; }}
    .perf-arrow::after {{ right:-1px; border-left:6px solid currentColor; }}
    .perf-arrow.worse {{ color:var(--bad); }}
    .perf-arrow.hidden {{ display:none; }}
    .perf-value {{ text-align:right; font-size:12px; font-variant-numeric:tabular-nums; }}
    .question-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; margin:0 0 16px; }}
    .question-meta {{ display:flex; gap:12px; justify-content:space-between; color:var(--muted); font-size:13px; }}
    .metric-strip {{ display:flex; gap:8px; flex-wrap:wrap; margin:0 0 14px; }}
    .metric-pill {{ display:flex; gap:10px; align-items:center; border:1px solid var(--line); border-radius:999px; padding:6px 10px; background:#fbfcfd; font-size:12px; }}
    .option-chart {{ display:grid; gap:12px; }}
    .option-block {{ display:grid; grid-template-columns:minmax(220px,0.8fr) minmax(420px,2fr); gap:16px; align-items:start; border-top:1px solid var(--line); padding-top:12px; }}
    .option-block:first-child {{ border-top:0; padding-top:0; }}
    .option-label {{ color:#334155; line-height:1.3; font-size:14px; font-weight:650; }}
    .bar-stack {{ display:grid; gap:6px; }}
    .bar-line {{ display:grid; grid-template-columns:minmax(150px,0.55fr) minmax(220px,1.45fr) 52px; gap:10px; align-items:center; }}
    .bar-series {{ font-size:12px; color:#475569; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .bar-series::before {{ content:""; display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; }}
    .actual-dot::before {{ background:var(--actual); }}
    .predicted-dot::before {{ background:var(--predicted); }}
    .uniform-dot::before {{ background:var(--uniform); }}
    .track {{ height:16px; border-radius:999px; background:#e8edf3; overflow:hidden; box-shadow:inset 0 0 0 1px rgba(0,0,0,.04); }}
    .fill {{ height:100%; border-radius:999px; }}
    .fill.actual {{ background:var(--actual); }}
    .fill.predicted {{ background:var(--predicted); }}
    .fill.uniform {{ background:var(--uniform); }}
    .bar-value {{ text-align:right; font-size:12px; font-variant-numeric:tabular-nums; }}
    .good {{ color:var(--good); font-weight:650; }}
    .bad {{ color:var(--bad); font-weight:650; }}
    @media (max-width: 860px) {{ .model-comparison {{ overflow-x:auto; }} .perf-row {{ grid-template-columns:1fr; }} .question-meta {{ display:block; }} .option-block {{ grid-template-columns:1fr; }} .bar-line {{ grid-template-columns:1fr; gap:4px; }} .bar-value {{ text-align:left; }} }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <header>
    <h1>{escape_html(display_title)} One-Shot Marginals</h1>
    <div class="subtle">Survey id: <code>{escape_html(survey)}</code></div>
    <div class="subtle">Predicted one-shot response probabilities compared with committed weighted respondent marginals and a uniform baseline.</div>
  </header>
  <main>
    {analysis_section}
    {score_table}
    <section class="controls"><strong>Show</strong>{''.join(controls)}</section>
    <section class="performance-card">
      <h2>Performance by Question and Model</h2>
      <div class="subtle">The selected metric controls this plot and the metric pills below.</div>
      <div class="metric-note" id="metric-note"></div>
      {''.join(performance_rows)}
    </section>
    {''.join(question_sections)}
  </main>
  <script type="application/json" id="report-data">{report_data}</script>
  <script>
    const toggles = document.querySelectorAll('[data-toggle-series]');
    const metricSelect = document.getElementById('metric-select');
    function updateSeries() {{
      toggles.forEach((toggle) => {{
        const id = toggle.dataset.toggleSeries;
        document.querySelectorAll(`[data-series="${{id}}"]`).forEach((el) => {{
          el.style.display = toggle.checked ? '' : 'none';
        }});
      }});
    }}
    function metricDatasetKey(metric) {{
      return metric.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
    }}
    function updateMetric() {{
      const metric = metricSelect.value;
      const key = metricDatasetKey(metric);
      const maxKey = 'max' + key.charAt(0).toUpperCase() + key.slice(1);
      document.getElementById('metric-note').innerHTML = '<b>Lower is better.</b> The yellow dashed marker is uniform; green arrows beat uniform and red arrows are worse.';
      document.querySelectorAll('.metric-value').forEach((el) => {{
        el.textContent = el.dataset[key] || '';
        el.classList.remove('good', 'bad');
      }});
      document.querySelectorAll('.perf-cell').forEach((el) => {{
        const value = Number(el.dataset[key] || 0);
        const max = Math.max(Number(el.dataset[maxKey] || 1), 0.000001);
        const width = Math.min(100, value / max * 100);
        const parent = el.closest('.perf-cells');
        const uniformKey = 'uniform' + key.charAt(0).toUpperCase() + key.slice(1);
        const uniformValue = Number(parent?.dataset[uniformKey] || 0);
        const uniformPct = Math.min(100, uniformValue / max * 100);
        const fill = el.querySelector('.perf-fill');
        const arrow = el.querySelector('.perf-arrow');
        fill.style.width = `${{width}}%`;
        fill.style.left = '0';
        const start = Math.min(width, uniformPct);
        const end = Math.max(width, uniformPct);
        arrow.style.left = `${{start}}%`;
        arrow.style.width = `${{end - start}}%`;
        arrow.classList.toggle('worse', value > uniformValue);
        arrow.classList.toggle('hidden', Math.abs(end - start) < 1);
        el.querySelector('.perf-value').textContent = value.toFixed(4);
      }});
      document.querySelectorAll('.perf-cells').forEach((el) => {{
        const uniformKey = 'uniform' + key.charAt(0).toUpperCase() + key.slice(1);
        const uniformValue = Number(el.dataset[uniformKey] || 0);
        const max = Math.max(Number(el.dataset[maxKey] || 1), 0.000001);
        const pct = Math.min(100, uniformValue / max * 100);
        const tracks = Array.from(el.querySelectorAll('.perf-track'));
        const baseline = el.querySelector('.perf-row-baseline');
        if (!tracks.length || !baseline) return;
        const bounds = el.getBoundingClientRect();
        const first = tracks[0].getBoundingClientRect();
        const last = tracks[tracks.length - 1].getBoundingClientRect();
        baseline.style.left = `${{first.left - bounds.left + first.width * pct / 100}}px`;
        baseline.style.top = `${{first.top - bounds.top - 8}}px`;
        baseline.style.height = `${{last.bottom - first.top + 16}}px`;
        baseline.querySelector('span').textContent = `uniform ${{uniformValue.toFixed(4)}}`;
      }});
    }}
    toggles.forEach((toggle) => toggle.addEventListener('change', updateSeries));
    metricSelect.addEventListener('change', updateMetric);
    window.addEventListener('resize', updateMetric);
    updateSeries();
    updateMetric();
  </script>
</body>
</html>
"""
