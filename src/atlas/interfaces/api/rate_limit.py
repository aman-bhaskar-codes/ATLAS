"""Per-principal rate limiting — the quota seam.

Token-bucket per API key (and per IP in anonymous local mode). In-memory is
correct at single-user scale; the interface matches what a Redis
implementation would expose, so swapping later is a constructor change.
429 responses include Retry-After per HTTP convention.
"""

from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse

from atlas.interfaces.api.auth import ANONYMOUS_LOCAL, Principal
from atlas.interfaces.api.errors import error_envelope

# How many allow() calls between sweeps of idle buckets. A counter rather than a
# background task: pruning is pure bookkeeping, and a timer would add a second
# thing that can fail at shutdown for no benefit.
_PRUNE_EVERY = 1000


class TokenBucketLimiter:
    def __init__(
        self,
        *,
        capacity: int = 60,
        refill_per_minute: float = 30.0,
    ) -> None:
        self._capacity = float(capacity)
        self._refill_per_s = refill_per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._calls_since_prune = 0

    def _take(self, key: str) -> float:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - last) * self._refill_per_s)
        self._buckets[key] = (tokens, now)
        return tokens

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, retry_after_s). Consumes one token when allowed."""
        self._calls_since_prune += 1
        if self._calls_since_prune >= _PRUNE_EVERY:
            self._calls_since_prune = 0
            self.prune()

        tokens = self._take(key)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, time.monotonic())
            return True, 0.0
        deficit = 1.0 - tokens
        return False, deficit / self._refill_per_s if self._refill_per_s else 1.0

    def prune(self, max_age_s: float = 3600.0) -> None:
        """Drop buckets untouched for max_age_s.

        Without this the dict grows one entry per distinct caller identity for
        the lifetime of the process — unbounded, since the identity is an IP in
        local mode.
        """
        now = time.monotonic()
        self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < max_age_s}

    def tracked_identities(self) -> int:
        """Live bucket count. Exposed for the prune test and for /ops metrics."""
        return len(self._buckets)


def quota_identity(request: Request) -> str:
    """The bucket key for this request.

    A real API key gets its own bucket. Everything else — including
    ``ANONYMOUS_LOCAL``, which is a single shared singleton and would otherwise
    collapse every local caller into one bucket — is keyed by IP, preserving the
    pre-auth behaviour exactly.
    """
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is not None and principal is not ANONYMOUS_LOCAL:
        return f"key:{principal.key_id}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


async def rate_limit(request: Request) -> JSONResponse | None:
    """Returns a 429 response when throttled, None when allowed.

    WHY a returned response (not HTTPException): this runs as middleware,
    OUTSIDE the route/exception-handler stack — raised exceptions would
    surface as opaque 500s.
    """
    limiter: TokenBucketLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return None
    allowed, retry_after = limiter.allow(quota_identity(request))
    if not allowed:
        return error_envelope(
            request,
            code="rate_limited",
            detail="rate limit exceeded",
            status=429,
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )
    return None
