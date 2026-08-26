"""Tests for retry manager."""

from __future__ import annotations

import pytest

from atlas.orchestration.errors import OrchestrationError
from atlas.orchestration.limits import ExecutionLimits, LimitCounter
from atlas.orchestration.managers.retry import RetryManager


class TestRetryManager:
    @pytest.fixture
    def recoverable_error(self) -> OrchestrationError:
        err = OrchestrationError("transient")
        err.recoverable = True
        return err

    @pytest.fixture
    def non_recoverable_error(self) -> OrchestrationError:
        return OrchestrationError("permanent")

    def _counter(self, max_retries: int = 3) -> LimitCounter:
        return LimitCounter(limits=ExecutionLimits(max_retries=max_retries))

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self) -> None:
        manager = RetryManager()
        counter = self._counter()

        async def success() -> str:
            return "success"

        result = await manager.run(success, counter)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_no_retries_used_on_success(self) -> None:
        manager = RetryManager()
        counter = self._counter()

        async def success() -> int:
            return 42

        await manager.run(success, counter)
        assert counter.retries == 0

    @pytest.mark.asyncio
    async def test_retries_recoverable_error(self, recoverable_error: OrchestrationError) -> None:
        call_count = 0

        async def failing_then_success() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise recoverable_error
            return "ok"

        manager = RetryManager(base_s=0.01, max_s=0.05)
        counter = self._counter()
        result = await manager.run(failing_then_success, counter)
        assert result == "ok"
        assert call_count == 3
        assert counter.retries == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self, recoverable_error: OrchestrationError) -> None:
        manager = RetryManager(base_s=0.01, max_s=0.02)
        counter = self._counter(max_retries=2)

        async def always_fails() -> str:
            raise recoverable_error

        with pytest.raises(OrchestrationError, match="transient"):
            await manager.run(always_fails, counter)

    @pytest.mark.asyncio
    async def test_non_recoverable_error_not_retried(self, non_recoverable_error: OrchestrationError) -> None:
        call_count = 0

        async def always_fails() -> str:
            nonlocal call_count
            call_count += 1
            raise non_recoverable_error

        manager = RetryManager(base_s=0.01, max_s=0.02)
        counter = self._counter()
        with pytest.raises(OrchestrationError, match="permanent"):
            await manager.run(always_fails, counter)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_budget_remaining(self, recoverable_error: OrchestrationError) -> None:
        call_count = 0

        async def always_fails() -> str:
            nonlocal call_count
            call_count += 1
            raise recoverable_error

        manager = RetryManager(base_s=0.01, max_s=0.02)
        counter = self._counter(max_retries=0)
        with pytest.raises(OrchestrationError, match="transient"):
            await manager.run(always_fails, counter)
        assert call_count == 1
