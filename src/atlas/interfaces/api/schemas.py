"""Pydantic schemas for the ATLAS FastAPI transport layer.

All response models carry schema_version for forward compatibility.
All mutation requests carry an idempotency_key.
Never return raw exception text, credentials, or provider payloads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    source: Literal["cli", "file", "whatsapp", "api", "scheduler", "system", "voice"]
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
    exact_preview: str = ""
    policy_version: str = "1"
    created_at: datetime
    decision_source: Literal["dashboard", "telegram", "cli"] | None = None
    code_required: bool = False
    confirmation_code: str | None = None


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
    type: Literal["image", "pdf", "code", "markdown", "file"]


class CreateTaskRequest(BaseModel):
    request: str = Field(max_length=20_000)
    source: Literal["api"] = "api"
    idempotency_key: str = Field(min_length=16)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_input(self) -> CreateTaskRequest:
        if not self.request.strip() and not self.attachments:
            raise ValueError("A request or attachment is required")
        return self


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
    idempotency_key: str = Field(min_length=16, max_length=200)
    confirmation_code: str | None = Field(default=None, pattern=r"^[0-9]{4}$", min_length=4, max_length=4)


class ErrorEnvelope(BaseModel):
    """Stable error shape. Never include raw exception text or stack traces."""

    error: ErrorDetail


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool = False
    detail: Any = None


class LaunchPlatformRequest(BaseModel):
    parent_task_id: str | None = None
    target: Literal["research", "ide"]
    workspace_name: str | None = None
    initial_query: str | None = None


class LaunchPlatformResponse(BaseModel):
    task_id: str
    workspace_id: str
    handoff_token: str
    redirect_path: str
    platform: str


class HandoffContextResponse(BaseModel):
    token: str
    task_id: str
    workspace_id: str
    platform: str
    parent_task_id: str | None = None
    parent_context: dict[str, Any] | None = None
    created_ts: str

