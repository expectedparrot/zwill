from __future__ import annotations

import html
import importlib.resources as resources
import os
import re
from pathlib import Path
from typing import Any

EP_REPORT_CSS = (
    resources.files("zwill").joinpath("assets/report.css").read_text(encoding="utf-8")
)


def copy_markdown_control() -> str:
    return """
<button type="button" class="copy-markdown" data-copy-markdown>Copy as Markdown</button>
<script>
(function () {
  function clean(text) {
    return (text || "").replace(/\\s+/g, " ").trim();
  }
  function escapeCell(text) {
    return clean(text).replace(/\\|/g, "\\\\|");
  }
  function tableMarkdown(table) {
    const rows = Array.from(table.rows).map((row) =>
      Array.from(row.cells).map((cell) => escapeCell(cell.innerText))
    ).filter((cells) => cells.length);
    if (!rows.length) return "";
    const width = Math.max(...rows.map((row) => row.length));
    const normalized = rows.map((row) => row.concat(Array(Math.max(0, width - row.length)).fill("")));
    const header = normalized[0];
    const separator = header.map(() => "---");
    return [
      "| " + header.join(" | ") + " |",
      "| " + separator.join(" | ") + " |",
      ...normalized.slice(1).map((row) => "| " + row.join(" | ") + " |")
    ].join("\\n");
  }
  function blockMarkdown(el) {
    if (el.closest("[data-copy-markdown], script, style")) return "";
    if (el.closest("table") && el.tagName.toLowerCase() !== "table") return "";
    const tag = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag)) return "#".repeat(Number(tag.slice(1))) + " " + clean(el.innerText);
    if (tag === "p") return clean(el.innerText);
    if (tag === "table") return tableMarkdown(el);
    if (tag === "img") return "![" + clean(el.alt || "image") + "](" + (el.getAttribute("src") || "") + ")";
    if (tag === "pre") return "```\\n" + el.innerText.trim() + "\\n```";
    if (tag === "ul" || tag === "ol") {
      return Array.from(el.children).filter((child) => child.tagName && child.tagName.toLowerCase() === "li")
        .map((li, index) => (tag === "ol" ? (index + 1) + ". " : "- ") + clean(li.innerText)).join("\\n");
    }
    return "";
  }
  function pageMarkdown() {
    const root = document.querySelector("main") || document.body;
    const blocks = Array.from(root.querySelectorAll("h1,h2,h3,h4,h5,h6,p,table,img,pre,ul,ol"))
      .map(blockMarkdown)
      .filter(Boolean);
    const title = document.querySelector("h1");
    if (title && !blocks[0]?.startsWith("# ")) blocks.unshift("# " + clean(title.innerText));
    return blocks.join("\\n\\n") + "\\n";
  }
  async function writeClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    area.style.top = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
  }
  document.querySelectorAll("[data-copy-markdown]").forEach((button) => {
    button.addEventListener("click", async () => {
      const markdown = pageMarkdown();
      const original = button.textContent;
      try {
        await writeClipboard(markdown);
        button.textContent = "Copied";
        button.classList.add("copied");
      } catch (error) {
        button.textContent = "Copy failed";
      }
      window.setTimeout(() => {
        button.textContent = original;
        button.classList.remove("copied");
      }, 1400);
    });
  });
}());
</script>
"""


def bundle_rel_link(path: str | Path, base: Path) -> str:
    return os.path.relpath(Path(path).resolve(), start=base.resolve()).replace(os.sep, "/")


def fmt_probs(probs: dict[str, float]) -> str:
    return "[" + ", ".join(f"{value:.3f}" for value in probs.values()) + "]"


def escape_html(value: Any) -> str:
    return html.escape(str(value), quote=True)


def escape_script_text(value: str) -> str:
    return value.replace("</script", "<\\/script")


def report_display_title(benchmark: str) -> tuple[str, str | None]:
    cleaned = str(benchmark or "").strip()
    if not cleaned:
        return "Survey Digital Twin Evaluation", None
    if cleaned == "cross_survey_twin_benchmark_seed789" or cleaned.startswith("cross_survey_twin_benchmark"):
        return "Cross-Survey Digital Twin Evaluation", cleaned
    if cleaned.endswith(" digital twin validation"):
        survey = cleaned[: -len(" digital twin validation")].strip()
        if survey:
            if survey == "w158_ccpolicy":
                return "W158 Climate Policy Digital Twin Validation", cleaned
            return f"{survey.replace('_', ' ').replace('-', ' ').title()} Digital Twin Validation", cleaned
        return "Survey Digital Twin Validation", cleaned
    title = re.sub(r"_seed\d+$", "", cleaned)
    title = title.replace("_", " ").replace("-", " ").strip()
    if "digital twin" not in title.lower():
        title = re.sub(r"\btwin\b", "digital twin", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).title()
    if not title:
        title = "Survey Digital Twin Evaluation"
    return title, cleaned if cleaned != title else None


def remove_leading_one_shot_analysis_heading(markdown: str) -> str:
    lines = markdown.strip().splitlines()
    if not lines:
        return markdown
    first = lines[0].strip().lower()
    if first in {"# analysis", "## analysis", "# one-shot analysis", "## one-shot analysis", "# one-shot marginals", "## one-shot marginals"}:
        return "\n".join(lines[1:]).lstrip()
    return markdown


def markdown_to_html(markdown: str) -> str:
    def inline(value: str) -> str:
        code_spans: list[str] = []

        def stash_code(match: re.Match[str]) -> str:
            code_spans.append(f"<code>{escape_html(match.group(1))}</code>")
            return f"\u0000CODE{len(code_spans) - 1}\u0000"

        text = re.sub(r"`([^`]+)`", stash_code, value)
        text = escape_html(text)
        text = re.sub(
            r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
            lambda match: f'<a href="{escape_html(match.group(2))}">{inline(match.group(1))}</a>',
            text,
        )
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
        for index, code in enumerate(code_spans):
            text = text.replace(f"\u0000CODE{index}\u0000", code)
        return text

    blocks: list[str] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.strip() in {"---", "***", "___"}:
            blocks.append("<hr>")
            i += 1
            continue
        if line.startswith("#"):
            level = min(3, len(line) - len(line.lstrip("#")))
            text = line[level:].strip()
            blocks.append(f"<h{level}>{inline(text)}</h{level}>")
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and set(lines[i + 1].replace("|", "").replace(":", "").replace("-", "").strip()) == set():
            headers = [cell.strip() for cell in line.strip("|").split("|")]
            i += 2
            body_rows = []
            while i < len(lines) and lines[i].startswith("|"):
                body_rows.append([cell.strip() for cell in lines[i].strip("|").split("|")])
                i += 1
            head = "".join(f"<th>{inline(cell)}</th>" for cell in headers)
            body = "".join(
                "<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>"
                for row in body_rows
            )
            blocks.append(f"<div class=\"table-wrap\"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")
            continue
        if line.startswith("- ") or re.match(r"^\d+\. ", line):
            ordered = bool(re.match(r"^\d+\. ", line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines):
                current = lines[i].rstrip()
                if ordered:
                    match = re.match(r"^\d+\. (.*)", current)
                else:
                    match = re.match(r"^- (.*)", current)
                if not match:
                    break
                items.append(f"<li>{inline(match.group(1))}</li>")
                i += 1
            blocks.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        paragraph = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith("#") and not lines[i].startswith("|") and not lines[i].startswith("- ") and not re.match(r"^\d+\. ", lines[i]):
            paragraph.append(lines[i].strip())
            i += 1
        blocks.append(f"<p>{inline(' '.join(paragraph))}</p>")
    return "\n".join(blocks)


PRACTITIONER_EXPLAINER_MARKDOWN = """## What This Report Means by Digital Twins

In this report, a *digital twin* is an AI simulation of an individual survey respondent: the model sees some of that respondent's prior survey answers, then predicts how the same respondent would answer a held-out or new question. One practical analogy is persona-based reasoning in product design and user research: practitioners have long used personas to make abstract customer data easier to reason with and to ask "what would this kind of person need, object to, or choose?" Digital twins are not the same as traditional personas, but they extend a related idea: they use many respondent-specific records, apply the persona-like reasoning at scale, and ask a model to project each respondent into a new decision setting.

This approach builds on a growing literature using language models to simulate human samples and behavior, including Argyle et al.'s work on "silicon samples" and algorithmic fidelity, Aher, Arriaga, and Kalai's "Turing Experiments" for replicating human-subject studies, Horton's framing of LLMs as simulated economic agents, and Park et al.'s generative-agent simulations of 1,000 real people. Expected Parrot is the company and open-source tooling provider behind EDSL, the system used here to build surveys, run language-model jobs remotely, store results, and generate this report.

References: [Cooper, *The Inmates Are Running the Asylum*](https://en.wikipedia.org/wiki/Alan_Cooper_%28software_designer%29); [Pruitt & Grudin, *Personas*](https://en.wikipedia.org/wiki/Persona_%28user_experience%29); [Argyle et al., *Out of One, Many*](https://arxiv.org/abs/2209.06899); [Aher, Arriaga & Kalai, *Using Large Language Models to Simulate Multiple Humans*](https://arxiv.org/abs/2208.10264); [Horton, *Large Language Models as Simulated Economic Agents*](https://arxiv.org/abs/2301.07543); [Park et al., *Generative Agent Simulations of 1,000 People*](https://arxiv.org/abs/2411.10109); [Expected Parrot EDSL documentation](https://docs.expectedparrot.com/en/latest).
"""


PRACTITIONER_DECISION_GUIDANCE_MARKDOWN = """## How to Use This Report

### Match Evidence to the Intended Use

Do not read one benchmark as a single yes/no answer about whether "digital twins work." A report may contain several twin exercises: different surveys, question families, held-out items, respondent samples, and models. Treat each exercise as evidence about a particular kind of use.

The standard of evidence depends less on whether the decision sounds "low" or "high" stakes in the abstract, and more on what the user needs the twin output to do:

- **Exact levels or public quantitative claims:** require the strongest evidence. If the goal is to say "62% would choose A," publish a population estimate, set a hard cutoff, or target people based on predicted probabilities, inspect calibration and run stronger validation.
- **Rank ordering or prioritization:** can tolerate more error in levels. If the goal is to decide which message, option, segment, or question is more promising, the key question is whether the benchmark shows reliable ordering, not whether every percentage is right.
- **Exploration and surfacing considerations:** can be useful even when point estimates are uncertain. If the goal is to find likely objections, missing arguments, subgroup concerns, or hypotheses to investigate, the twin output should be judged as structured qualitative evidence rather than as a survey estimate.

Decision stakes still matter: the more public, irreversible, costly, or consequential the decision, the more direct validation is warranted. But a fast internal ranking exercise and a publishable estimate should not be held to the same standard.

### Read Performance by Exercise

When a benchmark spans multiple surveys or held-out question families, discuss the results separately. A binary climate-policy exercise, a multi-option skill-importance exercise, a social-media attitude exercise, and a vignette blame exercise are not the same test. Strong performance in one exercise should not be generalized to all twins, and weak performance in one exercise should not erase evidence from another. Look for the pattern: which question types, option structures, and context signals worked, and which did not.

### When Direct Survey Research Is Infeasible

Sometimes survey research is infeasible rather than merely expensive. Competitors, regulators, voters in a future election, executives, or other strategically important audiences may not answer a survey at all, may not answer quickly enough, or may not answer candidly. In those cases, the right comparison is not always "twin output versus a perfect survey"; it may be "twin output versus no direct measurement." Twins can be useful in that setting even when they are not precise enough to publish as population estimates.

### Use Twins for Ranking and Exploration

Distinguish rank ordering from exact levels. Twins may be more reliable at identifying which option, message, segment, or question is relatively stronger than at estimating the exact percentage that would choose it.

They can also surface considerations, objections, and patterns to investigate, not just point-estimate predictions. For example, a twin study might suggest that a proposal is likely to worry respondents less because of its cost than because it feels unfair to a subgroup, or it might reveal an objection the research team had not thought to test directly. Treat those outputs as hypotheses and prompts for better thinking, not just as percentages.
"""


PRACTITIONER_HOLDOUT_MARKDOWN = """## Why This Report Uses Held-Out Questions

The held-out question is a proxy for the kind of new question a practitioner might later ask an instantiated digital twin. In a deployed use case, the model would see what is already known about a respondent and then project that respondent into a new decision setting. The benchmark recreates that situation by hiding one real survey question, giving the twin other answers from the same respondent, and scoring whether the twin can recover the answer the respondent actually gave.

This is intentionally a demanding test. Survey designers usually avoid asking many highly correlated questions when they can, because redundant questions waste respondent attention. That means a held-out item is often not just a near-duplicate of the context questions. If a twin performs well under that condition, it is evidence that it learned something useful about the respondent or question family, not merely that it copied an obvious neighboring item.

The hold-out design is still only a proxy. It tests generalization to questions from the same survey environment, with known response options and a real answer for scoring. A truly new question may use different wording, a different scale, or a different decision context. Treat strong hold-out performance as evidence of promise, then adjust confidence based on how similar the new question is to the tested held-out questions.
"""


def remove_reusable_practitioner_guidance(markdown: str) -> str:
    """Remove old generic stakes guidance when the wrapper supplies it."""
    lines = markdown.splitlines()
    output: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "## 1. Executive summary":
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## 2. "):
                j += 1
            block = "\n".join(lines[i:j])
            generic_markers = [
                "The practical answer is:",
                "Generic stakes ladder",
                "For low-stakes, reversible, internal, or time-sensitive decisions:",
                "For medium-stakes decisions:",
                "For high-stakes, public, publishable, expensive-to-reverse, or policy-critical decisions:",
                "For probability-sensitive decisions:",
            ]
            if j < len(lines) and any(marker in block for marker in generic_markers):
                i = j
                continue
        output.append(lines[i])
        i += 1
    return "\n".join(output).strip() + "\n"


def remove_redundant_report_title(markdown: str) -> str:
    lines = markdown.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return markdown
    first = lines[index].strip()
    if first.startswith("# "):
        title = first[2:].strip().lower()
        generic_title_terms = ["practitioner report", "digital twin benchmark", "digital twin evaluation"]
        if any(term in title for term in generic_title_terms):
            del lines[index]
            while index < len(lines) and not lines[index].strip():
                del lines[index]
            return "\n".join(lines).strip() + "\n"
    return markdown
