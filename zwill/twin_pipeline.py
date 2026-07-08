"""Twin prompt pipelines: a general slot for experimenting with how a digital
twin reasons and how its evidence is framed.

Instead of a single fixed prompt, a pipeline is an ordered list of **steps**. Each
step is a free-text question whose template has access to the scenario evidence
(``observed_answers_text``, ``respondent_metadata``, ``heldout_question_text``,
``heldout_options_text`` …) and, via EDSL answer-piping, every prior step's answer
as ``{{ <step_name>.answer }}``. Only the **final step** is scored: it must include
the ``{{ output_contract }}`` marker, which zwill replaces with the canonical
"return JSON probabilities" instruction so any pipeline stays measurable through
the same validation gate.

A length-1 pipeline is just the ordinary single-prompt twin. A length-2 pipeline
is e.g. "argue why high / why low" → "now predict", each a separate model call.

This module is pure (no filesystem / cli deps): callers resolve any
``template_path`` to inline template text before handing steps here.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import ZwillError

# Filled into the final step in place of the {{ output_contract }} marker. Kept
# identical in spirit to the single-prompt contract so scoring is unchanged.
TWIN_OUTPUT_CONTRACT = """Return only valid JSON. Do not include markdown fences, prose, or comments.

The JSON must have exactly this shape:
{"probabilities": [0.17, 0.83], "notes": "Brief explanation of the respondent-level estimate."}

The probabilities array must contain one number for each held-out option, in the same order as the options shown above. Each probability must be between 0 and 1, and they should sum to 1."""

_OUTPUT_CONTRACT_RE = re.compile(r"{{\s*output_contract\s*}}")
_VALID_STEP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_pipeline_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate a pipeline spec and produce ready-to-build question steps.

    Input: an ordered list of ``{"name", "template"[, "model"]}`` dicts (template
    already read from disk if a path was used). Output: the same list with the
    final step's ``{{ output_contract }}`` marker replaced by the canonical JSON
    instruction and a ``question_text`` field set for every step.

    Raises ZwillError on an empty pipeline, duplicate/invalid step names, a
    non-final step that references the output contract, or a final step that does
    not.
    """
    if not isinstance(steps, list) or not steps:
        raise ZwillError("invalid_input", "A twin prompt pipeline must be a non-empty list of steps.")
    seen: set[str] = set()
    resolved: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ZwillError("invalid_input", f"Pipeline step {index} must be an object with 'name' and 'template'.")
        name = str(step.get("name") or "").strip()
        template = step.get("template")
        if not name or not _VALID_STEP_NAME_RE.match(name):
            raise ZwillError(
                "invalid_input",
                f"Pipeline step {index} needs a valid 'name' (letters/digits/underscore, not starting with a digit); got {name!r}.",
            )
        if name in seen:
            raise ZwillError("invalid_input", f"Duplicate pipeline step name {name!r}; step names must be unique (they pipe as {{{{ {name}.answer }}}}).")
        seen.add(name)
        if not isinstance(template, str) or not template.strip():
            raise ZwillError("invalid_input", f"Pipeline step {name!r} needs a non-empty 'template' (or 'template_path').")
        is_final = index == len(steps) - 1
        has_contract = bool(_OUTPUT_CONTRACT_RE.search(template))
        if is_final and not has_contract:
            raise ZwillError(
                "invalid_input",
                f"The final pipeline step {name!r} must include the {{{{ output_contract }}}} marker so its answer is scoreable JSON.",
            )
        if not is_final and has_contract:
            raise ZwillError(
                "invalid_input",
                f"Only the final pipeline step may include {{{{ output_contract }}}}; step {name!r} is not final.",
            )
        question_text = _OUTPUT_CONTRACT_RE.sub(lambda _m: TWIN_OUTPUT_CONTRACT, template) if is_final else template
        resolved.append({"name": name, "question_text": question_text, "model": step.get("model")})
    return resolved


def pipeline_scored_question_name(steps: list[dict[str, Any]]) -> str:
    """The name of the final (scored) step."""
    return str(steps[-1]["name"])
