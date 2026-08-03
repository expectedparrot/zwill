from __future__ import annotations

from zwill.edsl_integration import (
    DEFAULT_EXPORT_MODEL,
    TWIN_VALIDATION_TARGETS,
    superseded_twin_model_labels,
    superseded_twin_model_warning,
)


def test_superseded_models_are_flagged_and_current_models_are_not() -> None:
    specs = [
        ("gpt-4.1", "openai"),
        ("gpt-4o", None),
        ("claude-3-opus", "anthropic"),
        ("gemini-1.5-pro", "google"),
        ("gpt-5.5", "openai"),
        ("claude-opus-4-8", "anthropic"),
        ("gemini-2.5-pro", "google"),
    ]
    flagged = superseded_twin_model_labels(specs)
    assert flagged == ["openai:gpt-4.1", "gpt-4o", "anthropic:claude-3-opus", "google:gemini-1.5-pro"]


def test_warning_fires_only_for_superseded_models() -> None:
    # The exact model the research-agent run used.
    warning = superseded_twin_model_warning([("gpt-4.1", "openai")])
    assert warning is not None
    assert warning["code"] == "superseded_twin_model"
    assert "openai:gpt-4.1" in warning["message"]
    assert DEFAULT_EXPORT_MODEL in warning["message"]  # points at the strong default

    # A current frontier model raises nothing.
    assert superseded_twin_model_warning([("gpt-5.5", "openai")]) is None


def test_default_export_model_is_a_current_model() -> None:
    # The omitted-model default must not itself be flagged as superseded.
    assert superseded_twin_model_labels([(DEFAULT_EXPORT_MODEL, "openai")]) == []


def test_validation_targets_cover_the_model_bearing_exports() -> None:
    assert TWIN_VALIDATION_TARGETS == {
        "twin-probability-job",
        "rank-utility-twin-job",
        "numeric-twin-job",
    }
