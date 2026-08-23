"""Tests for the env-configurable rate-limit quota.

The token bucket is an abuse control, but functional browser E2E runs need a
higher ceiling than production (every Playwright test gets a fresh browser
context, so the suite re-issues CORS preflights faster than the production
bucket refills). These tests pin BOTH halves of that contract:

* omitting the env vars must preserve the production quota exactly, and
* setting them must take effect,

so the E2E escape hatch can never silently become the default.

``create_app()`` is used directly (no lifespan): the limiter is constructed by
the factory, so no Atlas build is required.
"""

from __future__ import annotations

import time

import pytest

from atlas.interfaces.api.app import create_app
from atlas.interfaces.api.rate_limit import _PRUNE_EVERY, TokenBucketLimiter

_ENV_CAPACITY = "ATLAS_RATE_LIMIT_CAPACITY"
_ENV_REFILL = "ATLAS_RATE_LIMIT_PER_MINUTE"


def test_defaults_preserve_the_production_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env override: 120 burst tokens, then throttled."""
    monkeypatch.delenv(_ENV_CAPACITY, raising=False)
    monkeypatch.delenv(_ENV_REFILL, raising=False)

    limiter = create_app().state.rate_limiter

    assert all(limiter.allow("ip:test")[0] for _ in range(120)), "120-token burst expected"
    allowed, retry_after = limiter.allow("ip:test")
    assert allowed is False
    assert retry_after > 0, "a throttled caller must be told when to retry"


def test_env_override_changes_the_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV_CAPACITY, "3")
    monkeypatch.setenv(_ENV_REFILL, "0")  # no refill => deterministic

    limiter = create_app().state.rate_limiter

    assert [limiter.allow("ip:test")[0] for _ in range(4)] == [True, True, True, False]


def test_malformed_env_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in the env must not crash startup or disable the limiter."""
    monkeypatch.setenv(_ENV_CAPACITY, "not-a-number")
    monkeypatch.setenv(_ENV_REFILL, "also-bad")

    limiter = create_app().state.rate_limiter

    assert all(limiter.allow("ip:test")[0] for _ in range(120))
    assert limiter.allow("ip:test")[0] is False, "defaults (120) still applied"


def test_buckets_are_per_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exhausting one caller's bucket must not throttle a different caller."""
    monkeypatch.setenv(_ENV_CAPACITY, "1")
    monkeypatch.setenv(_ENV_REFILL, "0")

    limiter = create_app().state.rate_limiter

    assert limiter.allow("ip:first")[0] is True
    assert limiter.allow("ip:first")[0] is False
    assert limiter.allow("ip:second")[0] is True


# ── pruning ────────────────────────────────────────────────────────────────────
#
# THE LEAK THIS PINS: `prune()` existed and was called from exactly one place —
# a test. In the running server the bucket dict grew one permanent entry per
# distinct caller identity, and in local mode that identity is an IP address. A
# long-lived process behind anything that rotates client addresses leaked memory
# for its whole lifetime with no ceiling and no visibility.


def _backdate(limiter: TokenBucketLimiter, key: str, age_s: float) -> None:
    """Make an existing bucket look untouched for `age_s`.

    Reaches into `_buckets` on purpose. The alternative is patching
    `time.monotonic`, which the asyncio event loop reads for every timer — lying
    to the global clock is a worse trade than one documented private-attribute
    poke in a unit test of the module's own package.
    """
    tokens, _ = limiter._buckets[key]
    limiter._buckets[key] = (tokens, time.monotonic() - age_s)


def test_the_limiter_prunes_itself_without_a_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle identities disappear on their own, repeatedly.

    Two rounds, not one: if the call counter were never reset the first sweep
    would still pass while pruning on every subsequent call forever.
    """
    monkeypatch.setenv(_ENV_CAPACITY, "1")
    monkeypatch.setenv(_ENV_REFILL, "0")
    limiter = create_app().state.rate_limiter

    for round_no in (1, 2):
        stale = f"ip:gone-{round_no}"
        limiter.allow(stale)
        _backdate(limiter, stale, age_s=7200.0)
        assert stale in limiter._buckets

        for _ in range(_PRUNE_EVERY):
            limiter.allow("ip:live")

        assert stale not in limiter._buckets, f"round {round_no}: no sweep happened"

    assert limiter.tracked_identities() == 1, "only the live caller should remain"


def test_pruning_leaves_a_live_callers_tokens_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sweep must not hand a throttled caller a fresh burst.

    Dropping the live bucket instead of keeping it would be a quota bypass: the
    next request would rebuild it at full capacity, so any caller could reset
    their own limit by waiting for someone else's traffic to trigger a sweep.
    """
    monkeypatch.setenv(_ENV_CAPACITY, "5")
    monkeypatch.setenv(_ENV_REFILL, "0")
    limiter = create_app().state.rate_limiter

    for _ in range(3):
        assert limiter.allow("ip:live")[0] is True

    limiter.prune()

    assert "ip:live" in limiter._buckets, "an active bucket was swept"
    assert [limiter.allow("ip:live")[0] for _ in range(3)] == [True, True, False], (
        "the bucket refilled across the sweep — spent tokens came back"
    )


def test_tracked_identities_counts_distinct_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The number /ops reports, and the number the tests above assert on."""
    monkeypatch.setenv(_ENV_CAPACITY, "10")
    monkeypatch.setenv(_ENV_REFILL, "0")
    limiter = create_app().state.rate_limiter

    assert limiter.tracked_identities() == 0

    limiter.allow("ip:a")
    limiter.allow("ip:a")
    limiter.allow("key:b")

    assert limiter.tracked_identities() == 2
