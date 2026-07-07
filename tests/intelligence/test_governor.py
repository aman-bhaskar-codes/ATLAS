from datetime import UTC, datetime

import pytest

from atlas.infra.types import AuditRecord, CorrelationId
from atlas.intelligence.errors import BudgetExceededError
from atlas.intelligence.governance.budget import Budgets
from atlas.intelligence.governance.cost_governor import CostGovernor


class FakeAuditStore:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []
    
    async def cost_this_month(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.records)
    
    async def cost_today(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.records)

    async def cost_this_week(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.records)
        
    async def record(self, r: AuditRecord) -> None:
        self.records.append(r)

@pytest.mark.asyncio
async def test_cost_governor_allows_under_budget() -> None:
    store = FakeAuditStore()
    budgets = Budgets(daily_usd=10.0, monthly_usd=100.0)
    governor = CostGovernor(spend=store, budgets=budgets)
    
    await governor.check(projected_usd=0.1, task_id="t1")  # should not raise

@pytest.mark.asyncio
async def test_cost_governor_blocks_over_budget() -> None:
    store = FakeAuditStore()
    now = datetime.now(UTC)
    # Simulate $11 spent today
    await store.record(AuditRecord(
        correlation_id=CorrelationId("1"), ts=now, actor="x",
        action="x", outcome="ok", cost_usd=11.0, payload={},
    ))
    
    budgets = Budgets(daily_usd=10.0, monthly_usd=100.0)
    governor = CostGovernor(spend=store, budgets=budgets)
    
    with pytest.raises(BudgetExceededError):
        await governor.check(projected_usd=1.0, task_id="t1")
