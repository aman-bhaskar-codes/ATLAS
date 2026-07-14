"""Rate limiter registry for notification providers."""

from __future__ import annotations

import time


class TokenBucket:
    def __init__(self, capacity: int, fill_rate: float) -> None:
        self._capacity = capacity
        self._tokens = float(capacity)
        self._fill_rate = fill_rate
        self._last_ts = time.monotonic()

    def take(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_ts
        self._tokens = min(float(self._capacity), self._tokens + elapsed * self._fill_rate)
        self._last_ts = now
        
        if self._tokens < 1.0:
            time.sleep((1.0 - self._tokens) / self._fill_rate)
            self._tokens = 0.0
            self._last_ts = time.monotonic()
        else:
            self._tokens -= 1.0


class RateLimiterRegistry:
    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def register(self, provider: str, capacity: int = 5, fill_rate: float = 1.0) -> None:
        self._buckets[provider] = TokenBucket(capacity, fill_rate)

    def take(self, provider: str) -> None:
        bucket = self._buckets.get(provider)
        if bucket:
            bucket.take()
