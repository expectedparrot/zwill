"""Tests for the twin prompt pipeline spec resolution (pure, no LLM)."""

from __future__ import annotations

import pytest

from zwill.errors import ZwillError
from zwill.twin_pipeline import (
    TWIN_OUTPUT_CONTRACT,
    pipeline_scored_question_name,
    resolve_pipeline_steps,
)


def test_resolve_substitutes_output_contract_in_final_step_only() -> None:
    steps = resolve_pipeline_steps(
        [
            {"name": "reason", "template": "Argue both ways for {{ heldout_question_text }}."},
            {"name": "predict", "template": "Given {{ reason.answer }}, predict. {{ output_contract }}"},
        ]
    )
    assert [s["name"] for s in steps] == ["reason", "predict"]
    # the marker is replaced in the final step, and the reasoning pipe is left for EDSL
    assert "{{ output_contract }}" not in steps[1]["question_text"]
    assert TWIN_OUTPUT_CONTRACT.splitlines()[0] in steps[1]["question_text"]
    assert "{{ reason.answer }}" in steps[1]["question_text"]
    # intermediate step is untouched
    assert "output_contract" not in steps[0]["question_text"]
    assert pipeline_scored_question_name(steps) == "predict"


def test_single_step_pipeline_is_valid() -> None:
    steps = resolve_pipeline_steps([{"name": "predict", "template": "Predict. {{ output_contract }}"}])
    assert len(steps) == 1 and pipeline_scored_question_name(steps) == "predict"


def test_final_step_must_include_output_contract() -> None:
    with pytest.raises(ZwillError) as exc:
        resolve_pipeline_steps([{"name": "predict", "template": "Predict, no contract."}])
    assert "output_contract" in str(exc.value)


def test_non_final_step_may_not_include_output_contract() -> None:
    with pytest.raises(ZwillError):
        resolve_pipeline_steps(
            [
                {"name": "reason", "template": "Reason. {{ output_contract }}"},
                {"name": "predict", "template": "Predict. {{ output_contract }}"},
            ]
        )


def test_rejects_empty_dup_and_invalid_names() -> None:
    with pytest.raises(ZwillError):
        resolve_pipeline_steps([])
    with pytest.raises(ZwillError):  # duplicate name
        resolve_pipeline_steps(
            [
                {"name": "predict", "template": "a"},
                {"name": "predict", "template": "b {{ output_contract }}"},
            ]
        )
    with pytest.raises(ZwillError):  # invalid name (would break piping)
        resolve_pipeline_steps([{"name": "1bad", "template": "x {{ output_contract }}"}])
    with pytest.raises(ZwillError):  # missing template
        resolve_pipeline_steps([{"name": "predict"}])


def test_output_contract_marker_tolerates_spacing() -> None:
    steps = resolve_pipeline_steps([{"name": "p", "template": "Go.{{output_contract}}"}])
    assert TWIN_OUTPUT_CONTRACT.splitlines()[0] in steps[0]["question_text"]
