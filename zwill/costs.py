"""Actual-cost accounting from EDSL Results.

EDSL stores per-call cost and token usage on each result row under
`raw_model_response` (`<question>_cost`, `<question>_input_tokens`,
`<question>_output_tokens`, `<question>_thinking_tokens`). This lets zwill report
what a run actually cost without re-querying the provider or shelling out to the
`ep` CLI. All accessors degrade gracefully (zeros, `cost_data_available: False`)
when the fields are absent.
"""

from __future__ import annotations

from typing import Any


def estimate_job_cost_summary(job: Any) -> dict[str, Any]:
    """Pre-run cost estimate from an EDSL Jobs object.

    Wraps `job.estimate_job_cost()` defensively — an incomplete price table (or a
    fake job in tests) must never crash a dry run — and returns a compact summary
    with `available: False` when the estimate cannot be produced.
    """
    try:
        estimate = job.estimate_job_cost()
    except Exception as exc:  # pragma: no cover - pricing tables can be incomplete
        return {"available": False, "reason": str(exc)}
    if not isinstance(estimate, dict):
        return {"available": False}
    return {
        "available": True,
        "estimated_total_cost_usd": estimate.get("estimated_total_cost_usd"),
        "estimated_total_input_tokens": estimate.get("estimated_total_input_tokens"),
        "estimated_total_output_tokens": estimate.get("estimated_total_output_tokens"),
        "total_credits_hold": estimate.get("total_credits_hold"),
    }


def _row_model(row: dict[str, Any]) -> str:
    model_field = row.get("model")
    if isinstance(model_field, dict):
        return str(model_field.get("model") or model_field.get("model_name") or "unknown")
    return str(model_field) if model_field else "unknown"


def results_cost_summary(results: Any) -> dict[str, Any]:
    """Summarize $ cost and token usage across an EDSL Results dict.

    Returns a dict with `total_usd`, `call_count`, `cost_data_available`, and a
    per-model `by_model` breakdown (cost, calls, input/output/thinking tokens).
    """
    rows = results.get("data") if isinstance(results, dict) else None
    if not isinstance(rows, list):
        return {"total_usd": 0.0, "call_count": 0, "cost_data_available": False, "by_model": []}

    by_model: dict[str, dict[str, float]] = {}
    total = 0.0
    any_cost = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        rmr = row.get("raw_model_response")
        if not isinstance(rmr, dict):
            rmr = {}
        cost = in_tok = out_tok = think_tok = 0.0
        for key, value in rmr.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if key.endswith("_cost"):
                cost += value
                any_cost = True
            elif key.endswith("_input_tokens"):
                in_tok += value
            elif key.endswith("_output_tokens"):
                out_tok += value
            elif key.endswith("_thinking_tokens"):
                think_tok += value
        bucket = by_model.setdefault(
            _row_model(row),
            {"cost_usd": 0.0, "call_count": 0, "input_tokens": 0.0, "output_tokens": 0.0, "thinking_tokens": 0.0},
        )
        bucket["cost_usd"] += cost
        bucket["call_count"] += 1
        bucket["input_tokens"] += in_tok
        bucket["output_tokens"] += out_tok
        bucket["thinking_tokens"] += think_tok
        total += cost

    return {
        "total_usd": round(total, 4),
        "call_count": sum(int(b["call_count"]) for b in by_model.values()),
        "cost_data_available": any_cost,
        "by_model": [
            {
                "model": model,
                "cost_usd": round(bucket["cost_usd"], 4),
                "call_count": int(bucket["call_count"]),
                "input_tokens": int(bucket["input_tokens"]),
                "output_tokens": int(bucket["output_tokens"]),
                "thinking_tokens": int(bucket["thinking_tokens"]),
            }
            for model, bucket in sorted(by_model.items())
        ],
    }
