"""Timeout helper — every external await is bounded."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from atlas.orchestration.errors import OrchestrationTimeoutError


async def with_timeout[T](aw: Awaitable[T], *, seconds: float, what: str) -> T:
    try:
        return await asyncio.wait_for(aw, timeout=seconds)
    except TimeoutError as exc:
        raise OrchestrationTimeoutError(f"{what} timed out after {seconds}s") from exc
