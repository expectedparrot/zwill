#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def accuracy(correct: int, total: int) -> float | None:
    if total == 0:
        return None
    return correct / total


def summarize(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    overall = Counter()
    by_question: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        question = row["target_question"]
        actual = row["actual"]
        prediction = row.get("prediction")
        majority = row["train_majority_baseline"]
        options = manifest["question_options"][question]

        overall["n"] += 1
        overall["model_correct"] += int(prediction == actual)
        overall["parsed"] += int(prediction is not None)
        overall["majority_correct"] += int(majority == actual)
        overall["uniform_expected_correct"] += 1 / len(options)

        by_question[question]["n"] += 1
        by_question[question]["model_correct"] += int(prediction == actual)
        by_question[question]["parsed"] += int(prediction is not None)
        by_question[question]["majority_correct"] += int(majority == actual)
        by_question[question]["uniform_expected_correct"] += 1 / len(options)

    def counter_report(counter: Counter[str]) -> dict[str, Any]:
        n = counter["n"]
        return {
            "n": n,
            "parsed_rate": accuracy(counter["parsed"], n),
            "model_accuracy": accuracy(counter["model_correct"], n),
            "train_majority_accuracy": accuracy(counter["majority_correct"], n),
            "uniform_expected_accuracy": (
                counter["uniform_expected_correct"] / n if n else None
            ),
        }

    return {
        "overall": counter_report(overall),
        "by_question": {
            question: counter_report(counter)
            for question, counter in sorted(by_question.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads((args.run_dir / "manifest.json").read_text())
    rows = read_jsonl(args.run_dir / "predictions.jsonl")
    report = summarize(rows, manifest)
    out_path = args.run_dir / "score_report.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
