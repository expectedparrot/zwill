"""Scoring for continuous (numeric) twin targets elicited as quantiles.

A numeric twin predicts a *predictive distribution* over the target, represented
distribution-free as a set of quantiles (e.g. p05/p25/p50/p75/p95). Scoring uses
proper scoring rules for quantile forecasts:

- **Pinball (quantile) loss**, averaged over the elicited levels -- the headline
  proper score, the continuous analog of NLL. Lower is better.
- **CRPS** estimated from the quantiles (2 x mean pinball) -- the continuous
  analog of the Brier score.
- **Interval coverage** of the central intervals (calibration): does [p25,p75]
  cover ~50% of actuals, [p05,p95] ~90%?
- **MAE / bias** of the median (p50) as a point-estimate sanity check.

The reference baseline is the population's *marginal* quantiles (the climatology
forecast), scored the same way; skill = 1 - twin_pinball / baseline_pinball.
"""

from __future__ import annotations

import math
import re
from typing import Any

# Canonical quantile levels a numeric twin is asked to predict.
DEFAULT_QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95)
# Central intervals reported for calibration, as (low_level, high_level, nominal).
COVERAGE_INTERVALS: tuple[tuple[float, float, float], ...] = ((0.25, 0.75, 0.5), (0.05, 0.95, 0.9))


def _coerce_level(key: Any) -> float | None:
    """Accept levels as 0.05, '0.05', 5, '5', 'p05', 'q95', '95%'."""
    if isinstance(key, (int, float)) and not isinstance(key, bool):
        value = float(key)
    else:
        match = re.search(r"(\d+(?:\.\d+)?)", str(key))
        if not match:
            return None
        value = float(match.group(1))
    if value > 1.0:  # a percentile like 5, 25, 95 (or 95%)
        value /= 100.0
    if 0.0 < value < 1.0:
        return value
    return None


def parse_quantile_prediction(payload: Any) -> tuple[list[float], list[float], str | None]:
    """Extract (levels, values) from a model payload, sorted by level.

    Accepts ``{"quantiles": {level: value}}`` or a flat ``{level: value}`` /
    ``{"p05": ...}`` mapping. Returns ("", "", error) on failure.
    """
    source = payload
    if isinstance(payload, dict) and isinstance(payload.get("quantiles"), dict):
        source = payload["quantiles"]
    if not isinstance(source, dict) or not source:
        return [], [], "missing_quantiles"
    pairs: list[tuple[float, float]] = []
    for key, raw_value in source.items():
        level = _coerce_level(key)
        if level is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return [], [], "invalid_quantile_value"
        if not math.isfinite(value):
            return [], [], "invalid_quantile_value"
        pairs.append((level, value))
    if len(pairs) < 2:
        return [], [], "too_few_quantiles"
    pairs.sort(key=lambda item: item[0])
    levels = [level for level, _ in pairs]
    values = [value for _, value in pairs]
    return levels, values, None


def repair_quantile_values(values: list[float], bounds: tuple[float | None, float | None] = (None, None)) -> list[float]:
    """Enforce monotone non-decreasing quantile values and clamp to bounds.

    LLMs occasionally emit slightly non-monotone quantiles; project onto the
    isotonic constraint with a running max rather than rejecting the row.
    """
    low, high = bounds
    repaired: list[float] = []
    running = -math.inf
    for value in values:
        running = max(running, value)
        clamped = running
        if low is not None:
            clamped = max(clamped, low)
        if high is not None:
            clamped = min(clamped, high)
        repaired.append(clamped)
    return repaired


def pinball_loss(actual: float, level: float, quantile: float) -> float:
    """Quantile (pinball) loss for a single quantile level."""
    error = actual - quantile
    return level * error if error >= 0 else (level - 1.0) * error


def mean_pinball_loss(actual: float, levels: list[float], values: list[float]) -> float:
    return sum(pinball_loss(actual, level, value) for level, value in zip(levels, values)) / len(levels)


def crps_from_quantiles(actual: float, levels: list[float], values: list[float]) -> float:
    """CRPS estimated from the quantile representation (2 x mean pinball).

    This is the standard quantile-based CRPS estimator; it is exact in the limit
    of a dense, uniform quantile grid and a good approximation for a modest set.
    """
    return 2.0 * mean_pinball_loss(actual, levels, values)


def quantile_at(levels: list[float], values: list[float], level: float) -> float:
    """Value at `level`, interpolating/clamping against the elicited quantiles."""
    if level <= levels[0]:
        return values[0]
    if level >= levels[-1]:
        return values[-1]
    for index in range(1, len(levels)):
        if level <= levels[index]:
            left_level, right_level = levels[index - 1], levels[index]
            left_value, right_value = values[index - 1], values[index]
            if right_level == left_level:
                return right_value
            fraction = (level - left_level) / (right_level - left_level)
            return left_value + fraction * (right_value - left_value)
    return values[-1]


def score_numeric_prediction(actual: float, levels: list[float], values: list[float]) -> dict[str, float]:
    """Per-respondent numeric scoring metrics for one quantile forecast."""
    median = quantile_at(levels, values, 0.5)
    scores: dict[str, float] = {
        "pinball": mean_pinball_loss(actual, levels, values),
        "crps": crps_from_quantiles(actual, levels, values),
        "absolute_error": abs(actual - median),
        "median_prediction": median,
        "signed_error": median - actual,
    }
    for low_level, high_level, nominal in COVERAGE_INTERVALS:
        low = quantile_at(levels, values, low_level)
        high = quantile_at(levels, values, high_level)
        scores[f"covered_{int(round(nominal * 100))}"] = float(low <= actual <= high)
    return scores


def weighted_quantiles(values: list[float], weights: list[float], levels: list[float]) -> list[float]:
    """Survey-weighted population quantiles (the marginal / climatology baseline)."""
    pairs = sorted((float(value), float(weight)) for value, weight in zip(values, weights) if weight > 0)
    if not pairs:
        return [float("nan") for _ in levels]
    total = sum(weight for _, weight in pairs)
    result: list[float] = []
    for level in levels:
        target = level * total
        cumulative = 0.0
        chosen = pairs[-1][0]
        for value, weight in pairs:
            cumulative += weight
            if cumulative >= target:
                chosen = value
                break
        result.append(chosen)
    return result
