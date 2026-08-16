"""Execution persistence contracts.

WHY store abstractions: the orchestrator owns task lifecycle POLICY, not storage
MECHANICS. Persisting a task row, updating its state, and recording cancellation
intent are infrastructure concerns. Depending on these protocols keeps the
orchestration layer testable with an in-memory fake and lets the local SQLite
implementation be replaced (PostgreSQL, durable queue) without touching the
runtime.

The local implementations live in `infra/execution_store.py` and satisfy these
protocols structurally — infra may not import orchestration (import-linter
contract), so conformance is by signature, not inheritance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class ExecutionStore(Protocol):
    """Durable task lifecycle persistence."""

    async def create_task(
        self,
        *,
        task_id: str,
        source: str,
        payload_json: str,
        idempotency_key: str | None,
        created_ts: datetime,
    ) -> None:
        """Insert a new task row. Idempotent on task_id (INSERT OR IGNORE)."""
        ...

    async def update_task_state(self, *, task_id: str, state: str, updated_ts: datetime) -> None:
        """Persist the task's current lifecycle state."""
        ...


class CancellationStore(Protocol):
    """Durable record of cancellation intent.

    In-process tokens give immediate cooperative stops; the store makes the
    intent survive a crash or reach a task running elsewhere. A persisted
    'cancelling' row is authoritative: a restarted process must treat such a
    task as cancelled rather than resuming it.
    """

    async def request_cancellation(self, task_id: str) -> bool:
        """Record cancellation intent.

        Returns True if the task exists and is not already terminal.
        """
        ...

    async def is_cancelled(self, task_id: str) -> bool:
        """Whether cancellation intent is durably recorded for this task."""
        ...

    async def clear(self, task_id: str) -> None:
        """Drop cancellation intent (used when the run reaches a terminal state)."""
        ...
