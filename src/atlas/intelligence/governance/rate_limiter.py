"""Token bucket rate limiter."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from atlas.intelligence.errors import RateLimitError


@dataclass
class TokenBucket:
    capacity: float
    refill_per_s: float
    _tokens: float = field(default=0.0)
    _last: float = field(default_factory=time.perf_counter)

    def __post_init__(self) -> None:
        self._tokens = self.capacity

    def take(self, n: float = 1.0) -> None:
        now = time.perf_counter()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.refill_per_s)
        self._last = now
        if self._tokens < n:
            raise RateLimitError("local rate limit")
        self._tokens -= n
