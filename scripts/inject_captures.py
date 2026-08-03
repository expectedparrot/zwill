#!/usr/bin/env python3
"""Rebuild docs/index.html from the captured Pew worked run.

Reads the envelopes written by scripts/pew_driver.py and injects them into the
tutorial as closed disclosures under their commands. Command blocks whose text
must match the captured run (fetched paths and concrete job ids) are rewritten
from the captures' own argv, so every displayed
command is exactly what ran. Hand-written sample outputs are replaced by the
real captures.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TUTORIAL = REPO / "build" / "tutorial"
CAPTURES = TUTORIAL / "captures"
PAGE = REPO / "docs" / "index.html"
SOURCE_PAGE = REPO / "docs" / "tutorial-source.html"

IDS = json.loads((TUTORIAL / "ids.json").read_text())
PROB_ID = IDS["probability_job_id"]
TWIN_ID = IDS["twin_job_id"]
TWIN_JOBS_PATH = IDS["twin_job_path"]

TRUNCATE_ROWS = 2


def load(name: str) -> dict:
    return json.loads((CAPTURES / f"{name}.json").read_text())


def _trim(value, depth=0):
    """Cap list lengths and string sizes so disclosures stay readable."""
    if isinstance(value, list):
        keep = TRUNCATE_ROWS if depth <= 1 else 1
        if len(value) > keep:
            return [_trim(item, depth + 1) for item in value[:keep]] + [
                f"… {len(value) - keep} further entries elided …"
            ]
        return [_trim(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {key: _trim(item, depth + 1) for key, item in value.items()}
    limit = 400 if depth <= 1 else 200
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f" … [{len(value) - limit} more characters]"
    return value


def render_envelope(name: str) -> str:
    payload = load(name)["payload"]
    if isinstance(payload.get("data"), (dict, list)):
        payload["data"] = _trim(payload["data"])
    return json.dumps(payload, indent=2, ensure_ascii=False)


def details(name: str, label: str = "Show command output") -> str:
    body = html.escape(render_envelope(name))
    return (
        f'\n<details class="output"><summary>{label}</summary>'
        f"<pre><code>{body}</code></pre></details>"
    )


def human_figure(name: str, caption: str) -> str:
    text = (CAPTURES / f"{name}.txt").read_text().rstrip()
    return (
        f'\n<figure class="human"><pre class="human"><code>{html.escape(text)}</code></pre>'
        f"<figcaption>{caption}</figcaption></figure>"
    )


def command_pre(display: str) -> str:
    return f'<pre class="command"><code>{html.escape(display)}</code></pre>'


def argv_display(name: str) -> str:
    return load(name)["argv_display"]


REAL_RUN_CALLOUT = (
    '\n<div class="callout"><strong>This is the paid model execution used by this page.</strong> '
    "The checked-in job and raw results packages are retained under "
    "<code>docs/assets/pew-w158-real-run/</code>. Every prediction and downstream metric "
    "shown here traces to those artifacts.</div>"
)

# One entry per original <pre><code> block, in order. Actions:
#   keep          — leave the command text as-is
#   replace:<txt> — replace the block's text (still a command block)
#   drop          — delete the block (hand-written sample output now covered by a capture)
#   captures      — list of capture names to append as closed disclosures
#   human         — (name, caption) visible human-view figure to append
#   before        — HTML to insert before the block
SPEC: list[dict] = [
    {"i": 0, "keep": True},                                     # install block
    {"i": 1, "captures": ["02-init", "03-survey-create"]},      # init + survey create
    {"i": 2, "replace": argv_display("04-raw-add"), "captures": ["04-raw-add"]},
    {"i": 3, "captures": ["05-question-add"]},                  # manual demo
    {"i": 4, "keep": True},                                     # question JSONL sample
    {"i": 5, "replace": (
        argv_display("06-question-import") + "\n" +
        argv_display("07-respondent-import") + "\n" +
        argv_display("08-answer-import") + "\n\n" +
        argv_display("09-next")
    ), "captures": ["06-question-import", "07-respondent-import", "08-answer-import", "09-next"]},
    {"i": 6, "captures": ["10-status"]},
    {"i": 7, "drop": True},                                     # fake status output
    {"i": 8, "human": ("11h-table", "The first five respondents, straight from the imported answers — the same view agents read as JSON.")},
    {"i": 9, "drop": True},                                     # fake table output
    {"i": 10, "captures": ["12-survey-report"]},
    {"i": 11, "captures": ["13-commit"]},
    {"i": 12, "drop": True},                                    # fake commit output
    {"i": 13, "captures": ["14-next-committed"]},
    {"i": 14, "captures": ["15-prob-build"]},
    {"i": 15, "keep": True},                                    # ep inspect (external tool)
    {"i": 16, "keep": True},                                    # ep inspect sample output
    {"i": 17, "replace": (
        "ep run one_shot_jobs.ep --fresh --output one_shot_results.ep \\\n"
        "  --remote_inference_description 'Zwill Pew W158 published tutorial run' \\\n"
        "  --results_description 'Raw results for the published Zwill Pew W158 tutorial'"
    ), "before": REAL_RUN_CALLOUT,
     "human": ("15a-prob-ep-run", "The real one-shot remote-inference execution retained for this tutorial.")},
    {"i": 18, "captures": ["16-prob-import"]},
    {"i": 19, "replace": (
        f"zwill prob-results report --survey pew_w158 \\\n"
        f"  --job-id {PROB_ID} --format svg \\\n"
        f"  --path one-shot-marginals.svg"
    ), "captures": ["17-prob-report"]},
    {"i": 20, "captures": ["18-init-plan"]},
    {"i": 21, "captures": ["19-plan-validate"]},
    {"i": 22, "captures": ["20-plan-approve"]},
    {"i": 23, "captures": ["21-export-plan"]},
    {"i": 24, "replace": f"ep inspect {TWIN_JOBS_PATH}"},
    {"i": 25, "replace": (
        f"ep run {TWIN_JOBS_PATH} \\\n"
        f"  --fresh --output carbon_tax_twin_results.ep \\\n"
        f"  --remote_inference_description 'Zwill Pew W158 published tutorial run' \\\n"
        f"  --results_description 'Raw results for the published Zwill Pew W158 tutorial'\n\n"
        + argv_display("22-twin-import").replace(" --input-path ", " \\\n  --input-path ").replace(" --job-id ", " \\\n  --job-id ")
    ), "before": REAL_RUN_CALLOUT, "captures": ["22-twin-import"],
       "human": ("21a-twin-ep-run", "The real 100-interview remote-inference execution retained for this tutorial.")},
    {"i": 26, "replace": (
        f"zwill twin-results run-report --survey pew_w158 \\\n"
        f"  --job-id {TWIN_ID} --format html --path twin-audit.html"
    ), "captures": ["23-run-report"]},
    {"i": 27, "captures": ["24-study-list"],
     "human": ("24h-study-list", "The imported run at a glance: one job, 100 scored rows, no issues.")},
    {"i": 28, "drop": True},                                    # fake study list output
    {"i": 29, "replace": (
        f"zwill twin-results report --survey pew_w158 \\\n"
        f"  --job-id {TWIN_ID}"
    ), "captures": ["25-twin-report"]},
    {"i": 30, "drop": True},                                    # fake report output
    {"i": 31, "drop": True},                                    # fake model summary
    {"i": 32, "replace": (
        f"zwill twin-results report --survey pew_w158 \\\n"
        f"  --job-id {TWIN_ID} --format html --view full \\\n"
        f"  --path twin-results.html \\\n"
        f"  --calibration-svg-path twin-calibration.svg\n\n"
        f"zwill twin-results report --survey pew_w158 \\\n"
        f"  --job-id {TWIN_ID} --format csv \\\n"
        f"  --path twin-results.csv\n\n"
        f"zwill twin-results marginal-diagnostics --survey pew_w158 \\\n"
        f"  --job-id {TWIN_ID} \\\n"
        f"  --target probability-job \\\n"
        f"  --target-job-id {PROB_ID} \\\n"
        f"  --format svg --path twin-vs-one-shot-marginals.svg"
    ), "captures": ["26-twin-report-html", "27-twin-report-csv", "28-marginal-diagnostics"]},
    {"i": 33, "replace": (
        f"zwill twin-validate --survey pew_w158 \\\n"
        f"  --jobs {TWIN_ID} --out report_out/validation \\\n"
        f"  --embedder edsl --require-baseline --n-boot 2000 --seed 42"
    ), "before": (
        '\n<div class="callout"><strong>The conditional baseline is required in this run.</strong> '
        "<code>--embedder edsl</code> obtains the recorded embeddings through Expected Parrot, "
        "and <code>--require-baseline</code> makes the tutorial fail instead of publishing a "
        "partial validation if embedding or XGBoost fitting does not complete.</div>"
    ), "captures": ["29-twin-validate"]},
    {"i": 34, "captures": ["30-next-final"]},
    {"i": 35, "replace": (
        f"zwill report build --survey pew_w158 --path report_out \\\n"
        f"  --job-id {TWIN_ID} \\\n"
        f"  --probability-job-id {PROB_ID}"
    ), "captures": ["31-report-build", "32-report-list"]},
    {"i": 36, "drop": True},                                    # fake report JSON
]


EXPECTATIONS_CALLOUT = (
    '\n<div class="callout"><strong>If you follow along, expect small differences.</strong> '
    "Every command output on this page is a real captured envelope from one worked run against "
    "the bundled excerpt. If you run the same commands yourself, the machine-generated job ids "
    "and timestamps may differ, and fresh model inference can produce somewhat different "
    "probabilities. The tutorial's job packages, raw results, and validation bundle are retained "
    "under <code>docs/assets/pew-w158-real-run/</code>. Outputs are JSON envelopes by default; add "
    "<code>--human</code> to any command for a readable rendering.</div>"
)

DETAILS_CSS = (
    "details.output{margin:-8px 0 26px}"
    "details.output summary{color:var(--green);font-size:13px;font-weight:700;cursor:pointer}"
    "details.output[open] summary{margin-bottom:8px}"
    "details.output pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;max-height:520px;overflow:auto}"
    "figure.human{margin:22px 0 30px;padding:0;border:0}"
    "figure.human pre.human{margin:0 0 8px;color:var(--ink,#1a1a1a);background:#fbfdfb;"
    "border:1px solid #dfe7df;border-left:4px solid #9fd3b2;overflow:auto}"
    "figure.human figcaption{color:#667066;font-size:13px}"
)


def main() -> None:
    if not SOURCE_PAGE.exists():
        # One-time migration from the last authored page before captured
        # disclosures were injected directly into docs/index.html. Future
        # edits belong in tutorial-source.html; index.html is generated.
        source = subprocess.check_output(
            ["git", "show", "4f30e0c^:docs/index.html"],
            cwd=REPO,
            text=True,
        )
        SOURCE_PAGE.write_text(source)
    text = SOURCE_PAGE.read_text()

    # 1. Point the ingest chapter at the bundled example instead of the repo.
    text = text.replace("examples/pew_w158_tutorial/", "pew-w158-tutorial/")

    # 2. Process command blocks in reverse so offsets stay valid.
    matches = list(re.finditer(r"<pre[^>]*><code[^>]*>.*?</code></pre>", text, re.DOTALL))
    assert len(matches) >= 37, f"expected at least 37 blocks, found {len(matches)}"
    by_index = {entry["i"]: entry for entry in SPEC}
    for index in sorted(by_index, reverse=True):
        entry = by_index[index]
        match = matches[index]
        start, end = match.span()
        if entry.get("drop"):
            text = text[:start] + text[end:]
            continue
        block = match.group(0)
        if "replace" in entry:
            block = command_pre(entry["replace"])
        after = ""
        for name in entry.get("captures", []):
            label = "Show command output"
            display = argv_display(name)
            if len(entry.get("captures", [])) > 1:
                label = f"Show command output — {display.split(' --')[0]}"
            after += details(name, label)
        if "human" in entry:
            name, caption = entry["human"]
            after += human_figure(name, caption)
        before = entry.get("before", "")
        text = text[:start] + before + block + after + text[end:]

    # 3. Fetch step + expectations callout at the start of the worked run.
    ingest_heading = re.search(r'(<section id="ingest">\s*<h2[^>]*>.*?</h2>)', text, re.DOTALL)
    assert ingest_heading
    fetch_intro = (
        ingest_heading.group(1)
        + EXPECTATIONS_CALLOUT
        + "\n<p>The 100-respondent excerpt ships inside the package, so following along needs "
        "nothing beyond the install — no repository checkout, no paths to guess. One command "
        "writes a local copy (data files plus the provenance notes and README):</p>\n"
        + command_pre("zwill example fetch pew-w158-tutorial")
        + details("01-fetch")
    )
    text = text.replace(ingest_heading.group(1), fetch_intro, 1)

    # 4. Styling for disclosures and human figures.
    text = text.replace("</style>", DETAILS_CSS + "</style>", 1)

    # 5. Interactive summary labels.
    text = text.replace(
        "</body>",
        "<script>document.querySelectorAll('details.output').forEach(d=>{const s=d.querySelector('summary');"
        "const base=s.textContent.replace('Show','');d.addEventListener('toggle',()=>{"
        "s.textContent=(d.open?'Hide':'Show')+base;});});</script>\n</body>",
        1,
    )

    text = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
    PAGE.write_text(text)
    print(f"wrote {PAGE} {PAGE.stat().st_size} bytes")


if __name__ == "__main__":
    main()
