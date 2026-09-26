"""Execution capacity slots (Part 4 §11-§12).

A step must ACQUIRE capacity before executing — no unbounded gather. Global
and per-candidate semaphores keep one task from starving all others; the
explicit slot model is the seam a future distributed worker replaces (§89).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class SlotUsage:
    active_global: int = 0
    active_by_candidate: dict[str, int] = field(default_factory=dict)


class ExecutionSlots:
    def __init__(self, *, max_concurrent_steps: int = 8, per_candidate_limit: int = 2) -> None:
        self._global = asyncio.Semaphore(max_concurrent_steps)
        self._per_candidate_limit = per_candidate_limit
        self._per_candidate: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_candidate_limit))
        self.usage = SlotUsage()

    @asynccontextmanager
    async def acquire(self, candidate_id: str) -> AsyncIterator[None]:
        async with self._global:
            async with self._per_candidate[candidate_id]:
                self.usage.active_global += 1
                self.usage.active_by_candidate[candidate_id] = self.usage.active_by_candidate.get(candidate_id, 0) + 1
                try:
                    yield
                finally:
                    self.usage.active_global -= 1
                    self.usage.active_by_candidate[candidate_id] -= 1

    @property
    def max_concurrent_steps(self) -> int:
        return self._global._value

    def active(self) -> int:
        return self.usage.active_global


__all__ = ["ExecutionSlots"]
