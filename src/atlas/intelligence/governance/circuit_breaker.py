"""Token-bucket rate limiter + circuit breaker, per provider.

WHY per-provider: a rate limit or outage at DeepSeek must not affect GLM. The
breaker opens after N consecutive failures, half-opens after a cooldown, and the
selector treats an open breaker as 'unavailable' so traffic reroutes
automatically. This is how we 'never allow cascading failures'.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    fail_threshold: int = 5
    cooldown_s: float = 30.0
    _failures: int = 0
    _state: BreakerState = BreakerState.CLOSED
    _opened_at: float = 0.0

    def allow(self) -> bool:
        if self._state is BreakerState.OPEN:
            if time.perf_counter() - self._opened_at >= self.cooldown_s:
                self._state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.fail_threshold:
            self._state = BreakerState.OPEN
            self._opened_at = time.perf_counter()

    @property
    def state(self) -> BreakerState:
        return self._state
