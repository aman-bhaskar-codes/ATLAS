"""Crash recovery — resolve tasks orphaned by a process restart.

Default policy is FAIL-CLEAN: a task found in a non-terminal state with no
live run is marked failed with a structured reason and its checkpoints are
pruned. Automatic RESUME is deliberately not the default: re-executing a plan
whose completed steps had side effects requires per-tool idempotency keys;
until those exist everywhere, silent re-execution would risk duplicates.
"""

from __future__ import annotations

from atlas.infra.clock import Clock
from atlas.infra.execution_lease import ORPHAN_GUARD
from atlas.infra.logging import get_logger
from atlas.orchestration.checkpoint import CheckpointStore

_log = get_logger("atlas.orch.recovery")

_NON_TERMINAL = (
    "created",
    "ready",
    "building_context",
    "planning",
    "reasoning",
    "waiting_tool",
    "waiting_confirmation",
    "executing",
    "observing",
    "validating",
    "retrying",
    "cancelling",
)


async def recover_interrupted_tasks(
    db: object,
    checkpoints: CheckpointStore,
    clock: Clock,
    *,
    live_task_ids: frozenset[str] = frozenset(),
) -> list[str]:
    """Mark orphaned non-terminal tasks as failed. Returns recovered task ids.

    live_task_ids: tasks currently executing in THIS process (excluded).
    """
    placeholders = ",".join("?" for _ in _NON_TERMINAL)
    cur = await db.conn.execute(  # type: ignore[attr-defined]
        f"SELECT id,state,payload,updated_ts FROM tasks WHERE state IN ({placeholders})",
        _NON_TERMINAL,
    )
    rows = await cur.fetchall()
    await cur.close()
    recovered: list[str] = []
    for row in rows:
        task_id = row["id"]
        if task_id in live_task_ids:
            continue
        now = clock.now().isoformat()
        update_cur = await db.conn.execute(  # type: ignore[attr-defined]
            "UPDATE tasks SET state='failed',updated_ts=? WHERE id=? AND state=? AND payload=? "
            "AND updated_ts=? AND " + ORPHAN_GUARD,
            (now, task_id, row["state"], row["payload"], row["updated_ts"], "task:" + task_id, now),
        )
        matched = update_cur.rowcount == 1
        await update_cur.close()
        if not matched:
            continue
        await db.conn.commit()  # type: ignore[attr-defined]
        await checkpoints.prune(task_id, orphaned_at=now)
        recovered.append(task_id)
    if recovered:
        await db.conn.commit()  # type: ignore[attr-defined]
        _log.warning(
            "recovery.completed", event_type="orchestration", recovered=recovered, detail="orphaned tasks marked failed"
        )
    return recovered
