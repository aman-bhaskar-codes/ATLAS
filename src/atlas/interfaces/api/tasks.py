"""Background task tracking.

WHY this exists: `asyncio.create_task(...)` returns the only strong reference to
the task. Discarding it lets the event loop garbage-collect a task that is still
running, so long-lived fire-and-forget work can vanish mid-flight with nothing in
the logs. This is exactly what ruff's RUF006 flags.

Every fire-and-forget task in the API layer goes through `spawn()`, which keeps a
reference until the task completes and logs any exception the task raised (a task
whose exception is never retrieved otherwise only surfaces as an
"exception was never retrieved" warning at interpreter shutdown, if at all).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from atlas.infra.logging import get_logger

_log = get_logger("atlas.api.tasks")

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
    """Fire-and-forget `coro`, keeping a strong reference until it finishes."""
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def pending_count() -> int:
    """Number of tracked tasks still running. Used by tests and /ops metrics."""
    return len(_TASKS)
