"""Pydantic schemas for the ATLAS FastAPI transport layer.

All response models carry schema_version for forward compatibility.
All mutation requests carry an idempotency_key.
Never return raw exception text, credentials, or provider payloads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RuntimeStatusResponse(BaseModel):
    schema_version: int = 1
    state: Literal["starting", "ready", "degraded", "stopping", "stopped"]
    version: str
    environment: str
    kill_switch_active: bool
    active_task_count: int
    pending_approval_count: int
    last_audit_at: datetime | None


class HealthCheckItem(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str
    checked_at: datetime


class RuntimeHealthResponse(BaseModel):
    schema_version: int = 1
    overall: Literal["healthy", "degraded", "unavailable"]
    checks: list[HealthCheckItem]


class TaskResponse(BaseModel):
    schema_version: int = 1
    id: str
    correlation_id: str
    source: Literal["cli", "file", "whatsapp", "api", "scheduler", "system"]
    request: str
    state: Literal[
        "created",
        "ready",
        "building_context",
        "planning",
        "reasoning",
        "waiting_tool",
        "executing",
        "observing",
        "completed",
        "failed",
        "cancelled",
    ]
    ok: bool | None
    answer: str | None
    error: str | None
    steps_taken: int
    created_at: datetime
    updated_at: datetime


class TaskEventResponse(BaseModel):
    schema_version: int = 1
    event_id: str
    event_type: str
    ts: datetime
    task_id: str
    correlation_id: str
    execution_id: str | None
    # sequence enables client-side gap detection per the Phase Two spec
    sequence: int
    state: str
    summary: str
    capability: str | None
    operation: str | None
    provider: str | None
    tier: int | None = None
    requires_approval: bool = False
    # safe_metadata: scrubbed key/value pairs allowed for display, never raw payloads
    safe_metadata: dict[str, str] = Field(default_factory=dict)


class ApprovalResponse(BaseModel):
    schema_version: int = 1
    id: str
    task_id: str | None
    correlation_id: str
    execution_id: str | None
    capability: str
    operation: str
    tier: int
    prompt: str
    preview: str
    warnings: list[str]
    expires_at: datetime
    status: Literal["pending", "approved", "denied", "expired"]


class CapabilityResponse(BaseModel):
    schema_version: int = 1
    name: str
    state: Literal["ready", "degraded", "unavailable", "planned"]
    operations: list[str]
    providers: int
    healthy_providers: int
    requires_auth: bool


class AttachmentRef(BaseModel):
    id: str
    type: str


class CreateTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    source: Literal["api"] = "api"
    idempotency_key: str = Field(min_length=16)
    attachments: list[AttachmentRef] = Field(default_factory=list)


class CancelTaskRequest(BaseModel):
    request_id: str = Field(default="", description="Caller-assigned request id for tracing")
    idempotency_key: str = Field(min_length=16)
    reason: Literal["user_requested"] = "user_requested"


class CancelTaskResponse(BaseModel):
    schema_version: int = 1
    task_id: str
    accepted: bool
    state: str
    message: str


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "deny"]
    idempotency_key: str = Field(min_length=16)


class ErrorEnvelope(BaseModel):
    """Stable error shape. Never include raw exception text or stack traces."""

    error: ErrorDetail


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool = False
    detail: Any = None
