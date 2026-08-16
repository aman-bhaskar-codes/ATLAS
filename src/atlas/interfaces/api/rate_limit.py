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

from atlas.interfaces.api.auth import Principal


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

    def _take(self, key: str) -> float:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - last) * self._refill_per_s)
        self._buckets[key] = (tokens, now)
        return tokens

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, retry_after_s). Consumes one token when allowed."""
        tokens = self._take(key)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, time.monotonic())
            return True, 0.0
        deficit = 1.0 - tokens
        return False, deficit / self._refill_per_s if self._refill_per_s else 1.0

    def prune(self, max_age_s: float = 3600.0) -> None:
        now = time.monotonic()
        self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < max_age_s}


async def rate_limit(request: Request) -> JSONResponse | None:
    """Returns a 429 response when throttled, None when allowed.

    WHY a returned response (not HTTPException): this runs as middleware,
    OUTSIDE the route/exception-handler stack — raised exceptions would
    surface as opaque 500s.
    """
    limiter: TokenBucketLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return None
    principal: Principal | None = getattr(request.state, "principal", None)
    identity = principal.key_id if principal else f"ip:{request.client.host if request.client else 'unknown'}"
    allowed, retry_after = limiter.allow(identity)
    if not allowed:
        return JSONResponse(
            {"detail": "rate limit exceeded"},
            status_code=429,
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )
    return None
