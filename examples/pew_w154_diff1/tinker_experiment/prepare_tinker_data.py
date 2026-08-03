#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path


SYSTEM_PROMPT = (
    "You predict Pew survey responses from a respondent's prior answers. "
    "Use only the prior answers shown. Return exactly one of the listed options "
    "and no extra text. Do not explain your answer. Do not write a thinking "
    "process or analysis."
)


def read_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
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


def build_prompt(
    *,
    questions: dict[str, dict],
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
        + "\n\nReturn exactly one option."
        + "\nDo not include reasoning or any words before the option."
    )


def conversation_for(prompt: str, answer: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": answer},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--imports-dir", type=Path, default=Path("workdir/imports"))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--heldout-questions",
        default="diff1_c,diff1_e",
        help="Comma-separated target questions whose answers are hidden in prompts.",
    )
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
            train_counts[target_question][answer] += 1
            train_conversations.append(
                {
                    "id": short_hash(f"train:{respondent_id}:{target_question}"),
                    "respondent_id": respondent_id,
                    "target_question": target_question,
                    "messages": conversation_for(prompt, answer),
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
