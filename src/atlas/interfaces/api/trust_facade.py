"""Implementation of the Trust Center plane."""
from __future__ import annotations

from collections.abc import Sequence

from atlas.app import Atlas
from atlas.interfaces.api.control_plane import AtlasTrustPlane
from atlas.interfaces.api.idempotency import IdempotencyConflict, IdempotencyStore
from atlas.interfaces.api.projections import project_audit, project_task
from atlas.interfaces.api.schemas_trust import (
    ApprovalDecisionCommand, ApprovalView, AuditPage, MemoryCorrectionCommand,
    MemoryFactView, MemoryMutationReceipt, ProvenanceView, TaskPage, TaskView,
)


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
        rows = await cur.fetchall()

        items = [project_task(dict(row)) for row in rows[:limit]]
        next_cursor = items[-1].updated_at.isoformat() if len(rows) > limit else None

        return TaskPage(items=tuple(items), next_cursor=next_cursor)

    async def get_task(self, task_id: str) -> TaskView:
        cur = await self._atlas.db.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        if not row:
            raise KeyError(f"Task not found: {task_id}")
        return project_task(dict(row))

    async def pending_approvals(self) -> Sequence[ApprovalView]:
        return []  # Phase 4

    async def get_approval(self, approval_id: str) -> ApprovalView:
        raise KeyError(f"Approval not found: {approval_id}")

    async def decide_approval(self, command: ApprovalDecisionCommand) -> ApprovalView:
        raise NotImplementedError()

    async def search_memory(self, query: str, *, limit: int) -> Sequence[MemoryFactView]:
        return []

    async def get_memory_fact(self, fact_id: str) -> MemoryFactView:
        raise KeyError(f"Fact not found: {fact_id}")

    async def memory_provenance(self, fact_id: str) -> Sequence[ProvenanceView]:
        return []

    async def correct_memory(self, command: MemoryCorrectionCommand) -> MemoryMutationReceipt:
        raise NotImplementedError()

    async def supersede_memory(self, fact_id: str, *, idempotency_key: str, request_id: str) -> MemoryMutationReceipt:
        raise NotImplementedError()

    async def delete_memory(self, fact_id: str, *, idempotency_key: str, request_id: str) -> MemoryMutationReceipt:
        raise NotImplementedError()

    async def audit(
        self, *, task_id: str | None, correlation_id: str | None,
        execution_id: str | None, cursor: str | None, limit: int,
    ) -> AuditPage:
        return AuditPage(items=tuple(), next_cursor=None)

    async def audit_event(self, event_id: str) -> AuditEventView:
        raise KeyError(f"Event not found: {event_id}")
