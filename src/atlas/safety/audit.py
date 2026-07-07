"""Append-only audit log + cost source.

WHY two tables: audit_events stays compact and fast to query; big inputs/outputs
go in payloads. WHY it is the cost source of truth: money is recorded exactly
once, here, and the CostGovernor reads it — no second ledger to drift.
"""

from __future__ import annotations

import json

from atlas.infra.db import Database
from atlas.infra.types import AuditRecord


class AuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, rec: AuditRecord) -> None:
        payload_id: int | None = None
        if rec.payload is not None:
            cur = await self._db.conn.execute(
                "INSERT INTO payloads(body) VALUES (?)",
                (json.dumps(rec.payload, default=str),),
            )
            payload_id = int(cur.lastrowid) if cur.lastrowid is not None else None
        await self._db.conn.execute(
            "INSERT INTO audit_events(correlation_id, ts, actor, action, tool, tier, "
            "decision, outcome, payload_id, cost_tokens, cost_usd) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec.correlation_id, rec.ts.isoformat(), rec.actor, rec.action, rec.tool,
                int(rec.tier) if rec.tier is not None else None,
                rec.decision, rec.outcome, payload_id, rec.cost_tokens, rec.cost_usd,
            ),
        )
        await self._db.conn.commit()

    async def tail(self, limit: int = 50) -> list[dict[str, object]]:
        cur = await self._db.conn.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = list(await cur.fetchall())
        return [dict(r) for r in reversed(rows)]

    async def by_correlation(self, correlation_id: str) -> list[dict[str, object]]:
        cur = await self._db.conn.execute(
            "SELECT * FROM audit_events WHERE correlation_id=? ORDER BY id", (correlation_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def cost_today(self) -> float:
        cur = await self._db.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS s FROM audit_events "
            "WHERE ts >= date('now','start of day')"
        )
        row = await cur.fetchone()
        return float(row["s"]) if row else 0.0

    async def cost_this_week(self) -> float:
        cur = await self._db.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS s FROM audit_events "
            "WHERE ts >= date('now','weekday 0','-6 days')"
        )
        row = await cur.fetchone()
        return float(row["s"]) if row else 0.0

    async def cost_this_month(self) -> float:
        cur = await self._db.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS s FROM audit_events "
            "WHERE ts >= date('now','start of month')"
        )
        row = await cur.fetchone()
        return float(row["s"]) if row else 0.0
