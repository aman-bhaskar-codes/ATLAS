"""Fire-and-forget background task tracking.

WHY this exists: `asyncio.create_task(...)` returns the only strong reference to
the task. The event loop holds a *weak* one, so discarding the return value lets
a still-running task be garbage-collected mid-flight — the work silently stops
with nothing in the logs. This is exactly what ruff's RUF006 flags, and it was
suppressed project-wide until this module existed.

Second failure mode this closes: an exception inside a fire-and-forget task is
never raised anywhere. Without a done-callback that retrieves it, the only trace
is a "Task exception was never retrieved" warning at interpreter shutdown, if the
process exits cleanly at all. `spawn()` logs it when it happens.

Lives in `atlas.infra` because it is the bottom layer: memory, orchestration and
the API all spawn background work and all may import from here (see
importlinter.ini). It imports nothing but the logger.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from atlas.infra.logging import get_logger

_log = get_logger("atlas.infra.tasks")

# Module-level so the reference outlives the caller's frame. Entries are removed
# by the done-callback, so this does not grow without bound.
_TASKS: set[asyncio.Task[Any]] = set()


def _on_done(task: asyncio.Task[Any]) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _log.error(
            "background_task.failed",
            event_type="task",
            task_name=task.get_name(),
            exc_type=type(exc).__name__,
            exc_info=exc,
        )


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
    """Fire-and-forget `coro`, keeping a strong reference until it finishes.

    `name` shows up in the failure log and in `asyncio` task dumps, so make it
    identifying (include the task/episode id where there is one).
    """
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def pending_count() -> int:
    """Number of tracked tasks still running. Used by tests and /ops metrics."""
    return len(_TASKS)
