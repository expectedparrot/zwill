#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
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


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


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


def batched(rows: list[Any], batch_size: int) -> list[list[Any]]:
    return [rows[i : i + batch_size] for i in range(0, len(rows), batch_size)]


async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--model", default="Qwen/Qwen3.5-4B")
    parser.add_argument("--renderer", default="qwen3_5")
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--max-eval-examples", type=int, default=200)
    parser.add_argument("--sample-temperature", type=float, default=0.0)
    parser.add_argument("--sample-max-tokens", type=int, default=96)
    parser.add_argument("--eval-concurrency", type=int, default=16)
    parser.add_argument(
        "--sampler-checkpoint-name",
        default=None,
        help="If set, persist sampler weights under this name and store the model path.",
    )
    parser.add_argument(
        "--sampler-checkpoint-ttl-seconds",
        type=int,
        default=None,
        help="Optional TTL for persisted sampler weights. Omit for Tinker default.",
    )
    args = parser.parse_args()

    load_dotenv(Path(".env"))
    if "TINKER_API_KEY" not in os.environ:
        raise RuntimeError("TINKER_API_KEY is not set and was not found in .env")

    try:
        import numpy as np
        import tinker
        from tinker_cookbook.renderers import TrainOnWhat, get_renderer, get_text_content
        from tinker_cookbook.supervised.data import conversation_to_datum
    except ImportError as exc:
        raise RuntimeError(
            "Missing Tinker dependencies. Install with: "
            "uv pip install tinker tinker-cookbook"
        ) from exc

    train_rows = read_jsonl(args.run_dir / "train_conversations.jsonl")
    eval_rows = read_jsonl(args.run_dir / "eval_prompts.jsonl")
    if args.max_eval_examples:
        eval_rows = eval_rows[: args.max_eval_examples]

    metrics_path = args.run_dir / "training_metrics.jsonl"
    predictions_path = args.run_dir / "predictions.jsonl"
    for path in (metrics_path, predictions_path):
        if path.exists():
            path.unlink()

    service_client = tinker.ServiceClient()
    training_client = await service_client.create_lora_training_client_async(
        base_model=args.model,
        rank=args.lora_rank,
    )
    tokenizer = training_client.get_tokenizer()
    renderer = get_renderer(args.renderer, tokenizer)

    train_data = [
        conversation_to_datum(
            row["messages"],
            renderer,
            max_length=args.max_length,
            train_on_what=TrainOnWhat.LAST_ASSISTANT_MESSAGE,
        )
        for row in train_rows
    ]

    global_step = 0
    for epoch in range(args.epochs):
        for batch in batched(train_data, args.batch_size):
            started = time.time()
            fwdbwd_future = await training_client.forward_backward_async(
                batch, "cross_entropy"
            )
            optim_future = await training_client.optim_step_async(
                tinker.AdamParams(learning_rate=args.learning_rate)
            )
            fwdbwd_result = await fwdbwd_future.result_async()
            await optim_future.result_async()

            logprobs = np.concatenate(
                [out["logprobs"].tolist() for out in fwdbwd_result.loss_fn_outputs]
            )
            weights = np.concatenate(
                [datum.loss_fn_inputs["weights"].tolist() for datum in batch]
            )
            loss = float(-np.dot(logprobs, weights) / max(weights.sum(), 1.0))
            row = {
                "step": global_step,
                "epoch": epoch,
                "batch_examples": len(batch),
                "loss": loss,
                "seconds": round(time.time() - started, 3),
            }
            append_jsonl(metrics_path, row)
            print(json.dumps(row))
            global_step += 1

    sampler_model_path = None
    if args.sampler_checkpoint_name:
        save_future = await training_client.save_weights_for_sampler_async(
            name=args.sampler_checkpoint_name,
            ttl_seconds=args.sampler_checkpoint_ttl_seconds,
        )
        save_response = await save_future.result_async()
        sampler_model_path = save_response.path
        sampling_client = await training_client.create_sampling_client_async(
            model_path=sampler_model_path
        )
    else:
        sampling_client = await training_client.save_weights_and_get_sampling_client_async()
    stop_sequences = renderer.get_stop_sequences()
    params = tinker.SamplingParams(
        max_tokens=args.sample_max_tokens,
        temperature=args.sample_temperature,
        stop=stop_sequences,
    )

    semaphore = asyncio.Semaphore(args.eval_concurrency)

    async def predict_one(index: int, row: dict[str, Any]) -> dict[str, Any]:
        prompt = renderer.build_generation_prompt(row["messages"])
        async with semaphore:
            result = await sampling_client.sample_async(
                prompt=prompt,
                num_samples=1,
                sampling_params=params,
            )
        response, _ = renderer.parse_response(result.sequences[0].tokens)
        raw_text = get_text_content(response)
        prediction = normalize_label(raw_text, row["options"])
        return {
            "index": index,
            "id": row["id"],
            "respondent_id": row["respondent_id"],
            "target_question": row["target_question"],
            "actual": row["actual"],
            "prediction": prediction,
            "raw_prediction": raw_text,
            "is_correct": prediction == row["actual"],
            "train_majority_baseline": row["train_majority_baseline"],
        }

    prediction_rows = await asyncio.gather(
        *(predict_one(index, row) for index, row in enumerate(eval_rows))
    )
    prediction_rows.sort(key=lambda row: row["index"])

    correct = 0
    parsed = 0
    for row in prediction_rows:
        row.pop("index", None)
        correct += int(row["is_correct"])
        parsed += int(row["prediction"] is not None)
        append_jsonl(predictions_path, row)

    summary = {
        "model": args.model,
        "renderer": args.renderer,
        "lora_rank": args.lora_rank,
        "learning_rate": args.learning_rate,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "train_examples": len(train_rows),
        "eval_examples": len(eval_rows),
        "parsed_predictions": parsed,
        "accuracy": correct / len(eval_rows) if eval_rows else math.nan,
        "training_steps": global_step,
        "sampler_model_path": sampler_model_path,
        "sampler_checkpoint_name": args.sampler_checkpoint_name,
    }
    (args.run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
