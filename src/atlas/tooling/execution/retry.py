"""Retry classification + policy + backoff (Part 4 §24-§28).

Retry eligibility = error CLASS + step policy + SIDE-EFFECT awareness (§24):
a transient failure of an idempotent read retries; an uncertain side effect
NEVER retries automatically (fail safe, §22/§72) — it becomes
``SIDE_EFFECT_UNCERTAIN`` for the recovery controller to route to verification,
human, or an explicit recovery policy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from atlas.tooling.execution.models import RETRYABLE_CLASSES, RetryClass, RetryPolicy
from atlas.tooling.models.tool_result import FailureKind, UniversalToolResult

#: FailureKind (Part 1) → RetryClass. Structured kinds classify exactly;
#: free-text messages only classify on explicit markers (§25).
_KIND_TO_CLASS: dict[FailureKind, RetryClass] = {
    FailureKind.TIMEOUT: RetryClass.TIMEOUT,
    FailureKind.POLICY_DENIED: RetryClass.POLICY,
    FailureKind.HALTED: RetryClass.POLICY,
    FailureKind.UNAVAILABLE: RetryClass.TRANSIENT,
    FailureKind.VALIDATION: RetryClass.INVALID_INPUT,
    FailureKind.EXECUTION_ERROR: RetryClass.UNKNOWN,  # refined by message below
}

_MESSAGE_MARKERS: tuple[tuple[str, RetryClass], ...] = (
    ("rate limit", RetryClass.RATE_LIMIT),
    ("429", RetryClass.RATE_LIMIT),
    ("timed out", RetryClass.TIMEOUT),
    ("timeout", RetryClass.TIMEOUT),
    ("connection", RetryClass.NETWORK),
    ("network", RetryClass.NETWORK),
    ("credential", RetryClass.AUTH),
    ("unauthorized", RetryClass.AUTH),
    ("denied", RetryClass.POLICY),
    ("schema", RetryClass.SCHEMA),
    ("not found", RetryClass.NOT_FOUND),
    ("unsupported operation", RetryClass.INVALID_INPUT),
)


def classify_result(result: UniversalToolResult) -> RetryClass:
    """Map a universal tool result to a retry class (§25)."""
    if result.ok:
        return RetryClass.UNKNOWN  # caller only classifies failures
    if result.error is not None:
        kind_class = _KIND_TO_CLASS.get(result.error.kind, RetryClass.UNKNOWN)
        message = (result.error.message or "").lower()
        if kind_class == RetryClass.UNKNOWN:
            for marker, retry_class in _MESSAGE_MARKERS:
                if marker in message:
                    return retry_class
            return RetryClass.UNKNOWN
        # structured kind wins, but an explicit rate-limit marker refines it
        if kind_class in (RetryClass.TRANSIENT, RetryClass.TIMEOUT):
            for marker, retry_class in _MESSAGE_MARKERS:
                if marker in message:
                    return retry_class
        return kind_class
    return RetryClass.UNKNOWN


def classify_exception(exc: BaseException) -> RetryClass:
    """Map an adapter/engine exception to a retry class."""
    from asyncio import CancelledError
    from asyncio import TimeoutError as AsyncTimeoutError

    if isinstance(exc, CancelledError):
        return RetryClass.CANCELLED
    if isinstance(exc, AsyncTimeoutError):
        return RetryClass.TIMEOUT
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timed out" in message:
        return RetryClass.TIMEOUT
    if "connection" in name or "connection" in message:
        return RetryClass.NETWORK
    if "cancel" in name:
        return RetryClass.CANCELLED
    return RetryClass.UNKNOWN


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    retry_class: RetryClass
    delay_s: float
    reason: str


def should_retry(
    *,
    policy: RetryPolicy,
    retry_class: RetryClass,
    attempt: int,
    idempotent: bool,
    side_effects: bool,
) -> RetryDecision:
    """§24/§26/§28: class + policy + side-effect-aware eligibility.

    * Non-retryable class (policy denial, bad input, auth...) → no retry; the
      recovery controller routes to repair/fallback/human instead.
    * Retryable class BUT non-idempotent with side effects → SIDE_EFFECT_UNCERTAIN
      fail-safe: never blindly re-send an email/delete/push (§24/§72).
    * Unknown class with side effects → fail safe as well (§24).
    """
    retry_class = RetryClass(retry_class)  # tolerate plain-string classes
    if retry_class == RetryClass.CANCELLED:
        return RetryDecision(False, retry_class, 0.0, "cancelled — no further work")
    if retry_class not in policy.retryable_classes or retry_class not in RETRYABLE_CLASSES:
        return RetryDecision(False, retry_class, 0.0, f"class {retry_class.value} is not retryable; route to recovery")
    if attempt >= policy.max_attempts:
        return RetryDecision(False, retry_class, 0.0, f"attempt {attempt}/{policy.max_attempts} exhausted")
    if side_effects and not idempotent:
        return RetryDecision(
            False,
            RetryClass.SIDE_EFFECT_UNCERTAIN,
            0.0,
            "side-effect-aware fail-safe: non-idempotent side-effecting step is never auto-retried (§24)",
        )
    delay = _backoff_delay(policy, attempt)
    return RetryDecision(True, retry_class, delay, f"retryable class {retry_class.value}, attempt {attempt + 1}")


def _backoff_delay(policy: RetryPolicy, attempt: int) -> float:
    """§26/§28: exponential backoff with bounded jitter — never a tight loop."""
    delay = min(policy.initial_delay_s * (policy.backoff_factor ** (attempt - 1)), policy.max_delay_s)
    if policy.jitter:
        delay *= 0.5 + random.random()
    return round(delay, 4)


__all__ = [
    "RETRYABLE_CLASSES",
    "RetryDecision",
    "classify_exception",
    "classify_result",
    "should_retry",
]
