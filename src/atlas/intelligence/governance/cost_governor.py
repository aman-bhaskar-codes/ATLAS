"""Cost governor enforcement."""

from __future__ import annotations

from atlas.intelligence.errors import BudgetExceededError
from atlas.intelligence.governance.budget import Budgets, SpendSource


class CostGovernor:
    def __init__(self, spend: SpendSource, budgets: Budgets) -> None:
        self._spend = spend
        self._b = budgets
        self._task_spend: dict[str, float] = {}

    async def check(self, projected_usd: float, *, task_id: str | None) -> None:
        try:
            today = await self._spend.cost_today()
            week = await self._spend.cost_this_week()
            month = await self._spend.cost_this_month()
        except Exception as exc:
            raise BudgetExceededError(f"spend unknown, fail-closed: {exc!r}") from exc
        if today + projected_usd > self._b.daily_usd:
            raise BudgetExceededError(f"daily cap ${self._b.daily_usd}: at ${today:.2f}")
        if week + projected_usd > self._b.weekly_usd:
            raise BudgetExceededError(f"weekly cap ${self._b.weekly_usd}")
        if month + projected_usd > self._b.monthly_usd:
            raise BudgetExceededError(f"monthly cap ${self._b.monthly_usd}")
        if task_id is not None:
            spent = self._task_spend.get(task_id, 0.0)
            if spent + projected_usd > self._b.per_task_usd:
                raise BudgetExceededError(f"per-task cap ${self._b.per_task_usd}")

    def record_task_spend(self, task_id: str, usd: float) -> None:
        self._task_spend[task_id] = self._task_spend.get(task_id, 0.0) + usd
