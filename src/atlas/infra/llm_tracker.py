"""LLM call tracker — records every model invocation for cost analysis.

WHY: Vamos requires granular LLM cost tracking per-task and per-model.
The llm_calls table stores every inference call with provider, model,
token counts, cost, and latency. This feeds the evaluation dashboard
and helps optimize model selection over time.
"""

from __future__ import annotations

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger

_log = get_logger("atlas.llm_tracker")


class LLMCallTracker:
    def __init__(self, db: Database, ids: IdGenerator, clock: Clock) -> None:
        self._db = db
        self._ids = ids
        self._clock = clock

    async def record(
        self, *, task_id: str | None = None, step_index: int | None = None,
        provider: str, model: str,
        tokens_in: int, tokens_out: int,
        cost_usd: float, latency_ms: int,
        cached: bool = False,
    ) -> str:
        """Record a single LLM call."""
        call_id = self._ids.new()
        await self._db.conn.execute(
            "INSERT INTO llm_calls(id, task_id, step_index, provider, model, "
            "tokens_in, tokens_out, cost_usd, latency_ms, cached, created_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (call_id, task_id, step_index, provider, model,
             tokens_in, tokens_out, cost_usd, latency_ms, int(cached),
             self._clock.now().isoformat()),
        )
        await self._db.conn.commit()
        return call_id

    async def cost_by_model(self) -> list[dict[str, object]]:
        """Aggregate cost and token usage grouped by model."""
        cur = await self._db.conn.execute(
            "SELECT model, COUNT(*) as calls, "
            "SUM(tokens_in) as total_in, SUM(tokens_out) as total_out, "
            "SUM(cost_usd) as total_cost, AVG(latency_ms) as avg_latency "
            "FROM llm_calls GROUP BY model ORDER BY total_cost DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def cost_by_task(self, task_id: str) -> dict[str, object]:
        """Total cost and calls for a specific task."""
        cur = await self._db.conn.execute(
            "SELECT COUNT(*) as calls, "
            "SUM(tokens_in) as total_in, SUM(tokens_out) as total_out, "
            "SUM(cost_usd) as total_cost "
            "FROM llm_calls WHERE task_id=?", (task_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else {"calls": 0, "total_in": 0, "total_out": 0, "total_cost": 0.0}

    async def recent(self, limit: int = 50) -> list[dict[str, object]]:
        """Most recent LLM calls."""
        cur = await self._db.conn.execute(
            "SELECT * FROM llm_calls ORDER BY created_ts DESC LIMIT ?", (limit,)
        )
        rows = list(await cur.fetchall())
        return [dict(r) for r in reversed(rows)]

    async def cache_hit_rate(self) -> float:
        """Percentage of calls that hit the cache."""
        cur = await self._db.conn.execute(
            "SELECT COUNT(*) as total, SUM(cached) as hits FROM llm_calls"
        )
        row = await cur.fetchone()
        if not row or int(row["total"]) == 0:
            return 0.0
        return float(row["hits"]) / float(row["total"])
