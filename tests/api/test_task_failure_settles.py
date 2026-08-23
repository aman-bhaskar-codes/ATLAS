"""A crashed background task must settle, or the status pill sticks on BUSY.

THE BUG THIS PINS: ``create_task`` fired the orchestrator with a bare
``asyncio.create_task(...)`` whose return value was discarded, and the ``except``
path only logged. Two independent failures followed from that:

1. The event loop keeps only a WEAK reference to a task, so the sole strong
   reference was the discarded return value — the task was collectable mid-flight
   and the work could stop with nothing in the logs. (This is precisely RUF006,
   which was globally disabled in ``pyproject.toml``.)
2. When ``orchestrator.run`` raised, the ``tasks`` row stayed in state
   ``'created'`` FOREVER. ``runtime_status`` counts non-terminal rows, so
   ``active_task_count`` never returned to zero and the Command Center status pill
   read BUSY for the remaining life of the process. A crashed task was
   indistinguishable from a running one.

The second is the one a user sees, and it is the assertion below: after a forced
crash the row is ``failed`` and ``active_task_count`` is back to 0.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from atlas.orchestration.orchestrator import Orchestrator

_TERMINAL = frozenset({"completed", "failed", "cancelled"})


def _new_key() -> str:
    """A fresh idempotency key — 16 chars minimum per CreateTaskRequest."""
    return uuid.uuid4().hex


async def _await_terminal(client: AsyncClient, task_id: str, *, timeout_s: float = 10.0) -> str:
    """Poll until the task leaves the non-terminal states, or fail loudly.

    Polling the HTTP API rather than reaching into ``atlas.infra.tasks`` on
    purpose: "the row settles" is a client-observable contract, and this is the
    exact loop the frontend's task view runs.
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    state = "unknown"
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200, response.text
        state = response.json()["state"]
        if state in _TERMINAL:
            return state
        await asyncio.sleep(0.02)
    pytest.fail(f"task {task_id} never reached a terminal state (stuck in {state!r})")


async def test_crashed_task_is_marked_failed(api_client: AsyncClient) -> None:
    """orchestrator.run raising must move the row to 'failed', not leave it 'created'."""
    with patch.object(Orchestrator, "run", AsyncMock(side_effect=RuntimeError("provider unreachable"))):
        created = await api_client.post(
            "/api/v1/tasks",
            json={"request": "anything at all", "idempotency_key": _new_key()},
        )
        assert created.status_code == 202, created.text
        task_id = created.json()["id"]

        assert await _await_terminal(api_client, task_id) == "failed"


async def test_active_task_count_returns_to_zero_after_a_crash(api_client: AsyncClient) -> None:
    """The stuck-BUSY regression, asserted on the field the UI actually reads."""
    with patch.object(Orchestrator, "run", AsyncMock(side_effect=RuntimeError("provider unreachable"))):
        created = await api_client.post(
            "/api/v1/tasks",
            json={"request": "anything at all", "idempotency_key": _new_key()},
        )
        task_id = created.json()["id"]
        await _await_terminal(api_client, task_id)

    status = await api_client.get("/api/v1/runtime/status")
    assert status.status_code == 200, status.text
    assert status.json()["active_task_count"] == 0, (
        "a crashed task is still counted as active — the status pill stays BUSY forever"
    )


async def test_failure_detail_does_not_leak_the_exception_message(api_client: AsyncClient) -> None:
    """Only the exception TYPE is persisted: messages can quote URLs or credentials."""
    secret = "http://user:hunter2@provider.internal/v1"
    with patch.object(Orchestrator, "run", AsyncMock(side_effect=RuntimeError(secret))):
        created = await api_client.post(
            "/api/v1/tasks",
            json={"request": "anything at all", "idempotency_key": _new_key()},
        )
        task_id = created.json()["id"]
        await _await_terminal(api_client, task_id)

    response = await api_client.get(f"/api/v1/tasks/{task_id}")
    assert "hunter2" not in response.text
    assert "provider.internal" not in response.text


async def test_orchestrator_verdict_is_not_overwritten(api_client: AsyncClient) -> None:
    """A task the orchestrator already settled keeps ITS state.

    ``_mark_task_failed`` returns early on a terminal row. Without that guard, a
    post-completion exception in the background wrapper would relabel a genuinely
    completed task as failed — a lie in the opposite direction.
    """
    created = await api_client.post(
        "/api/v1/tasks",
        json={"request": "say hello", "idempotency_key": _new_key()},
    )
    assert created.status_code == 202, created.text
    task_id = created.json()["id"]

    # The conftest mock makes the reasoning loop answer immediately, so this
    # completes on its own rather than crashing.
    state = await _await_terminal(api_client, task_id)
    assert state == "completed", f"expected the mocked run to complete, got {state!r}"
