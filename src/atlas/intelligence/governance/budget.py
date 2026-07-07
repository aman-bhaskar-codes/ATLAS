"""Budget + cost governor — multi-window, fail-closed.

WHY read spend from the audit log: single source of truth for money (Phase 1).
WHY fail-closed: if spend can't be determined, deny the paid call. Enforces
daily/weekly/monthly and per-task caps; projected cost is estimated pre-call and
reconciled post-call by the runtime's telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SpendSource(Protocol):
    async def cost_today(self) -> float: ...
    async def cost_this_week(self) -> float: ...
    async def cost_this_month(self) -> float: ...


@dataclass(frozen=True)
class Budgets:
    daily_usd: float = 1.0
    weekly_usd: float = 5.0
    monthly_usd: float = 15.0
    per_task_usd: float = 0.50
