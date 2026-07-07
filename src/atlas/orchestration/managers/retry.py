"""Retry manager — bounded, recoverable-only, backed off.

WHY consult error.recoverable: we never retry a CancellationError or a
ContextError; we do retry a transient ToolExecutionError/TimeoutError up to the
limit. Backoff prevents hammering a flaky dependency.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from atlas.orchestration.errors import OrchestrationError
from atlas.orchestration.limits import LimitCounter

T = TypeVar("T")


class RetryManager:
    def __init__(self, base_s: float = 0.5, max_s: float = 8.0) -> None:
        self._base = base_s
        self._max = max_s

    async def run(self, fn: Callable[[], Awaitable[T]], counter: LimitCounter) -> T:
        while True:
            try:
                return await fn()
            except OrchestrationError as exc:
                if not exc.recoverable or not counter.tick_retry():
                    raise
                delay = min(self._base * (2 ** (counter.retries - 1)), self._max)
                delay += random.uniform(0, delay * 0.25)
                await asyncio.sleep(delay)
