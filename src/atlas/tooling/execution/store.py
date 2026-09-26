"""Durable execution state (Part 4 §19-§21/§69/§81).

One SQLite row per run (typed state JSON — step states, attempts, counters;
never pickles, §76 of Part 2 conventions) plus append-only versioned
checkpoints at step boundaries. Updates are single-statement upserts on the
shared WAL connection; a crash leaves the previous committed state valid
(§57/§81) and resume rebuilds from it (§21).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from atlas.infra.db import Database
from atlas.tooling.execution.models import ExecutionRun

CHECKPOINT_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ExecutionRunStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ── Run state ─────────────────────────────────────────────────── #

    async def save_run(self, run: ExecutionRun) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO execution_runs
                (run_id, task_id, correlation_id, route_plan_id, route_id, status,
                 outcome, payload_json, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                outcome=excluded.outcome,
                payload_json=excluded.payload_json,
                updated_ts=excluded.updated_ts
            """,
            (
                run.run_id,
                run.task_id,
                run.correlation_id,
                run.route_plan_id,
                run.route_id,
                run.status.value,
                run.outcome.value if run.outcome else None,
                run.model_dump_json(),
                _now(),
                _now(),
            ),
        )
        await self._db.conn.commit()

    async def load_run(self, run_id: str) -> ExecutionRun | None:
        cur = await self._db.conn.execute("SELECT payload_json FROM execution_runs WHERE run_id = ?", (run_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return ExecutionRun.model_validate_json(str(row["payload_json"]))

    async def list_runs(self, *, task_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT run_id, task_id, route_plan_id, status, outcome, created_ts, updated_ts FROM execution_runs"
        params: tuple[Any, ...] = ()
        if task_id is not None:
            sql += " WHERE task_id = ?"
            params = (task_id,)
        sql += " ORDER BY created_ts DESC LIMIT ?"
        params = (*params, limit)
        cur = await self._db.conn.execute(sql, params)
        out: list[dict[str, Any]] = []
        async for row in cur:
            out.append(dict(row))
        return out

    # ── Checkpoints (§19/§20/§69) ─────────────────────────────────── #

    async def save_checkpoint(
        self,
        run: ExecutionRun,
        *,
        boundary: str,
        step_id: str | None = None,
        plan: Any | None = None,
    ) -> int:
        """Append an immutable, versioned checkpoint snapshot (§49/§69).
        The plan travels INSIDE the checkpoint so a fresh process can resume
        without any in-memory state (§21/§68)."""
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "boundary": boundary,
            "step_id": step_id,
            "run": run.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json") if plan is not None else None,
            "saved_at": _now(),
        }
        cur = await self._db.conn.execute(
            "INSERT INTO execution_run_checkpoints (run_id, schema_version, payload_json, created_ts) "
            "VALUES (?, ?, ?, ?)",
            (run.run_id, CHECKPOINT_SCHEMA_VERSION, json.dumps(payload, ensure_ascii=False, default=str), _now()),
        )
        await self._db.conn.commit()
        return int(cur.lastrowid or 0)

    async def load_latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        cur = await self._db.conn.execute(
            "SELECT schema_version, payload_json FROM execution_run_checkpoints "
            "WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        if int(row["schema_version"]) != CHECKPOINT_SCHEMA_VERSION:
            # §69: forward-compat hook — unknown schema versions are refused
            # loudly rather than half-interpreted.
            raise ValueError(
                f"checkpoint schema version {row['schema_version']} != supported {CHECKPOINT_SCHEMA_VERSION}"
            )
        loaded: dict[str, Any] = json.loads(str(row["payload_json"]))
        return loaded


__all__ = ["CHECKPOINT_SCHEMA_VERSION", "ExecutionRunStore"]
