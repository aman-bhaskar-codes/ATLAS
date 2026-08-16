"""Execution checkpoints — durable progress for interrupted tasks.

WHY: a crash mid-task previously left the tasks row stuck in 'reasoning'
forever and lost all progress. Checkpoints capture (goal, plan, compacted
history, step) after every reasoning step. Crash recovery then has two honest
options: RESUME (re-enter the loop with restored state — only safe when the
plan's remaining steps are idempotent) or FAIL CLEAN (mark orphaned tasks
failed with a structured reason). Default policy is fail-clean; resume is
explicit.

The CheckpointStore depends on a narrow StorageBackend protocol (save/get/
list/delete) so the SQLite implementation can be replaced without touching
orchestration — the same seam a PostgreSQL/Redis backend will implement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator


@dataclass(frozen=True)
class ExecutionCheckpoint:
    task_id: str
    step: int
    goal: dict[str, Any]  # serialized GoalState
    plan: dict[str, Any]  # serialized Plan
    history_summary: str
    created_ts: datetime
    id: str = ""
    tenant_id: str = "local"


class StorageBackend(Protocol):
    """Minimal persistence seam for checkpoints (infra implements)."""

    async def save_checkpoint(self, record: dict[str, Any]) -> str: ...
    async def latest_checkpoint(self, task_id: str) -> dict[str, Any] | None: ...
    async def list_checkpoints(self, limit: int = 100) -> list[dict[str, Any]]: ...
    async def delete_checkpoints(self, task_id: str) -> int: ...


class SQLiteCheckpointBackend:
    """SQLite implementation of StorageBackend (migration 16)."""

    def __init__(self, db: Any, ids: IdGenerator) -> None:
        self._db = db
        self._ids = ids

    async def save_checkpoint(self, record: dict[str, Any]) -> str:
        row_id = record.get("id") or self._ids.execution_id()
        await self._db.conn.execute(
            "INSERT OR REPLACE INTO execution_checkpoints "
            "(id, task_id, tenant_id, step, state_json, created_ts) VALUES (?,?,?,?,?,?)",
            (
                row_id,
                record["task_id"],
                record.get("tenant_id", "local"),
                record["step"],
                json.dumps(record["state"]),
                record["created_ts"],
            ),
        )
        await self._db.conn.commit()
        return str(row_id)

    async def latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM execution_checkpoints WHERE task_id = ? ORDER BY created_ts DESC, rowid DESC LIMIT 1",
            (task_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return {
            "id": d["id"],
            "task_id": d["task_id"],
            "tenant_id": d["tenant_id"],
            "step": d["step"],
            "state": json.loads(d["state_json"]),
            "created_ts": d["created_ts"],
        }

    async def list_checkpoints(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = await self._db.conn.execute(
            "SELECT task_id, MAX(created_ts) AS latest FROM execution_checkpoints "
            "GROUP BY task_id ORDER BY latest DESC LIMIT ?",
            (limit,),
        )
        return [{"task_id": r["task_id"], "latest_ts": r["latest"]} for r in await cur.fetchall()]

    async def delete_checkpoints(self, task_id: str) -> int:
        cur = await self._db.conn.execute("DELETE FROM execution_checkpoints WHERE task_id = ?", (task_id,))
        await self._db.conn.commit()
        return int(cur.rowcount or 0)


class CheckpointStore:
    """Typed facade over a StorageBackend."""

    def __init__(self, backend: StorageBackend, clock: Clock) -> None:
        self._backend = backend
        self._clock = clock

    async def save(self, checkpoint: ExecutionCheckpoint) -> str:
        state = {
            "goal": checkpoint.goal,
            "plan": checkpoint.plan,
            "history_summary": checkpoint.history_summary,
        }
        return await self._backend.save_checkpoint(
            {
                "id": checkpoint.id or None,
                "task_id": checkpoint.task_id,
                "tenant_id": checkpoint.tenant_id,
                "step": checkpoint.step,
                "state": state,
                "created_ts": checkpoint.created_ts.isoformat(),
            }
        )

    async def latest(self, task_id: str) -> ExecutionCheckpoint | None:
        record = await self._backend.latest_checkpoint(task_id)
        if record is None:
            return None
        state = record["state"]
        return ExecutionCheckpoint(
            id=record["id"],
            task_id=record["task_id"],
            tenant_id=record["tenant_id"],
            step=record["step"],
            goal=state.get("goal", {}),
            plan=state.get("plan", {}),
            history_summary=state.get("history_summary", ""),
            created_ts=datetime.fromisoformat(record["created_ts"]),
        )

    async def prune(self, task_id: str) -> int:
        """Remove checkpoints for a finished task (called at terminal state)."""
        return await self._backend.delete_checkpoints(task_id)

    async def interrupted_tasks(self) -> list[str]:
        """Task ids with checkpoints but no live run — candidates for recovery."""
        return [r["task_id"] for r in await self._backend.list_checkpoints()]
