"""Implementation of the Trust Center plane."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from atlas.app import Atlas
from atlas.interfaces.api.control_plane import AtlasTrustPlane
from atlas.interfaces.api.errors import NotFoundError
from atlas.interfaces.api.idempotency import IdempotencyStore
from atlas.interfaces.api.projections import project_audit, project_task
from atlas.interfaces.api.schemas_trust import (
    ApprovalDecisionCommand,
    ApprovalView,
    AuditEventView,
    AuditExport,
    AuditFilters,
    AuditPage,
    MemoryCorrectionCommand,
    MemoryFactView,
    MemoryMutationReceipt,
    ProvenanceView,
    TaskPage,
    TaskView,
)
from atlas.safety.approvals import ApprovalCodeError, ApprovalConflict


class DefaultAtlasTrustPlane(AtlasTrustPlane):
    def __init__(self, atlas: Atlas) -> None:
        self._atlas = atlas
        self._idemp = IdempotencyStore(atlas.db)

    async def list_tasks(self, *, cursor: str | None, limit: int) -> TaskPage:
        # Cursor is updated_ts. We want descending.
        query = "SELECT * FROM tasks"
        params: list[str] = []
        if cursor:
            query += " WHERE updated_ts < ?"
            params.append(cursor)
        query += " ORDER BY updated_ts DESC LIMIT ?"
        params.append(str(limit + 1))

        cur = await self._atlas.db.conn.execute(query, tuple(params))
        rows: list[Any] = list(await cur.fetchall())

        items = [project_task(dict(row)) for row in rows[:limit]]
        next_cursor = items[-1].updated_at.isoformat() if len(rows) > limit else None

        return TaskPage(items=tuple(items), next_cursor=next_cursor)

    async def get_task(self, task_id: str) -> TaskView:
        cur = await self._atlas.db.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        if not row:
            raise NotFoundError(f"Task not found: {task_id}")
        return project_task(dict(row))

    async def pending_approvals(self) -> Sequence[ApprovalView]:
        queue = getattr(self._atlas, "approvals", None)
        return (
            tuple(ApprovalView.model_validate(ticket.model_dump()) for ticket in await queue.pending()) if queue else ()
        )

    async def get_approval(self, approval_id: str) -> ApprovalView:
        queue = getattr(self._atlas, "approvals", None)
        ticket = await queue.get(approval_id) if queue else None
        if ticket is None:
            raise NotFoundError("Approval not found")
        return ApprovalView.model_validate(ticket.model_dump())

    async def decide_approval(self, command: ApprovalDecisionCommand) -> ApprovalView:
        queue = getattr(self._atlas, "approvals", None)
        if queue is None:
            raise NotFoundError("Approval not found")
        try:
            ticket = await queue.decide(
                command.approval_id, command.decision, command.idempotency_key, command.confirmation_code
            )
        except ApprovalCodeError as exc:
            raise HTTPException(422, str(exc)) from None
        except ApprovalConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except LookupError:
            raise NotFoundError("Approval not found") from None
        return ApprovalView.model_validate(ticket.model_dump())

    async def search_memory(self, query: str, *, limit: int) -> Sequence[MemoryFactView]:
        return []

    async def get_memory_fact(self, fact_id: str) -> MemoryFactView:
        raise NotFoundError(f"Fact not found: {fact_id}")

    async def memory_provenance(self, fact_id: str) -> Sequence[ProvenanceView]:
        return []

    async def correct_memory(self, command: MemoryCorrectionCommand) -> MemoryMutationReceipt:
        raise NotImplementedError()

    async def supersede_memory(self, fact_id: str, *, idempotency_key: str, request_id: str) -> MemoryMutationReceipt:
        raise NotImplementedError()

    async def delete_memory(self, fact_id: str, *, idempotency_key: str, request_id: str) -> MemoryMutationReceipt:
        raise NotImplementedError()

    async def audit(
        self,
        *,
        task_id: str | None,
        correlation_id: str | None,
        execution_id: str | None,
        cursor: str | None,
        limit: int,
        since: datetime | None = None,
        tier: int | None = None,
    ) -> AuditPage:
        """Query the real audit_events table (safety.audit.AuditLog.record()
        is the single writer — see infra/db.py migration 001).

        BUG FIX: this previously returned a hardcoded empty page. The
        `project_audit` projection already existed and was imported but
        never called.

        `task_id` is resolved to a correlation_id via a tasks lookup because
        audit_events only stores correlation_id, not task_id, at write time
        (see AuditLog.record()'s INSERT columns). `execution_id` has no
        backing column anywhere in the schema today — it is accepted for
        API-contract stability but intentionally not filtered on; adding
        real support requires a schema change, which is out of scope here.
        """
        effective_correlation_id = correlation_id
        if task_id and not effective_correlation_id:
            cur = await self._atlas.db.conn.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,))
            row = await cur.fetchone()
            if row and row["payload"]:
                try:
                    effective_correlation_id = json.loads(row["payload"]).get("correlation_id")
                except json.JSONDecodeError:
                    effective_correlation_id = None
            if not effective_correlation_id:
                # Task exists but has no resolvable correlation_id, or the
                # task itself doesn't exist — either way, no events to show.
                return AuditPage(items=tuple(), next_cursor=None)

        query = "SELECT * FROM audit_events WHERE 1=1"
        params: list[str] = []
        if effective_correlation_id:
            query += " AND correlation_id = ?"
            params.append(effective_correlation_id)
        if cursor:
            query += " AND id < ?"
            params.append(cursor)
        if since is not None:
            # SQLite datetime normalizes stored Z/UTC/offset forms before comparison.
            query += " AND julianday(ts) >= julianday(?)"
            params.append(since.isoformat())
        if tier is not None:
            query += " AND tier = ?"
            params.append(str(tier))
        query += " ORDER BY id DESC LIMIT ?"
        params.append(str(limit + 1))

        cur = await self._atlas.db.conn.execute(query, tuple(params))
        rows: list[Any] = list(await cur.fetchall())

        items = [project_audit(dict(row)) for row in rows[:limit]]
        next_cursor = str(rows[limit - 1]["id"]) if len(rows) > limit else None
        return AuditPage(items=tuple(items), next_cursor=next_cursor)

    async def export_audit(self, *, since: datetime | None, tier: int | None, max_events: int) -> AuditExport:
        # Bound the work independently of transport validation. Fetching one extra
        # row distinguishes a complete export from truncation at the exact boundary.
        max_events = max(1, min(max_events, 1000))
        page = await self.audit(
            task_id=None,
            correlation_id=None,
            execution_id=None,
            cursor=None,
            limit=max_events,
            since=since,
            tier=tier,
        )
        return AuditExport(
            items=page.items,
            generated_at=datetime.now(UTC),
            filters=AuditFilters(since=since, tier=tier),
            max_events=max_events,
            truncated=page.next_cursor is not None,
        )

    async def audit_event(self, event_id: str) -> AuditEventView:
        raise NotFoundError(f"Event not found: {event_id}")
