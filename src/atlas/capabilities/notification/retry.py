"""Notification retry engine — backoff+jitter+failover.

Determines if a failed attempt should be retried, and applies backoff.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_backoff_s: float
    max_backoff_s: float


class RetryEngine:
    def __init__(self, policy: RetryPolicy) -> None:
        self._default_policy = policy

    def policy(self, allow_retry: bool) -> RetryPolicy:
        if not allow_retry:
            return RetryPolicy(1, 0, 0)
        return self._default_policy

    def should_retry(self, attempt: int, policy: RetryPolicy) -> bool:
        return attempt < policy.max_attempts

    async def backoff(self, attempt: int, policy: RetryPolicy) -> None:
        if attempt >= policy.max_attempts:
            return
        # Exponential backoff with full jitter
        temp = min(policy.max_backoff_s, policy.base_backoff_s * (2 ** (attempt - 1)))
        sleep_s = random.uniform(0, temp)
        await asyncio.sleep(sleep_s)
