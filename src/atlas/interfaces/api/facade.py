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

import json
import secrets
import shutil
import urllib.parse
from typing import TYPE_CHECKING, Any, Literal, Protocol

from fastapi import HTTPException

from atlas.app import Atlas
from atlas.infra.execution_lease import ORPHAN_GUARD
from atlas.infra.ids import TaskId
from atlas.infra.logging import get_logger
from atlas.infra.tasks import spawn
from atlas.infra.types import InboundEvent
from atlas.interfaces.api.errors import NotFoundError
from atlas.interfaces.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalResponse,
    CancelTaskRequest,
    CancelTaskResponse,
    CapabilityResponse,
    CreateTaskRequest,
    HandoffContextResponse,
    HealthCheckItem,
    LaunchPlatformRequest,
    LaunchPlatformResponse,
    RuntimeHealthResponse,
    RuntimeStatusResponse,
    TaskEventResponse,
    TaskResponse,
)
from atlas.orchestration.saga import SagaCoordinator
from atlas.safety.approvals import ApprovalCodeError, ApprovalConflict

if TYPE_CHECKING:
    from atlas.interfaces.api.event_store import TaskEventStore

_log = get_logger("atlas.api.facade")

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})

# Keyed by the enum *value* so this layer does not import atlas.bootstrap.
# ComponentStatus: healthy | degraded | unavailable | failed.
_COMPONENT_STATUS_TO_CHECK: dict[str, Literal["pass", "warn", "fail"]] = {
    "healthy": "pass",
    "degraded": "warn",
    "unavailable": "fail",
    "failed": "fail",
}

# SystemState: booting | initializing | degraded | ready | busy | recovering |
# shutting_down | failed. Anything mid-transition reports degraded rather than
# healthy — a booting system is not yet serving.
_SYSTEM_STATE_TO_OVERALL: dict[str, Literal["healthy", "degraded", "unavailable"]] = {
    "ready": "healthy",
    "busy": "healthy",
    "booting": "degraded",
    "initializing": "degraded",
    "degraded": "degraded",
    "recovering": "degraded",
    "shutting_down": "unavailable",
    "failed": "unavailable",
}

# Capability value -> the Atlas attribute holding the platform that executes it.
# Only KNOWLEDGE routes through the provider registry (bootstrap/capabilities.py
# registers providers for it alone); the rest are served by a platform object
# built at composition time, so platform presence is the only runtime evidence
# available for them. Keyed by the enum *value* to keep this layer independent of
# atlas.capabilities imports. `browser_platform` is genuinely None when
# config.browser.enabled is False, which is what makes this check meaningful
# rather than a tautology.
_CAPABILITY_PLATFORM_ATTR: dict[str, str] = {
    "knowledge": "knowledge_platform",
    "email": "email_platform",
    "calendar": "calendar_platform",
    "contacts": "contacts_platform",
    "weather": "weather_platform",
    "location": "location_platform",
    "currency": "currency_platform",
    "browser": "browser_platform",
    "computer_use": "computer_use",
    "public_api": "public_api",
}


class AtlasControlPlane(Protocol):
    async def runtime_status(self) -> RuntimeStatusResponse: ...
    async def runtime_health(self) -> RuntimeHealthResponse: ...
    async def create_task(self, command: CreateTaskRequest) -> TaskResponse: ...
    async def task_for_idempotency_key(self, key: str) -> TaskResponse | None: ...
    async def cancel_task(self, task_id: str, command: CancelTaskRequest) -> CancelTaskResponse: ...
    async def pending_approvals(self) -> list[ApprovalResponse]: ...
    async def decide_approval(self, approval_id: str, command: ApprovalDecisionRequest) -> ApprovalResponse: ...
    async def task_events(self, task_id: str, after_sequence: int | None) -> list[TaskEventResponse]: ...
    async def get_tasks(self) -> list[TaskResponse]: ...
    async def get_task(self, task_id: str) -> TaskResponse: ...
    async def get_capabilities(self) -> list[CapabilityResponse]: ...
    async def launch_platform(self, command: LaunchPlatformRequest) -> LaunchPlatformResponse: ...
    async def get_handoff(self, token: str) -> HandoffContextResponse | None: ...


class DefaultAtlasControlPlane:
    """Concrete implementation wrapping Atlas and TaskEventStore."""

    def __init__(
        self,
        atlas: Atlas,
        event_store: TaskEventStore,
        version: str,
        handoff_store: dict[str, HandoffContextResponse] | None = None,
    ) -> None:
        self.atlas = atlas
        self.event_store = event_store
        # Passed in rather than hardcoded: the running package version is the
        # only honest answer, and it lives on app.state (set by the lifespan from
        # importlib.metadata). A literal here silently goes stale.
        self.version = version
        self._handoff_store = handoff_store if handoff_store is not None else {}

    async def runtime_status(self) -> RuntimeStatusResponse:
        kill_switch = self.atlas.killswitch.is_active()

        # Count non-terminal tasks from DB (authoritative)
        cur = await self.atlas.db.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE state NOT IN ('completed','failed','cancelled')"
        )
        row = await cur.fetchone()
        active_tasks = row[0] if row else 0
        await cur.close()

        cur2 = await self.atlas.db.conn.execute("SELECT ts FROM audit_events ORDER BY id DESC LIMIT 1")
        audit_row = await cur2.fetchone()
        last_audit = audit_row["ts"] if audit_row else None
        await cur2.close()

        return RuntimeStatusResponse(
            state="ready" if not kill_switch else "degraded",
            version=self.version,
            environment=self.atlas.settings.env,
            kill_switch_active=kill_switch,
            active_task_count=active_tasks,
            pending_approval_count=(
                await self.atlas.approvals.count_pending() if getattr(self.atlas, "approvals", None) else 0
            ),
            last_audit_at=last_audit,
        )

    async def runtime_health(self) -> RuntimeHealthResponse:
        """Real per-component health from the RuntimeSupervisor.

        The supervisor already runs genuine checks (database, safety,
        intelligence, orchestration, memory, capability) on a 60s loop and backs
        /live, /ready and /health. This endpoint previously reported a single
        "database" check derived from `db.health()`, which only tested that a
        connection object existed — so the Command Center's health strip stayed
        green through any failure that did not drop the connection.

        `overall` comes from the supervisor's own SystemState rather than being
        re-derived from the check list: the supervisor knows which components are
        critical (CRITICAL_COMPONENTS), and this layer does not.
        """
        supervisor = getattr(self.atlas, "runtime_supervisor", None)
        if supervisor is not None:
            report = supervisor.get_health_report()  # synchronous
            checked_at = self.atlas.clock.now()
            checks = [
                HealthCheckItem(
                    name=name,
                    status=_COMPONENT_STATUS_TO_CHECK.get(health.status.value, "fail"),
                    detail=health.detail or health.status.value,
                    checked_at=checked_at,
                )
                for name, health in sorted(report.components.items())
            ]
            if checks:
                return RuntimeHealthResponse(
                    overall=_SYSTEM_STATE_TO_OVERALL.get(report.overall_status.value, "degraded"),
                    checks=checks,
                )

        # No supervisor, or it has not recorded any component yet (a bare Atlas
        # built outside the managed startup path): report the one thing we can
        # actually verify rather than assuming health.
        db_ok = await self.atlas.db.health()
        return RuntimeHealthResponse(
            overall="healthy" if db_ok else "unavailable",
            checks=[
                HealthCheckItem(
                    name="database",
                    status="pass" if db_ok else "fail",
                    detail="Connected" if db_ok else "Disconnected",
                    checked_at=self.atlas.clock.now(),
                )
            ],
        )

    async def get_tasks(self) -> list[TaskResponse]:
        cur = await self.atlas.db.conn.execute(
            "SELECT id, source, state, payload, created_ts, updated_ts FROM tasks ORDER BY created_ts DESC LIMIT 20"
        )
        rows = await cur.fetchall()
        await cur.close()
        return [_row_to_task(r) for r in rows]

    async def get_task(self, task_id: str) -> TaskResponse:
        cur = await self.atlas.db.conn.execute(
            "SELECT id, source, state, payload, created_ts, updated_ts FROM tasks WHERE id = ?",
            (task_id,),
        )
        r = await cur.fetchone()
        await cur.close()
        if not r:
            raise NotFoundError(f"Task not found: {task_id}")
        return _row_to_task(r)

    async def task_for_idempotency_key(self, key: str) -> TaskResponse | None:
        cur = await self.atlas.db.conn.execute(
            "SELECT id, source, state, payload, created_ts, updated_ts FROM tasks WHERE idempotency_key = ?", (key,)
        )
        row = await cur.fetchone()
        await cur.close()
        return _row_to_task(row) if row else None

    async def create_task(self, command: CreateTaskRequest) -> TaskResponse:
        """Accept a task, persist it immediately, run it in the background.

        Returns the task in state 'created'. Execution happens asynchronously.
        The caller should open the SSE stream to follow progress.
        """
        # Check idempotency: if this key was already used, return the existing task
        existing = await self.task_for_idempotency_key(command.idempotency_key)
        if existing:
            return existing

        task_id = self.atlas.ids.task_id()
        corr_id = self.atlas.ids.correlation_id()
        now = self.atlas.clock.now()
        prepare = getattr(self.atlas.orchestrator, "prepare_task", None)
        discard = getattr(self.atlas.orchestrator, "discard_admission", None)
        if prepare is not None:
            await prepare(str(task_id))
        try:
            inserted = await self.atlas.db.conn.execute(
            "INSERT INTO tasks(id, source, state, payload, idempotency_key, created_ts, updated_ts) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",
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
        except BaseException:
            if discard is not None:
                await discard(str(task_id))
            raise
        if inserted.rowcount == 0:
            if discard is not None:
                await discard(str(task_id))
            # Another retry may have won after our initial read. Only the
            # inserting caller launches execution; every retry returns its row.
            existing = await self.task_for_idempotency_key(command.idempotency_key)
            if existing is None:
                raise NotFoundError("Task disappeared during idempotent creation")
            return existing

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
                result = await self.atlas.orchestrator.run(inbound)

                # Fetch existing payload to merge
                cur = await self.atlas.db.conn.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,))
                row = await cur.fetchone()
                await cur.close()
                payload = json.loads(row["payload"]) if row and row["payload"] else {}

                payload["ok"] = result.ok
                if result.error:
                    payload["error"] = result.error
                if getattr(result, "answer", None):
                    payload["answer"] = result.answer
                elif result.ok and getattr(result, "observations", None):
                    payload["answer"] = (
                        str(result.observations[-1].content) if result.observations else "Task completed."
                    )

                await self.atlas.db.conn.execute(
                    "UPDATE tasks SET payload = ?, updated_ts = ? WHERE id = ?",
                    (json.dumps(payload), self.atlas.clock.now().isoformat(), task_id),
                )
                await self.atlas.db.conn.commit()

            except Exception as exc:
                # Settle the row. Without this the task stays 'created' forever:
                # runtime_status counts non-terminal rows, so active_task_count
                # never drops and the UI status pill is stuck on BUSY for the rest
                # of the process lifetime — a crashed task that looks like a
                # running one.
                _log.exception(
                    "task.background_run_failed",
                    event_type="task",
                    task_id=task_id,
                    exc_type=type(exc).__name__,
                )
                try:
                    if discard is not None:
                        await discard(str(task_id))
                    await self._mark_task_failed(task_id, exc)
                except Exception:
                    _log.exception("task.mark_failed_failed", event_type="task", task_id=task_id)
            finally:
                if discard is not None:
                    await discard(str(task_id))

        spawn(_run_safely(), name=f"atlas-task-{task_id}")

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

    async def _mark_task_failed(self, task_id: str, exc: BaseException) -> None:
        """Move a crashed task to a terminal state, preserving its payload.

        The payload is merged rather than replaced: `_row_to_task` reads `request`
        and `correlation_id` out of it, and losing those would blank the task in
        the UI. Only the exception TYPE is stored — the message can quote provider
        URLs or credentials.
        """
        cur = await self.atlas.db.conn.execute("SELECT state, payload FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        await cur.close()
        if row is None or row["state"] in _TERMINAL_STATES:
            return  # already settled by the orchestrator; do not overwrite its verdict

        payload = json.loads(row["payload"]) if row["payload"] else {}
        payload["ok"] = False
        payload["error"] = f"task failed: {type(exc).__name__}"
        await self.atlas.db.conn.execute(
            "UPDATE tasks SET state='failed',payload=?,updated_ts=? WHERE id=? "
            "AND state NOT IN ('failed','completed','archived') AND " + ORPHAN_GUARD,
            (json.dumps(payload), self.atlas.clock.now().isoformat(), task_id,
             "task:" + task_id, self.atlas.clock.now().isoformat()),
        )
        await self.atlas.db.conn.commit()

    async def cancel_task(self, task_id: str, command: CancelTaskRequest) -> CancelTaskResponse:
        """Signal cancellation. Returns server decision — may not stop immediately."""
        cur = await self.atlas.db.conn.execute("SELECT state, payload FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        await cur.close()
        if not row:
            raise NotFoundError(f"Task not found: {task_id}")

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
        queue = getattr(self.atlas, "approvals", None)
        if queue is not None:
            payload = json.loads(row["payload"]) if row["payload"] else {}
            correlation_id = payload.get("correlation_id")
            if correlation_id:
                await queue.cancel(str(correlation_id))

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
        queue = getattr(self.atlas, "approvals", None)
        if queue is None:
            return []
        return [ApprovalResponse.model_validate(ticket.model_dump()) for ticket in await queue.pending()]

    async def decide_approval(self, approval_id: str, command: ApprovalDecisionRequest) -> ApprovalResponse:
        queue = getattr(self.atlas, "approvals", None)
        if queue is None:
            raise NotFoundError("Approval not found")
        try:
            ticket = await queue.decide(
                approval_id, command.decision, command.idempotency_key, command.confirmation_code
            )
        except ApprovalCodeError as exc:
            raise HTTPException(422, str(exc)) from None
        except ApprovalConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except LookupError:
            raise NotFoundError("Approval not found") from None
        return ApprovalResponse.model_validate(ticket.model_dump())

    async def get_capabilities(self) -> list[CapabilityResponse]:
        """Report what the runtime actually has, per capability.

        Every field here used to be a literal: `state="ready"`, `providers=1`,
        `healthy_providers=1`, `requires_auth=False` for all seven capabilities,
        regardless of runtime state. Now:

        * `requires_auth` is read from the spec, where it is a real, varying field
          (False for knowledge/weather/location/currency, True for
          email/contacts/calendar).
        * `providers` / `healthy_providers` are the real size of the dispatcher's
          provider chain (`ProviderRegistry`) and the subset whose circuit breaker
          is closed. Only KNOWLEDGE is provider-backed today; the other six are
          served by a platform object, so a truthful 0 is expected there and does
          NOT mean broken — see the state derivation below.
        * `state` is derived, never assumed.
        """
        providers_registry = self.atlas.cap_providers
        caps: list[CapabilityResponse] = []
        for spec in self.atlas.cap_registry.all():
            registered = providers_registry.for_capability(spec.capability)
            healthy = providers_registry.healthy_for_capability(spec.capability)
            caps.append(
                CapabilityResponse(
                    name=spec.capability.value,
                    state=self._capability_state(spec.capability.value, len(registered), len(healthy)),
                    operations=list(spec.operations),
                    providers=len(registered),
                    healthy_providers=len(healthy),
                    requires_auth=spec.requires_auth,
                )
            )
        return caps

    def _capability_state(
        self, capability: str, provider_count: int, healthy_count: int
    ) -> Literal["ready", "degraded", "unavailable", "planned"]:
        """Derive capability state from live runtime state only.

        Two independent execution paths exist, so there are two evidence sources:
        a provider chain walked by CapabilityDispatcher, or a platform object
        built at composition time. Each branch is a checkable fact; a capability
        with neither reports 'planned' rather than guessing.
        """
        if provider_count:
            # Dispatchable via the provider chain. candidates() raises
            # NoProviderAvailable when every breaker is open, so zero healthy
            # providers is a genuine degradation, not a cosmetic one.
            return "ready" if healthy_count else "degraded"

        attr = _CAPABILITY_PLATFORM_ATTR.get(capability)
        if attr is None:
            return "planned"
        return "ready" if getattr(self.atlas, attr, None) is not None else "unavailable"

    async def get_handoff(self, token: str) -> HandoffContextResponse | None:
        return self._handoff_store.get(token)

    async def launch_platform(self, command: LaunchPlatformRequest) -> LaunchPlatformResponse:
        saga = SagaCoordinator(f"launch_platform_{command.target}")
        task_id = self.atlas.ids.task_id()
        workspace_id = self.atlas.ids.task_id()
        corr_id = self.atlas.ids.correlation_id()
        handoff_token = f"hnd_{secrets.token_urlsafe(24)}"
        now_ts = self.atlas.clock.now().isoformat()

        try:
            # Step 1: Record persistence in SQLite tasks table
            payload_data = {
                "request": command.initial_query or f"Launch {command.target} platform",
                "correlation_id": str(corr_id),
                "platform": command.target,
                "workspace_id": str(workspace_id),
                "parent_task_id": command.parent_task_id,
            }
            await self.atlas.db.conn.execute(
                "INSERT INTO tasks(id, source, state, payload, idempotency_key, created_ts, updated_ts) "
                "VALUES (?, ?, 'created', ?, ?, ?, ?)",
                (
                    str(task_id),
                    "api",
                    json.dumps(payload_data),
                    f"launch_{handoff_token}",
                    now_ts,
                    now_ts,
                ),
            )
            await self.atlas.db.conn.commit()

            async def undo_db() -> None:
                await self.atlas.db.conn.execute("DELETE FROM tasks WHERE id = ?", (str(task_id),))
                await self.atlas.db.conn.commit()

            saga.register_compensation("delete_task_db_record", undo_db)

            # Step 2: Physical workspace directory provisioning (if ide)
            if command.target == "ide":
                ws_dir = self.atlas.settings.data_dir / "workspaces" / str(workspace_id)
                ws_dir.mkdir(parents=True, exist_ok=True)

                def undo_fs() -> None:
                    if ws_dir.exists():
                        shutil.rmtree(ws_dir, ignore_errors=True)

                saga.register_compensation("remove_workspace_dir", undo_fs)

                ide_svc = getattr(self.atlas, "ide_service", None)
                if ide_svc is not None:
                    ws_label = command.workspace_name or f"workspace-{str(workspace_id)[:8]}"
                    session = await ide_svc.open_workspace(str(ws_dir), ws_label)
                    workspace_id = TaskId(str(session.workspace.id))

                    def undo_ide() -> None:
                        ide_svc.close_workspace(str(workspace_id))

                    saga.register_compensation("close_ide_workspace", undo_ide)

            # Step 3: Snapshot binding & handoff token
            parent_context: dict[str, Any] | None = None
            if command.parent_task_id:
                cur = await self.atlas.db.conn.execute(
                    "SELECT id, source, state, payload FROM tasks WHERE id = ?", (command.parent_task_id,)
                )
                parent_row = await cur.fetchone()
                await cur.close()
                if parent_row:
                    parent_payload = json.loads(parent_row["payload"]) if parent_row["payload"] else {}
                    parent_context = {
                        "parent_id": parent_row["id"],
                        "source": parent_row["source"],
                        "state": parent_row["state"],
                        "request": parent_payload.get("request", ""),
                    }

            handoff_data = HandoffContextResponse(
                token=handoff_token,
                task_id=str(task_id),
                workspace_id=str(workspace_id),
                platform=command.target,
                parent_task_id=command.parent_task_id,
                parent_context=parent_context,
                created_ts=now_ts,
            )
            self._handoff_store[handoff_token] = handoff_data

            def undo_handoff() -> None:
                self._handoff_store.pop(handoff_token, None)

            saga.register_compensation("revoke_handoff_token", undo_handoff)

            # Compute redirect path
            if command.target == "ide":
                redirect_path = f"/workspaces?workspaceId={workspace_id}&handoff={handoff_token}"
            else:
                q_part = f"&q={urllib.parse.quote(command.initial_query)}" if command.initial_query else ""
                redirect_path = f"/research?taskId={task_id}&handoff={handoff_token}{q_part}"

            return LaunchPlatformResponse(
                task_id=str(task_id),
                workspace_id=str(workspace_id),
                handoff_token=handoff_token,
                redirect_path=redirect_path,
                platform=command.target,
            )
        except Exception as exc:
            await saga.rollback(exc)
            raise HTTPException(status_code=500, detail=f"Saga failed to launch {command.target}: {exc}") from exc



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
