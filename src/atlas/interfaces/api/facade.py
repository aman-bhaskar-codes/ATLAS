"""Application facade for the FastAPI transport layer.

This facade is the ONLY layer allowed to touch Atlas internals from HTTP.
It translates between the API Pydantic schemas and the domain components.

Design rules (per Gap Audit):
- Never redesign Orchestrator, SafetyEngine, AuditLog, or EventPublisher
- create_task fires the orchestrator as a background asyncio Task (non-blocking, 202 Accepted)
- cancel_task calls orchestrator.cancel() and returns server decision — NOT fake success
- task_events reads from the task_events DB table via TaskEventStore (NOT from episodes)
- runtime_status counts active tasks from DB, not from in-memory state
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Protocol

from fastapi import Request

from atlas.app import Atlas
from atlas.infra.types import InboundEvent
from atlas.interfaces.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalResponse,
    CancelTaskRequest,
    CancelTaskResponse,
    CapabilityResponse,
    CreateTaskRequest,
    HealthCheckItem,
    RuntimeHealthResponse,
    RuntimeStatusResponse,
    TaskEventResponse,
    TaskResponse,
)

if TYPE_CHECKING:
    from atlas.interfaces.api.event_store import TaskEventStore


_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


class AtlasControlPlane(Protocol):
    async def runtime_status(self) -> RuntimeStatusResponse: ...
    async def runtime_health(self) -> RuntimeHealthResponse: ...
    async def create_task(self, command: CreateTaskRequest) -> TaskResponse: ...
    async def cancel_task(self, task_id: str, command: CancelTaskRequest) -> CancelTaskResponse: ...
    async def pending_approvals(self) -> list[ApprovalResponse]: ...
    async def decide_approval(self, approval_id: str, command: ApprovalDecisionRequest) -> ApprovalResponse: ...
    async def task_events(self, task_id: str, after_sequence: int | None) -> list[TaskEventResponse]: ...
    async def get_tasks(self) -> list[TaskResponse]: ...
    async def get_task(self, task_id: str) -> TaskResponse: ...
    async def get_capabilities(self) -> list[CapabilityResponse]: ...


class DefaultAtlasControlPlane:
    """Concrete implementation wrapping Atlas and TaskEventStore."""

    def __init__(self, atlas: Atlas, event_store: TaskEventStore) -> None:
        self.atlas = atlas
        self.event_store = event_store

    async def runtime_status(self) -> RuntimeStatusResponse:
        kill_switch = self.atlas.killswitch.is_active()

        # Count non-terminal tasks from DB (authoritative)
        cur = await self.atlas.db.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE state NOT IN ('completed','failed','cancelled')"
        )
        row = await cur.fetchone()
        active_tasks = row[0] if row else 0

        cur2 = await self.atlas.db.conn.execute("SELECT ts FROM audit_events ORDER BY id DESC LIMIT 1")
        audit_row = await cur2.fetchone()
        last_audit = audit_row["ts"] if audit_row else None

        return RuntimeStatusResponse(
            state="ready" if not kill_switch else "degraded",
            version="1.0.0",
            environment=self.atlas.settings.env,
            kill_switch_active=kill_switch,
            active_task_count=active_tasks,
            pending_approval_count=0,  # Phase Two: approval storage deferred
            last_audit_at=last_audit,
        )

    async def runtime_health(self) -> RuntimeHealthResponse:
        db_ok = await self.atlas.db.health()
        checks = [
            HealthCheckItem(
                name="database",
                status="pass" if db_ok else "fail",
                detail="Connected" if db_ok else "Disconnected",
                checked_at=self.atlas.clock.now(),
            )
        ]
        return RuntimeHealthResponse(
            overall="healthy" if db_ok else "degraded",
            checks=checks,
        )

    async def get_tasks(self) -> list[TaskResponse]:
        cur = await self.atlas.db.conn.execute(
            "SELECT id, source, state, payload, created_ts, updated_ts FROM tasks ORDER BY created_ts DESC LIMIT 20"
        )
        rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]

    async def get_task(self, task_id: str) -> TaskResponse:
        cur = await self.atlas.db.conn.execute(
            "SELECT id, source, state, payload, created_ts, updated_ts FROM tasks WHERE id = ?",
            (task_id,),
        )
        r = await cur.fetchone()
        if not r:
            raise KeyError(f"Task not found: {task_id}")
        return _row_to_task(r)

    async def create_task(self, command: CreateTaskRequest) -> TaskResponse:
        """Accept a task, persist it immediately, run it in the background.

        Returns the task in state 'created'. Execution happens asynchronously.
        The caller should open the SSE stream to follow progress.
        """
        # Check idempotency: if this key was already used, return the existing task
        cur = await self.atlas.db.conn.execute(
            "SELECT id, source, state, payload, created_ts, updated_ts FROM tasks WHERE idempotency_key = ?",
            (command.idempotency_key,),
        )
        existing = await cur.fetchone()
        if existing:
            return _row_to_task(existing)

        task_id = self.atlas.ids.task_id()
        corr_id = self.atlas.ids.correlation_id()
        now = self.atlas.clock.now()

        await self.atlas.db.conn.execute(
            "INSERT INTO tasks(id, source, state, payload, idempotency_key, created_ts, updated_ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                task_id,
                "api",
                "created",
                json.dumps({"request": command.request, "correlation_id": str(corr_id)}),
                command.idempotency_key,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await self.atlas.db.conn.commit()

        # Build the inbound event and fire the orchestrator as a background task.
        # The orchestrator owns all state transitions from here; we never fake progress.
        inbound = InboundEvent(
            correlation_id=corr_id,
            source="api",
            content=command.request,
            task_id=task_id,
        )
        async def _run_safely() -> None:
            try:
                await self.atlas.orchestrator.run(inbound)
            except Exception as e:
                import logging
                import traceback
                logging.getLogger("atlas.api").error(f"BACKGROUND TASK FAILED: {e}\n{traceback.format_exc()}")

        asyncio.create_task(
            _run_safely(),
            name=f"atlas-task-{task_id}",
        )

        return TaskResponse(
            id=task_id,
            correlation_id=str(corr_id),
            source="api",
            request=command.request,
            state="created",
            ok=None,
            answer=None,
            error=None,
            steps_taken=0,
            created_at=now,
            updated_at=now,
        )

    async def cancel_task(self, task_id: str, command: CancelTaskRequest) -> CancelTaskResponse:
        """Signal cancellation. Returns server decision — may not stop immediately."""
        cur = await self.atlas.db.conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")

        current_state: str = row["state"]
        if current_state in _TERMINAL_STATES:
            return CancelTaskResponse(
                task_id=task_id,
                accepted=False,
                state=current_state,
                message=f"Task is already in terminal state '{current_state}' and cannot be cancelled.",
            )

        # Delegate to the authoritative cancel authority
        self.atlas.orchestrator.cancel(task_id)

        return CancelTaskResponse(
            task_id=task_id,
            accepted=True,
            state="cancelling",
            message="Cancellation requested. The current provider call may finish before stopping.",
        )

    async def task_events(self, task_id: str, after_sequence: int | None = None) -> list[TaskEventResponse]:
        """Return ordered events from the task_events table (not episodes)."""
        return await self.event_store.list_events(task_id, after_sequence=after_sequence)

    async def pending_approvals(self) -> list[ApprovalResponse]:
        # Phase Two: approval storage is deferred. Return empty list.
        # TODO(Phase 3): subscribe safety.confirm.requested events → approvals table
        return []

    async def decide_approval(self, approval_id: str, command: ApprovalDecisionRequest) -> ApprovalResponse:
        raise NotImplementedError("decide_approval requires approval storage (Phase Three)")

    async def get_capabilities(self) -> list[CapabilityResponse]:
        caps = []
        for spec in self.atlas.cap_registry.all():
            caps.append(
                CapabilityResponse(
                    name=spec.capability.value,
                    state="ready",
                    operations=list(spec.operations),
                    providers=1,
                    healthy_providers=1,
                    requires_auth=False,
                )
            )
        return caps


def _row_to_task(r: object) -> TaskResponse:
    """Convert a raw aiosqlite Row to a TaskResponse."""
    p = json.loads(r["payload"]) if r["payload"] else {}  # type: ignore[index]
    return TaskResponse(
        id=r["id"],  # type: ignore[index]
        correlation_id=p.get("correlation_id", ""),
        source=r["source"],  # type: ignore[index]
        request=p.get("request", ""),
        state=r["state"],  # type: ignore[index]
        ok=p.get("ok"),
        answer=p.get("answer"),
        error=p.get("error"),
        steps_taken=p.get("steps_taken", 0),
        created_at=r["created_ts"],  # type: ignore[index]
        updated_at=r["updated_ts"],  # type: ignore[index]
    )


def get_control_plane_from_request(request: Request) -> DefaultAtlasControlPlane:
    """FastAPI dependency helper — builds control plane from app.state."""
    return DefaultAtlasControlPlane(
        atlas=request.app.state.atlas,
        event_store=request.app.state.event_store,
    )
