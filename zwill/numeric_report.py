"""HTML report for continuous (quantile) twin predictions.

Renders the numeric measures visually: a reliability / calibration diagram (the
key diagnostic for a distributional forecast), skill bars vs the marginal
baseline, and interval-coverage bars. Self-contained inline SVG.
"""

from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

_PALETTE = ["#1f6feb", "#e08600", "#8a3ffc", "#1a7f37", "#b42318"]
_BASELINE = "baseline:marginal-quantile"
_BASELINE_COLOR = "#8891a5"


def _weight(row: dict[str, Any]) -> float:
    return float(row.get("weight", 1.0))


def reliability_curve(rows: list[dict[str, Any]], levels: list[float]) -> list[float | None]:
    """Empirical (weighted) coverage of each predicted quantile level.

    For a calibrated forecast the fraction of actuals at or below the predicted
    tau-quantile should be ~tau; plotting nominal vs empirical gives the
    reliability diagram.
    """
    curve: list[float | None] = []
    for index in range(len(levels)):
        total = 0.0
        below = 0.0
        for row in rows:
            values = row.get("quantile_values") or []
            if index >= len(values):
                continue
            weight = _weight(row)
            total += weight
            if float(row["actual_value"]) <= float(values[index]):
                below += weight
        curve.append(below / total if total > 0 else None)
    return curve


def numeric_report_payload(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    levels = list(summary.get("quantile_levels") or [])
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_model[row["model_label"]].append(row)
    reliability = {label: reliability_curve(model_rows, levels) for label, model_rows in by_model.items()}
    return {**summary, "levels": levels, "reliability": reliability}


def _color_for(model: str, twin_models: list[str]) -> str:
    if model == _BASELINE:
        return _BASELINE_COLOR
    return _PALETTE[twin_models.index(model) % len(_PALETTE)]


def _reliability_svg(levels: list[float], reliability: dict[str, list[float | None]], twin_models: list[str]) -> str:
    width, height = 460, 420
    left, right, top, bottom = 58, 24, 40, 54
    plot_w, plot_h = width - left - right, height - top - bottom

    def px(value: float) -> float:
        return left + value * plot_w

    def py(value: float) -> float:
        return top + (1.0 - value) * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        "<style>text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;fill:#172033;font-size:12px}"
        ".grid{stroke:#e6e9ef;stroke-width:1}.diag{stroke:#1a7f37;stroke-width:2;stroke-dasharray:6 5}"
        ".axis{stroke:#98a2b3;stroke-width:1}.pt{stroke-width:0}</style>",
        f'<text x="{left}" y="24" font-weight="700">Reliability (calibration) diagram</text>',
    ]
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<line class="grid" x1="{px(tick):.1f}" y1="{top}" x2="{px(tick):.1f}" y2="{top + plot_h}"/>')
        parts.append(f'<line class="grid" x1="{left}" y1="{py(tick):.1f}" x2="{left + plot_w}" y2="{py(tick):.1f}"/>')
        parts.append(f'<text x="{px(tick):.1f}" y="{top + plot_h + 16:.1f}" text-anchor="middle" fill="#5c667a">{tick:g}</text>')
        parts.append(f'<text x="{left - 8:.1f}" y="{py(tick) + 4:.1f}" text-anchor="end" fill="#5c667a">{tick:g}</text>')
    parts.append(f'<line class="diag" x1="{px(0)}" y1="{py(0)}" x2="{px(1)}" y2="{py(1)}"/>')
    parts.append(f'<text x="{px(0.62):.1f}" y="{py(0.72):.1f}" fill="#1a7f37">perfectly calibrated</text>')

    order = [*twin_models, _BASELINE] if _BASELINE in reliability else list(twin_models)
    for model in order:
        curve = reliability.get(model) or []
        points = [(levels[i], curve[i]) for i in range(min(len(levels), len(curve))) if curve[i] is not None]
        if not points:
            continue
        color = _color_for(model, twin_models)
        poly = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in points)
        dash = ' stroke-dasharray="4 4"' if model == _BASELINE else ""
        parts.append(f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2.5"{dash}/>')
        for x, y in points:
            parts.append(f'<circle class="pt" cx="{px(x):.1f}" cy="{py(y):.1f}" r="3.5" fill="{color}"/>')

    parts.append(f'<text x="{left + plot_w / 2:.1f}" y="{height - 10}" text-anchor="middle" fill="#5c667a">Predicted quantile level (nominal)</text>')
    parts.append(f'<text transform="translate(16,{top + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle" fill="#5c667a">Empirical coverage</text>')
    # legend
    ly = top + 6
    for model in order:
        parts.append(f'<rect x="{left + plot_w - 150}" y="{ly - 8}" width="12" height="12" fill="{_color_for(model, twin_models)}"/>')
        parts.append(f'<text x="{left + plot_w - 134}" y="{ly + 2}">{escape(model.replace("baseline:", "baseline "))}</text>')
        ly += 18
    parts.append("</svg>")
    return "".join(parts)


def _bar_row(label: str, value: float | None, max_value: float, color: str, fmt: str = "{:.3f}") -> str:
    pct = 0.0 if not value or max_value <= 0 else min(1.0, value / max_value) * 100
    text = "—" if value is None else fmt.format(value)
    return (
        f'<div class="barrow"><span class="barlabel">{escape(label)}</span>'
        f'<span class="bartrack"><span class="barfill" style="width:{pct:.1f}%;background:{color}"></span></span>'
        f'<span class="barval">{text}</span></div>'
    )


def render_numeric_report_html(payload: dict[str, Any]) -> str:
    models = payload.get("models", {})
    twin_models = [m for m in models if m != _BASELINE]
    levels = payload.get("levels", [])
    reliability = payload.get("reliability", {})
    skill = payload.get("pinball_skill_vs_marginal", {})

    rows_html = []
    for model, summary in models.items():
        rows_html.append(
            "<tr><td>{m}</td><td class='n'>{n}</td><td class='n'>{pb:.3f}</td><td class='n'>{cr:.3f}</td>"
            "<td class='n'>{mae:.3f}</td><td class='n'>{c50:.0%}</td><td class='n'>{c90:.0%}</td><td class='n'>{sk}</td></tr>".format(
                m=escape(model),
                n=summary.get("rows", 0),
                pb=summary.get("mean_pinball") or 0.0,
                cr=summary.get("mean_crps") or 0.0,
                mae=summary.get("mean_absolute_error") or 0.0,
                c50=summary.get("coverage_50") or 0.0,
                c90=summary.get("coverage_90") or 0.0,
                sk="—" if model == _BASELINE or skill.get(model) is None else f"{skill[model]:.1%}",
            )
        )

    max_pinball = max((s.get("mean_pinball") or 0.0 for s in models.values()), default=1.0) or 1.0
    pinball_bars = "".join(_bar_row(m, models[m].get("mean_pinball"), max_pinball, _color_for(m, twin_models)) for m in [*twin_models, _BASELINE] if m in models)
    coverage_bars = ""
    for nominal, key in ((0.5, "coverage_50"), (0.9, "coverage_90")):
        coverage_bars += f'<div class="covgroup"><div class="covtitle">Nominal {nominal:.0%} interval &mdash; target coverage {nominal:.0%}</div>'
        coverage_bars += "".join(_bar_row(m, models[m].get(key), 1.0, _color_for(m, twin_models), "{:.0%}") for m in [*twin_models, _BASELINE] if m in models)
        coverage_bars += "</div>"

    reliability_svg = _reliability_svg(levels, reliability, twin_models) if levels else ""

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Numeric twin report</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#172033;max-width:900px;margin:0 auto;padding:24px;line-height:1.5}}
h1{{font-size:1.6rem}} h2{{font-size:1.2rem;margin-top:2rem;border-bottom:1px solid #e6e9ef;padding-bottom:.3rem}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{padding:6px 10px;border-bottom:1px solid #eef1f5;text-align:left}}
td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
.muted{{color:#5c667a}} .grid2{{display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start}}
.barrow{{display:flex;align-items:center;gap:10px;margin:4px 0}} .barlabel{{width:190px;font-size:13px}}
.bartrack{{flex:1;background:#eef1f5;border-radius:4px;height:14px;overflow:hidden}} .barfill{{display:block;height:14px}}
.barval{{width:60px;text-align:right;font-variant-numeric:tabular-nums;font-size:13px}}
.covgroup{{margin:10px 0}} .covtitle{{font-size:13px;color:#5c667a;margin-bottom:2px}}
</style></head><body>
<h1>Numeric twin validation</h1>
<p class="muted">Continuous target predicted as a quantile distribution and scored with proper scoring rules
(pinball loss, CRPS), interval coverage, and skill vs the population marginal-quantile baseline. Metrics are survey-weighted.</p>

<h2>Reliability &amp; skill</h2>
<div class="grid2">
  <div>{reliability_svg}</div>
  <div style="flex:1;min-width:260px">
    <div class="covtitle">Mean pinball loss (lower is better)</div>
    {pinball_bars}
    <div style="margin-top:16px">{coverage_bars}</div>
  </div>
</div>
<p class="muted">The reliability diagram plots each predicted quantile level against the fraction of actuals at or below it;
points on the green diagonal are perfectly calibrated. Points below the diagonal mean the twin's quantiles sit too low
(under-coverage); above means too high (over-coverage / under-confident).</p>

<h2>Summary</h2>
<table><thead><tr><th>Model</th><th class="n">Rows</th><th class="n">Pinball</th><th class="n">CRPS</th>
<th class="n">Median MAE</th><th class="n">Cov 50%</th><th class="n">Cov 90%</th><th class="n">Pinball skill</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table>
</body></html>"""
