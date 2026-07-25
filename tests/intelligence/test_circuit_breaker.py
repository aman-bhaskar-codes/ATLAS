"""Tests for CircuitBreaker — fail thresholds, open/half-open/closed transitions."""

from __future__ import annotations

import time

from atlas.intelligence.governance.circuit_breaker import BreakerState, CircuitBreaker


def test_starts_closed_and_allows() -> None:
    cb = CircuitBreaker(fail_threshold=3, cooldown_s=30.0)
    assert cb.state is BreakerState.CLOSED
    assert cb.allow() is True


def test_opens_after_threshold_failures() -> None:
    cb = CircuitBreaker(fail_threshold=3, cooldown_s=30.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state is BreakerState.CLOSED  # not yet
    cb.record_failure()
    assert cb.state is BreakerState.OPEN  # type: ignore
    assert cb.allow() is False


def test_success_resets_failures_and_closes() -> None:
    cb = CircuitBreaker(fail_threshold=3, cooldown_s=30.0)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()       # resets counter
    cb.record_failure()       # 1 failure only
    cb.record_failure()       # 2 failures — still closed (reset earlier)
    assert cb.state is BreakerState.CLOSED
    assert cb.allow() is True


def test_open_blocks_until_cooldown() -> None:
    cb = CircuitBreaker(fail_threshold=1, cooldown_s=60.0)
    cb.record_failure()
    assert cb.state is BreakerState.OPEN
    # Manually rewind the opened_at timestamp to simulate cooldown passed
    cb._opened_at = time.perf_counter() - 61.0
    # Now allow() should transition to HALF_OPEN and return True
    assert cb.allow() is True
    assert cb.state is BreakerState.HALF_OPEN  # type: ignore


def test_half_open_closes_on_success() -> None:
    cb = CircuitBreaker(fail_threshold=1, cooldown_s=0.0)
    cb.record_failure()
    # cooldown=0 means it transitions immediately to HALF_OPEN on next allow()
    cb.allow()
    assert cb.state is BreakerState.HALF_OPEN
    cb.record_success()
    assert cb.state is BreakerState.CLOSED  # type: ignore
    assert cb.allow() is True


def test_half_open_reopens_on_failure() -> None:
    """After a failure in HALF_OPEN, the breaker should return to OPEN.
    We use a long cooldown so the re-opened breaker stays OPEN and blocks."""
    cb = CircuitBreaker(fail_threshold=1, cooldown_s=60.0)
    # Force open the breaker
    cb.record_failure()
    assert cb.state is BreakerState.OPEN
    # Manually expire the cooldown so allow() transitions to HALF_OPEN
    cb._opened_at = time.perf_counter() - 61.0
    assert cb.allow() is True          # → HALF_OPEN
    assert cb.state is BreakerState.HALF_OPEN  # type: ignore
    # A failure in HALF_OPEN re-opens it
    cb.record_failure()
    assert cb.state is BreakerState.OPEN
    # Cooldown NOT expired yet — must block
    assert cb.allow() is False
