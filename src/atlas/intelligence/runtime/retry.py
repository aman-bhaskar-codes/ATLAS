"""Retry engine — transient intra-provider failures.

WHY: Fallback handles inter-provider switches. Retry handles small blips
(e.g., timeout, socket reset, transient 500) before we give up on the
current model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from atlas.infra.logging import get_logger
from atlas.intelligence.errors import IntelligenceError

_log = get_logger("atlas.intel.retry")

T = TypeVar("T")


class RetryEngine:
    def __init__(self, max_attempts: int = 3, base_backoff_s: float = 1.0) -> None:
        self._max = max_attempts
        self._backoff = base_backoff_s

    async def run(self, func: Callable[[], Awaitable[T]]) -> T:
        last: Exception | None = None
        for i in range(self._max):
            try:
                return await func()
            except IntelligenceError as exc:
                last = exc
                if not exc.retryable:
                    raise
                if i < self._max - 1:
                    sleep_s = self._backoff * (2 ** i)
                    _log.info("retry.wait", attempt=i+1, wait=sleep_s, error=repr(exc))
                    await asyncio.sleep(sleep_s)
        raise last or RuntimeError("unreachable")
