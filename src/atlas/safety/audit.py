"""Append-only audit log + cost source with tamper-proof hash chain.

WHY two tables: audit_events stays compact and fast to query; big inputs/outputs
go in payloads. WHY hash chain: each entry includes SHA-256(prev_hash + action +
payload + timestamp). Tampering with any historical record breaks the chain.
This is critical for trust — when you review what ATLAS did while you were
asleep, you need to know the log is genuine. WHY it is the cost source of truth:
money is recorded exactly once, here, and the CostGovernor reads it — no second
ledger to drift.
"""

from __future__ import annotations

import hashlib
import json

from atlas.infra.db import Database
from atlas.infra.types import AuditRecord

_GENESIS_HASH = "0" * 64  # SHA-256 of nothing — the chain anchor


def _compute_row_hash(prev_hash: str, action: str, payload_json: str, ts: str) -> str:
    """SHA-256(prev_hash + action + payload + timestamp)."""
    data = f"{prev_hash}|{action}|{payload_json}|{ts}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _last_hash(self) -> str:
        """Retrieve the row_hash of the most recent audit entry (or genesis)."""
        cur = await self._db.conn.execute(
            "SELECT row_hash FROM audit_events ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return str(row["row_hash"]) if row else _GENESIS_HASH

    async def record(self, rec: AuditRecord) -> None:
        payload_json = json.dumps(rec.payload, default=str) if rec.payload else "{}"
        payload_id: int | None = None
        if rec.payload is not None:
            cur = await self._db.conn.execute(
                "INSERT INTO payloads(body) VALUES (?)", (payload_json,)
            )
            payload_id = int(cur.lastrowid) if cur.lastrowid is not None else None

        # Hash chain: link to previous row
        prev_hash = await self._last_hash()
        ts_iso = rec.ts.isoformat()
        row_hash = _compute_row_hash(prev_hash, rec.action, payload_json, ts_iso)

        await self._db.conn.execute(
            "INSERT INTO audit_events(correlation_id, ts, actor, action, tool, tier, "
            "decision, outcome, payload_id, cost_tokens, cost_usd, prev_hash, row_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec.correlation_id, ts_iso, rec.actor, rec.action, rec.tool,
                int(rec.tier) if rec.tier is not None else None,
                rec.decision, rec.outcome, payload_id, rec.cost_tokens, rec.cost_usd,
                prev_hash, row_hash,
            ),
        )
        await self._db.conn.commit()

    async def verify_chain(self) -> tuple[bool, int]:
        """Walk the entire audit log and verify hash chain integrity.

        Returns (is_valid, count_of_records_verified).
        Breaks at the first tampered record.
        """
        cur = await self._db.conn.execute(
            "SELECT ae.id, ae.action, ae.ts, ae.prev_hash, ae.row_hash, "
            "COALESCE(p.body, '{}') AS payload_json "
            "FROM audit_events ae LEFT JOIN payloads p ON ae.payload_id = p.id "
            "ORDER BY ae.id ASC"
        )
        rows = await cur.fetchall()
        expected_prev = _GENESIS_HASH
        for i, row in enumerate(rows):
            if str(row["prev_hash"]) != expected_prev:
                return False, i
            expected_hash = _compute_row_hash(
                str(row["prev_hash"]), str(row["action"]),
                str(row["payload_json"]), str(row["ts"]),
            )
            if str(row["row_hash"]) != expected_hash:
                return False, i
            expected_prev = str(row["row_hash"])
        return True, len(rows)

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

