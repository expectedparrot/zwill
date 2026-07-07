from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .report_common import *  # noqa: F403
from .twin_baseline import conditional_baseline_appendix_html, has_conditional_baseline
from .twin_bootstrap import bootstrap_ci_section_html
from .twin_scoring import skill_score_section_html


def render_twin_supporting_artifacts_section(pages: list[dict[str, Any]], output_dir: Path) -> str:
    items = []
    for page in pages:
        if page.get("primary", True):
            continue
        title = html.escape(str(page.get("title") or page.get("page_id") or ""), quote=True)
        description = html.escape(str(page.get("description") or ""), quote=True)
        path = page.get("path")
        href = html.escape(bundle_rel_link(path, output_dir), quote=True) if path else ""
        if path:
            action = f'<a class="button secondary" href="{href}">Open</a>'
        else:
            status = html.escape(str(page.get("status", "not_ready")).replace("_", " ").title(), quote=True)
            action = f'<span class="badge">{status}</span>'
        items.append(
            "<li>"
            f"<div><strong>{title}</strong><span>{description}</span></div>"
            f"{action}"
            "</li>"
        )
    if not items:
        return ""
    return f"""
    <section class="summary-card">
      <h2>Supporting Artifacts</h2>
      <p class="subtle">Audit and comparison pages are generated for inspection, but this page is the main twin validation report.</p>
      <ul class="supporting-artifacts">{''.join(items)}</ul>
    </section>
    <style>
      .supporting-artifacts {{ list-style:none; padding:0; margin:0; display:grid; gap:10px; }}
      .supporting-artifacts li {{ border:1px solid var(--line); border-radius:8px; padding:12px; display:flex; justify-content:space-between; gap:14px; align-items:center; background:#fff; }}
      .supporting-artifacts span {{ display:block; color:var(--muted); }}
    </style>
"""


def render_twin_value_diagnostics_section(diagnostics: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    joint = diagnostics.get("joint_structure") or {}
    subgroup = diagnostics.get("subgroup_marginals") or {}
    conditional = diagnostics.get("conditional_consistency") or {}

    def metric(value: Any, precision: int = 3) -> str:
        return "" if value is None else f"{float(value):.{precision}f}"

    joint_rows = []
    for row in (joint.get("rows") or [])[:20]:
        joint_rows.append(
            "<tr>"
            f"<td>{esc(row.get('left_question'))}<br><span>{esc(row.get('left_question_text', ''))}</span></td>"
            f"<td>{esc(row.get('right_question'))}<br><span>{esc(row.get('right_question_text', ''))}</span></td>"
            f"<td>{esc(row.get('model_label'))}</td>"
            f"<td class=\"numeric\">{esc(row.get('respondents'))}</td>"
            f"<td class=\"numeric\">{metric(row.get('joint_l1'))}</td>"
            f"<td class=\"numeric\">{metric(row.get('empirical_cramers_v'))}</td>"
            f"<td class=\"numeric\">{metric(row.get('twin_cramers_v'))}</td>"
            f"<td>{esc(row.get('warning', ''))}</td>"
            "</tr>"
        )
    subgroup_rows = []
    for row in (subgroup.get("rows") or [])[:20]:
        subgroup_rows.append(
            "<tr>"
            f"<td>{esc(row.get('heldout_question'))}<br><span>{esc(row.get('heldout_question_text', ''))}</span></td>"
            f"<td>{esc(row.get('segment_question'))} = {esc(row.get('segment_value'))}</td>"
            f"<td>{esc(row.get('model_label'))}</td>"
            f"<td class=\"numeric\">{esc(row.get('rows'))}</td>"
            f"<td class=\"numeric\">{metric(row.get('l1'))}</td>"
            f"<td class=\"numeric\">{metric(row.get('js_divergence'))}</td>"
            f"<td>{esc(row.get('warning', ''))}</td>"
            "</tr>"
        )
    conditional_rows = []
    for row in (conditional.get("rows") or [])[:20]:
        conditional_rows.append(
            "<tr>"
            f"<td>{esc(row.get('condition_question'))} = {esc(row.get('condition_value'))}</td>"
            f"<td>{esc(row.get('target_question'))}<br><span>{esc(row.get('target_question_text', ''))}</span></td>"
            f"<td>{esc(row.get('model_label'))}</td>"
            f"<td class=\"numeric\">{esc(row.get('rows'))}</td>"
            f"<td class=\"numeric\">{metric(row.get('l1'))}</td>"
            f"<td class=\"numeric\">{metric(row.get('js_divergence'))}</td>"
            f"<td>{esc(row.get('warning', ''))}</td>"
            "</tr>"
        )
    return f"""
    <section class="summary-card twin-value-diagnostics">
      <h2>Joint Structure And Slicing Diagnostics</h2>
      <p class="subtle">These deterministic diagnostics test twin-specific claims that one-shot aggregate marginals cannot answer: crosstabs, subgroup slices, and conditional consistency. Lower L1 and JS values mean the twin-implied distribution is closer to the empirical distribution.</p>
      <div class="diagnostic-summary-grid">
        <div><b>{esc(joint.get('pair_count', 0))}</b><span>joint question pairs scored</span></div>
        <div><b>{esc(subgroup.get('cell_count', 0))}</b><span>subgroup cells scored</span></div>
        <div><b>{esc(conditional.get('cell_count', 0))}</b><span>conditional cells scored</span></div>
      </div>
      <h3>Best Recovered Crosstabs</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Question A</th><th>Question B</th><th>Twin set</th><th>Rows</th><th>Joint L1</th><th>Empirical V</th><th>Twin V</th><th>Warning</th></tr></thead>
          <tbody>{''.join(joint_rows) or '<tr><td colspan="8">No joint-structure diagnostics met the cell-size threshold.</td></tr>'}</tbody>
        </table>
      </div>
      <h3>Largest Subgroup Marginal Gaps</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Held-out</th><th>Segment</th><th>Twin set</th><th>Rows</th><th>L1</th><th>JS</th><th>Warning</th></tr></thead>
          <tbody>{''.join(subgroup_rows) or '<tr><td colspan="7">No subgroup diagnostics met the cell-size threshold.</td></tr>'}</tbody>
        </table>
      </div>
      <h3>Largest Conditional Consistency Gaps</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Condition</th><th>Target</th><th>Twin set</th><th>Rows</th><th>L1</th><th>JS</th><th>Warning</th></tr></thead>
          <tbody>{''.join(conditional_rows) or '<tr><td colspan="7">No conditional diagnostics met the cell-size threshold.</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <style>
      .twin-value-diagnostics h3 {{ margin-top:18px; }}
      .twin-value-diagnostics td span {{ color:var(--muted); font-size:12px; }}
      .diagnostic-summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; margin:12px 0 16px; }}
      .diagnostic-summary-grid div {{ border:1px solid var(--line); border-radius:8px; padding:10px; background:#fbfcfd; }}
      .diagnostic-summary-grid b {{ display:block; font-size:20px; }}
      .diagnostic-summary-grid span {{ color:var(--muted); font-size:12px; }}
    </style>
"""


def render_twin_report_html(
    survey: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
    health: dict[str, Any] | None = None,
) -> str:
    display_title, _raw_title = report_display_title(f"{survey} digital twin validation")

    def summarize_rows(model_rows: list[dict[str, Any]]) -> dict[str, float]:
        marginal_rows = [row for row in model_rows if row.get("empirical_marginal_probability_actual", row.get("marginal_probability_actual")) is not None]
        values = {
            "rows": len(model_rows),
            "accuracy": sum(row["top1_correct"] for row in model_rows) / len(model_rows),
            "mean_probability_actual": sum(row["probability_actual"] for row in model_rows) / len(model_rows),
            "mean_negative_log_likelihood": sum(row["negative_log_likelihood"] for row in model_rows) / len(model_rows),
            "mean_brier": sum(row["brier"] for row in model_rows) / len(model_rows),
        }
        if marginal_rows:
            values.update(
                {
                    "mean_empirical_marginal_probability_actual": sum(
                        row.get("empirical_marginal_probability_actual", row.get("marginal_probability_actual")) for row in marginal_rows
                    )
                    / len(marginal_rows),
                    "mean_empirical_marginal_negative_log_likelihood": sum(
                        row.get("empirical_marginal_negative_log_likelihood", row.get("marginal_negative_log_likelihood"))
                        for row in marginal_rows
                    )
                    / len(marginal_rows),
                    "mean_empirical_marginal_brier": sum(
                        row.get("empirical_marginal_brier", row.get("marginal_brier")) for row in marginal_rows
                    )
                    / len(marginal_rows),
                }
            )
        return values

    heldout_questions = sorted({str(row.get("heldout_question")) for row in rows})
    heldout_texts = sorted({str(row.get("heldout_question_text")) for row in rows if row.get("heldout_question_text")})
    respondent_count = len({row.get("respondent_id") for row in rows})
    model_names = sorted({str(row.get("twin_set_label") or row.get("model_label") or row.get("model")) for row in rows})
    skill_score_section = skill_score_section_html(rows)
    bootstrap_ci_section = bootstrap_ci_section_html(rows)
    conditional_baseline_appendix = (
        conditional_baseline_appendix_html() if has_conditional_baseline(model_names) else ""
    )
    context_counts = sorted({len(row.get("observed_answers", [])) for row in rows})
    actual_counts: dict[str, int] = {}
    for row in rows:
        actual = str(row.get("actual_answer"))
        actual_counts[actual] = actual_counts.get(actual, 0) + 1
    actual_summary = ", ".join(f"{answer}: {count}" for answer, count in sorted(actual_counts.items()))
    context_summary = ", ".join(str(count) for count in context_counts)
    heldout_label = ", ".join(heldout_questions)
    heldout_text = heldout_texts[0] if len(heldout_texts) == 1 else f"{len(heldout_texts)} question texts"
    option_counts = sorted({len(row.get("option_labels", [])) for row in rows if row.get("option_labels")})
    random_accuracy = 1.0 / option_counts[0] if len(option_counts) == 1 and option_counts[0] else None
    random_error = 1.0 - random_accuracy if random_accuracy is not None else None

    study_summary_rows = [
        ("Held-out question", heldout_label),
        ("Held-out text", heldout_text),
        ("Respondents", respondent_count),
        ("Models", ", ".join(model_names)),
        ("Context answers per twin", context_summary),
        ("Actual answer counts", actual_summary),
        ("Random-choice accuracy", f"{random_accuracy:.3f}" if random_accuracy is not None else "mixed option counts"),
        ("Random-choice error rate", f"{random_error:.3f}" if random_error is not None else "mixed option counts"),
    ]
    study_summary = "".join(
        f"<tr><th>{escape_html(label)}</th><td>{escape_html(value)}</td></tr>"
        for label, value in study_summary_rows
    )
    import_health = (health or {}).get("import", {})
    health_rows = [
        ("Job id", str((health or {}).get("job_id", ""))),
        ("Raw rows", str(import_health.get("row_count", len(rows)))),
        ("Extracted rows", str(import_health.get("extracted_count", len(rows)))),
        ("Import issues", str(import_health.get("issue_count", 0))),
        ("Stored raw", str(import_health.get("stored_path", ""))),
    ]
    health_table_rows = "".join(
        f"<tr><th>{escape_html(label)}</th><td>{escape_html(value)}</td></tr>"
        for label, value in health_rows
        if value
    )
    performance_rows = []
    for model, values in summary.items():
        error_rate = 1.0 - values["top1_accuracy"]
        nll_delta = values["mean_uniform_negative_log_likelihood"] - values["mean_negative_log_likelihood"]
        brier_delta = values["mean_uniform_brier"] - values["mean_brier"]
        p_delta = values["mean_probability_actual"] - values["mean_uniform_probability_actual"]
        marginal_p = values.get("mean_empirical_marginal_probability_actual", values.get("mean_marginal_probability_actual"))
        marginal_nll = values.get(
            "mean_empirical_marginal_negative_log_likelihood",
            values.get("mean_marginal_negative_log_likelihood"),
        )
        marginal_brier = values.get("mean_empirical_marginal_brier", values.get("mean_marginal_brier"))
        performance_rows.append(
            "<tr>"
            f"<td>{escape_html(model)}</td>"
            f"<td class=\"numeric\">{values['rows']}</td>"
            f"<td class=\"numeric\">{values['top1_accuracy']:.3f}</td>"
            f"<td class=\"numeric\">{error_rate:.3f}</td>"
            f"<td class=\"numeric\">{values['mean_probability_actual']:.3f}</td>"
            f"<td class=\"numeric\">{values['mean_uniform_probability_actual']:.3f}</td>"
            f"<td class=\"numeric {'good' if p_delta >= 0 else 'bad'}\">{p_delta:+.3f}</td>"
            f"<td class=\"numeric\">{marginal_p:.3f}</td>" if marginal_p is not None else "<td></td>"
            f"<td class=\"numeric {'good' if (values['mean_probability_actual'] - marginal_p) >= 0 else 'bad'}\">{values['mean_probability_actual'] - marginal_p:+.3f}</td>" if marginal_p is not None else "<td></td>"
            f"<td class=\"numeric\">{values['mean_negative_log_likelihood']:.3f}</td>"
            f"<td class=\"numeric\">{values['mean_uniform_negative_log_likelihood']:.3f}</td>"
            f"<td class=\"numeric {'good' if nll_delta >= 0 else 'bad'}\">{nll_delta:+.3f}</td>"
            f"<td class=\"numeric\">{marginal_nll:.3f}</td>" if marginal_nll is not None else "<td></td>"
            f"<td class=\"numeric {'good' if (marginal_nll - values['mean_negative_log_likelihood']) >= 0 else 'bad'}\">{marginal_nll - values['mean_negative_log_likelihood']:+.3f}</td>" if marginal_nll is not None else "<td></td>"
            f"<td class=\"numeric\">{values['mean_brier']:.3f}</td>"
            f"<td class=\"numeric\">{values['mean_uniform_brier']:.3f}</td>"
            f"<td class=\"numeric {'good' if brier_delta >= 0 else 'bad'}\">{brier_delta:+.3f}</td>"
            f"<td class=\"numeric\">{marginal_brier:.3f}</td>" if marginal_brier is not None else "<td></td>"
            f"<td class=\"numeric {'good' if (marginal_brier - values['mean_brier']) >= 0 else 'bad'}\">{marginal_brier - values['mean_brier']:+.3f}</td>" if marginal_brier is not None else "<td></td>"
            "</tr>"
        )
    by_question_model: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("heldout_question")), str(row.get("twin_set_label") or row.get("model_label") or row.get("model")))
        by_question_model.setdefault(key, []).append(row)
    heldout_performance_rows = []
    for (question, model), model_rows in sorted(by_question_model.items()):
        values = summarize_rows(model_rows)
        marginal_p = values.get("mean_empirical_marginal_probability_actual", values.get("mean_marginal_probability_actual"))
        marginal_nll = values.get(
            "mean_empirical_marginal_negative_log_likelihood",
            values.get("mean_marginal_negative_log_likelihood"),
        )
        marginal_brier = values.get("mean_empirical_marginal_brier", values.get("mean_marginal_brier"))
        heldout_performance_rows.append(
            "<tr>"
            f"<td>{escape_html(question)}</td>"
            f"<td>{escape_html(model)}</td>"
            f"<td class=\"numeric\">{values['rows']:.0f}</td>"
            f"<td class=\"numeric\">{values['accuracy']:.3f}</td>"
            f"<td class=\"numeric\">{1.0 - values['accuracy']:.3f}</td>"
            f"<td class=\"numeric\">{values['mean_probability_actual']:.3f}</td>"
            f"<td class=\"numeric\">{marginal_p:.3f}</td>" if marginal_p is not None else "<td></td>"
            f"<td class=\"numeric {'good' if (values['mean_probability_actual'] - marginal_p) >= 0 else 'bad'}\">{values['mean_probability_actual'] - marginal_p:+.3f}</td>" if marginal_p is not None else "<td></td>"
            f"<td class=\"numeric\">{values['mean_negative_log_likelihood']:.3f}</td>"
            f"<td class=\"numeric\">{marginal_nll:.3f}</td>" if marginal_nll is not None else "<td></td>"
            f"<td class=\"numeric {'good' if (marginal_nll - values['mean_negative_log_likelihood']) >= 0 else 'bad'}\">{marginal_nll - values['mean_negative_log_likelihood']:+.3f}</td>" if marginal_nll is not None else "<td></td>"
            f"<td class=\"numeric\">{values['mean_brier']:.3f}</td>"
            f"<td class=\"numeric\">{marginal_brier:.3f}</td>" if marginal_brier is not None else "<td></td>"
            f"<td class=\"numeric {'good' if (marginal_brier - values['mean_brier']) >= 0 else 'bad'}\">{marginal_brier - values['mean_brier']:+.3f}</td>" if marginal_brier is not None else "<td></td>"
            "</tr>"
        )
    metric_definitions = [
        ("Accuracy", "Share of twins where the model's highest-probability option matched the respondent's actual answer.", "Higher is better."),
        ("Error", "Share of twins where the model's highest-probability option did not match the actual answer.", "Lower is better."),
        ("p(actual)", "Mean probability assigned to the respondent's actual answer.", "Higher is better."),
        ("NLL", "Negative log likelihood: -log(p(actual)). Penalizes confident probability on the wrong option.", "Lower is better."),
        ("Brier", "Squared error against the one-hot actual answer across options.", "Lower is better."),
        ("Random baseline", "Uniform random choice over the available options for the held-out question.", "Compare model values to this baseline."),
        (
            "Empirical marginal baseline",
            "Observed respondent marginal distribution for this held-out question. This is useful for known survey items, but is not available for a truly new question.",
            "A stronger oracle-style baseline than random choice.",
        ),
        ("Delta", "Model improvement over a baseline. For p(actual), model minus baseline; for NLL and Brier, baseline minus model.", "Positive is better."),
    ]
    metric_definition_rows = "".join(
        "<tr>"
        f"<td>{escape_html(metric)}</td>"
        f"<td>{escape_html(definition)}</td>"
        f"<td>{escape_html(interpretation)}</td>"
        "</tr>"
        for metric, definition, interpretation in metric_definitions
    )

    best_nll = min((values["mean_negative_log_likelihood"] for values in summary.values()), default=0.0)
    score_cards = []
    for model, values in summary.items():
        score_cards.append(
            "<article class=\"score-card\">"
            f"<div class=\"score-title\"><span>{escape_html(model)}</span>{'<b>best NLL</b>' if values['mean_negative_log_likelihood'] == best_nll else ''}</div>"
            "<div class=\"score-grid\">"
            f"<div><label>Mean p(actual)</label><strong>{values['mean_probability_actual']:.3f}</strong></div>"
            f"<div><label>Uniform p</label><strong>{values['mean_uniform_probability_actual']:.3f}</strong></div>"
            f"<div><label>Mean NLL</label><strong>{values['mean_negative_log_likelihood']:.3f}</strong></div>"
            f"<div><label>Error rate</label><strong>{1.0 - values['top1_accuracy']:.3f}</strong></div>"
            f"<div><label>ECE</label><strong>{values.get('expected_calibration_error', 0.0):.3f}</strong></div>"
            f"<div><label>NLL p95</label><strong>{values.get('negative_log_likelihood_p95', 0.0):.3f}</strong></div>"
            f"<div><label>Mean confidence</label><strong>{values.get('mean_top_confidence', 0.0):.3f}</strong></div>"
            "</div>"
            "</article>"
        )

    diagnostics = diagnostics or {}
    baseline_rows = []
    for model, values in diagnostics.get("baseline_comparison", {}).items():
        baseline_rows.append(
            "<tr>"
            f"<td>{escape_html(model)}</td>"
            f"<td class=\"numeric {'good' if values.get('p_actual_vs_empirical', 0) >= 0 else 'bad'}\">{values.get('p_actual_vs_empirical'):+.3f}</td>"
            f"<td class=\"numeric {'good' if values.get('nll_vs_empirical', 0) >= 0 else 'bad'}\">{values.get('nll_vs_empirical'):+.3f}</td>"
            f"<td class=\"numeric {'good' if values.get('brier_vs_empirical', 0) >= 0 else 'bad'}\">{values.get('brier_vs_empirical'):+.3f}</td>"
            "</tr>"
        )
    empirical_win_rows = "".join(
        "<tr>"
        f"<td>{escape_html(item.get('heldout_question'))}</td>"
        f"<td>{escape_html(item.get('model'))}</td>"
        f"<td class=\"numeric bad\">{item.get('nll_vs_empirical'):+.3f}</td>"
        "</tr>"
        for item in diagnostics.get("empirical_wins", [])[:10]
    )
    model_win_rows = "".join(
        "<tr>"
        f"<td>{escape_html(item.get('heldout_question'))}</td>"
        f"<td>{escape_html(item.get('model'))}</td>"
        f"<td class=\"numeric good\">{item.get('nll_vs_empirical'):+.3f}</td>"
        "</tr>"
        for item in diagnostics.get("model_wins", [])[:10]
    )
    calibration_rows = []
    for model, bins in diagnostics.get("calibration", {}).items():
        for item in bins:
            if item.get("rows"):
                calibration_rows.append(
                    "<tr>"
                    f"<td>{escape_html(model)}</td>"
                    f"<td>{escape_html(item.get('bin'))}</td>"
                    f"<td class=\"numeric\">{item.get('rows')}</td>"
                    f"<td class=\"numeric\">{item.get('mean_confidence'):.3f}</td>"
                    f"<td class=\"numeric\">{item.get('accuracy'):.3f}</td>"
                    "</tr>"
                )
    worst_miss_rows = []
    for row in diagnostics.get("worst_misses", [])[:12]:
        worst_miss_rows.append(
            "<tr>"
            f"<td>{escape_html(row.get('respondent_id'))}</td>"
            f"<td>{escape_html(row.get('heldout_question'))}</td>"
            f"<td>{escape_html(row.get('model_label') or row.get('model'))}</td>"
            f"<td>{escape_html(row.get('actual_answer'))}</td>"
            f"<td class=\"numeric\">{row.get('probability_actual'):.3f}</td>"
            f"<td class=\"numeric\">{row.get('negative_log_likelihood'):.3f}</td>"
            f"<td>{escape_html(row.get('notes'))}</td>"
            "</tr>"
        )
    overconfident_rows = []
    for row in diagnostics.get("overconfident_misses", [])[:12]:
        predicted = row.get("probabilities", {})
        top_option, top_probability = max(predicted.items(), key=lambda item: item[1]) if predicted else ("", 0.0)
        overconfident_rows.append(
            "<tr>"
            f"<td>{escape_html(row.get('respondent_id'))}</td>"
            f"<td>{escape_html(row.get('heldout_question'))}</td>"
            f"<td>{escape_html(row.get('model_label') or row.get('model'))}</td>"
            f"<td>{escape_html(row.get('actual_answer'))}</td>"
            f"<td>{escape_html(top_option)}</td>"
            f"<td class=\"numeric\">{float(top_probability):.3f}</td>"
            f"<td class=\"numeric\">{row.get('probability_actual'):.3f}</td>"
            f"<td class=\"numeric\">{row.get('negative_log_likelihood'):.3f}</td>"
            "</tr>"
        )
    confusion_rows = []
    for key, actuals in diagnostics.get("confusion", {}).items():
        heldout, _, model = key.partition("::")
        for actual, predicted_counts in actuals.items():
            for predicted, count in predicted_counts.items():
                confusion_rows.append(
                    "<tr>"
                    f"<td>{escape_html(heldout)}</td>"
                    f"<td>{escape_html(model)}</td>"
                    f"<td>{escape_html(actual)}</td>"
                    f"<td>{escape_html(predicted)}</td>"
                    f"<td class=\"numeric\">{count}</td>"
                    "</tr>"
                )

    marginal_comparison_rows = []
    marginal_options_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for option in diagnostics.get("marginal_options", []):
        key = (str(option.get("heldout_question")), str(option.get("model_label")))
        marginal_options_by_key.setdefault(key, []).append(option)
    for comparison in diagnostics.get("marginal_comparisons", []):
        key = (str(comparison.get("heldout_question")), str(comparison.get("model_label")))
        option_rows = []
        for option in sorted(
            marginal_options_by_key.get(key, []),
            key=lambda item: (-float(item.get("predicted_probability") or 0.0), str(item.get("option_label"))),
        ):
            predicted_probability = float(option.get("predicted_probability") or 0.0)
            target_probability = option.get("target_probability")
            target_probability = float(target_probability) if target_probability is not None else None
            max_probability = max(predicted_probability, target_probability or 0.0, 0.001)
            predicted_width = 100 * predicted_probability / max_probability
            target_width = 100 * (target_probability or 0.0) / max_probability
            target_bar = (
                f"<div><span class=\"target-bar\" style=\"width:{target_width:.1f}%\"></span><b>{target_probability:.1%}</b></div>"
                if target_probability is not None
                else "<div class=\"missing-target\">No empirical target</div>"
            )
            option_rows.append(
                "<div class=\"marginal-option\">"
                f"<div class=\"marginal-label\">{escape_html(option.get('option_label'))}</div>"
                "<div class=\"marginal-bars\">"
                f"<div><span class=\"twin-bar\" style=\"width:{predicted_width:.1f}%\"></span><b>{predicted_probability:.1%}</b></div>"
                f"{target_bar}"
                "</div>"
                "</div>"
            )
        metric_bits = []
        if comparison.get("l1") is not None:
            metric_bits = [
                f"L1 {float(comparison.get('l1')):.3f}",
                f"MAE {float(comparison.get('mae')):.3f}",
                f"JS {float(comparison.get('js_divergence')):.3f}",
                "top agrees" if comparison.get("top_option_agrees") else "top differs",
            ]
        else:
            metric_bits = ["No empirical marginal available"]
        marginal_comparison_rows.append(
            "<article class=\"marginal-card\">"
            "<div class=\"marginal-head\">"
            f"<div><h3>{escape_html(comparison.get('heldout_question'))}</h3><p>{escape_html(comparison.get('heldout_question_text') or '')}</p></div>"
            f"<div class=\"marginal-meta\"><b>{escape_html(comparison.get('model_label'))}</b><span>{escape_html(comparison.get('respondent_count'))} respondents</span></div>"
            "</div>"
            f"<div class=\"marginal-metrics\">{' | '.join(escape_html(bit) for bit in metric_bits)}</div>"
            f"{''.join(option_rows)}"
            "</article>"
        )

    rows_sorted = sorted(
        rows,
        key=lambda row: (
            row.get("heldout_question") or "",
            row.get("respondent_id") or "",
            row.get("model_label") or row.get("model") or "",
        ),
    )
    detail_row_limit = 250
    detail_rows_omitted = max(0, len(rows_sorted) - detail_row_limit)
    question_options = "".join(f"<option value=\"{escape_html(question)}\">{escape_html(question)}</option>" for question in heldout_questions)
    model_options = "".join(f"<option value=\"{escape_html(model)}\">{escape_html(model)}</option>" for model in model_names)
    table_rows = []
    for row in rows_sorted[:detail_row_limit]:
        predicted = row.get("probabilities", {})
        top_option = max(predicted.items(), key=lambda item: item[1])[0] if predicted else None
        probability_cells = "".join(
            "<div class=\"prob-row\">"
            f"<span>{escape_html(option)}</span>"
            f"<b>{float(probability):.3f}</b>"
            f"{'<em>actual</em>' if option == row.get('actual_answer') else ''}"
            "</div>"
            for option, probability in predicted.items()
        )
        raw_response = json.dumps(
            {
                "probabilities": row.get("raw_probabilities"),
                "notes": row.get("notes"),
            },
            ensure_ascii=False,
        )
        display_model = row.get("twin_set_label") or row.get("model_label") or row.get("model")
        table_rows.append(
            "<tr "
            f"data-heldout=\"{escape_html(row.get('heldout_question'))}\" "
            f"data-model=\"{escape_html(display_model)}\" "
            f"data-outcome=\"{'correct' if row.get('top1_correct') else 'wrong'}\" "
            f"data-pactual=\"{row['probability_actual']:.12f}\" "
            f"data-nll=\"{row['negative_log_likelihood']:.12f}\">"
            f"<td class=\"respondent\"><b>{escape_html(row.get('respondent_id'))}</b><span>{escape_html(row.get('heldout_question'))}</span></td>"
            f"<td>{escape_html(row.get('actual_answer'))}</td>"
            f"<td>{escape_html(display_model)}</td>"
            f"<td class=\"prob-cell\">{probability_cells}</td>"
            f"<td class=\"numeric\">{row['probability_actual']:.3f}</td>"
            f"<td>{escape_html(top_option)}</td>"
            f"<td><span class=\"{'good' if row.get('top1_correct') else 'bad'}\">{'correct' if row.get('top1_correct') else 'wrong'}</span></td>"
            f"<td class=\"numeric\">{row['negative_log_likelihood']:.3f}</td>"
            f"<td class=\"raw\"><code>{escape_html(raw_response)}</code></td>"
            "</tr>"
        )

    report_data = escape_html(
        json.dumps(
            {
                "row_count": len(rows),
                "detail_row_limit": detail_row_limit,
                "raw_prediction_rows_included": False,
                "summary": summary,
                "diagnostics": diagnostics,
            },
            separators=(",", ":"),
        )
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(survey)} Digital Twin Report</title>
  <style>
    {EP_REPORT_CSS}
    :root {{ color-scheme: light; --ink:#17202a; --muted:#607080; --line:#d8dee6; --bg:#f7f8fa; --panel:#ffffff; --good:#0b7a3b; --bad:#b42318; --prob:#2563eb; --error:#475569; --uniform:#eab308; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 36px 22px; background:#fff; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    .subtle {{ color:var(--muted); font-size:14px; }}
    main {{ padding:24px 36px 44px; max-width:1320px; margin:0 auto; }}
    .score-strip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; margin-bottom:18px; }}
    .score-card, .summary-card, .table-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .summary-card {{ margin-bottom:18px; }}
    .summary-card h2 {{ font-size:18px; margin:0 0 12px; }}
    .summary-grid {{ display:grid; grid-template-columns:minmax(280px,.65fr) minmax(600px,1.35fr); gap:14px; align-items:start; }}
    .score-title {{ display:flex; justify-content:space-between; gap:10px; align-items:center; font-weight:700; margin-bottom:12px; }}
    .score-title b {{ color:var(--good); background:#e7f6ed; border:1px solid #b7e0c6; border-radius:999px; padding:3px 8px; font-size:12px; }}
    .score-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }}
    .score-grid div {{ background:#fbfcfd; border:1px solid var(--line); border-radius:6px; padding:8px; }}
    label {{ display:block; color:var(--muted); font-size:12px; margin-bottom:4px; }}
    strong, em {{ font-variant-numeric:tabular-nums; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:6px; }}
    .controls {{ display:flex; gap:10px 18px; flex-wrap:wrap; align-items:center; background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin:0 0 18px; }}
    .controls label {{ display:flex; align-items:center; gap:6px; margin:0; color:#334155; cursor:pointer; }}
    .controls select {{ min-height:30px; border:1px solid var(--line); border-radius:6px; background:#fff; color:var(--ink); padding:3px 8px; }}
    table {{ width:100%; border-collapse:collapse; min-width:1180px; font-size:13px; }}
    .summary-card table {{ min-width:0; }}
    .performance-summary th, .performance-summary td {{ white-space:nowrap; }}
    th, td {{ text-align:left; vertical-align:top; border-bottom:1px solid var(--line); padding:9px 10px; }}
    th {{ position:sticky; top:0; z-index:1; background:#f8fafc; color:#334155; font-size:12px; }}
    tr:last-child td {{ border-bottom:0; }}
    .respondent b {{ display:block; max-width:210px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .respondent span {{ display:block; color:var(--muted); font-size:12px; margin-top:3px; }}
    .prob-cell {{ min-width:210px; }}
    .prob-row {{ display:grid; grid-template-columns:minmax(90px,1fr) 54px 48px; gap:8px; align-items:center; margin-bottom:4px; }}
    .prob-row:last-child {{ margin-bottom:0; }}
    .prob-row b, .numeric {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .prob-row em {{ color:var(--good); font-style:normal; font-size:11px; font-weight:700; }}
    .marginal-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:14px; }}
    .marginal-card {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:#fbfcfd; }}
    .marginal-head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:8px; }}
    .marginal-head h3 {{ font-size:15px; margin:0 0 4px; }}
    .marginal-head p {{ margin:0; color:var(--muted); font-size:12px; line-height:1.35; }}
    .marginal-meta {{ text-align:right; min-width:120px; }}
    .marginal-meta b, .marginal-meta span {{ display:block; font-size:12px; }}
    .marginal-meta span, .marginal-metrics {{ color:var(--muted); }}
    .marginal-metrics {{ border-top:1px solid var(--line); border-bottom:1px solid var(--line); padding:6px 0; margin:8px 0; font-size:12px; }}
    .marginal-option {{ display:grid; grid-template-columns:minmax(160px,.9fr) minmax(220px,1.1fr); gap:10px; align-items:center; padding:5px 0; border-bottom:1px solid #edf0f4; }}
    .marginal-option:last-child {{ border-bottom:0; }}
    .marginal-label {{ font-size:12px; line-height:1.3; overflow-wrap:anywhere; }}
    .marginal-bars {{ display:grid; gap:4px; }}
    .marginal-bars div {{ display:grid; grid-template-columns:1fr 52px; gap:8px; align-items:center; min-height:12px; }}
    .marginal-bars span {{ display:block; height:8px; border-radius:4px; min-width:1px; }}
    .marginal-bars b {{ font-size:11px; font-variant-numeric:tabular-nums; text-align:right; }}
    .twin-bar {{ background:var(--prob); }}
    .target-bar {{ background:var(--uniform); }}
    .missing-target {{ color:var(--muted); font-size:12px; }}
    .raw {{ min-width:360px; max-width:520px; }}
    code {{ white-space:pre-wrap; overflow-wrap:anywhere; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; color:#334155; }}
    .good {{ color:var(--good); }}
    .bad {{ color:var(--bad); }}
    @media (max-width: 860px) {{ header, main {{ padding-left:16px; padding-right:16px; }} .score-grid {{ grid-template-columns:repeat(2,1fr); }} .summary-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <header>
    <h1>{escape_html(display_title)}</h1>
    <div class="subtle">Survey id: <code>{escape_html(survey)}</code></div>
    <div class="subtle">Respondent-level held-out answer predictions compared with what each respondent actually said. Error rate is the share where the model's highest-probability option was not the actual answer.</div>
  </header>
  <main>
    <section class="summary-card">
      <h2>Study Summary</h2>
      <div class="summary-grid">
        <div class="table-wrap">
          <table>
            <tbody>{study_summary}</tbody>
          </table>
        </div>
        <div class="table-wrap">
          <table class="performance-summary">
            <thead>
              <tr>
                <th>Model</th>
                <th>Rows</th>
                <th>Accuracy</th>
                <th>Error</th>
                <th>p(actual)</th>
                <th>Random p</th>
                <th>p delta</th>
                <th>Empirical p</th>
                <th>vs empirical p</th>
                <th>NLL</th>
                <th>Random NLL</th>
                <th>NLL delta</th>
                <th>Empirical NLL</th>
                <th>vs empirical NLL</th>
                <th>Brier</th>
                <th>Random Brier</th>
                <th>Brier delta</th>
                <th>Empirical Brier</th>
                <th>vs empirical Brier</th>
              </tr>
            </thead>
            <tbody>{''.join(performance_rows)}</tbody>
          </table>
        </div>
      </div>
      <div class="subtle" style="margin-top:10px;"><a href="#metric-definitions">Definitions</a></div>
    </section>
    <section class="summary-card">
      <h2>Run Health</h2>
      <div class="table-wrap">
        <table>
          <tbody>{health_table_rows}</tbody>
        </table>
      </div>
    </section>
    <section class="score-strip">{''.join(score_cards)}</section>
    <section class="summary-card">
      <h2>Diagnostics</h2>
      <div class="summary-grid">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Model</th><th>p vs empirical</th><th>NLL vs empirical</th><th>Brier vs empirical</th></tr></thead>
            <tbody>{''.join(baseline_rows)}</tbody>
          </table>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Held-out</th><th>Model</th><th>NLL vs empirical</th></tr></thead>
            <tbody>{model_win_rows or '<tr><td colspan="3">No model-over-empirical wins.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
      <div class="summary-grid" style="margin-top:14px;">
        <div class="table-wrap">
          <table>
            <thead><tr><th>Held-out</th><th>Model</th><th>NLL vs empirical</th></tr></thead>
            <tbody>{empirical_win_rows or '<tr><td colspan="3">No empirical-over-model wins.</td></tr>'}</tbody>
          </table>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Model</th><th>Confidence bin</th><th>Rows</th><th>Mean confidence</th><th>Accuracy</th></tr></thead>
            <tbody>{''.join(calibration_rows)}</tbody>
          </table>
        </div>
      </div>
    </section>
    <section class="summary-card">
      <h2>Overconfident Misses</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Respondent</th><th>Held-out</th><th>Model</th><th>Actual</th><th>Top prediction</th><th>Top p</th><th>p(actual)</th><th>NLL</th></tr></thead>
          <tbody>{''.join(overconfident_rows) or '<tr><td colspan="8">No wrong top predictions.</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <section class="summary-card">
      <h2>Option Confusion</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Held-out</th><th>Model</th><th>Actual</th><th>Top prediction</th><th>Count</th></tr></thead>
          <tbody>{''.join(confusion_rows)}</tbody>
        </table>
      </div>
    </section>
    <section class="summary-card">
      <h2>Largest Misses</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Respondent</th><th>Held-out</th><th>Model</th><th>Actual</th><th>p(actual)</th><th>NLL</th><th>Notes</th></tr></thead>
          <tbody>{''.join(worst_miss_rows)}</tbody>
        </table>
      </div>
    </section>
    <section class="summary-card">
      <h2>Performance by Held-out Question</h2>
      <div class="table-wrap">
        <table class="performance-summary">
          <thead>
            <tr>
              <th>Held-out</th>
              <th>Model</th>
              <th>Rows</th>
              <th>Accuracy</th>
              <th>Error</th>
              <th>p(actual)</th>
              <th>Empirical p</th>
              <th>vs empirical p</th>
              <th>NLL</th>
              <th>Empirical NLL</th>
              <th>vs empirical NLL</th>
              <th>Brier</th>
              <th>Empirical Brier</th>
              <th>vs empirical Brier</th>
            </tr>
          </thead>
          <tbody>{''.join(heldout_performance_rows)}</tbody>
        </table>
      </div>
    </section>
    <section class="summary-card">
      <h2>Question Marginals</h2>
      <div class="subtle" style="margin-bottom:12px;">Blue bars are the twin-implied population distribution; yellow bars are the empirical marginal when available.</div>
      <div class="marginal-grid">{''.join(marginal_comparison_rows) or '<div class="subtle">No marginal comparisons available.</div>'}</div>
    </section>
    <section class="table-card">
      <section class="controls">
        <strong>Respondents</strong>
        <span class="subtle">Showing {len(table_rows)} detail rows{f'; {detail_rows_omitted} omitted' if detail_rows_omitted else ''}. Use audit or microdata exports for full row-level inspection.</span>
        <label>Held-out <select id="heldout-filter"><option value="">All</option>{question_options}</select></label>
        <label>Model <select id="model-filter"><option value="">All</option>{model_options}</select></label>
        <label><input type="checkbox" id="wrong-only"> Wrong only</label>
        <label>Sort <select id="sort-select"><option value="respondent">Respondent</option><option value="pactual_asc">Lowest p(actual)</option><option value="nll_desc">Highest NLL</option></select></label>
      </section>
      <div class="table-wrap">
        <table id="twin-table">
          <thead>
            <tr>
              <th>Respondent</th>
              <th>Actual</th>
              <th>Model</th>
              <th>Predicted option probabilities</th>
              <th>p(actual)</th>
              <th>Top prediction</th>
              <th>Outcome</th>
              <th>NLL</th>
              <th>Raw model response</th>
            </tr>
          </thead>
          <tbody>{''.join(table_rows)}</tbody>
        </table>
      </div>
    </section>
    <section class="summary-card" id="metric-definitions">
      <h2>Metric Definitions</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Metric</th><th>Definition</th><th>Interpretation</th></tr></thead>
          <tbody>{metric_definition_rows}</tbody>
        </table>
      </div>
    </section>
  </main>
  <script type="application/json" id="twin-report-data">{report_data}</script>
  <script>
    const tbody = document.querySelector('#twin-table tbody');
    const originalRows = Array.from(tbody.querySelectorAll('tr'));
    const heldoutFilter = document.getElementById('heldout-filter');
    const modelFilter = document.getElementById('model-filter');
    const wrongOnly = document.getElementById('wrong-only');
    const sortSelect = document.getElementById('sort-select');
    function updateRows() {{
      let rows = originalRows.slice();
      const heldout = heldoutFilter.value;
      const model = modelFilter.value;
      rows.forEach((row) => {{
        const visible = (!heldout || row.dataset.heldout === heldout) &&
          (!model || row.dataset.model === model) &&
          (!wrongOnly.checked || row.dataset.outcome === 'wrong');
        row.style.display = visible ? '' : 'none';
      }});
      if (sortSelect.value === 'pactual_asc') {{
        rows.sort((a, b) => Number(a.dataset.pactual) - Number(b.dataset.pactual));
      }} else if (sortSelect.value === 'nll_desc') {{
        rows.sort((a, b) => Number(b.dataset.nll) - Number(a.dataset.nll));
      }} else {{
        rows.sort((a, b) => a.children[0].innerText.localeCompare(b.children[0].innerText));
      }}
      rows.forEach((row) => tbody.appendChild(row));
    }}
    [heldoutFilter, modelFilter, wrongOnly, sortSelect].forEach((el) => el.addEventListener('change', updateRows));
    updateRows();
  </script>
  {skill_score_section}
  {bootstrap_ci_section}
  {conditional_baseline_appendix}
</body>
</html>"""


def render_twin_summary_report_html(
    survey: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
    health: dict[str, Any] | None = None,
) -> str:
    diagnostics = diagnostics or {}
    display_title, _raw_title = report_display_title(f"{survey} digital twin validation")
    heldout_questions = sorted({str(row.get("heldout_question")) for row in rows})
    respondent_count = len({row.get("respondent_id") for row in rows})
    model_names = sorted({str(row.get("twin_set_label") or row.get("model_label") or row.get("model")) for row in rows})
    skill_score_section = skill_score_section_html(rows)
    bootstrap_ci_section = bootstrap_ci_section_html(rows)
    conditional_baseline_appendix = (
        conditional_baseline_appendix_html() if has_conditional_baseline(model_names) else ""
    )
    import_health = (health or {}).get("import", {})
    job_label = (health or {}).get("job_id") or ", ".join(str(job) for job in (health or {}).get("job_ids", [])[:4])
    if (health or {}).get("job_ids") and len((health or {}).get("job_ids", [])) > 4:
        job_label += f" + {len((health or {}).get('job_ids', [])) - 4} more"

    def signed_cell(value: Any, higher_is_better: bool = True) -> str:
        if value is None:
            return "<td></td>"
        numeric = float(value)
        good = numeric >= 0 if higher_is_better else numeric <= 0
        return f"<td class=\"numeric {'good' if good else 'bad'}\">{numeric:+.3f}</td>"

    def numeric_cell(value: Any, precision: int = 3) -> str:
        if value is None:
            return "<td></td>"
        return f"<td class=\"numeric\">{float(value):.{precision}f}</td>"

    def natural_question_key(value: Any) -> tuple[str, int, str]:
        text = str(value)
        prefix = "".join(ch for ch in text if not ch.isdigit())
        digits = "".join(ch for ch in text if ch.isdigit())
        return (prefix, int(digits) if digits else 10**9, text)

    twin_set_descriptions = diagnostics.get("twin_set_descriptions", {})

    def twin_set_markup(label: Any) -> str:
        label_text = str(label or "")
        info = twin_set_descriptions.get(label_text, {})
        description = str(info.get("description") or label_text)
        details = []
        model_label = info.get("model_label")
        if model_label and model_label != description:
            details.append(f"model: {model_label}")
        if info.get("job_id"):
            details.append(f"job: {info.get('job_id')}")
        if info.get("source_name"):
            details.append(f"source: {info.get('source_name')}")
        if not info and label_text != description:
            details.append(label_text)
        detail_html = f"<span>{escape_html(' | '.join(details))}</span>" if details else ""
        return f"<b>{escape_html(description)}</b>{detail_html}"

    def compact_value(value: Any, limit: int = 42) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return escape_html(text)
        return f"<span title=\"{escape_html(text)}\">{escape_html(text[: limit - 1])}…</span>"

    def twin_set_cell(label: Any) -> str:
        return f"<td>{twin_set_markup(label)}</td>"

    overview_rows = [
        ("Prediction rows", f"{len(rows):,}"),
        ("Respondents", f"{respondent_count:,}"),
        ("Held-out questions", f"{len(heldout_questions):,}"),
        ("Twin sets", f"{len(model_names):,}"),
        ("Import issues", str(import_health.get("issue_count", 0))),
        ("Job", job_label or "multiple jobs"),
    ]
    overview_html = "".join(
        "<tr>"
        f"<th>{escape_html(label)}</th>"
        f"<td>{compact_value(value)}</td>"
        "</tr>"
        for label, value in overview_rows
    )
    metric_definitions = [
        (
            "Accuracy",
            "Mean of 1[top predicted option equals actual answer].",
            "Higher is better. This is a hard-choice score; it ignores probability assigned to non-top options.",
        ),
        (
            "p(actual)",
            "For each respondent, read the predicted probability assigned to the answer they actually gave; report the mean.",
            "Higher is better. This is often more informative than accuracy for probabilistic twins.",
        ),
        (
            "NLL",
            "-log(p(actual)), averaged across rows.",
            "Lower is better. It strongly penalizes confident misses.",
        ),
        (
            "Brier",
            "Sum over options of (predicted probability - one-hot actual outcome)^2, averaged across rows.",
            "Lower is better. It penalizes probability mass placed away from the actual answer.",
        ),
        (
            "ECE",
            "Expected calibration error: bin rows by top-choice confidence, then average |bin accuracy - bin confidence| weighted by bin size.",
            "Lower is better. High values mean stated confidence does not match observed correctness.",
        ),
        (
            "NLL improvement vs uniform",
            "Uniform-random NLL minus model NLL.",
            "Positive means the twin beats the deployable uniform baseline.",
        ),
        (
            "NLL improvement vs empirical oracle",
            "Empirical-marginal NLL minus model NLL.",
            "Positive means individual twin probabilities beat an oracle that already knows the true answer distribution for this question.",
        ),
        (
            "L1",
            "Sum over options of |twin-implied marginal - empirical marginal|.",
            "Lower is better. Maximum is 2.0 for non-overlapping distributions.",
        ),
        (
            "JS",
            "Jensen-Shannon divergence between twin-implied and empirical marginal distributions.",
            "Lower is better. It is a symmetric, smoothed KL-style distribution distance.",
        ),
        (
            "Largest option deltas",
            "For each option: twin-implied marginal minus empirical marginal, sorted by absolute difference.",
            "Positive means twins over-predict that option; negative means they under-predict it.",
        ),
        (
            "Confidence gap",
            "|top-choice accuracy - mean top-choice confidence| for that question and twin set.",
            "Lower is better. Large gaps flag question-level miscalibration.",
        ),
        (
            "Takeaway",
            "Deterministic rule from NLL lift, marginal distance, calibration gap, and oracle comparison.",
            "Use it as a triage label, not as a replacement for the detailed metrics.",
        ),
    ]
    metric_definition_rows = "".join(
        "<tr>"
        f"<td>{escape_html(metric)}</td>"
        f"<td>{escape_html(calculation)}</td>"
        f"<td>{escape_html(interpretation)}</td>"
        "</tr>"
        for metric, calculation, interpretation in metric_definitions
    )

    model_rows = []
    for model, values in summary.items():
        baseline = diagnostics.get("baseline_comparison", {}).get(model, {})
        model_rows.append(
            "<tr>"
            f"{twin_set_cell(model)}"
            f"<td class=\"numeric\">{values.get('rows', 0)}</td>"
            f"<td class=\"numeric\">{values.get('top1_accuracy', 0.0):.3f}</td>"
            f"<td class=\"numeric\">{values.get('mean_probability_actual', 0.0):.3f}</td>"
            f"<td class=\"numeric\">{values.get('mean_negative_log_likelihood', 0.0):.3f}</td>"
            f"<td class=\"numeric\">{values.get('mean_brier', 0.0):.3f}</td>"
            f"<td class=\"numeric\">{values.get('expected_calibration_error', 0.0):.3f}</td>"
            f"{signed_cell(baseline.get('nll_vs_uniform'))}"
            f"{signed_cell(baseline.get('nll_vs_empirical'))}"
            "</tr>"
        )
    missing_one_shot_note = (
        "<p class=\"subtle\">No-persona one-shot aggregate baselines are reported separately when imported. "
        "They are not repeated as empty rows in each twin table because they benchmark aggregate distributions, "
        "not the twin-specific joint, subgroup, or respondent-level diagnostics.</p>"
    )

    marginal_comparison_by_key = {
        (str(comparison.get("heldout_question")), str(comparison.get("model_label"))): comparison
        for comparison in diagnostics.get("marginal_comparisons", [])
    }
    marginal_options_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for option in diagnostics.get("marginal_options", []):
        key = (str(option.get("heldout_question")), str(option.get("model_label")))
        marginal_options_by_key.setdefault(key, []).append(option)

    def question_takeaway(
        nll_vs_uniform: float | None,
        nll_vs_empirical: float | None,
        l1: float | None,
        calibration_gap: float | None,
    ) -> str:
        if nll_vs_uniform is not None and nll_vs_uniform < -0.02:
            return "Worse than uniform"
        if calibration_gap is not None and calibration_gap >= 0.20:
            return "Overconfident"
        if l1 is not None and l1 >= 0.45:
            return "Marginal mismatch"
        if nll_vs_empirical is not None and nll_vs_empirical >= 0.10:
            return "Strong individual signal"
        if nll_vs_empirical is not None and nll_vs_empirical >= 0.02:
            return "Individual signal"
        if nll_vs_uniform is not None and nll_vs_uniform >= 0.02:
            return "Beats uniform"
        return "Close to baseline"

    chart_colors = ["#2563eb", "#7c3aed", "#0f766e", "#c2410c"]

    def compact_twin_label(model: Any) -> str:
        label_text = str(model or "")
        info = twin_set_descriptions.get(label_text, {})
        return str(info.get("description") or label_text)

    def question_marginal_chart(question: str, by_model: dict[str, dict[str, Any]]) -> str:
        models = sorted(by_model)
        option_order = []
        target_by_option: dict[str, float] = {}
        predicted_by_model: dict[str, dict[str, float]] = {}
        for model in models:
            model_options = marginal_options_by_key.get((str(question), str(model)), [])
            predicted_by_model[model] = {}
            for row in model_options:
                option = str(row.get("option_label"))
                if option not in option_order:
                    option_order.append(option)
                predicted_by_model[model][option] = float(row.get("predicted_probability") or 0.0)
                if row.get("target_probability") is not None and option not in target_by_option:
                    target_by_option[option] = float(row.get("target_probability") or 0.0)
        if not option_order or not target_by_option:
            return ""

        uniform_probability = 1 / len(option_order)
        uniform_marker = f"<em class=\"uniform-marker\" style=\"left:{uniform_probability * 100:.1f}%\"></em>"
        legend = [
            "<span><i class=\"actual-key\"></i>Empirical</span>",
            *[
                f"<span><i style=\"background:{chart_colors[index % len(chart_colors)]}\"></i>{escape_html(compact_twin_label(model))}</span>"
                for index, model in enumerate(models)
            ],
            f"<span><i class=\"uniform-key\"></i>Uniform ({uniform_probability:.1%})</span>",
        ]
        option_rows = []
        for option in option_order:
            bars = [
                "<div class=\"dist-bar-line\">"
                "<div class=\"dist-series actual-key-label\">Empirical</div>"
                f"<div class=\"dist-track\"><span class=\"actual-bar\" style=\"width:{target_by_option.get(option, 0.0) * 100:.1f}%\"></span>{uniform_marker}</div>"
                f"<div class=\"dist-value\">{target_by_option.get(option, 0.0):.1%}</div>"
                "</div>"
            ]
            for index, model in enumerate(models):
                probability = predicted_by_model.get(model, {}).get(option, 0.0)
                color = chart_colors[index % len(chart_colors)]
                bars.append(
                    "<div class=\"dist-bar-line\">"
                    f"<div class=\"dist-series\">{escape_html(compact_twin_label(model))}</div>"
                    f"<div class=\"dist-track\"><span style=\"width:{probability * 100:.1f}%; background:{color}\"></span>{uniform_marker}</div>"
                    f"<div class=\"dist-value\">{probability:.1%}</div>"
                    "</div>"
                )
            option_rows.append(
                "<div class=\"dist-option-row\">"
                f"<div class=\"dist-option-label\">{escape_html(option)}</div>"
                f"<div class=\"dist-bars\">{''.join(bars)}</div>"
                "</div>"
            )
        return (
            "<div class=\"question-dist-chart\">"
            f"<div class=\"dist-legend\">{''.join(legend)}</div>"
            f"<div class=\"dist-option-grid\">{''.join(option_rows)}</div>"
            "</div>"
        )

    question_blocks = []
    question_text_by_name: dict[str, str] = {}
    for comparison in diagnostics.get("marginal_comparisons", []):
        if comparison.get("heldout_question") and comparison.get("heldout_question_text"):
            question_text_by_name[str(comparison.get("heldout_question"))] = str(comparison.get("heldout_question_text"))
    for question, by_model in sorted((diagnostics.get("summary_by_question") or {}).items(), key=lambda item: natural_question_key(item[0])):
        question_text = question_text_by_name.get(str(question), "")
        chart_html = question_marginal_chart(str(question), by_model)
        table_rows = []
        for model, values in sorted(by_model.items()):
            marginal_nll = values.get("mean_empirical_marginal_negative_log_likelihood", values.get("mean_marginal_negative_log_likelihood"))
            nll_vs_empirical = marginal_nll - values["mean_negative_log_likelihood"] if marginal_nll is not None else None
            nll_vs_uniform = values.get("mean_uniform_negative_log_likelihood") - values["mean_negative_log_likelihood"] if values.get("mean_uniform_negative_log_likelihood") is not None else None
            mean_confidence = values.get("mean_top_confidence")
            accuracy = values.get("top1_accuracy")
            calibration_gap = abs(mean_confidence - accuracy) if mean_confidence is not None and accuracy is not None else None
            comparison = marginal_comparison_by_key.get((str(question), str(model)), {})
            l1 = comparison.get("l1")
            takeaway = question_takeaway(nll_vs_uniform, nll_vs_empirical, l1, calibration_gap)
            table_rows.append(
                "<tr>"
                f"{twin_set_cell(model)}"
                f"<td class=\"numeric\">{values.get('rows', 0)}</td>"
                f"<td class=\"numeric\">{values.get('top1_accuracy', 0.0):.3f}</td>"
                f"<td class=\"numeric\">{values.get('mean_negative_log_likelihood', 0.0):.3f}</td>"
                f"{signed_cell(nll_vs_uniform)}"
                f"{signed_cell(nll_vs_empirical)}"
                f"{numeric_cell(calibration_gap)}"
                f"{numeric_cell(l1)}"
                f"<td><b>{escape_html(takeaway)}</b></td>"
                "</tr>"
            )
        if by_model:
            first_values = next(iter(by_model.values()))
            uniform_nll = first_values.get("mean_uniform_negative_log_likelihood")
            marginal_nll = first_values.get(
                "mean_empirical_marginal_negative_log_likelihood",
                first_values.get("mean_marginal_negative_log_likelihood"),
            )
            uniform_vs_empirical = marginal_nll - uniform_nll if marginal_nll is not None and uniform_nll is not None else None
            uniform_nll_cell = f"<td class=\"numeric\">{uniform_nll:.3f}</td>" if uniform_nll is not None else "<td></td>"
            table_rows.append(
                "<tr class=\"baseline-row\">"
                "<td><b>Uniform random</b><span>Deployable no-information baseline</span></td>"
                f"<td class=\"numeric\">{first_values.get('rows', 0)}</td>"
                "<td></td>"
                f"{uniform_nll_cell}"
                f"{signed_cell(0.0 if uniform_nll is not None else None)}"
                f"{signed_cell(uniform_vs_empirical)}"
                "<td></td>"
                "<td></td>"
                "<td><b>Uniform baseline</b></td>"
                "</tr>"
            )
        question_blocks.append(
            "<section class=\"question-performance-block\">"
            f"<div class=\"question-block-head\"><b>{escape_html(question)}</b><span>{escape_html(question_text)}</span></div>"
            f"{chart_html}"
            "<div class=\"table-wrap question-table-wrap\">"
            "<table>"
            "<thead><tr><th>Twin set</th><th>Rows</th><th>Accuracy</th><th>NLL</th><th>NLL improvement vs uniform</th><th>NLL improvement vs empirical oracle</th><th>Confidence gap</th><th>Marginal L1</th><th>Takeaway</th></tr></thead>"
            f"<tbody>{''.join(table_rows)}</tbody>"
            "</table>"
            "</div>"
            "</section>"
        )

    marginal_rows = []
    for comparison in diagnostics.get("marginal_comparisons", []):
        key = (str(comparison.get("heldout_question")), str(comparison.get("model_label")))
        options = sorted(
            marginal_options_by_key.get(key, []),
            key=lambda item: (-(item.get("abs_difference") or 0.0), str(item.get("option_label"))),
        )[:5]
        option_deltas = []
        for option in options:
            delta = option.get("difference")
            delta_text = f"{float(delta):+.1%}" if delta is not None else "n/a"
            option_deltas.append(f"{option.get('option_label')}: {delta_text}")
        l1_cell = (
            f"<td class=\"numeric\">{float(comparison.get('l1')):.3f}</td>"
            if comparison.get("l1") is not None
            else "<td></td>"
        )
        js_cell = (
            f"<td class=\"numeric\">{float(comparison.get('js_divergence')):.3f}</td>"
            if comparison.get("js_divergence") is not None
            else "<td></td>"
        )
        marginal_rows.append(
            "<tr>"
            f"<td><b>{escape_html(comparison.get('heldout_question'))}</b><span>{escape_html(comparison.get('heldout_question_text') or '')}</span></td>"
            f"{twin_set_cell(comparison.get('model_label'))}"
            f"<td class=\"numeric\">{comparison.get('respondent_count', 0)}</td>"
            f"{l1_cell}"
            f"{js_cell}"
            f"<td>{'yes' if comparison.get('top_option_agrees') else 'no' if comparison.get('top_option_agrees') is not None else ''}</td>"
            f"<td>{escape_html(comparison.get('predicted_top_option') or '')}</td>"
            f"<td>{escape_html(comparison.get('target_top_option') or '')}</td>"
            f"<td>{escape_html('; '.join(option_deltas))}</td>"
            "</tr>"
        )

    overconfident_rows = []
    for row in diagnostics.get("overconfident_misses", [])[:8]:
        predicted = row.get("probabilities", {})
        top_option, top_probability = max(predicted.items(), key=lambda item: item[1]) if predicted else ("", 0.0)
        overconfident_rows.append(
            "<tr>"
            f"<td>{escape_html(row.get('heldout_question'))}</td>"
            f"<td>{escape_html(row.get('actual_answer'))}</td>"
            f"<td>{escape_html(top_option)}</td>"
            f"<td class=\"numeric\">{float(top_probability):.3f}</td>"
            f"<td class=\"numeric\">{row.get('probability_actual'):.3f}</td>"
            f"<td class=\"numeric\">{row.get('negative_log_likelihood'):.3f}</td>"
            "</tr>"
        )

    summary_data = escape_html(
        json.dumps(
            {
                "summary": summary,
                "diagnostics": {
                    "baseline_comparison": diagnostics.get("baseline_comparison", {}),
                    "marginal_comparisons": diagnostics.get("marginal_comparisons", []),
                    "marginal_options": diagnostics.get("marginal_options", []),
                    "expected_calibration_error": diagnostics.get("expected_calibration_error", {}),
                    "twin_set_descriptions": twin_set_descriptions,
                },
                "health": health or {},
            },
            separators=(",", ":"),
        )
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(display_title)}</title>
  <style>
    {EP_REPORT_CSS}
    :root {{ --ink:var(--ep-dark); --muted:var(--ep-gray); --line:var(--ep-border); --panel:#fff; --good:#0b7a3b; --bad:#b42318; }}
    body {{ max-width:1220px; }}
    header {{ margin-bottom:1.5rem; }}
    main {{ padding-bottom:2rem; }}
    .subtle {{ color:var(--muted); font-size:13px; }}
    .panel, .question-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .panel {{ margin-bottom:18px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:6px; }}
    .overview-table th {{ width:190px; }}
    .question-performance-list {{ display:grid; gap:14px; }}
    .question-performance-block {{ border:1px solid var(--line); border-radius:8px; overflow:hidden; background:#fff; }}
    .question-block-head {{ background:#fbfcfd; border-bottom:1px solid var(--line); padding:10px 12px; }}
    .question-block-head b {{ display:block; font-size:14px; margin-bottom:3px; }}
    .question-block-head span {{ display:block; color:var(--muted); font-size:12px; line-height:1.35; }}
    .question-dist-chart {{ display:grid; gap:8px; }}
    .question-performance-block .question-dist-chart {{ padding:10px 12px 12px; border-bottom:1px solid var(--line); }}
    .question-table-wrap {{ border:0; border-radius:0; }}
    .dist-legend {{ display:flex; flex-wrap:wrap; gap:8px 14px; color:var(--muted); font-size:12px; }}
    .dist-legend span {{ display:inline-flex; align-items:center; gap:5px; margin:0; }}
    .dist-legend i {{ display:inline-block; width:9px; height:9px; border-radius:2px; background:#2563eb; }}
    .dist-legend .actual-key, .actual-bar {{ background:#d99a00; }}
    .dist-legend .uniform-key {{ width:2px; height:12px; border-radius:0; background:#111827; }}
    .dist-option-grid {{ display:grid; gap:7px; }}
    .dist-option-row {{ display:grid; grid-template-columns:minmax(170px,.44fr) minmax(280px,.56fr); gap:12px; align-items:start; border-top:1px solid #edf0f4; padding-top:7px; }}
    .dist-option-row:first-child {{ border-top:0; padding-top:0; }}
    .dist-option-label {{ color:#334155; font-size:12px; line-height:1.3; overflow-wrap:anywhere; }}
    .dist-bars {{ display:grid; gap:3px; }}
    .dist-bar-line {{ display:grid; grid-template-columns:minmax(86px,120px) minmax(110px,1fr) 44px; gap:7px; align-items:center; min-height:14px; }}
    .dist-series {{ color:var(--muted); font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .dist-track {{ position:relative; height:8px; background:#eef2f7; border-radius:999px; overflow:hidden; }}
    .dist-track span {{ display:block; height:100%; border-radius:999px; min-width:1px; }}
    .uniform-marker {{ position:absolute; top:-2px; bottom:-2px; width:2px; margin-left:-1px; background:#111827; opacity:.72; }}
    .dist-value {{ text-align:right; font-size:11px; font-variant-numeric:tabular-nums; color:#475569; }}
    .baseline-row td {{ background:#fcfcfd; color:var(--muted); }}
    td span {{ display:block; color:var(--muted); font-size:12px; line-height:1.35; margin-top:3px; }}
    .numeric {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .good {{ color:var(--good); }}
    .bad {{ color:var(--bad); }}
    @media (max-width:760px) {{ .dist-option-row, .dist-bar-line {{ grid-template-columns:1fr; }} .dist-value {{ text-align:left; }} }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <header>
    <h1>{escape_html(display_title)}</h1>
    <div class="subtle">Survey id: <code>{escape_html(survey)}</code></div>
    <div class="subtle">High-level validation summary for digital twin predictions. Use the full report or microdata table for row-level auditing.</div>
  </header>
  <main>
    <section class="panel">
      <h2>Run Overview</h2>
      <div class="table-wrap">
        <table class="overview-table">
          <tbody>{overview_html}</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>Model Performance</h2>
      <p><a href="#metric-definitions">Definitions</a></p>
      {missing_one_shot_note}
      <div class="table-wrap">
        <table>
          <thead><tr><th>Twin set</th><th>Rows</th><th>Accuracy</th><th>p(actual)</th><th>NLL</th><th>Brier</th><th>ECE</th><th>NLL improvement vs uniform</th><th>NLL improvement vs empirical oracle</th></tr></thead>
          <tbody>{''.join(model_rows)}</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>Question Performance</h2>
      <p>For NLL improvement columns, positive values mean the twin set is better than the baseline; negative values mean the baseline is better. The empirical marginal baseline is an oracle diagnostic because it uses the true answer distribution for the question.</p>
      <div class="question-performance-list">{''.join(question_blocks)}</div>
    </section>
    <section class="panel">
      <h2>Marginal Divergence</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Question</th><th>Twin set</th><th>Rows</th><th>L1</th><th>JS</th><th>Top agrees</th><th>Twin top</th><th>Empirical top</th><th>Largest option deltas</th></tr></thead>
          <tbody>{''.join(marginal_rows) or '<tr><td colspan="9">No marginal diagnostics available.</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <h2>Overconfident Misses</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Question</th><th>Actual</th><th>Top prediction</th><th>Top p</th><th>p(actual)</th><th>NLL</th></tr></thead>
          <tbody>{''.join(overconfident_rows) or '<tr><td colspan="6">No overconfident misses.</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <section class="panel" id="metric-definitions">
      <h2>Metric Definitions</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Metric</th><th>How calculated</th><th>How to read it</th></tr></thead>
          <tbody>{metric_definition_rows}</tbody>
        </table>
      </div>
    </section>
    {skill_score_section}
  {bootstrap_ci_section}
    {conditional_baseline_appendix}
  </main>
  <script type="application/json" id="twin-summary-report-data">{summary_data}</script>
</body>
</html>"""


def render_twin_run_report_html(payload: dict[str, Any]) -> str:
    survey = payload.get("survey", "")
    display_title, _raw_title = report_display_title(str(survey))
    page_title = f"{display_title} Twin Run Report"
    job_id = payload.get("job_id", "")
    construction = payload.get("construction", {})
    import_metadata = payload.get("import", {})
    run = payload.get("run", {})

    def kv_rows(items: list[tuple[str, Any]]) -> str:
        rows = []
        for key, value in items:
            if isinstance(value, (list, dict)):
                display = json.dumps(value, indent=2, ensure_ascii=False)
            else:
                display = "" if value is None else str(value)
            rows.append(f"<tr><th>{escape_html(key)}</th><td>{escape_html(display)}</td></tr>")
        return "".join(rows)

    def context_question_count_display() -> str:
        value = construction.get("context_question_count")
        if value is not None:
            return str(value)
        prompt_examples = payload.get("prompt_examples") or []
        counts = sorted(
            {
                int(example.get("observed_answer_count"))
                for example in prompt_examples
                if isinstance(example.get("observed_answer_count"), int)
            }
        )
        if counts:
            return str(counts[0]) if len(counts) == 1 else f"{counts[0]}-{counts[-1]} observed answers in examples"
        heldout = construction.get("heldout_questions") or []
        if heldout:
            return "All available non-held-out questions"
        return "Not recorded"

    overview = [
        ("Survey", survey),
        ("Job id", job_id),
        ("Status", run.get("status") or "imported"),
        ("Created/imported", run.get("created_at") or import_metadata.get("imported_at")),
        ("Source results", import_metadata.get("source_path") or run.get("results_path")),
        ("Stored raw", import_metadata.get("stored_path") or run.get("stored_raw")),
        ("Rows", import_metadata.get("row_count") or run.get("row_count")),
        ("Extracted", import_metadata.get("extracted_count") or run.get("extracted_count")),
        ("Issues", import_metadata.get("issue_count") or run.get("issue_count")),
    ]
    construction_rows = kv_rows(
        [
            ("Held-out questions", construction.get("heldout_questions")),
            ("Scenario count", construction.get("scenario_count")),
            ("Prompt variant", construction.get("prompt_variant")),
            ("Context question count", context_question_count_display()),
            ("Sample respondents", construction.get("sample_respondents")),
            ("Seed", construction.get("seed")),
            ("Complete cases", construction.get("complete_cases")),
            ("Balance actual", construction.get("balance_actual")),
            ("Stratify actual", construction.get("stratify_actual")),
            ("Include agent material", construction.get("include_agent_material")),
            ("Agent material kinds", construction.get("agent_material_kinds")),
            ("Agent material tags", construction.get("agent_material_tags")),
            ("Twin material paths", construction.get("twin_material_paths")),
            ("Twin material count", construction.get("twin_material_count")),
            ("Skipped missing held-out", construction.get("skipped_missing_heldout_count")),
            ("Allow missing actual", construction.get("allow_missing_actual")),
        ]
    )
    question_row_parts = []
    for row in payload.get("questions", []):
        observed_summary = row.get("observed_answer_summary") or f"{row.get('prediction_rows', 0)} scored predictions"
        question_row_parts.append(
            "<tr>"
            f"<td><b>{escape_html(row.get('question'))}</b><span>{escape_html(row.get('question_text') or '')}</span></td>"
            f"<td class=\"numeric\">{row.get('prediction_rows', 0)}</td>"
            f"<td class=\"numeric\">{row.get('respondents', 0)}</td>"
            f"<td class=\"numeric\">{row.get('option_count', 0)}</td>"
            f"<td>{escape_html(observed_summary)}</td>"
            f"<td>{escape_html(', '.join(row.get('models', [])))}</td>"
            "</tr>"
        )
    question_rows = "".join(question_row_parts)
    model_rows = "".join(
        "<tr>"
        f"<td>{escape_html(row.get('model_label'))}</td>"
        f"<td class=\"numeric\">{row.get('rows', 0)}</td>"
        f"<td><code>{escape_html(json.dumps(row.get('parameters', {}), separators=(',', ':')))}</code></td>"
        "</tr>"
        for row in payload.get("models", [])
    )
    issue_rows = "".join(
        "<tr>"
        f"<td>{escape_html(issue.get('row', ''))}</td>"
        f"<td>{escape_html(issue.get('heldout_question') or issue.get('question') or '')}</td>"
        f"<td>{escape_html(issue.get('model', ''))}</td>"
        f"<td>{escape_html(issue.get('error', ''))}</td>"
        "</tr>"
        for issue in (import_metadata.get("issues") or [])[:20]
    )
    prompt_blocks = []
    for example in payload.get("prompt_examples", []):
        twin = example.get("twin", {}) or {}
        prompt_blocks.append(
            "<article class=\"example-card\">"
            f"<h3>{escape_html(example.get('heldout_question'))} / {escape_html(example.get('respondent_id'))}</h3>"
            "<dl>"
            f"<dt>Twin</dt><dd>respondent {escape_html(twin.get('respondent_id') or example.get('respondent_id'))}; agent index {escape_html(twin.get('agent_index'))}; scenario index {escape_html(twin.get('scenario_index'))}</dd>"
            f"<dt>Interview hash</dt><dd>{escape_html(twin.get('interview_hash') or '')}</dd>"
            f"<dt>Model</dt><dd>{escape_html(example.get('model_label'))}</dd>"
            f"<dt>Observed answers</dt><dd>{escape_html(example.get('observed_answer_count'))}</dd>"
            f"<dt>Agent material chars</dt><dd>{escape_html(example.get('agent_material_chars'))}</dd>"
            f"<dt>Twin material chars</dt><dd>{escape_html(example.get('twin_material_chars'))}</dd>"
            "</dl>"
            f"<details><summary>Twin identity</summary><pre>{escape_html(json.dumps(twin, indent=2, ensure_ascii=False))}</pre></details>"
            f"<details><summary>Jinja prompt template</summary><pre>{escape_html(example.get('prompt_template') or 'No prompt template recorded.')}</pre></details>"
            f"<details><summary>System prompt</summary><pre>{escape_html(example.get('system_prompt') or 'No system prompt recorded.')}</pre></details>"
            f"<details><summary>User prompt</summary><pre>{escape_html(example.get('user_prompt') or 'No user prompt recorded.')}</pre></details>"
            f"<details><summary>Model answer</summary><pre>{escape_html(json.dumps(example.get('model_answer', {}), indent=2, ensure_ascii=False))}</pre></details>"
            f"<details><summary>Raw model response text</summary><pre>{escape_html(example.get('model_response_text') or 'No raw model response text recorded.')}</pre></details>"
            f"<details><summary>Raw model response metadata</summary><pre>{escape_html(json.dumps(example.get('raw_model_response', {}), indent=2, ensure_ascii=False))}</pre></details>"
            f"<details><summary>Scenario inputs</summary><pre>{escape_html(json.dumps(example.get('scenario', {}), indent=2, ensure_ascii=False))}</pre></details>"
            "</article>"
        )

    report_data = escape_html(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(page_title)}</title>
  <style>
    {EP_REPORT_CSS}
    :root {{ --ink:var(--ep-dark); --muted:var(--ep-gray); --line:var(--ep-border); --panel:#fff; }}
    body {{ max-width:1180px; }}
    header {{ margin-bottom:1.5rem; }}
    main {{ padding-bottom:2rem; }}
    h2 {{ margin-top:0; }}
    h3 {{ font-size:1.1rem; }}
    p, .subtle {{ color:var(--muted); line-height:1.35; }}
    .panel, .example-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; margin-bottom:18px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; margin:0; }}
    th, td {{ text-align:left; border-bottom:1px solid #edf0f4; padding:8px 9px; vertical-align:top; }}
    th {{ background:var(--ep-green); color:#fff; font-size:12px; }}
    .grid th {{ width:210px; }}
    tr:nth-child(even) {{ background:var(--ep-light-gray); }}
    tr:hover {{ background:#e8f5e9; }}
    td span {{ display:block; color:var(--muted); font-size:12px; line-height:1.35; margin-top:3px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:6px; }}
    .numeric {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    code, pre {{ font-family:var(--font-mono); font-size:12px; }}
    pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#1e1e1e; color:#d4d4d4; border:1px solid var(--line); border-radius:6px; padding:10px; max-height:360px; overflow:auto; }}
    details {{ margin-top:10px; }}
    summary {{ cursor:pointer; font-weight:700; }}
    dl {{ display:grid; grid-template-columns:150px 1fr; gap:4px 10px; margin:0 0 8px; font-size:13px; }}
    dt {{ color:var(--muted); }}
    dd {{ margin:0; }}
    @media (max-width:760px) {{ dl {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <header>
    <h1>{escape_html(page_title)}</h1>
    <div class="subtle">Audit view for what this digital twin job was, how it was imported, and what prompts/scenarios were run.</div>
    <div class="subtle">Survey id: <code>{escape_html(str(survey))}</code></div>
  </header>
  <main>
    <section class="grid">
      <div class="panel"><h2>Run Overview</h2><table>{kv_rows(overview)}</table></div>
      <div class="panel"><h2>Construction</h2><table>{construction_rows}</table></div>
    </section>
    <section class="panel">
      <h2>Held-Out Questions</h2>
      <div class="table-wrap"><table><thead><tr><th>Question</th><th>Rows</th><th>Respondents</th><th>Options</th><th>Observed target answers</th><th>Models</th></tr></thead><tbody>{question_rows or '<tr><td colspan="6">No question rows found.</td></tr>'}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>Models</h2>
      <div class="table-wrap"><table><thead><tr><th>Model</th><th>Rows</th><th>Parameters</th></tr></thead><tbody>{model_rows or '<tr><td colspan="3">No model rows found.</td></tr>'}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>Import Issues</h2>
      <div class="table-wrap"><table><thead><tr><th>Row</th><th>Question</th><th>Model</th><th>Error</th></tr></thead><tbody>{issue_rows or '<tr><td colspan="4">No import issues recorded.</td></tr>'}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>Prompt Examples</h2>
      <p>Examples are sampled from the stored raw Results file. The twin identity identifies the respondent/agent/scenario row, the Jinja template is the prompt construction, the user prompt is the rendered text sent to the model, and the model answer/raw response show what came back. Older/manual imports may not include every field.</p>
      {''.join(prompt_blocks) or '<div class="subtle">No prompt examples available.</div>'}
    </section>
  </main>
  <script type="application/json" id="twin-run-report-data">{report_data}</script>
</body>
</html>"""


def render_twin_job_comparison_report_html(payload: dict[str, Any]) -> str:
    survey = payload.get("survey", "")
    display_title, _raw_title = report_display_title(str(survey))
    page_title = f"{display_title} Twin Job Comparison"
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    diagnostics = payload.get("diagnostics", {})
    job_ids = payload.get("job_ids", [])
    descriptions = diagnostics.get("twin_set_descriptions", {})
    skill_score_section = skill_score_section_html(rows)
    bootstrap_ci_section = bootstrap_ci_section_html(rows)
    conditional_baseline_appendix = (
        conditional_baseline_appendix_html()
        if has_conditional_baseline([str(row.get("model_label")) for row in rows])
        else ""
    )

    def numeric(value: Any, precision: int = 3) -> str:
        if value is None:
            return ""
        return f"{float(value):.{precision}f}"

    def pct(value: Any) -> str:
        if value is None:
            return ""
        return f"{float(value) * 100:.1f}%"

    def signed(value: Any) -> str:
        if value is None:
            return ""
        return f"{float(value):+.3f}"

    def natural_key(value: Any) -> tuple[str, int, str]:
        text = str(value)
        prefix = "".join(ch for ch in text if not ch.isdigit())
        digits = "".join(ch for ch in text if ch.isdigit())
        return (prefix, int(digits) if digits else 10**9, text)

    def set_title(label: Any) -> str:
        info = descriptions.get(str(label), {})
        return str(info.get("description") or label or "")

    def set_detail(label: Any) -> str:
        info = descriptions.get(str(label), {})
        parts = []
        for key, prefix in [("job_id", "job"), ("model_label", "model"), ("source_name", "source")]:
            if info.get(key):
                parts.append(f"{prefix}: {info.get(key)}")
        return " | ".join(parts)

    overview = [
        ("Jobs", f"{len(job_ids):,}"),
        ("Twin sets", f"{len(summary):,}"),
        ("Prediction rows", f"{len(rows):,}"),
        ("Respondents", f"{len({row.get('respondent_id') for row in rows}):,}"),
        ("Held-out questions", f"{len({row.get('heldout_question') for row in rows}):,}"),
    ]
    overview_html = "".join(
        f"<article class=\"metric-card\"><label>{escape_html(label)}</label><strong>{escape_html(value)}</strong></article>"
        for label, value in overview
    )

    summary_rows = []
    best_nll = min((values.get("mean_negative_log_likelihood", 0.0) for values in summary.values()), default=None)
    for label, values in sorted(summary.items(), key=lambda item: item[1].get("mean_negative_log_likelihood", 0.0)):
        baseline = diagnostics.get("baseline_comparison", {}).get(label, {})
        summary_rows.append(
            "<tr>"
            f"<td><b>{escape_html(set_title(label))}</b><span>{escape_html(set_detail(label))}</span></td>"
            f"<td class=\"numeric\">{values.get('rows', 0)}</td>"
            f"<td class=\"numeric\">{numeric(values.get('top1_accuracy'))}</td>"
            f"<td class=\"numeric\">{numeric(values.get('mean_probability_actual'))}</td>"
            f"<td class=\"numeric {'winner' if best_nll is not None and values.get('mean_negative_log_likelihood') == best_nll else ''}\">{numeric(values.get('mean_negative_log_likelihood'))}</td>"
            f"<td class=\"numeric\">{numeric(values.get('mean_brier'))}</td>"
            f"<td class=\"numeric\">{numeric(values.get('expected_calibration_error'))}</td>"
            f"<td class=\"numeric {'good' if (baseline.get('nll_vs_uniform') or 0) >= 0 else 'bad'}\">{signed(baseline.get('nll_vs_uniform'))}</td>"
            f"<td class=\"numeric {'good' if (baseline.get('nll_vs_empirical') or 0) >= 0 else 'bad'}\">{signed(baseline.get('nll_vs_empirical'))}</td>"
            "</tr>"
        )

    job_rows = []
    for label, info in sorted(descriptions.items(), key=lambda item: set_title(item[0])):
        job_rows.append(
            "<tr>"
            f"<td><b>{escape_html(set_title(label))}</b><span>{escape_html(label)}</span></td>"
            f"<td>{escape_html(info.get('job_id') or '')}</td>"
            f"<td>{escape_html(info.get('model_label') or '')}</td>"
            f"<td>{escape_html(info.get('source_name') or '')}</td>"
            f"<td>{escape_html(info.get('created_at') or '')}</td>"
            "</tr>"
        )

    option_rows_by_question: dict[str, dict[str, list[dict[str, Any]]]] = {}
    target_by_question: dict[str, dict[str, float]] = {}
    question_text: dict[str, str] = {}
    for row in diagnostics.get("marginal_options", []):
        question = str(row.get("heldout_question"))
        label = str(row.get("model_label"))
        option_rows_by_question.setdefault(question, {}).setdefault(label, []).append(row)
        if row.get("heldout_question_text"):
            question_text[question] = str(row.get("heldout_question_text"))
        if row.get("target_probability") is not None:
            target_by_question.setdefault(question, {})[str(row.get("option_label"))] = float(row.get("target_probability"))

    marginal_by_question_label = {
        (str(row.get("heldout_question")), str(row.get("model_label"))): row
        for row in diagnostics.get("marginal_comparisons", [])
    }
    question_metrics = diagnostics.get("summary_by_question", {})
    chart_colors = ["#2563eb", "#7c3aed", "#0f766e", "#c2410c", "#be123c", "#4d7c0f"]

    def question_chart(question: str, labels: list[str]) -> str:
        target = target_by_question.get(question, {})
        options = list(target)
        if not options:
            for label in labels:
                for row in option_rows_by_question.get(question, {}).get(label, []):
                    option = str(row.get("option_label"))
                    if option not in options:
                        options.append(option)
        if not options:
            return '<div class="subtle">No marginal option rows available for this question.</div>'
        uniform = 1 / len(options)
        marker = f"<em class=\"uniform-marker\" style=\"left:{uniform * 100:.1f}%\"></em>"
        legend = [
            "<span><i class=\"actual-dot\"></i>Actual empirical</span>",
            *[
                f"<span><i style=\"background:{chart_colors[index % len(chart_colors)]}\"></i>{escape_html(set_title(label))}</span>"
                for index, label in enumerate(labels)
            ],
            f"<span><i class=\"uniform-dot\"></i>Uniform {uniform:.1%}</span>",
        ]
        by_label_option = {
            label: {str(row.get("option_label")): float(row.get("predicted_probability") or 0.0) for row in option_rows_by_question.get(question, {}).get(label, [])}
            for label in labels
        }
        option_blocks = []
        for option in options:
            actual = target.get(option)
            option_winner = None
            if actual is not None:
                option_winner = min(labels, key=lambda label: abs(by_label_option.get(label, {}).get(option, 0.0) - actual))
            bars = []
            if actual is not None:
                bars.append(
                    "<div class=\"bar-line\">"
                    "<div class=\"bar-label actual-label\">Actual</div>"
                    f"<div class=\"bar-track\"><span class=\"actual-bar\" style=\"width:{actual * 100:.1f}%\"></span>{marker}</div>"
                    f"<div class=\"bar-value\">{pct(actual)}</div>"
                    "</div>"
                )
            for index, label in enumerate(labels):
                probability = by_label_option.get(label, {}).get(option, 0.0)
                winner = " option-winner" if option_winner == label else ""
                bars.append(
                    f"<div class=\"bar-line{winner}\">"
                    f"<div class=\"bar-label\">{escape_html(set_title(label))}</div>"
                    f"<div class=\"bar-track\"><span style=\"width:{probability * 100:.1f}%; background:{chart_colors[index % len(chart_colors)]}\"></span>{marker}</div>"
                    f"<div class=\"bar-value\">{pct(probability)}</div>"
                    "</div>"
                )
            option_winner_text = f"<span>closest: {escape_html(set_title(option_winner))}</span>" if option_winner else ""
            option_blocks.append(
                "<div class=\"option-block\">"
                f"<div class=\"option-name\"><b>{escape_html(option)}</b>{option_winner_text}</div>"
                f"<div class=\"option-bars\">{''.join(bars)}</div>"
                "</div>"
            )
        return f"<div class=\"legend\">{''.join(legend)}</div>{''.join(option_blocks)}"

    question_blocks = []
    for question, by_label in sorted(question_metrics.items(), key=lambda item: natural_key(item[0])):
        labels = sorted(by_label)
        if not labels:
            continue
        comparisons = [marginal_by_question_label.get((str(question), label), {}) for label in labels]
        comparisons_with_l1 = [row for row in comparisons if row.get("l1") is not None]
        overall_winner = min(comparisons_with_l1, key=lambda row: row.get("l1")) if comparisons_with_l1 else {}
        metric_rows = []
        best_nll_question = min((values.get("mean_negative_log_likelihood", 0.0) for values in by_label.values()), default=None)
        for label, values in sorted(by_label.items(), key=lambda item: item[1].get("mean_negative_log_likelihood", 0.0)):
            comparison = marginal_by_question_label.get((str(question), label), {})
            metric_rows.append(
                "<tr>"
                f"<td><b>{escape_html(set_title(label))}</b><span>{escape_html(set_detail(label))}</span></td>"
                f"<td class=\"numeric\">{values.get('rows', 0)}</td>"
                f"<td class=\"numeric\">{numeric(values.get('top1_accuracy'))}</td>"
                f"<td class=\"numeric {'winner' if best_nll_question is not None and values.get('mean_negative_log_likelihood') == best_nll_question else ''}\">{numeric(values.get('mean_negative_log_likelihood'))}</td>"
                f"<td class=\"numeric\">{numeric(values.get('mean_brier'))}</td>"
                f"<td class=\"numeric {'winner' if overall_winner and comparison is overall_winner else ''}\">{numeric(comparison.get('l1'))}</td>"
                f"<td class=\"numeric\">{numeric(comparison.get('js_divergence'))}</td>"
                f"<td>{'yes' if comparison.get('top_option_agrees') else 'no' if comparison.get('top_option_agrees') is not None else ''}</td>"
                "</tr>"
            )
        question_blocks.append(
            "<section class=\"question-card\">"
            f"<div class=\"question-head\"><div><h2>{escape_html(question)}</h2><p>{escape_html(question_text.get(str(question), ''))}</p></div>"
            f"<div class=\"winner-pill\">overall closest marginal: {escape_html(set_title(overall_winner.get('model_label')) if overall_winner else 'n/a')}</div></div>"
            f"{question_chart(str(question), labels)}"
            "<div class=\"table-wrap\"><table><thead><tr><th>Twin set</th><th>Rows</th><th>Accuracy</th><th>NLL</th><th>Brier</th><th>Marginal L1</th><th>JS</th><th>Top agrees</th></tr></thead>"
            f"<tbody>{''.join(metric_rows)}</tbody></table></div>"
            "</section>"
        )

    report_data = escape_html(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(page_title)}</title>
  <style>
    {EP_REPORT_CSS}
    :root {{ --ink:var(--ep-dark); --muted:var(--ep-gray); --line:var(--ep-border); --panel:#fff; --good:#176f3d; --bad:#a13a2a; --win:#fff6cc; }}
    body {{ max-width:1280px; }}
    header {{ margin-bottom:1.5rem; }}
    main {{ padding-bottom:2rem; }}
    h2 {{ margin-top:0; }}
    p,.subtle,td span,.option-name span {{ color:var(--muted); line-height:1.35; }}
    .metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:18px; }}
    .metric-card,.panel,.question-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .metric-card label {{ display:block; color:var(--muted); font-size:12px; }}
    .metric-card strong {{ display:block; font-size:22px; margin-top:2px; }}
    .panel,.question-card {{ margin-bottom:18px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:6px; background:#fff; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; margin:0; }}
    th,td {{ text-align:left; border-bottom:1px solid #edf0f4; padding:8px 9px; vertical-align:top; }}
    th {{ background:var(--ep-green); color:#fff; font-size:12px; }}
    tr:nth-child(even) {{ background:var(--ep-light-gray); }}
    tr:hover {{ background:#e8f5e9; }}
    td span {{ display:block; font-size:12px; margin-top:3px; }}
    .numeric {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .good {{ color:var(--good); }}
    .bad {{ color:var(--bad); }}
    .winner {{ background:var(--win); font-weight:700; }}
    .question-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:12px; }}
    .winner-pill {{ border:1px solid #e7d98b; background:var(--win); border-radius:999px; padding:5px 10px; font-size:12px; white-space:nowrap; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:10px 16px; align-items:center; margin:8px 0 12px; font-size:12px; color:var(--muted); }}
    .legend i {{ display:inline-block; width:10px; height:10px; border-radius:999px; margin-right:5px; vertical-align:-1px; }}
    .actual-dot,.actual-bar {{ background:#111827; }}
    .uniform-dot {{ background:#94a3b8; }}
    .option-block {{ display:grid; grid-template-columns:minmax(190px,.42fr) minmax(300px,.58fr); gap:14px; align-items:start; border-top:1px solid #edf0f4; padding:9px 0; }}
    .option-name b {{ display:block; }}
    .bar-line {{ display:grid; grid-template-columns:minmax(110px,170px) minmax(180px,1fr) 54px; gap:8px; align-items:center; min-height:18px; }}
    .bar-line.option-winner .bar-label {{ font-weight:700; }}
    .bar-track {{ position:relative; height:13px; background:#eef2f6; border-radius:999px; overflow:hidden; }}
    .bar-track span {{ position:absolute; inset:0 auto 0 0; border-radius:999px; min-width:1px; }}
    .uniform-marker {{ position:absolute; top:-4px; bottom:-4px; width:2px; background:#64748b; z-index:2; }}
    .bar-value {{ text-align:right; font-variant-numeric:tabular-nums; font-size:12px; }}
    .bar-label {{ color:#334155; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    @media (max-width:820px) {{ .question-head,.option-block,.bar-line {{ display:block; }} .winner-pill {{ white-space:normal; margin-top:8px; }} .bar-track {{ margin:4px 0; }} .bar-value {{ text-align:left; }} }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <header>
    <h1>{escape_html(page_title)}</h1>
    <div class="subtle">Side-by-side comparison of imported digital twin jobs. Question charts compare empirical marginals with each job's twin-implied marginal.</div>
    <div class="subtle">Survey id: <code>{escape_html(str(survey))}</code></div>
  </header>
  <main>
    <section class="metrics">{overview_html}</section>
    <section class="panel">
      <h2>Overall Performance</h2>
      <div class="table-wrap"><table><thead><tr><th>Twin set</th><th>Rows</th><th>Accuracy</th><th>p(actual)</th><th>NLL</th><th>Brier</th><th>ECE</th><th>NLL vs uniform</th><th>NLL vs empirical oracle</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>Compared Jobs</h2>
      <div class="table-wrap"><table><thead><tr><th>Twin set</th><th>Job id</th><th>Model</th><th>Source</th><th>Created/imported</th></tr></thead><tbody>{''.join(job_rows) or '<tr><td colspan="5">No job metadata available.</td></tr>'}</tbody></table></div>
    </section>
    {''.join(question_blocks) or '<section class="panel"><div class="subtle">No question comparison rows available.</div></section>'}
    {skill_score_section}
  {bootstrap_ci_section}
    {conditional_baseline_appendix}
  </main>
  <script type="application/json" id="twin-job-comparison-data">{report_data}</script>
</body>
</html>"""


def render_twin_benchmark_report_html(payload: dict[str, Any]) -> str:
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    benchmark_name = payload.get("benchmark", "twin benchmark")
    summary_rows = []
    for model, values in summary.items():
        summary_rows.append(
            "<tr>"
            f"<td>{escape_html(model)}</td>"
            f"<td class=\"numeric\">{values['survey_count']}</td>"
            f"<td class=\"numeric\">{values['mean_accuracy']:.3f}</td>"
            f"<td class=\"numeric\">{values['mean_nll']:.3f}</td>"
            f"<td class=\"numeric\">{values['mean_brier']:.3f}</td>"
            f"<td class=\"numeric\">{values['mean_ece']:.3f}</td>"
            f"<td class=\"numeric {'good' if values.get('mean_nll_vs_empirical', 0) >= 0 else 'bad'}\">{values.get('mean_nll_vs_empirical', 0):+.3f}</td>"
            "</tr>"
        )

    detail_rows = []
    for row in rows:
        detail_rows.append(
            "<tr>"
            f"<td>{escape_html(row.get('survey'))}</td>"
            f"<td>{escape_html(row.get('heldout_questions'))}</td>"
            f"<td class=\"numeric\">{row.get('option_count', '')}</td>"
            f"<td>{escape_html(row.get('model'))}</td>"
            f"<td class=\"numeric\">{row.get('rows', 0)}</td>"
            f"<td class=\"numeric\">{row.get('accuracy', 0):.3f}</td>"
            f"<td class=\"numeric\">{row.get('nll', 0):.3f}</td>"
            f"<td class=\"numeric\">{row.get('nll_p95', 0):.3f}</td>"
            f"<td class=\"numeric\">{row.get('brier', 0):.3f}</td>"
            f"<td class=\"numeric\">{row.get('ece', 0):.3f}</td>"
            f"<td class=\"numeric {'good' if row.get('nll_vs_empirical', 0) >= 0 else 'bad'}\">{row.get('nll_vs_empirical', 0):+.3f}</td>"
            "</tr>"
        )

    guidance = []
    for model, values in summary.items():
        if values.get("mean_nll_vs_empirical", 0) < 0:
            guidance.append(f"{model} trails the empirical marginal baseline on mean NLL across this benchmark.")
        if values.get("mean_ece", 0) > 0.15:
            guidance.append(f"{model} shows elevated calibration error; inspect overconfident misses before using probabilities operationally.")
    guidance_rows = "".join(f"<li>{escape_html(item)}</li>" for item in guidance) or "<li>No major benchmark-level warnings.</li>"

    report_data = escape_script_text(json.dumps(payload, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(benchmark_name)} Twin Benchmark</title>
  <style>
    :root {{ color-scheme: light; --ink:#17202a; --muted:#607080; --line:#d8dee6; --bg:#f7f8fa; --panel:#ffffff; --good:#0b7a3b; --bad:#b42318; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 36px 22px; background:#fff; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    .subtle {{ color:var(--muted); font-size:14px; }}
    main {{ padding:24px 36px 44px; max-width:1320px; margin:0 auto; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; margin-bottom:18px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:6px; }}
    table {{ width:100%; border-collapse:collapse; min-width:980px; font-size:13px; }}
    th, td {{ text-align:left; vertical-align:top; border-bottom:1px solid var(--line); padding:9px 10px; }}
    th {{ background:#f8fafc; color:#334155; font-size:12px; }}
    tr:last-child td {{ border-bottom:0; }}
    .numeric {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .good {{ color:var(--good); }}
    .bad {{ color:var(--bad); }}
    li {{ margin:6px 0; }}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <header>
    <h1>{escape_html(benchmark_name)} Twin Benchmark</h1>
    <div class="subtle">Cross-survey preflight check for digital twin probability estimates. Use this to compare models, calibration, overconfidence risk, and lift over empirical marginals.</div>
  </header>
  <main>
    <section class="card">
      <h2>Model Summary</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Model</th><th>Surveys</th><th>Accuracy</th><th>NLL</th><th>Brier</th><th>ECE</th><th>NLL vs empirical</th></tr></thead>
          <tbody>{''.join(summary_rows)}</tbody>
        </table>
      </div>
    </section>
    <section class="card">
      <h2>Practical Guidance</h2>
      <ul>{guidance_rows}</ul>
    </section>
    <section class="card">
      <h2>Survey Details</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Survey</th><th>Held-out</th><th>Options</th><th>Model</th><th>Rows</th><th>Accuracy</th><th>NLL</th><th>NLL p95</th><th>Brier</th><th>ECE</th><th>NLL vs empirical</th></tr></thead>
          <tbody>{''.join(detail_rows)}</tbody>
        </table>
      </div>
    </section>
  </main>
  <script type="application/json" id="twin-benchmark-data">{report_data}</script>
</body>
</html>"""


def render_twin_practitioner_report_html(
    payload: dict[str, Any],
    markdown: str,
    generation: dict[str, Any] | None = None,
) -> str:
    benchmark = payload.get("benchmark", "twin benchmark")
    display_title, benchmark_detail = report_display_title(str(benchmark))
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    best_accuracy = max(rows, key=lambda row: row.get("accuracy", 0.0), default={})
    best_confidence_model = min(
        summary.items(),
        key=lambda item: (
            item[1].get("mean_ece") if item[1].get("mean_ece") is not None else 999.0,
            item[1].get("mean_nll", 999.0),
        ),
        default=(None, {}),
    )

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.0f}%"

    def signed(value: float | None) -> str:
        return "n/a" if value is None else f"{value:+.3f}"

    detail_rows = []
    for row in rows:
        confidence = "good" if (row.get("ece") is not None and row.get("ece", 1.0) < 0.1) else "mixed"
        if row.get("nll_p95") is not None and row.get("nll_p95", 0.0) >= 10.0:
            confidence = "overconfident misses"
        detail_rows.append(
            "<tr>"
            f"<td><code>{escape_html(row.get('survey'))}</code></td>"
            f"<td><code>{escape_html(row.get('heldout_questions'))}</code></td>"
            f"<td><code>{escape_html(row.get('model'))}</code></td>"
            f"<td class=\"num\">{row.get('rows', 0)}</td>"
            f"<td class=\"num\">{escape_html(row.get('option_count') or 'mixed')}</td>"
            f"<td class=\"num\">{pct(row.get('accuracy'))}</td>"
            f"<td>{escape_html(confidence)}</td>"
            f"<td class=\"num\">{signed(row.get('nll_vs_empirical'))}</td>"
            "</tr>"
        )

    best_model_text = escape_html(best_confidence_model[0] or "n/a")
    report_data = escape_script_text(json.dumps(payload, separators=(",", ":")))
    generation_data = escape_script_text(json.dumps(generation or {}, separators=(",", ":")))
    report_markdown = remove_redundant_report_title(remove_reusable_practitioner_guidance(markdown))
    generated_body = markdown_to_html(report_markdown)
    plot_artifacts = payload.get("plot_artifacts", [])
    plot_cards = []
    for artifact in plot_artifacts:
        svg = str(artifact.get("svg", "")).strip()
        html = str(artifact.get("html", "")).strip()
        if not svg and not html:
            continue
        body = html if html else svg
        plot_cards.append(
            '<div class="plot-card">'
            f'<h3>{escape_html(artifact.get("title") or artifact.get("plot_id") or "Study plot")}</h3>'
            f'<div class="plot-frame">{body}</div>'
            "</div>"
        )
    plots_section = (
        '<section class="section">'
        '<h2>Study Plots</h2>'
        '<p class="detail">Deterministic plots generated from stored comparison data; these are not model-generated.</p>'
        + "".join(plot_cards)
        + "</section>"
        if plot_cards
        else ""
    )
    explainer_body = markdown_to_html(PRACTITIONER_EXPLAINER_MARKDOWN)
    decision_guidance_body = markdown_to_html(PRACTITIONER_DECISION_GUIDANCE_MARKDOWN)
    holdout_body = markdown_to_html(PRACTITIONER_HOLDOUT_MARKDOWN)
    copy_markdown = (
        PRACTITIONER_EXPLAINER_MARKDOWN.rstrip()
        + "\n\n---\n\n"
        + PRACTITIONER_HOLDOUT_MARKDOWN.rstrip()
        + "\n\n---\n\n"
        + PRACTITIONER_DECISION_GUIDANCE_MARKDOWN.rstrip()
        + "\n\n---\n\n"
        + report_markdown.strip()
        + "\n"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(display_title)}</title>
  <style>
    :root {{ --bg:#fafafa; --panel:#fff; --ink:#202124; --muted:#5f6872; --line:#dfe3e6; --accent:#2f6f4f; --accent-2:#34679a; --accent-soft:#edf3f0; --warn:#b36b18; --warn-soft:#f7eddf; --bad:#aa3a3a; --bad-soft:#f5e6e6; --soft:#f4f7f8; --mono:"SFMono-Regular",Consolas,Menlo,monospace; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.56 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
    main {{ max-width:1180px; margin:0 auto; padding:34px 22px 80px; }}
    header {{ margin-bottom:24px; }}
    .header-row {{ display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }}
    .brand {{ display:flex; align-items:center; gap:10px; color:var(--accent); font-weight:760; letter-spacing:.01em; margin-bottom:18px; }}
    .brand-mark {{ min-width:58px; height:34px; border-radius:9px; border:1px solid #c8ddd2; background:linear-gradient(135deg,#e3f0e8,#fff); display:grid; place-items:center; color:var(--accent); font-weight:800; padding:0 8px; }}
    .brand-kicker {{ display:block; color:var(--muted); font-size:12px; font-weight:650; text-transform:uppercase; letter-spacing:.08em; }}
    h1 {{ margin:0 0 10px; font-size:34px; line-height:1.12; letter-spacing:0; }}
    h2 {{ margin:0 0 14px; font-size:13px; line-height:1.25; text-transform:uppercase; letter-spacing:.08em; color:var(--accent); }}
    h3 {{ margin:0 0 10px; font-size:20px; line-height:1.25; letter-spacing:0; }}
    p {{ margin:8px 0; }}
    code {{ background:var(--accent-soft); border-radius:4px; padding:1px 5px; font-family:var(--mono); font-size:13px; }}
    .subtle {{ color:var(--muted); max-width:900px; font-size:17px; }}
    .detail {{ color:var(--muted); font-size:12.5px; margin-top:8px; }}
    .section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px 20px; margin:18px 0; }}
    .explainer {{ background:#fff; }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:16px 0 4px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }}
    .metric .label {{ color:var(--muted); font-size:12.5px; margin-bottom:4px; }}
    .metric .value {{ font-size:31px; line-height:1; font-weight:760; color:var(--accent); }}
    .callout {{ border-radius:7px; padding:14px 16px; margin:14px 0; border:1px solid var(--line); }}
    .callout.good {{ background:var(--accent-soft); border-color:#c8ddd2; }}
    .callout.warn {{ background:var(--warn-soft); border-color:#ead4b6; }}
    .callout.bad {{ background:var(--bad-soft); border-color:#e0bbbb; }}
    table {{ width:100%; border-collapse:collapse; margin:10px 0; font-size:13.2px; }}
    th, td {{ border:1px solid var(--line); padding:7px 8px; text-align:left; vertical-align:top; }}
    th {{ color:var(--ink); font-weight:700; background:#f0f3f4; }}
    .table-wrap {{ overflow-x:auto; }}
    .num {{ text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; }}
    ul, ol {{ margin:8px 0 0 22px; padding:0; }}
    li {{ margin:6px 0; }}
    .two-col {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .copy-control {{ display:flex; align-items:center; flex:0 0 auto; gap:10px; margin-top:2px; }}
    button {{ appearance:none; background:var(--accent); border:1px solid #245d42; border-radius:6px; color:#fff; cursor:pointer; font:inherit; font-size:14px; font-weight:700; padding:8px 12px; }}
    button:focus {{ outline:3px solid #c8ddd2; outline-offset:2px; }}
    button:hover {{ background:#285f45; }}
    .copy-status {{ color:var(--muted); font-size:13px; min-width:56px; }}
    .plot-card {{ border:1px solid var(--line); border-radius:8px; background:#fff; padding:14px; margin:14px 0; }}
    .plot-frame {{ overflow-x:auto; }}
    .plot-frame svg {{ display:block; max-width:100%; height:auto; }}
    @media (max-width:850px) {{ main {{ padding:24px 14px 48px; }} .grid,.two-col {{ grid-template-columns:1fr; }} .header-row {{ display:block; }} .copy-control {{ margin-top:14px; }} table {{ display:block; overflow-x:auto; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div class="brand" aria-label="Expected Parrot">
        <div class="brand-mark" aria-hidden="true">E[🦜]</div>
        <div><span class="brand-kicker">Expected Parrot</span>Survey Digital Twin Report</div>
      </div>
      <div class="header-row">
        <div>
          <h1>{escape_html(display_title)}</h1>
          <p class="subtle">Generated from recorded survey benchmark data. The report focuses on what a practitioner can do, how much validation is warranted, and where confidence scores need extra scrutiny.</p>
          {f'<p class="detail">Benchmark ID: <code>{escape_html(benchmark_detail)}</code></p>' if benchmark_detail else ''}
        </div>
        <div class="copy-control"><button type="button" id="copy-markdown">Copy Markdown</button><span class="copy-status" id="copy-status" aria-live="polite"></span></div>
      </div>
    </header>
    <section class="section explainer">
      {explainer_body}
    </section>
    <section class="section explainer">
      {holdout_body}
    </section>
    <section class="section explainer">
      {decision_guidance_body}
    </section>
    {plots_section}
    <section class="section">
      {generated_body}
    </section>
    <section class="section">
      <h2>Generated From Recorded Study Data</h2>
      <div class="grid">
        <div class="metric"><div class="label">Strongest result</div><div class="value">{pct(best_accuracy.get('accuracy'))}</div><div class="subtle">{escape_html(best_accuracy.get('survey', 'n/a'))}</div></div>
        <div class="metric"><div class="label">Best confidence default</div><div class="value">{best_model_text}</div><div class="subtle">Based on benchmark confidence quality</div></div>
        <div class="metric"><div class="label">Main risk</div><div class="value">Confidence</div><div class="subtle">Very confident wrong guesses</div></div>
      </div>
    </section>
  </main>
  <script type="application/json" id="twin-practitioner-data">{report_data}</script>
  <script type="application/json" id="twin-practitioner-generation">{generation_data}</script>
  <script type="text/plain" id="markdown-report">{escape_script_text(copy_markdown)}</script>
  <script>
    const button = document.getElementById("copy-markdown");
    const status = document.getElementById("copy-status");
    const markdown = document.getElementById("markdown-report").textContent.trim();
    async function copyMarkdown() {{
      try {{
        await navigator.clipboard.writeText(markdown);
        status.textContent = "Copied";
      }} catch (error) {{
        const textarea = document.createElement("textarea");
        textarea.value = markdown;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
        status.textContent = "Copied";
      }}
      window.setTimeout(() => {{ status.textContent = ""; }}, 1800);
    }}
    button.addEventListener("click", copyMarkdown);
  </script>
</body>
</html>"""
