from __future__ import annotations

import csv
import json
import math
import random
import re
import statistics
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any

from .reporting import EP_REPORT_CSS, copy_markdown_control, markdown_to_html, report_display_title
from .twin_report import weighted_row_mean


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def rankdata(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        average = (index + 1 + end) / 2
        for rank_index in range(index, end):
            ranks[indexed[rank_index][0]] = average
        index = end
    return ranks


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    left_var = sum(value * value for value in left_centered)
    right_var = sum(value * value for value in right_centered)
    if left_var <= 0 or right_var <= 0:
        return None
    return sum(left_value * right_value for left_value, right_value in zip(left_centered, right_centered)) / math.sqrt(left_var * right_var)


def spearman(left: list[float], right: list[float]) -> float | None:
    return pearson(rankdata(left), rankdata(right))


def safe_prefix(path: Path) -> str:
    return re.sub(r"_executive_summary$", "", path.stem)


def remove_leading_executive_summary_heading(markdown: str) -> str:
    lines = markdown.strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.fullmatch(r"#{1,3}\s+Executive Summary\s*", lines[0].strip(), flags=re.IGNORECASE):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip() + ("\n" if lines else "")


def write_lift_histogram(rows: list[dict[str, Any]], path: Path, *, baseline: str = "uniform") -> dict[str, Any]:
    lifts = []
    for row in rows:
        probability_actual = float(row.get("probability_actual", 0.0))
        if baseline == "empirical":
            baseline_probability = row.get("empirical_marginal_probability_actual", row.get("marginal_probability_actual"))
            if baseline_probability is None or float(baseline_probability) <= 0:
                continue
            lifts.append(probability_actual / float(baseline_probability))
        else:
            options = row.get("option_labels") or []
            if options:
                lifts.append(probability_actual / (1.0 / len(options)))
    bins = [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4, 5, 6, 8]
    labels = [f"{low:g}-{high:g}x" for low, high in zip(bins[:-1], bins[1:])] + [f"{bins[-1]:g}x+"]
    counts = [sum(1 for value in lifts if low <= value < high) for low, high in zip(bins[:-1], bins[1:])]
    counts.append(sum(1 for value in lifts if value >= bins[-1]))
    summary = {
        "rows": len(lifts),
        "mean_lift": mean(lifts),
        "median_lift": statistics.median(lifts) if lifts else 0.0,
        "share_above_1": sum(value > 1 for value in lifts) / len(lifts) if lifts else 0.0,
        "bins": [{"label": label, "count": count} for label, count in zip(labels, counts)],
    }

    width, height = 980, 430
    left, right, top, bottom = 58, 28, 44, 82
    plot_width = width - left - right
    plot_height = height - top - bottom
    max_count = max(counts or [1])
    bar_gap = 8
    bar_width = (plot_width - bar_gap * (len(counts) - 1)) / max(1, len(counts))
    y0 = top + plot_height
    baseline_label = "empirical marginal oracle" if baseline == "empirical" else "uniform baseline"
    parts = svg_header(width, height, f"Probability on actual answer vs {baseline_label}")
    for fraction in [0, 0.25, 0.5, 0.75, 1]:
        y = top + plot_height * (1 - fraction)
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="label muted" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{max_count * fraction:.0f}</text>')
    marker_x = None
    for index, (label, count) in enumerate(zip(labels, counts)):
        x = left + index * (bar_width + bar_gap)
        bar_height = plot_height * count / max_count if max_count else 0
        y = y0 - bar_height
        parts.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" rx="2"/>')
        parts.append(f'<text class="label" x="{x + bar_width / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle">{count}</text>')
        parts.append(f'<text class="label muted" x="{x + bar_width / 2:.1f}" y="{y0 + 18:.1f}" text-anchor="middle" transform="rotate(-35 {x + bar_width / 2:.1f} {y0 + 18:.1f})">{escape(label)}</text>')
        if label == "1-1.25x":
            marker_x = x
    if marker_x is not None:
        parts.append(f'<line class="marker" x1="{marker_x:.1f}" x2="{marker_x:.1f}" y1="{top}" y2="{y0}"/>')
        parts.append(f'<text class="small" x="{marker_x + 6:.1f}" y="{top + 16}" fill="#188038">1x baseline</text>')
    parts.append(f'<text class="small muted" x="{width - right}" y="28" text-anchor="end">N={len(lifts)} | mean lift={summary["mean_lift"]:.2f}x | median={summary["median_lift"]:.2f}x | &gt;1x={summary["share_above_1"]:.0%}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts))
    path.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def individual_signal_permutation(rows: list[dict[str, Any]], *, simulations: int, seed: int) -> dict[str, Any]:
    def score(scored: list[dict[str, Any]]) -> dict[str, float]:
        probabilities = [float(row.get("probabilities", {}).get(row.get("actual_answer"), 0.0)) for row in scored]
        nll = [-math.log(max(value, 1e-12)) for value in probabilities]
        return {"mean_p_actual": mean(probabilities), "mean_nll": mean(nll)}

    by_question: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_question[str(row.get("heldout_question"))].append(index)
    observed = score(rows)
    rng = random.Random(seed)
    null_p = []
    null_nll = []
    for _ in range(simulations):
        shuffled = [dict(row) for row in rows]
        for indexes in by_question.values():
            actuals = [rows[index].get("actual_answer") for index in indexes]
            rng.shuffle(actuals)
            for index, actual in zip(indexes, actuals):
                shuffled[index]["actual_answer"] = actual
        result = score(shuffled)
        null_p.append(result["mean_p_actual"])
        null_nll.append(result["mean_nll"])
    per_question = []
    for offset, (question, indexes) in enumerate(sorted(by_question.items())):
        question_rows = [rows[index] for index in indexes]
        question_observed = score(question_rows)
        question_rng = random.Random(seed + offset + 1)
        question_null_p = []
        question_null_nll = []
        for _ in range(simulations):
            shuffled = [dict(row) for row in question_rows]
            actuals = [row.get("actual_answer") for row in question_rows]
            question_rng.shuffle(actuals)
            for row, actual in zip(shuffled, actuals):
                row["actual_answer"] = actual
            result = score(shuffled)
            question_null_p.append(result["mean_p_actual"])
            question_null_nll.append(result["mean_nll"])
        per_question.append(
            {
                "question": question,
                "rows": len(question_rows),
                "observed_mean_p_actual": question_observed["mean_p_actual"],
                "null_mean_p_actual_mean": mean(question_null_p),
                "p_value_mean_p_actual": (sum(value >= question_observed["mean_p_actual"] for value in question_null_p) + 1)
                / (simulations + 1),
                "observed_mean_nll": question_observed["mean_nll"],
                "null_mean_nll_mean": mean(question_null_nll),
                "p_value_mean_nll": (sum(value <= question_observed["mean_nll"] for value in question_null_nll) + 1)
                / (simulations + 1),
            }
        )
    return {
        "rows": len(rows),
        "questions": len(by_question),
        "observed_mean_p_actual": observed["mean_p_actual"],
        "null_mean_p_actual_mean": mean(null_p),
        "p_value_mean_p_actual": (sum(value >= observed["mean_p_actual"] for value in null_p) + 1) / (simulations + 1),
        "observed_mean_nll": observed["mean_nll"],
        "null_mean_nll_mean": mean(null_nll),
        "p_value_mean_nll": (sum(value <= observed["mean_nll"] for value in null_nll) + 1) / (simulations + 1),
        "simulations": simulations,
        "per_question": per_question,
    }


def aggregate_groups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        question = str(row.get("heldout_question"))
        options = [str(option) for option in row.get("option_labels", [])]
        if not question or not options:
            continue
        group = groups.setdefault(
            question,
            {
                "question": question,
                "question_text": row.get("heldout_question_text"),
                "options": options,
                "predicted": {option: 0.0 for option in options},
                "actual": row.get("empirical_marginal_probabilities") or row.get("marginal_probabilities") or {},
                "n": 0,
            },
        )
        group["n"] += 1
        for option in options:
            group["predicted"][option] = group["predicted"].get(option, 0.0) + float(row.get("probabilities", {}).get(option, 0.0))
    for group in groups.values():
        for option in list(group["predicted"]):
            group["predicted"][option] /= max(1, group["n"])
    return groups


def write_pairwise_order_chart(groups: dict[str, dict[str, Any]], path: Path) -> dict[str, Any]:
    rows = []
    total_correct = 0.0
    total_pairs = 0
    for question, group in sorted(groups.items()):
        actual = {str(option): float(value) for option, value in (group.get("actual") or {}).items()}
        options = [option for option in group["options"] if option in actual]
        correct = 0.0
        pairs = 0
        for left_index, left_option in enumerate(options):
            for right_option in options[left_index + 1:]:
                actual_delta = actual[left_option] - actual[right_option]
                predicted_delta = group["predicted"].get(left_option, 0.0) - group["predicted"].get(right_option, 0.0)
                if actual_delta == 0:
                    continue
                pairs += 1
                if predicted_delta == 0:
                    correct += 0.5
                elif (actual_delta > 0 and predicted_delta > 0) or (actual_delta < 0 and predicted_delta < 0):
                    correct += 1
        if pairs:
            total_correct += correct
            total_pairs += pairs
            rows.append({"question": question, "n": group["n"], "option_count": len(options), "ordered_pairs": pairs, "pairwise_order_accuracy": correct / pairs})
    summary = {"questions": len(rows), "total_ordered_option_pairs": total_pairs, "pairwise_order_accuracy": total_correct / total_pairs if total_pairs else 0.0, "chance_baseline": 0.5}
    path.with_suffix(".json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2) + "\n")
    with path.with_suffix(".csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["question", "n", "option_count", "ordered_pairs", "pairwise_order_accuracy"])
        writer.writeheader()
        writer.writerows(rows)

    width, height = 980, 350
    left, right, top, bottom = 70, 30, 52, 105
    plot_width = width - left - right
    plot_height = height - top - bottom
    def y(value):
        return top + (1 - value) * plot_height

    bar_gap = 20
    bar_width = (plot_width - bar_gap * max(0, len(rows) - 1)) / max(1, len(rows))
    parts = svg_header(width, height, "Probability of ordering option pairs correctly")
    for tick in [0, 0.25, 0.5, 0.75, 1]:
        yy = y(tick)
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{yy:.1f}" y2="{yy:.1f}"/>')
        parts.append(f'<text class="label muted" x="{left - 10}" y="{yy + 4:.1f}" text-anchor="end">{tick:.0%}</text>')
    parts.append(f'<line class="chance" x1="{left}" x2="{width - right}" y1="{y(0.5):.1f}" y2="{y(0.5):.1f}"/>')
    for index, row in enumerate(rows):
        accuracy = float(row["pairwise_order_accuracy"])
        x = left + index * (bar_width + bar_gap)
        yy = y(accuracy)
        height_value = top + plot_height - yy
        css = "bar" if accuracy >= 0.5 else "bad"
        parts.append(f'<rect class="{css}" x="{x:.1f}" y="{yy:.1f}" width="{bar_width:.1f}" height="{height_value:.1f}" rx="3"/>')
        parts.append(f'<text class="label" x="{x + bar_width / 2:.1f}" y="{yy - 6:.1f}" text-anchor="middle">{accuracy:.0%}</text>')
        parts.append(f'<text class="label muted" x="{x + bar_width / 2:.1f}" y="{top + plot_height + 24:.1f}" text-anchor="middle">{escape(row["question"])}</text>')
    parts.append(f'<text class="small muted" x="{width - right}" y="28" text-anchor="end">Overall={summary["pairwise_order_accuracy"]:.1%} over {total_pairs} option pairs</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts))
    return {"summary": summary, "rows": rows, "svg_path": str(path)}


def write_spearman_detail(groups: dict[str, dict[str, Any]], path: Path, *, simulations: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    rows = []
    for question, group in sorted(groups.items()):
        actual = {str(option): float(value) for option, value in (group.get("actual") or {}).items()}
        options = [option for option in group["options"] if option in actual]
        if len(options) < 2:
            continue
        predicted = [group["predicted"].get(option, 0.0) for option in options]
        target = [actual[option] for option in options]
        rho = spearman(predicted, target)
        if rho is None:
            continue
        null = []
        for _ in range(simulations):
            shuffled = predicted[:]
            rng.shuffle(shuffled)
            null.append(spearman(shuffled, target) or 0.0)
        rows.append({"question": question, "n": group["n"], "option_count": len(options), "spearman_rank": rho, "random_rank_p_value": (sum(value >= rho for value in null) + 1) / (simulations + 1)})
    values = [row["spearman_rank"] for row in rows]
    summary = {"questions": len(rows), "mean_spearman": mean(values), "median_spearman": statistics.median(values) if values else 0.0, "simulations": simulations}
    path.with_suffix(".json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2) + "\n")
    with path.with_suffix(".csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["question", "n", "option_count", "spearman_rank", "random_rank_p_value"])
        writer.writeheader()
        writer.writerows(rows)
    return {"summary": summary, "rows": rows}


def svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;fill:#172033}.muted{fill:#5c667a}.grid{stroke:#e6e9ef;stroke-width:1}.bar{fill:#1f6feb}.bad{fill:#b42318}.chance{stroke:#188038;stroke-width:3;stroke-dasharray:6 5}.marker{stroke:#188038;stroke-width:3}.label{font-size:12px}.title{font-size:20px;font-weight:700}.small{font-size:12px}</style>',
        f'<text class="title" x="58" y="28">{escape(title)}</text>',
    ]


def render_html(
    *,
    survey: str,
    metrics: dict[str, float],
    questions: list[dict[str, str]],
    lift_svg: Path,
    empirical_lift_svg: Path | None,
    lift: dict[str, Any],
    empirical_lift: dict[str, Any] | None,
    individual: dict[str, Any],
    pairwise_svg: Path,
    pairwise: dict[str, Any],
    spearman_detail: dict[str, Any],
    generated_markdown: str | None = None,
    generation: dict[str, Any] | None = None,
) -> str:
    display_title, _raw_title = report_display_title(str(survey))
    question_rows = "".join(f"<tr><td><code>{escape(row['question'])}</code></td><td>{escape(row['text'])}</td></tr>" for row in questions)
    pairwise_accuracy = pairwise["summary"].get("pairwise_order_accuracy", 0.0)
    mean_spearman = spearman_detail["summary"].get("mean_spearman", 0.0)
    # Pairwise/Spearman are option-ordering metrics: they only apply when held-out
    # questions have multiple orderable options. For single-select (e.g. binary)
    # surveys there are no ordered pairs, so report N/A rather than a misleading 0%.
    has_ordering_data = int(pairwise["summary"].get("total_ordered_option_pairs", 0) or 0) > 0
    question_count = int(metrics.get("question_count", 0.0))
    individual_p = individual.get("p_value_mean_p_actual")
    individual_signal_text = (
        "Permutation testing found respondent-level signal beyond aggregate marginals."
        if individual_p is not None and float(individual_p) < 0.05
        else "Permutation testing did not find respondent-level signal beyond aggregate marginals."
    )
    ranking_caveat = (
        "These rank-order statistics are preliminary because they come from fewer than ten held-out questions."
        if question_count < 10
        else "These rank-order statistics are based on a broader held-out question set."
    )
    if has_ordering_data:
        ranking_why = f"Twins order option pairs correctly about {pairwise_accuracy:.0%} of the time versus 50% by chance. {ranking_caveat}"
        ranking_readout = (
            f"When using twins to rank concepts or answer options, this validation suggests they put option pairs in the "
            f"right order about {pairwise_accuracy:.0%} of the time, versus 50% by chance. {ranking_caveat}"
        )
        spearman_sentence = f"Mean Spearman rank correlation is {mean_spearman:.2f}."
    else:
        ranking_why = "This survey's held-out questions are single-select, so option-ordering accuracy does not apply and was not evaluated."
        ranking_readout = (
            "This survey's held-out questions are single-select, so option-ordering (pairwise and Spearman rank) metrics "
            "do not apply and were not evaluated."
        )
        spearman_sentence = ""
    empirical_lift_block = ""
    if empirical_lift_svg and empirical_lift:
        empirical_lift_block = f"""<h3>Lift Versus Empirical Marginal Oracle</h3><p>Mean lift versus the empirical marginal oracle is {empirical_lift['mean_lift']:.2f}x. This stricter comparison is only available because the held-out target was observed in the validation data; it is not available for future unanswered questions.</p><img src="{escape(empirical_lift_svg.name)}" alt="Histogram of lift over empirical marginal probability assigned to the actual answer." style="width:100%;max-width:980px;height:auto;border:1px solid var(--line);border-radius:8px;background:white;margin-top:8px">"""
    per_question_rows = "".join(
        f"<tr><td><code>{escape(row['question'])}</code></td><td class=\"num\">{row['rows']}</td><td class=\"num\">{row['observed_mean_p_actual']:.3f}</td><td class=\"num\">{row['null_mean_p_actual_mean']:.3f}</td><td class=\"num\">{row['p_value_mean_p_actual']:.5f}</td></tr>"
        for row in individual.get("per_question", [])
    )
    generation_note = ""
    if generation:
        generation_note = (
            f"<p class=\"subtle\">Generated analysis: <code>{escape(str(generation.get('report_id') or ''))}</code>"
            f"{' · model: <code>' + escape(str(generation.get('model'))) + '</code>' if generation.get('model') else ''}</p>"
        )
    if generated_markdown:
        generated_body = markdown_to_html(remove_leading_executive_summary_heading(generated_markdown))
        executive_body = (
            "<div class=\"panel span-12 callout\">"
            "<h2>Executive Summary</h2>"
            f"{generated_body}"
            f"{generation_note}"
            "</div>"
        )
    else:
        executive_body = f"""<div class="panel span-12 callout"><h2>Executive Summary</h2><p>This deterministic fallback summarizes held-out validation diagnostics for a digital twin AgentList built from survey microdata. It is not a substitute for the frontier-model executive analysis step; use the generated executive analysis when available.</p><p>{individual_signal_text} The current evidence is therefore strongest for aggregate opinion structure, directional ranking, and exploratory simulation rather than respondent-level targeting.</p></div>"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(display_title)} Digital Twin Executive Summary</title>
<style>
{EP_REPORT_CSS}
:root{{--line:var(--ep-border);--accent:var(--ep-green)}}
body{{max-width:1040px}}
header{{margin-bottom:1.5rem}}
.wrap{{max-width:1040px;margin:0 auto}}
.badge{{display:inline-block;color:#0f5132;background:#dff3e6;border:1px solid #b7dfc4;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:600;margin-bottom:10px}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;margin-top:18px}}
.panel{{background:#fff;border:1px solid var(--ep-border);border-radius:8px;padding:18px}}
.panel h2{{margin-top:0}}
.span-12{{grid-column:span 12}}
.span-4{{grid-column:span 4}}
.callout{{border-left:4px solid var(--ep-green);padding-left:18px}}
.metric .value{{font-size:28px;font-weight:700;line-height:1.1}}
.metric .label{{color:var(--ep-gray);font-size:13px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
@media(max-width:760px){{.span-4{{grid-column:span 12}}}}
</style></head><body>
{copy_markdown_control()}
<header><div class="wrap"><div class="badge">Executive Summary</div><h1>Digital Twin AgentList Validation</h1><p class="subtle">{escape(display_title)} · Survey id: <code>{escape(survey)}</code></p></div></header>
<main class="wrap"><section class="grid">
{executive_body}
<div class="panel span-4 metric"><div class="value">{metrics['row_count']:,.0f}</div><div class="label">Validation rows</div></div><div class="panel span-4 metric"><div class="value">{metrics['question_count']:,.0f}</div><div class="label">Held-out questions</div></div><div class="panel span-4 metric"><div class="value">{lift['share_above_1']:.0%}</div><div class="label">Rows above uniform</div></div>
<div class="panel span-12"><h2>Decision Guidance</h2><table><thead><tr><th>Decision use</th><th>Recommendation</th><th>Why</th></tr></thead><tbody><tr><td>Exploratory concept screening</td><td><strong>Use cautiously</strong></td><td>The validation shows lift over uniform guessing, but {individual_signal_text.lower()}</td></tr><tr><td>Ranking options or messages</td><td><strong>Preliminary directional use</strong></td><td>{ranking_why}</td></tr><tr><td>Exact market sizing, targeting, or public claims</td><td><strong>Do not use alone</strong></td><td>Held-out validation supports aggregate/directional signal, not precise standalone estimates or individual-level action.</td></tr></tbody></table></div>
<div class="panel span-12"><h2>What Was Held Out?</h2><p>The validation held out observed answers and predicted them from the remaining respondent context. Unless a run report records more specific exclusions, treat the context policy as all available observed answers except the current held-out target.</p><table><thead><tr><th>Question</th><th>Held-out target</th></tr></thead><tbody>{question_rows}</tbody></table></div>
<div class="panel span-12"><h2>Main Evidence</h2><table><thead><tr><th>Metric</th><th class="num">Twin</th><th class="num">Uniform over options</th></tr></thead><tbody><tr><td>Mean probability assigned to actual answer</td><td class="num">{metrics['mean_probability_actual']:.1%}</td><td class="num">{metrics['mean_uniform_probability_actual']:.1%}</td></tr><tr><td>Negative log likelihood</td><td class="num">{metrics['mean_negative_log_likelihood']:.3f}</td><td class="num">{metrics['mean_uniform_negative_log_likelihood']:.3f}</td></tr><tr><td>Brier score</td><td class="num">{metrics['mean_brier']:.3f}</td><td class="num">{metrics['mean_uniform_brier']:.3f}</td></tr></tbody></table></div>
<div class="panel span-12"><h2>Accuracy Lift Distribution</h2><h3>Lift Versus Uniform</h3><p>Mean lift over uniform is {lift['mean_lift']:.2f}x, median lift is {lift['median_lift']:.2f}x, and {lift['share_above_1']:.1%} of rows are above the uniform baseline. This asks whether twins beat random guessing over answer options.</p><img src="{escape(lift_svg.name)}" alt="Histogram of lift over uniform probability assigned to the actual answer." style="width:100%;max-width:980px;height:auto;border:1px solid var(--line);border-radius:8px;background:white;margin-top:8px">{empirical_lift_block}</div>
<div class="panel span-12"><h2>Individual Signal Beyond Marginals</h2><p>Within-question permutation keeps each prediction vector fixed and shuffles actual answers across respondents. It tests respondent-specific matching beyond question-level marginal structure; it does not test whether predictions beat uniform. A low p-value means respondent-specific matching is stronger than shuffled labels. A high p-value with good uniform lift means the model may be capturing aggregate or marginal structure rather than individual-level signal.</p><table><thead><tr><th>Statistic</th><th class="num">Observed</th><th class="num">Permutation null</th><th class="num">p-value</th></tr></thead><tbody><tr><td>Mean probability assigned to actual answer</td><td class="num">{individual['observed_mean_p_actual']:.3f}</td><td class="num">{individual['null_mean_p_actual_mean']:.3f}</td><td class="num">{individual['p_value_mean_p_actual']:.5f}</td></tr><tr><td>Mean negative log likelihood</td><td class="num">{individual['observed_mean_nll']:.3f}</td><td class="num">{individual['null_mean_nll_mean']:.3f}</td><td class="num">{individual['p_value_mean_nll']:.5f}</td></tr></tbody></table><h3>Per-question permutation results</h3><table><thead><tr><th>Question</th><th class="num">Rows</th><th class="num">Observed p(actual)</th><th class="num">Null p(actual)</th><th class="num">p-value</th></tr></thead><tbody>{per_question_rows}</tbody></table></div>
<div class="panel span-12"><h2>Marginal Rank Order</h2><p><strong>Plain-English readout:</strong> {ranking_readout}</p><img src="{escape(pairwise_svg.name)}" alt="Bar chart showing pairwise option ordering accuracy by validation question." style="width:100%;max-width:980px;height:auto;border:1px solid var(--line);border-radius:8px;background:white;margin-top:8px"><p>{spearman_sentence}</p></div>
<div class="panel span-12"><h2>Operating Recommendation</h2><ul><li>Use twins to shortlist, rank, and stress-test concepts before fielding real research.</li><li>For consequential decisions, field a small validation survey on finalist concepts and compare against twin predictions.</li><li>Track performance by question family over time.</li></ul></div>
</section></main></body></html>
"""


def build_executive_summary(
    rows: list[dict[str, Any]],
    *,
    survey: str,
    path: Path,
    markdown_path: Path | None,
    simulations: int,
    seed: int,
    generated_markdown: str | None = None,
    generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = safe_prefix(path)
    base = path.parent / prefix
    # Survey-weighted means (population estimates), matching twin-validate. With
    # all-1.0 weights these equal the plain means, so unweighted surveys are
    # unaffected; genuine survey weights (e.g. Pew) make these population-correct
    # instead of silently disagreeing with the validation report.
    def _wmean(key: str) -> float:
        return float(weighted_row_mean(rows, key) or 0.0)

    metrics = {
        "row_count": float(len(rows)),
        "question_count": float(len({row.get("heldout_question") for row in rows if row.get("heldout_question")})),
        "mean_probability_actual": _wmean("probability_actual"),
        "mean_uniform_probability_actual": _wmean("uniform_probability_actual"),
        "mean_negative_log_likelihood": _wmean("negative_log_likelihood"),
        "mean_uniform_negative_log_likelihood": _wmean("uniform_negative_log_likelihood"),
        "mean_brier": _wmean("brier"),
        "mean_uniform_brier": _wmean("uniform_brier"),
    }
    questions = []
    seen = set()
    for row in rows:
        question = str(row.get("heldout_question"))
        if question and question not in seen:
            seen.add(question)
            questions.append({"question": question, "text": str(row.get("heldout_question_text") or "")})

    lift_svg = base.with_name(f"{prefix}_actual_answer_lift_histogram.svg")
    empirical_lift_svg = base.with_name(f"{prefix}_empirical_marginal_lift_histogram.svg")
    pairwise_svg = base.with_name(f"{prefix}_pairwise_order_accuracy.svg")
    lift = write_lift_histogram(rows, lift_svg)
    empirical_lift = write_lift_histogram(rows, empirical_lift_svg, baseline="empirical")
    individual = individual_signal_permutation(rows, simulations=simulations, seed=seed)
    individual_path = base.with_name(f"{prefix}_individual_predictive_power_permutation.json")
    individual_path.write_text(json.dumps(individual, indent=2) + "\n")
    per_question_path = base.with_name(f"{prefix}_individual_predictive_power_by_question.csv")
    with per_question_path.open("w", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "question",
                "rows",
                "observed_mean_p_actual",
                "null_mean_p_actual_mean",
                "p_value_mean_p_actual",
                "observed_mean_nll",
                "null_mean_nll_mean",
                "p_value_mean_nll",
            ],
        )
        writer.writeheader()
        writer.writerows(individual.get("per_question", []))
    groups = aggregate_groups(rows)
    pairwise = write_pairwise_order_chart(groups, pairwise_svg)
    spearman_detail = write_spearman_detail(groups, base.with_name(f"{prefix}_marginal_rank_order.svg"), simulations=max(100, min(simulations, 5000)), seed=seed)
    path.write_text(
        render_html(
            survey=survey,
            metrics=metrics,
            questions=questions,
            lift_svg=lift_svg,
            empirical_lift_svg=empirical_lift_svg,
            lift=lift,
            empirical_lift=empirical_lift,
            individual=individual,
            pairwise_svg=pairwise_svg,
            pairwise=pairwise,
            spearman_detail=spearman_detail,
            generated_markdown=generated_markdown,
            generation=generation,
        )
    )
    markdown_path = markdown_path or path.with_suffix(".md")
    if generated_markdown:
        markdown_path.write_text(generated_markdown.strip() + "\n")
    else:
        markdown_path.write_text(
            "# Executive Summary: Digital Twin AgentList Validation\n\n"
            f"Survey: `{survey}`\n\n"
            f"Validation rows: {len(rows):,}\n\n"
            f"Mean p(actual): {metrics['mean_probability_actual']:.3f} vs uniform {metrics['mean_uniform_probability_actual']:.3f}.\n\n"
            f"Individual signal beyond marginals permutation p-value: {individual['p_value_mean_p_actual']:.5f}.\n\n"
            + (
                f"Pairwise option ordering accuracy: {pairwise['summary']['pairwise_order_accuracy']:.1%}.\n"
                if int(pairwise["summary"].get("total_ordered_option_pairs", 0) or 0) > 0
                else "Pairwise option ordering accuracy: not applicable (held-out questions are single-select).\n"
            )
        )
    return {
        "path": str(path),
        "markdown_path": str(markdown_path),
        "rows": len(rows),
        "questions": int(metrics["question_count"]),
        "metrics": metrics,
        "lift": lift,
        "empirical_lift": empirical_lift,
        "individual_signal": individual,
        "pairwise_ordering": pairwise,
        "spearman_rank_order": spearman_detail,
        "artifacts": {
            "lift_histogram": str(lift_svg),
            "empirical_lift_histogram": str(empirical_lift_svg),
            "individual_predictive_power": str(individual_path),
            "individual_predictive_power_by_question": str(per_question_path),
            "pairwise_order_accuracy": str(pairwise_svg),
            "spearman_rank_order": str(base.with_name(f"{prefix}_marginal_rank_order.json")),
            "spearman_rank_order_csv": str(base.with_name(f"{prefix}_marginal_rank_order.csv")),
            "pairwise_order_accuracy_csv": str(pairwise_svg.with_suffix(".csv")),
        },
        "generation": generation or {"mode": "deterministic_fallback"},
    }
