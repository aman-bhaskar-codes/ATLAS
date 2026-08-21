"""Statistical comparison for experiments (Prompt 4 §22).

"Do not invent fake scientific certainty from 5 tasks." All machinery is
appropriate to the amount of data:

- mean / median / variance always;
- confidence intervals via normal approximation, widened for small n;
- significance is NEVER claimed below MIN_N_FOR_SIGNIFICANCE samples;
- paired evaluation when the same tasks ran under both arms.

Standard library only — no heavyweight stats dependency for a single-user
learning plane.
"""

from __future__ import annotations

import math
import statistics

MIN_N_FOR_SIGNIFICANCE = 10  # below this we describe, we do not conclude
SMALL_N = 5  # below this even descriptive deltas are flagged as weak


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def variance(values: list[float]) -> float:
    return statistics.pvariance(values) if len(values) > 1 else 0.0


def confidence_interval(
    baseline: list[float], candidate: list[float], *, paired: bool
) -> tuple[float | None, float | None]:
    """Approximate 95% CI for the candidate-baseline delta. Returns (None,
    None) when there is not enough data to estimate a spread."""
    n = min(len(baseline), len(candidate))
    if n < 2:
        return None, None
    if paired:
        deltas = [c - b for b, c in zip(baseline, candidate, strict=False)]
        delta_mean = statistics.fmean(deltas)
        se = math.sqrt(statistics.pvariance(deltas) / n)
    else:
        delta_mean = mean(candidate) - mean(baseline)
        se = math.sqrt(variance(baseline) / len(baseline) + variance(candidate) / len(candidate))
    # Wider multiplier for small samples — we refuse false precision.
    z = 2.776 if n < MIN_N_FOR_SIGNIFICANCE else 1.96  # ~t(0.975, df=4) vs normal
    return delta_mean - z * se, delta_mean + z * se


def effect_size(baseline: list[float], candidate: list[float]) -> float | None:
    """Cohen's d for the delta; None when pooled spread is zero/unknown."""
    if len(baseline) < 2 or len(candidate) < 2:
        return None
    pooled = math.sqrt((variance(baseline) + variance(candidate)) / 2)
    if pooled == 0:
        return None
    return (mean(candidate) - mean(baseline)) / pooled


def is_significant(
    baseline: list[float],
    candidate: list[float],
    *,
    ci_low: float | None,
    ci_high: float | None,
) -> bool:
    """Significant only with enough data AND a CI that excludes zero."""
    n = min(len(baseline), len(candidate))
    if n < MIN_N_FOR_SIGNIFICANCE:
        return False
    if ci_low is None or ci_high is None:
        return False
    return ci_low > 0 or ci_high < 0


def strength_note(n: int) -> str:
    """Honest descriptor of how much the numbers can support."""
    if n < SMALL_N:
        return "descriptive only — too few tasks to draw conclusions"
    if n < MIN_N_FOR_SIGNIFICANCE:
        return "suggestive — below the significance threshold"
    return "sufficient for statistical comparison"


__all__ = [
    "MIN_N_FOR_SIGNIFICANCE",
    "SMALL_N",
    "confidence_interval",
    "effect_size",
    "is_significant",
    "mean",
    "median",
    "strength_note",
    "variance",
]
