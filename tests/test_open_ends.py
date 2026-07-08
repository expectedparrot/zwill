"""Tests for the open-end coding core (codebook + coding, no LLM calls)."""

from __future__ import annotations

from zwill.open_ends import (
    UNCLASSIFIED_CODE,
    coded_question_and_answers,
    normalize_codebook,
    parse_coded_answer,
    render_codebook_text,
)


def test_normalize_codebook_accepts_shapes_and_dedupes_codes() -> None:
    parsed = {
        "themes": [
            {"code": "optimistic", "label": "Optimistic", "description": "hopeful"},
            {"label": "Optimistic", "description": "collides on slug"},  # dup code -> suffixed
            "Chaotic",  # bare string
            {"nope": 1},  # unusable -> skipped
        ]
    }
    cb = normalize_codebook(parsed)
    codes = [t["code"] for t in cb]
    assert codes[0] == "optimistic"
    assert codes[1].startswith("optimistic_")  # de-duplicated
    assert "chaotic" in codes
    assert len(cb) == 3
    # a bare list is also accepted
    assert normalize_codebook(["A", "B"])[0]["label"] == "A"


def test_normalize_codebook_respects_n_themes_and_rejects_empty() -> None:
    cb = normalize_codebook({"themes": [f"t{i}" for i in range(10)]}, n_themes=3)
    assert len(cb) == 3
    for bad in ({}, {"themes": []}, "not a list"):
        try:
            normalize_codebook(bad)
        except Exception as exc:  # ZwillError
            assert "theme" in str(exc).lower() or "codebook" in str(exc).lower()
        else:
            raise AssertionError(f"expected failure for {bad!r}")


def test_parse_coded_answer_matches_and_falls_back() -> None:
    valid = {"optimistic", "chaotic", "very_worried"}
    assert parse_coded_answer({"code": "chaotic"}, valid) == "chaotic"
    assert parse_coded_answer({"code": "CHAOTIC"}, valid) == "chaotic"  # case-insensitive
    assert parse_coded_answer("chaotic", valid) == "chaotic"  # bare string
    assert parse_coded_answer({"theme": "Very Worried"}, valid) == "very_worried"  # slugged label -> code
    assert parse_coded_answer({"code": "nonsense"}, valid) == UNCLASSIFIED_CODE
    assert parse_coded_answer(None, valid) == UNCLASSIFIED_CODE


def test_render_codebook_text() -> None:
    text = render_codebook_text([{"code": "a", "label": "A", "description": "desc"}])
    assert "a: A — desc" in text


def test_coded_question_and_answers_builds_mc_question() -> None:
    codebook = [
        {"code": "optimistic", "label": "Optimistic", "description": ""},
        {"code": "chaotic", "label": "Chaotic", "description": ""},
    ]
    results = {
        "data": [
            {"scenario": {"respondent_id": "r1"}, "answer": {"theme_code": '{"code": "optimistic"}'}},
            {"scenario": {"respondent_id": "r2"}, "answer": {"theme_code": '{"code": "chaotic"}'}},
            {"scenario": {"respondent_id": "r3"}, "answer": {"theme_code": '{"code": "???"}'}},  # -> unclassified
            {"scenario": {}, "answer": {"theme_code": '{"code": "chaotic"}'}},  # no respondent -> skipped
        ]
    }

    def parse_answer(row):
        import json

        raw = next(v for v in row["answer"].values())
        return json.loads(raw)

    question, rows, dist = coded_question_and_answers(
        results,
        source_question="q_open",
        coded_question_name="q_open_coded",
        codebook=codebook,
        source_text="How do you feel?",
        parse_answer=parse_answer,
    )
    assert question["question_type"] == "multiple_choice"
    assert question["question_name"] == "q_open_coded"
    # unclassified appears as an option only because r3 used it
    assert question["question_options"] == ["optimistic", "chaotic", UNCLASSIFIED_CODE]
    assert question["option_labels"]["optimistic"] == "Optimistic"
    assert len(rows) == 3 and all(r["question"] == "q_open_coded" for r in rows)
    assert dist == {"optimistic": 1, "chaotic": 1, UNCLASSIFIED_CODE: 1}


def test_coded_question_omits_unused_unclassified_option() -> None:
    codebook = [{"code": "a", "label": "A", "description": ""}]
    results = {"data": [{"scenario": {"respondent_id": "r1"}, "answer": {"theme_code": '{"code": "a"}'}}]}

    def parse_answer(row):
        import json

        return json.loads(next(v for v in row["answer"].values()))

    question, rows, dist = coded_question_and_answers(
        results,
        source_question="q",
        coded_question_name="q_coded",
        codebook=codebook,
        source_text="?",
        parse_answer=parse_answer,
    )
    assert question["question_options"] == ["a"]  # no unclassified bucket added
    assert dist == {"a": 1}
