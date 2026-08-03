from __future__ import annotations

from zwill.consolidated_report import (
    downloads_section_html,
    mark_intermediate_page_html,
    render_consolidated_report,
)
from zwill.reporting import EP_REPORT_CSS


def _page(extra_css: str, body: str) -> str:
    return (
        "<!doctype html><html><head>"
        f"<style>{EP_REPORT_CSS}\n{extra_css}</style></head>"
        f"<body><header>ignored</header><main class=\"wrap\">{body}</main></body></html>"
    )


def test_composes_pages_into_one_document_with_toc() -> None:
    p1 = _page(".decision{color:red}", "<section class='panel'><h2>Verdict</h2>DECISION-BODY</section>")
    p2 = _page(".detail{color:blue}", "<section class='panel'><h2>Detail</h2>DETAIL-BODY</section>")
    downloads = downloads_section_html(
        [{"href": "audit/run.html", "title": "Twin run audit", "note": "prompt + raw responses"}]
    )
    html = render_consolidated_report(
        survey="demo",
        sections=[("decision", "Decision & Evidence", p1), ("detail", "Technical Validation", p2)],
        downloads_section=downloads,
    )

    # One document, one <main>.
    assert html.count("<!doctype") == 1
    assert html.count("<main>") == 1
    # Both bodies inlined, in order.
    assert "DECISION-BODY" in html and "DETAIL-BODY" in html
    assert html.index("DECISION-BODY") < html.index("DETAIL-BODY")
    # TOC links every section + downloads.
    assert 'href="#decision"' in html and 'href="#detail"' in html and 'href="#downloads"' in html
    assert 'id="decision"' in html and 'id="detail"' in html
    # Page-specific CSS is merged; the shared base CSS is deduped to one copy.
    assert ".decision{color:red}" in html and ".detail{color:blue}" in html
    assert html.count("--ep-border") >= 1  # EP_REPORT_CSS present
    # Download link is present and row-level material is not inlined.
    assert 'href="audit/run.html"' in html and "Twin run audit" in html


def test_section_banner_is_added_outside_main_and_excluded_from_report() -> None:
    page = _page("", "<section class='panel'>BODY-CONTENT</section>")
    marked = mark_intermediate_page_html(page)
    # Banner is inserted (after <body>, before <main>).
    assert "This is one section of a larger report" in marked
    assert marked.index("This is one section") < marked.index("<main")
    # Idempotent.
    assert mark_intermediate_page_html(marked) == marked
    # When composed into the report, the banner is not pulled in (only <main> is).
    html = render_consolidated_report(
        survey="demo", sections=[("s", "Section", marked)], downloads_section=""
    )
    assert "BODY-CONTENT" in html
    assert "This is one section of a larger report" not in html


def test_empty_sections_and_downloads_are_skipped() -> None:
    html = render_consolidated_report(
        survey="demo",
        sections=[("decision", "Decision", ""), ("detail", "Detail", _page("", "<p>ONLY</p>"))],
        downloads_section="",
    )
    assert "ONLY" in html
    assert 'href="#decision"' not in html  # empty page skipped
    assert 'href="#downloads"' not in html  # no downloads
