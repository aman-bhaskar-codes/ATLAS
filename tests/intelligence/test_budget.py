"""Tests for CostGovernor / Budgets — multi-window enforcement, fail-closed design."""

from __future__ import annotations

import pytest

from atlas.intelligence.errors import BudgetExceededError
from atlas.intelligence.governance.budget import Budgets
from atlas.intelligence.governance.cost_governor import CostGovernor


class FakeSpend:
    """Controllable fake SpendSource — returns whatever we set."""
    def __init__(
        self,
        today: float = 0.0,
        week: float = 0.0,
        month: float = 0.0,
        *,
        raise_on_call: bool = False,
    ) -> None:
        self._today = today
        self._week = week
        self._month = month
        self._raise = raise_on_call

    async def cost_today(self) -> float:
        if self._raise:
            raise RuntimeError("db unreachable")
        return self._today

    async def cost_this_week(self) -> float:
        if self._raise:
            raise RuntimeError("db unreachable")
        return self._week

    async def cost_this_month(self) -> float:
        if self._raise:
            raise RuntimeError("db unreachable")
        return self._month


_BUDGETS = Budgets(daily_usd=5.0, weekly_usd=20.0, monthly_usd=50.0, per_task_usd=1.0)


@pytest.mark.asyncio
async def test_within_all_budgets_passes() -> None:
    gov = CostGovernor(spend=FakeSpend(today=0.5, week=2.0, month=5.0), budgets=_BUDGETS)
    await gov.check(projected_usd=0.1, task_id="t1")  # must not raise


@pytest.mark.asyncio
async def test_daily_cap_enforced() -> None:
    gov = CostGovernor(spend=FakeSpend(today=4.95), budgets=_BUDGETS)
    with pytest.raises(BudgetExceededError, match="daily"):
        await gov.check(projected_usd=0.10, task_id="t1")


@pytest.mark.asyncio
async def test_weekly_cap_enforced() -> None:
    gov = CostGovernor(spend=FakeSpend(today=0.0, week=19.95), budgets=_BUDGETS)
    with pytest.raises(BudgetExceededError, match="weekly"):
        await gov.check(projected_usd=0.10, task_id="t1")


@pytest.mark.asyncio
async def test_monthly_cap_enforced() -> None:
    gov = CostGovernor(spend=FakeSpend(today=0.0, week=0.0, month=49.95), budgets=_BUDGETS)
    with pytest.raises(BudgetExceededError, match="monthly"):
        await gov.check(projected_usd=0.10, task_id="t1")


@pytest.mark.asyncio
async def test_per_task_cap_enforced() -> None:
    gov = CostGovernor(spend=FakeSpend(), budgets=_BUDGETS)
    # Record spend just under cap, then a new call pushes over
    gov.record_task_spend("t1", 0.95)
    with pytest.raises(BudgetExceededError, match="per-task"):
        await gov.check(projected_usd=0.10, task_id="t1")


@pytest.mark.asyncio
async def test_per_task_cap_resets_per_task_id() -> None:
    """Each task_id has its own accumulated spend."""
    gov = CostGovernor(spend=FakeSpend(), budgets=_BUDGETS)
    gov.record_task_spend("t1", 0.95)
    # t2 has zero spend — should pass
    await gov.check(projected_usd=0.10, task_id="t2")


@pytest.mark.asyncio
async def test_spend_source_failure_is_fail_closed() -> None:
    """If the audit DB is unreachable, we must NOT allow the call (fail-closed)."""
    gov = CostGovernor(spend=FakeSpend(raise_on_call=True), budgets=_BUDGETS)
    with pytest.raises(BudgetExceededError, match="spend unknown"):
        await gov.check(projected_usd=0.10, task_id="t1")


@pytest.mark.asyncio
async def test_no_task_id_skips_per_task_check() -> None:
    """task_id=None means we're in a non-taskified call — skip per-task gate."""
    gov = CostGovernor(spend=FakeSpend(), budgets=_BUDGETS)
    gov.record_task_spend("anon", 0.99)  # this task_id won't match None
    await gov.check(projected_usd=0.10, task_id=None)  # must not raise
