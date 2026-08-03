#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def normalize_label(text: str, options: list[str]) -> str | None:
    cleaned = " ".join(text.strip().split())
    json_prediction = extract_json_answer(text, options)
    if json_prediction is not None:
        return json_prediction
    lowered = cleaned.lower()
    for option in options:
        if lowered == option.lower():
            return option
    for option in options:
        if option.lower() in lowered:
            return option
    return None


def extract_json_answer(text: str, options: list[str]) -> str | None:
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        answer = payload.get("answer")
        if not isinstance(answer, str):
            continue
        for option in options:
            if answer.strip().lower() == option.lower():
                return option
    return None


async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--eval-prompts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--renderer", default="qwen3_5_disable_thinking")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--env-path", type=Path, default=Path(".env"))
    args = parser.parse_args()

    load_dotenv(args.env_path)
    if "TINKER_API_KEY" not in os.environ:
        raise RuntimeError("TINKER_API_KEY is not set")

    import tinker
    from tinker_cookbook.renderers import get_renderer, get_text_content

    service_client = tinker.ServiceClient()
    sampling_client = await service_client.create_sampling_client_async(
        model_path=args.model_path
    )
    tokenizer = sampling_client.get_tokenizer()
    renderer = get_renderer(args.renderer, tokenizer)
    params = tinker.SamplingParams(
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        stop=renderer.get_stop_sequences(),
    )
    rows = read_jsonl(args.eval_prompts)[: args.limit]
    semaphore = asyncio.Semaphore(args.concurrency)

    async def predict_one(index: int, row: dict[str, Any]) -> dict[str, Any]:
        prompt = renderer.build_generation_prompt(row["messages"])
        async with semaphore:
            result = await sampling_client.sample_async(
                prompt=prompt,
                num_samples=1,
                sampling_params=params,
            )
        response, _ = renderer.parse_response(result.sequences[0].tokens)
        content = get_text_content(response)
        prediction = normalize_label(content, row["options"])
        return {
            "index": index,
            "id": row["id"],
            "respondent_id": row["respondent_id"],
            "target_question": row["target_question"],
            "actual": row["actual"],
            "prediction": prediction,
            "raw_prediction": content,
            "is_correct": prediction == row["actual"],
            "model_path": args.model_path,
        }

    predictions = await asyncio.gather(
        *(predict_one(index, row) for index, row in enumerate(rows))
    )
    predictions.sort(key=lambda row: row["index"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for row in predictions:
            row.pop("index", None)
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            print(json.dumps(row))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
