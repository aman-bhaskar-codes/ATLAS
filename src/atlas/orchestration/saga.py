"""Saga coordinator with LIFO compensating rollback stack.

Guarantees atomicity across multi-step platform provisioning and handoffs:
Step 1: Record persistence in SQLite (Task/Workspace)
Step 2: Physical directory / Sandbox attachment
Step 3: Trajectory & Working Memory snapshot binding
On failure: rolls back all completed steps in reverse order (LIFO, O(S)).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from atlas.infra.logging import get_logger

_log = get_logger("atlas.orch.saga")

RollbackFn = Callable[[], Awaitable[None] | None]


class SagaRollbackError(Exception):
    """Raised when a compensation step itself fails during rollback."""

    def __init__(self, primary_error: Exception, rollback_errors: list[Exception]) -> None:
        super().__init__(f"Saga failed: {primary_error}; {len(rollback_errors)} rollback error(s)")
        self.primary_error = primary_error
        self.rollback_errors = rollback_errors


class SagaCoordinator:
    """Manages an active sequence of operational steps and their inverse compensations."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._stack: list[tuple[str, RollbackFn]] = []

    def register_compensation(self, name: str, fn: RollbackFn) -> None:
        """Push a compensation callback to the LIFO stack."""
        self._stack.append((name, fn))

    async def rollback(self, cause: Exception) -> None:
        """Unwind all registered compensations in reverse order."""
        _log.warning(
            "saga.rollback_started",
            saga=self.name,
            cause=repr(cause),
            steps_to_unwind=len(self._stack),
        )
        errors: list[Exception] = []
        while self._stack:
            step_name, fn = self._stack.pop()
            try:
                res = fn()
                if inspect.isawaitable(res):
                    await res
                _log.info("saga.step_compensated", saga=self.name, step=step_name)
            except Exception as exc:
                _log.error("saga.compensation_failed", saga=self.name, step=step_name, error=repr(exc))
                errors.append(exc)
        if errors:
            raise SagaRollbackError(cause, errors)
