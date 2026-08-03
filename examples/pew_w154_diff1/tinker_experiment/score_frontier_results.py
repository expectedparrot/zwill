#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_results(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as f:
        return json.load(f)


def parse_probability_answer(text: str, options: list[str]) -> tuple[list[float] | None, str | None]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            return None, None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None, None
    probs = payload.get("probabilities")
    if not isinstance(probs, list) or len(probs) != len(options):
        return None, None
    try:
        probs = [float(p) for p in probs]
    except (TypeError, ValueError):
        return None, None
    if any(p < 0 for p in probs) or sum(probs) <= 0:
        return None, None
    total = sum(probs)
    probs = [p / total for p in probs]
    top_index = max(range(len(probs)), key=lambda i: probs[i])
    return probs, options[top_index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    result = load_results(args.results)
    prediction_rows = []
    counts = Counter()
    by_question: dict[str, Counter[str]] = defaultdict(Counter)

    for item in result["data"]:
        scenario = item["scenario"]
        answer_text = item["answer"]["response_probabilities"]
        options = scenario["heldout_options"]
        probs, prediction = parse_probability_answer(answer_text, options)
        actual = scenario["actual_answer"]
        probability_actual = None
        nll = None
        if probs is not None:
            probability_actual = probs[options.index(actual)]
            nll = -math.log(max(probability_actual, 1e-12))
        row = {
            "id": scenario.get("tinker_eval_id"),
            "respondent_id": scenario["respondent_id"],
            "target_question": scenario["heldout_question_name"],
            "actual": actual,
            "prediction": prediction,
            "probabilities": probs,
            "probability_actual": probability_actual,
            "negative_log_likelihood": nll,
            "raw_prediction": answer_text,
            "is_correct": prediction == actual,
            "train_majority_baseline": scenario.get("train_majority_baseline"),
            "model": item["model"],
        }
        prediction_rows.append(row)

        q = row["target_question"]
        counts["n"] += 1
        counts["parsed"] += int(prediction is not None)
        counts["model_correct"] += int(row["is_correct"])
        counts["majority_correct"] += int(row["train_majority_baseline"] == actual)
        counts["uniform_expected_correct"] += 1 / len(options)
        if probability_actual is not None:
            counts["p_actual_sum"] += probability_actual
            counts["nll_sum"] += nll
            counts["prob_n"] += 1

        by_question[q]["n"] += 1
        by_question[q]["parsed"] += int(prediction is not None)
        by_question[q]["model_correct"] += int(row["is_correct"])
        by_question[q]["majority_correct"] += int(row["train_majority_baseline"] == actual)
        by_question[q]["uniform_expected_correct"] += 1 / len(options)
        if probability_actual is not None:
            by_question[q]["p_actual_sum"] += probability_actual
            by_question[q]["nll_sum"] += nll
            by_question[q]["prob_n"] += 1

    def report(c: Counter[str]) -> dict[str, Any]:
        n = c["n"]
        prob_n = c["prob_n"]
        return {
            "n": n,
            "parsed_rate": c["parsed"] / n if n else None,
            "model_accuracy": c["model_correct"] / n if n else None,
            "train_majority_accuracy": c["majority_correct"] / n if n else None,
            "uniform_expected_accuracy": c["uniform_expected_correct"] / n if n else None,
            "mean_probability_actual": c["p_actual_sum"] / prob_n if prob_n else None,
            "mean_negative_log_likelihood": c["nll_sum"] / prob_n if prob_n else None,
        }

    summary = {
        "results_path": str(args.results),
        "overall": report(counts),
        "by_question": {q: report(c) for q, c in sorted(by_question.items())},
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "frontier_predictions.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in prediction_rows)
    )
    (args.out_dir / "frontier_score_report.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
