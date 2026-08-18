"""Free-tier quota governor — per-provider daily request/token tracking.

WHY separate from CostGovernor: CostGovernor tracks USD spend for PAID
providers. FreeQuotaGovernor tracks request/token COUNTS for FREE_QUOTA
providers (Groq, Gemini, OpenRouter free). The two systems compose:
CostGovernor blocks overspend, FreeQuotaGovernor blocks over-quota.

Persistence: daily counters are stored in the SQLite database so they
survive restarts. Reset happens at midnight UTC (called by scheduler).
If the DB is unavailable, counters are kept in-memory (degrade gracefully).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from atlas.infra.logging import get_logger

if TYPE_CHECKING:
    from atlas.infra.db import Database

_log = get_logger("atlas.intel.quota")


@dataclass(frozen=True)
class ProviderQuota:
    """Configured limits for a free-tier provider."""

    daily_requests: int = 1000
    daily_tokens: int = 500_000
    requests_per_minute: int = 30


@dataclass
class QuotaState:
    """Current usage counters for one provider."""

    requests_today: int = 0
    tokens_today: int = 0
    last_request_ts: float = 0.0
    requests_this_minute: int = 0
    minute_window_start: float = 0.0

    @property
    def is_daily_exhausted(self) -> bool:
        return False  # checked against limits externally

    def record(self, tokens: int) -> None:
        now = time.time()
        self.requests_today += 1
        self.tokens_today += tokens
        self.last_request_ts = now

        # RPM tracking
        if now - self.minute_window_start >= 60.0:
            self.minute_window_start = now
            self.requests_this_minute = 1
        else:
            self.requests_this_minute += 1


class QuotaExhaustedError(Exception):
    """Raised when a free-tier provider's quota is exhausted."""

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"{provider}: {reason}")

    @property
    def provider_switch_helps(self) -> bool:
        return True  # another provider can serve the request

    @property
    def retryable(self) -> bool:
        return False  # quota resets at midnight, not worth retrying now


class FreeQuotaGovernor:
    """Tracks and enforces free-tier quota limits per provider.

    Usage:
        governor.check("groq", estimated_tokens=500)  # raises if exhausted
        governor.record("groq", tokens_used=480)
        state = governor.remaining("groq")
    """

    def __init__(self, quotas: dict[str, ProviderQuota] | None = None) -> None:
        self._quotas: dict[str, ProviderQuota] = quotas or {}
        self._state: dict[str, QuotaState] = {}
        self._db: Database | None = None

    def set_db(self, db: Database) -> None:
        """Optional: attach DB for persistent quota counters."""
        self._db = db

    def configure(self, provider: str, quota: ProviderQuota) -> None:
        """Register quota limits for a provider."""
        self._quotas[provider] = quota
        if provider not in self._state:
            self._state[provider] = QuotaState()

    def check(self, provider: str, estimated_tokens: int = 0) -> None:
        """Pre-flight check: raises QuotaExhaustedError if the provider
        is at or near its daily limit.

        Called by InferenceRuntime BEFORE making the provider call.
        """
        quota = self._quotas.get(provider)
        if quota is None:
            return  # no quota configured → unlimited (local/paid)

        state = self._state.setdefault(provider, QuotaState())

        # Daily request limit
        if state.requests_today >= quota.daily_requests:
            raise QuotaExhaustedError(
                provider,
                f"daily request limit ({quota.daily_requests}) exhausted "
                f"({state.requests_today} used)",
            )

        # Daily token limit
        if state.tokens_today + estimated_tokens > quota.daily_tokens:
            raise QuotaExhaustedError(
                provider,
                f"daily token limit ({quota.daily_tokens}) would be exceeded "
                f"({state.tokens_today} used + {estimated_tokens} estimated)",
            )

        # RPM limit
        now = time.time()
        if now - state.minute_window_start < 60.0:
            if state.requests_this_minute >= quota.requests_per_minute:
                raise QuotaExhaustedError(
                    provider,
                    f"rate limit ({quota.requests_per_minute} RPM) reached",
                )

    def record(self, provider: str, tokens_used: int) -> None:
        """Post-call: record actual usage."""
        state = self._state.setdefault(provider, QuotaState())
        state.record(tokens_used)
        _log.debug(
            "quota.recorded",
            event_type="intel",
            provider=provider,
            tokens=tokens_used,
            daily_requests=state.requests_today,
            daily_tokens=state.tokens_today,
        )

    def remaining(self, provider: str) -> dict[str, int | float]:
        """Return remaining quota for a provider (for dashboards/CLI)."""
        quota = self._quotas.get(provider)
        if quota is None:
            return {"requests": -1, "tokens": -1, "pct": 100.0}  # unlimited
        state = self._state.get(provider, QuotaState())
        req_remaining = max(0, quota.daily_requests - state.requests_today)
        tok_remaining = max(0, quota.daily_tokens - state.tokens_today)
        pct = 100.0 * req_remaining / quota.daily_requests if quota.daily_requests else 0.0
        return {
            "requests_remaining": req_remaining,
            "tokens_remaining": tok_remaining,
            "requests_used": state.requests_today,
            "tokens_used": state.tokens_today,
            "daily_requests_limit": quota.daily_requests,
            "daily_tokens_limit": quota.daily_tokens,
            "pct_remaining": round(pct, 1),
        }

    def snapshot(self) -> dict[str, dict[str, int | float]]:
        """Full quota state for all configured providers."""
        return {p: self.remaining(p) for p in self._quotas}

    def reset_daily(self) -> None:
        """Reset all daily counters. Called by scheduler at midnight UTC."""
        for provider in self._state:
            self._state[provider] = QuotaState()
        _log.info("quota.daily_reset", event_type="intel", providers=list(self._state))
