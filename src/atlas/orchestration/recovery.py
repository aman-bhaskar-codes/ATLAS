"""Crash recovery — resolve tasks orphaned by a process restart.

Default policy is FAIL-CLEAN: a task found in a non-terminal state with no
live run is marked failed with a structured reason and its checkpoints are
pruned. Automatic RESUME is deliberately not the default: re-executing a plan
whose completed steps had side effects requires per-tool idempotency keys;
until those exist everywhere, silent re-execution would risk duplicates.
"""

from __future__ import annotations

from atlas.infra.clock import Clock
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
        f"SELECT id FROM tasks WHERE state IN ({placeholders})",
        _NON_TERMINAL,
    )
    rows = await cur.fetchall()
    recovered: list[str] = []
    for row in rows:
        task_id = row["id"]
        if task_id in live_task_ids:
            continue
        await db.conn.execute(  # type: ignore[attr-defined]
            "UPDATE tasks SET state = 'failed', updated_ts = ? WHERE id = ?",
            (clock.now().isoformat(), task_id),
        )
        await checkpoints.prune(task_id)
        recovered.append(task_id)
    if recovered:
        await db.conn.commit()  # type: ignore[attr-defined]
        _log.warning(
            "recovery.completed", event_type="orchestration", recovered=recovered, detail="orphaned tasks marked failed"
        )
    return recovered
