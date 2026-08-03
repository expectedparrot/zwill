#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"


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
    lowered = cleaned.lower()
    for option in options:
        if lowered == option.lower():
            return option
    for option in options:
        if option.lower() in lowered:
            return option
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--eval-prompts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--env-path", type=Path, default=Path(".env"))
    args = parser.parse_args()

    load_dotenv(args.env_path)
    if "TINKER_API_KEY" not in os.environ:
        raise RuntimeError("TINKER_API_KEY is not set")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the OpenAI SDK with: uv pip install openai") from exc

    client = OpenAI(base_url=BASE_URL, api_key=os.environ["TINKER_API_KEY"])
    rows = read_jsonl(args.eval_prompts)[: args.limit]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for row in rows:
            response = client.chat.completions.create(
                model=args.model_path,
                messages=row["messages"],
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
            content = response.choices[0].message.content or ""
            prediction = normalize_label(content, row["options"])
            out_row = {
                "id": row["id"],
                "respondent_id": row["respondent_id"],
                "target_question": row["target_question"],
                "actual": row["actual"],
                "prediction": prediction,
                "raw_prediction": content,
                "is_correct": prediction == row["actual"],
                "model_path": args.model_path,
            }
            f.write(json.dumps(out_row, separators=(",", ":")) + "\n")
            print(json.dumps(out_row))


if __name__ == "__main__":
    main()
