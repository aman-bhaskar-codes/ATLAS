"""Tests for timeout helper."""

from __future__ import annotations

import asyncio

import pytest

from atlas.orchestration.errors import OrchestrationTimeoutError
from atlas.orchestration.managers.timeout import with_timeout


class TestWithTimeout:
    @pytest.mark.asyncio
    async def test_returns_result_when_fast(self) -> None:
        result = await with_timeout(asyncio.sleep(0, result="done"), seconds=5.0, what="test")
        assert result == "done"

    @pytest.mark.asyncio
    async def test_raises_timeout_when_slow(self) -> None:
        with pytest.raises(OrchestrationTimeoutError, match=r"slow op timed out after 0.01s"):
            await with_timeout(asyncio.sleep(10), seconds=0.01, what="slow op")

    @pytest.mark.asyncio
    async def test_timeout_error_contains_what(self) -> None:
        with pytest.raises(OrchestrationTimeoutError, match="my operation"):
            await with_timeout(asyncio.sleep(10), seconds=0.001, what="my operation")

    @pytest.mark.asyncio
    async def test_immediate_result(self) -> None:
        async def immediate() -> int:
            return 42

        result = await with_timeout(immediate(), seconds=1.0, what="immediate")
        assert result == 42
