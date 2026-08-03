#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = (
    "You predict Pew survey responses from a respondent's prior answers. "
    "Use only the prior answers shown. You may reason about the respondent's "
    "pattern of answers, but your final response must be one valid JSON object "
    "with exactly two keys: rationale and answer."
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def stable_shuffle(values: list[str], seed: int) -> list[str]:
    rng = random.Random(seed)
    shuffled = list(values)
    rng.shuffle(shuffled)
    return shuffled


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def answer_pattern_summary(
    *,
    questions: dict[str, dict[str, Any]],
    respondent_answers: dict[str, str],
    target_question: str,
) -> str:
    counts = Counter(
        answer
        for question_name, answer in respondent_answers.items()
        if question_name != target_question and question_name in questions
    )
    if not counts:
        return "No prior DIFF1 answers are available."
    parts = [f"{count} prior answer(s) were '{answer}'" for answer, count in counts.most_common()]
    return "; ".join(parts) + "."


def build_prompt(
    *,
    questions: dict[str, dict[str, Any]],
    respondent_answers: dict[str, str],
    target_question: str,
) -> str:
    target = questions[target_question]
    options = target["question_options"]
    prior_lines = []
    for question_name in sorted(questions):
        if question_name == target_question:
            continue
        answer = respondent_answers.get(question_name)
        if answer is None:
            continue
        prior_lines.append(
            f"- {questions[question_name]['question_text']}\n  Answer: {answer}"
        )

    return (
        "Prior answers from this respondent:\n"
        + "\n".join(prior_lines)
        + "\n\nHeld-out question:\n"
        + target["question_text"]
        + "\n\nOptions:\n"
        + "\n".join(f"- {option}" for option in options)
        + "\n\nReturn only valid JSON with this exact shape:\n"
        + '{"rationale":"short respondent-level reasoning based only on prior answers",'
        + '"answer":"one exact option"}'
        + "\nThe answer value must be exactly one of the listed options."
    )


def assistant_payload(
    *,
    questions: dict[str, dict[str, Any]],
    respondent_answers: dict[str, str],
    target_question: str,
    answer: str,
) -> str:
    rationale = (
        answer_pattern_summary(
            questions=questions,
            respondent_answers=respondent_answers,
            target_question=target_question,
        )
        + f" Given that pattern, the held-out response is predicted as '{answer}'."
    )
    return json.dumps({"rationale": rationale, "answer": answer}, separators=(",", ":"))


def conversation_for(prompt: str, answer_payload: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": answer_payload},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--imports-dir", type=Path, default=Path("workdir/imports"))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--heldout-questions", default="diff1_c,diff1_e")
    parser.add_argument("--train-respondents", type=int, default=1000)
    parser.add_argument("--eval-respondents", type=int, default=300)
    parser.add_argument("--seed", type=int, default=154)
    args = parser.parse_args()

    questions = {
        row["question_name"]: row
        for row in read_jsonl(args.imports_dir / "questions.jsonl")
    }
    heldout_questions = [q.strip() for q in args.heldout_questions.split(",") if q.strip()]
    unknown = sorted(set(heldout_questions) - set(questions))
    if unknown:
        raise ValueError(f"Unknown held-out questions: {unknown}")

    answers_by_respondent: dict[str, dict[str, str]] = defaultdict(dict)
    for row in read_jsonl(args.imports_dir / "answers.jsonl"):
        if "answer" in row:
            answers_by_respondent[row["respondent_id"]][row["question"]] = row["answer"]

    required_questions = set(questions)
    complete_ids = [
        respondent_id
        for respondent_id, answers in answers_by_respondent.items()
        if required_questions <= set(answers)
    ]
    split_ids = stable_shuffle(complete_ids, args.seed)
    if args.train_respondents + args.eval_respondents > len(split_ids):
        raise ValueError(
            "Requested more train/eval respondents than complete respondent count "
            f"({len(split_ids)})."
        )
    train_ids = split_ids[: args.train_respondents]
    eval_ids = split_ids[
        args.train_respondents : args.train_respondents + args.eval_respondents
    ]

    train_conversations = []
    eval_prompts = []
    train_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for respondent_id in train_ids:
        answers = answers_by_respondent[respondent_id]
        for target_question in heldout_questions:
            answer = answers[target_question]
            prompt = build_prompt(
                questions=questions,
                respondent_answers=answers,
                target_question=target_question,
            )
            payload = assistant_payload(
                questions=questions,
                respondent_answers=answers,
                target_question=target_question,
                answer=answer,
            )
            train_counts[target_question][answer] += 1
            train_conversations.append(
                {
                    "id": short_hash(f"train:{respondent_id}:{target_question}"),
                    "respondent_id": respondent_id,
                    "target_question": target_question,
                    "messages": conversation_for(prompt, payload),
                }
            )

    majority_by_question = {
        question: counts.most_common(1)[0][0] for question, counts in train_counts.items()
    }
    for respondent_id in eval_ids:
        answers = answers_by_respondent[respondent_id]
        for target_question in heldout_questions:
            prompt = build_prompt(
                questions=questions,
                respondent_answers=answers,
                target_question=target_question,
            )
            eval_prompts.append(
                {
                    "id": short_hash(f"eval:{respondent_id}:{target_question}"),
                    "respondent_id": respondent_id,
                    "target_question": target_question,
                    "question_text": questions[target_question]["question_text"],
                    "options": questions[target_question]["question_options"],
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "actual": answers[target_question],
                    "train_majority_baseline": majority_by_question[target_question],
                }
            )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "train_conversations.jsonl", train_conversations)
    write_jsonl(args.out_dir / "eval_prompts.jsonl", eval_prompts)
    manifest = {
        "imports_dir": str(args.imports_dir),
        "mode": "reasoning_json",
        "heldout_questions": heldout_questions,
        "seed": args.seed,
        "complete_respondents": len(complete_ids),
        "train_respondents": len(train_ids),
        "eval_respondents": len(eval_ids),
        "train_examples": len(train_conversations),
        "eval_examples": len(eval_prompts),
        "question_options": {
            question: questions[question]["question_options"] for question in heldout_questions
        },
        "train_label_counts": {
            question: dict(counts) for question, counts in train_counts.items()
        },
        "train_majority_baseline": majority_by_question,
        "files": {
            "train_conversations": "train_conversations.jsonl",
            "eval_prompts": "eval_prompts.jsonl",
        },
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
