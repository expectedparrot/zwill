from __future__ import annotations

import html
from typing import Any


def render_bootstrap_forest_svg(result: dict[str, Any]) -> str:
    """Render paired model-minus-baseline bootstrap intervals around zero."""
    deltas = result.get("deltas_vs_baseline") or {}
    rows: list[tuple[str, str, dict[str, Any], bool]] = []
    metrics = [
        ("probability_actual", "p(actual)", True),
        ("negative_log_likelihood", "NLL", False),
        ("brier", "Brier", False),
        ("top1_correct", "Top-1", True),
    ]
    for model, block in sorted((deltas.get("models") or {}).items()):
        macro = block.get("macro") or {}
        for key, label, higher_is_better in metrics:
            stat = macro.get(key)
            if stat:
                rows.append((str(model), label, stat, higher_is_better))
    if not rows:
        return ""
    width = 920
    row_h = 42
    top, bottom, left, right = 84, 48, 250, 90
    height = top + bottom + row_h * len(rows)
    plot_w = width - left - right
    extent = max(abs(float(stat[bound])) for _model, _label, stat, _hib in rows for bound in ("lo", "hi")) or 1.0
    extent *= 1.12

    def px(value: float) -> float:
        return left + (value + extent) / (2 * extent) * plot_w

    zero_x = px(0.0)
    baseline = html.escape(str(deltas.get("baseline_model") or "baseline"))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;fill:#18332f}.title{font-size:20px;font-weight:700}.sub{font-size:12px;fill:#61716e}.label{font-size:13px}.ci{stroke:#52625f;stroke-width:2}.zero{stroke:#9aa8a4;stroke-width:1.5;stroke-dasharray:5 4}.win{fill:#167d61}.other{fill:#b66b13}</style>',
        '<text class="title" x="24" y="30">Paired bootstrap differences</text>',
        f'<text class="sub" x="24" y="51">Model minus {baseline}; intervals that clear zero in the favorable direction are green.</text>',
        f'<line class="zero" x1="{zero_x:.1f}" y1="{top - 18}" x2="{zero_x:.1f}" y2="{height - bottom + 8}"/>',
    ]
    last_model = None
    for index, (model, label, stat, higher_is_better) in enumerate(rows):
        y = top + index * row_h
        delta, lo, hi = (float(stat[key]) for key in ("delta", "lo", "hi"))
        clears = lo > 0 or hi < 0
        improves = delta > 0 if higher_is_better else delta < 0
        color_class = "win" if clears and improves else "other"
        model_text = model if model != last_model else ""
        last_model = model
        parts.extend(
            [
                f'<text class="label" x="24" y="{y + 5}">{html.escape(model_text)}</text>',
                f'<text class="label" x="175" y="{y + 5}">{html.escape(label)}</text>',
                f'<line class="ci" x1="{px(lo):.1f}" y1="{y}" x2="{px(hi):.1f}" y2="{y}"/>',
                f'<line class="ci" x1="{px(lo):.1f}" y1="{y - 5}" x2="{px(lo):.1f}" y2="{y + 5}"/>',
                f'<line class="ci" x1="{px(hi):.1f}" y1="{y - 5}" x2="{px(hi):.1f}" y2="{y + 5}"/>',
                f'<circle class="{color_class}" cx="{px(delta):.1f}" cy="{y}" r="5"/>',
                f'<text class="sub" x="{width - 82}" y="{y + 4}">{delta:+.3f}</text>',
            ]
        )
    parts.extend(
        [
            f'<text class="sub" x="{left}" y="{height - 16}">{-extent:.2f}</text>',
            f'<text class="sub" x="{zero_x:.1f}" y="{height - 16}" text-anchor="middle">0</text>',
            f'<text class="sub" x="{left + plot_w}" y="{height - 16}" text-anchor="end">+{extent:.2f}</text>',
            "</svg>",
        ]
    )
    return "".join(parts)


def render_marginal_diagnostics_svg(payload: dict[str, Any]) -> str:
    """Render target and twin-implied option probabilities as grouped bars."""
    option_rows = payload.get("options") or []
    if not option_rows:
        return ""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in option_rows:
        groups.setdefault((str(row.get("heldout_question")), str(row.get("model_label"))), []).append(row)
    width, left, right, top = 920, 250, 70, 66
    row_h, group_gap = 34, 30
    height = top + 44 + sum(len(rows) * row_h + group_gap for rows in groups.values())
    plot_w = width - left - right
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;fill:#18332f}.title{font-size:20px;font-weight:700}.sub{font-size:12px;fill:#61716e}.label{font-size:13px}.target{fill:#aeb9b6}.twin{fill:#167d61}.grid{stroke:#e1e7e5}</style>',
        '<text class="title" x="24" y="30">Twin-implied versus target marginals</text>',
        '<rect class="target" x="24" y="43" width="12" height="12"/><text class="sub" x="42" y="54">Target</text>',
        '<rect class="twin" x="102" y="43" width="12" height="12"/><text class="sub" x="120" y="54">Twin implied</text>',
    ]
    y = top
    for (question, model), rows in groups.items():
        parts.append(f'<text class="label" x="24" y="{y + 5}" font-weight="700">{html.escape(question)} · {html.escape(model)}</text>')
        y += 22
        for row in rows:
            target = float(row.get("target_probability") or 0.0)
            predicted = float(row.get("predicted_probability") or 0.0)
            parts.extend(
                [
                    f'<text class="label" x="24" y="{y + 15}">{html.escape(str(row.get("option_label")))}</text>',
                    f'<line class="grid" x1="{left}" y1="{y + 17}" x2="{left + plot_w}" y2="{y + 17}"/>',
                    f'<rect class="target" x="{left}" y="{y}" width="{target * plot_w:.1f}" height="11" rx="2"/>',
                    f'<rect class="twin" x="{left}" y="{y + 13}" width="{predicted * plot_w:.1f}" height="11" rx="2"/>',
                    f'<text class="sub" x="{width - 62}" y="{y + 10}">{target:.1%}</text>',
                    f'<text class="sub" x="{width - 62}" y="{y + 24}">{predicted:.1%}</text>',
                ]
            )
            y += row_h
        y += group_gap
    parts.append("</svg>")
    return "".join(parts)
