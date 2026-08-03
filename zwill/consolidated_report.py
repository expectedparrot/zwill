"""Compose the individual report pages into one scrollable report with a TOC.

Each source page is a standalone document with the same skeleton
(``<style>{EP_REPORT_CSS} …page css…</style>`` then ``<main>…sections…</main>``).
We lift each page's ``<main>`` body into a titled section, merge the page-specific
CSS once into a shared head, and prepend a sticky table of contents. Per-twin /
row-level material is never inlined here — it ships as linked downloads — so the
single file stays lean.
"""

from __future__ import annotations

import re
from html import escape

from .reporting import EP_REPORT_CSS, copy_markdown_control, report_display_title

_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.S)
_MAIN_RE = re.compile(r"<main[^>]*>(.*)</main>", re.S)
_BODY_RE = re.compile(r"(<body[^>]*>)", re.I)

_SECTION_BANNER_MARK = "This is one section of a larger report"
_SECTION_BANNER = (
    "<div style=\"max-width:1040px;margin:12px auto;padding:10px 14px;border:1px solid #f0c36d;"
    "background:#fef7e6;border-radius:8px;font:14px/1.45 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
    "color:#664d03\"><strong>" + _SECTION_BANNER_MARK + ".</strong> "
    "Open the <a href=\"{index_href}\">full report</a> for all sections, the table of contents, "
    "and the executive summary.</div>"
)


def mark_intermediate_page_html(html: str, index_href: str = "index.html") -> str:
    """Insert a 'this is a section' banner after ``<body>``.

    Placed outside ``<main>`` so :func:`render_consolidated_report` (which lifts
    only the ``<main>`` body) never pulls it into the combined report. Idempotent.
    """
    if _SECTION_BANNER_MARK in html:
        return html
    match = _BODY_RE.search(html)
    if not match:
        return html
    banner = _SECTION_BANNER.format(index_href=escape(index_href))
    return html[: match.end()] + "\n" + banner + html[match.end() :]

_CONSOLIDATED_CSS = """
.report-shell { max-width: 1180px; margin: 0 auto; padding: 0 16px 4rem; }
.report-toc { position: sticky; top: 0; z-index: 30; background: var(--ep-bg, #fff);
  border-bottom: 1px solid var(--ep-border, #e2e5ea); padding: 10px 0; margin-bottom: 1rem; }
.report-toc-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
  color: var(--ep-gray, #5c667a); }
.report-toc ol { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 6px 0 0; padding: 0; list-style: none; font-size: 13px; }
.report-toc a { color: var(--ep-green, #1a7f37); text-decoration: none; font-weight: 600; }
.report-toc a:hover { text-decoration: underline; }
.report-section { margin-top: 2.75rem; padding-top: 1.5rem; border-top: 2px solid var(--ep-border, #e2e5ea);
  scroll-margin-top: 76px; }
.report-section > .report-section-head h2 { font-size: 1.5rem; margin: 0 0 .75rem; }
.report-section > .report-section-head p { margin: 0 0 1rem; color: var(--ep-gray, #5c667a); }
.report-downloads ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
.report-downloads li { border: 1px solid var(--ep-border, #e2e5ea); border-radius: 8px; padding: 12px 14px; }
.report-downloads a { font-weight: 600; }
.report-downloads .why { color: var(--ep-gray, #5c667a); font-size: 13px; margin-top: 2px; }
"""


def _page_style(html: str) -> str:
    match = _STYLE_RE.search(html)
    return match.group(1) if match else ""


def _page_main_inner(html: str) -> str:
    match = _MAIN_RE.search(html)
    if match:
        return match.group(1).strip()
    # Fallback: a bare fragment with no <main> wrapper.
    return html.strip()


def downloads_section_html(links: list[dict[str, str]]) -> str:
    """A 'Downloads & appendices' section linking row-level / reference artifacts."""
    items = [link for link in links if link.get("href")]
    if not items:
        return ""
    rows = "".join(
        "<li>"
        f"<a href=\"{escape(str(link['href']))}\">{escape(str(link.get('title') or link['href']))}</a>"
        f"<div class=\"why\">{escape(str(link.get('note') or ''))}</div>"
        "</li>"
        for link in items
    )
    return (
        "<section id=\"downloads\" class=\"report-section report-downloads\">"
        "<div class=\"report-section-head\"><h2>Downloads &amp; appendices</h2>"
        "<p>Row-level and reference material is linked rather than inlined, so this report stays lightweight.</p></div>"
        f"<ul>{rows}</ul></section>"
    )


def render_consolidated_report(
    *,
    survey: str,
    sections: list[tuple[str, str, str]],
    downloads_section: str = "",
) -> str:
    """One report from many pages.

    ``sections`` is a list of ``(anchor, title, page_html)``; empty/None page
    HTML is skipped. ``downloads_section`` is appended as-is (already an HTML
    ``<section>``) and linked from the TOC.
    """
    display_title, _raw = report_display_title(str(survey))
    merged_css = [EP_REPORT_CSS]
    seen_extra: set[str] = set()
    section_blocks: list[str] = []
    toc_items: list[str] = []
    for anchor, title, page_html in sections:
        if not page_html:
            continue
        extra = _page_style(page_html).replace(EP_REPORT_CSS, "").strip()
        if extra and extra not in seen_extra:
            seen_extra.add(extra)
            merged_css.append(extra)
        body = _page_main_inner(page_html)
        section_blocks.append(
            f"<section id=\"{escape(anchor)}\" class=\"report-section\">"
            f"<div class=\"report-section-head\"><h2>{escape(title)}</h2></div>"
            f"{body}</section>"
        )
        toc_items.append(f"<li><a href=\"#{escape(anchor)}\">{escape(title)}</a></li>")
    if downloads_section:
        section_blocks.append(downloads_section)
        toc_items.append("<li><a href=\"#downloads\">Downloads &amp; appendices</a></li>")

    toc = (
        "<nav class=\"report-toc\" aria-label=\"Contents\">"
        "<div class=\"report-toc-title\">Contents</div>"
        f"<ol>{''.join(toc_items)}</ol></nav>"
    )
    head_css = "\n".join(merged_css) + _CONSOLIDATED_CSS
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(display_title)} Digital Twin Validation Report</title>
  <style>
{head_css}
  </style>
</head>
<body>
  {copy_markdown_control()}
  <div class="report-shell">
    <header>
      <h1>{escape(display_title)} — Digital Twin Validation</h1>
      <div class="subtle">Survey id: <code>{escape(str(survey))}</code></div>
    </header>
    {toc}
    <main>
      {''.join(section_blocks)}
    </main>
  </div>
</body>
</html>
"""
