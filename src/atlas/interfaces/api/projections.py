"""Safe projections. Internal records are never returned directly."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from atlas.interfaces.api.schemas_trust import AuditEventView, SafeError, TaskView


def safe_error(exc: Exception) -> SafeError:
    code = getattr(exc, "code", "runtime_error")
    retryable = bool(getattr(exc, "retryable", False))
    return SafeError(code=str(code), message="The operation could not be completed.", retryable=retryable)


def project_task(record: dict[str, Any]) -> TaskView:
    error = record.get("error")
    return TaskView(
        id=str(record["id"]),
        correlation_id=str(record.get("correlation_id", "")),
        request=str(record.get("request", "")),
        state=str(record.get("state", "failed")),
        answer=str(record["answer"]) if record.get("answer") else None,
        error=error if isinstance(error, SafeError) else None,
        created_at=_as_datetime(record["created_ts"]),
        updated_at=_as_datetime(record["updated_ts"]),
        duration_ms=record.get("duration_ms"),
        steps_taken=int(record.get("steps_taken", 0)),
        approval_count=int(record.get("approval_count", 0)),
        artifact_count=int(record.get("artifact_count", 0)),
        memory_write_count=int(record.get("memory_write_count", 0)),
        retryability=str(record.get("retryability", "unknown")),
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
        redaction=str(record.get("redaction", "partial")),
        safe_payload_summary=str(record["safe_payload_summary"])
        if record.get("safe_payload_summary") else None,
    )


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
