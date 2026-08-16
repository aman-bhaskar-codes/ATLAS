"""Safe projections. Internal records are never returned directly."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, cast

from atlas.interfaces.api.schemas_trust import AuditEventView, SafeError, TaskView


def safe_error(exc: Exception) -> SafeError:
    code = getattr(exc, "code", "runtime_error")
    retryable = bool(getattr(exc, "retryable", False))
    return SafeError(code=str(code), message="The operation could not be completed.", retryable=retryable)


def project_task(record: dict[str, Any]) -> TaskView:
    """Build a TaskView from a raw `tasks` row.

    BUG FIX: the `tasks` table (see infra/db.py migration 002) only has
    columns id/source/state/payload/idempotency_key/attempts/not_before/
    created_ts/updated_ts. Everything else — correlation_id, request,
    answer, error, steps_taken — is JSON-encoded inside `payload`, exactly
    as `facade.py::_row_to_task` already decodes it. This function used to
    read those as flat dict keys that never existed on the row, so every
    field below silently fell back to its default on every request.
    """
    raw_payload = record.get("payload")
    payload: dict[str, Any] = {}
    if isinstance(raw_payload, str) and raw_payload:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            payload = {}

    error = payload.get("error")

    # NOTE: duration_ms, approval_count, artifact_count, memory_write_count,
    # and retryability are not computed or written anywhere in the
    # orchestrator/safety pipeline today (verified: no writer exists for
    # these fields on a task record). They correctly default below rather
    # than fabricate a value — wiring real values requires instrumenting
    # the orchestrator to persist these onto the task payload on each
    # state transition, which is separate feature work, not this bug fix.
    return TaskView(
        id=str(record["id"]),
        correlation_id=str(payload.get("correlation_id", "")),
        source=cast(Literal["cli", "file", "whatsapp", "api", "scheduler", "system"], record.get("source", "api")),
        request=str(payload.get("request", "")),
        state=str(record.get("state", "failed")),
        ok=payload.get("ok"),
        answer=str(payload["answer"]) if payload.get("answer") else None,
        error=SafeError(code="task_failed", message=str(error), retryable=False) if error else None,
        created_at=_as_datetime(record["created_ts"]),
        updated_at=_as_datetime(record["updated_ts"]),
        duration_ms=payload.get("duration_ms"),
        steps_taken=int(payload.get("steps_taken", 0)),
        approval_count=int(payload.get("approval_count", 0)),
        artifact_count=int(payload.get("artifact_count", 0)),
        memory_write_count=int(payload.get("memory_write_count", 0)),
        retryability=cast(Literal["safe", "unsafe", "unknown"], payload.get("retryability", "unknown")),
    )


def project_audit(record: dict[str, Any]) -> AuditEventView:
    return AuditEventView(
        id=str(record["id"]),
        ts=_as_datetime(record["ts"]),
        actor=str(record.get("actor", "unknown")),
        action=str(record.get("action", "unknown")),
        tool=record.get("tool"),
        capability=record.get("capability"),
        tier=record.get("tier"),
        decision=record.get("decision"),
        outcome=record.get("outcome"),
        task_id=record.get("task_id"),
        correlation_id=record.get("correlation_id"),
        execution_id=record.get("execution_id"),
        redaction=cast(Literal["none", "partial", "full"], record.get("redaction", "partial")),
        safe_payload_summary=str(record["safe_payload_summary"]) if record.get("safe_payload_summary") else None,
    )


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
