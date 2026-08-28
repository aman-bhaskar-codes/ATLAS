"""Safe, versioned read models for the Trust Center.

These schemas intentionally do not expose internal task payloads, model prompts,
provider responses, secrets, or raw audit arguments.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SafeError(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    message: str
    retryable: bool = False


class TaskView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    correlation_id: str
    source: Literal["cli", "file", "whatsapp", "api", "scheduler", "system", "voice"]
    request: str
    state: str
    ok: bool | None = None
    answer: str | None = None
    error: SafeError | None = None
    created_at: datetime
    updated_at: datetime
    duration_ms: int | None = None
    steps_taken: int = 0
    approval_count: int = 0
    artifact_count: int = 0
    memory_write_count: int = 0
    retryability: Literal["safe", "unsafe", "unknown"] = "unknown"


class TaskPage(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[TaskView, ...]
    next_cursor: str | None = None


class ApprovalView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    task_id: str | None = None
    execution_id: str | None = None
    capability: str
    operation: str
    tier: int
    prompt: str
    exact_preview: str
    warnings: tuple[str, ...] = ()
    policy_version: str
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "approved", "denied", "expired"]
    decision_source: Literal["dashboard", "telegram", "cli"] | None = None


class MemoryFactView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    version: int
    text: str
    kind: str
    confidence: float
    salience: float
    created_at: datetime
    updated_at: datetime
    superseded_by: str | None = None
    provenance_count: int = 0
    status: Literal["active", "superseded", "deleted"] = "active"


class ProvenanceView(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_type: Literal["episode", "task", "user_edit", "external"]
    source_id: str
    summary: str
    captured_at: datetime
    provider: str | None = None
    confidence: float | None = None


class AuditEventView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    ts: datetime
    actor: str
    action: str
    tool: str | None = None
    capability: str | None = None
    tier: int | None = None
    decision: str | None = None
    outcome: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None
    execution_id: str | None = None
    redaction: Literal["none", "partial", "full"] = "partial"
    safe_payload_summary: str | None = None


class AuditPage(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[AuditEventView, ...]
    next_cursor: str | None = None


class ApprovalDecisionCommand(BaseModel):
    approval_id: str
    decision: Literal["approve", "deny"]
    idempotency_key: str = Field(min_length=16, max_length=200)
    request_id: str = Field(min_length=8, max_length=200)


class MemoryCorrectionCommand(BaseModel):
    fact_id: str
    replacement_text: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str = Field(min_length=16, max_length=200)
    request_id: str = Field(min_length=8, max_length=200)


class MemoryMutationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)
    accepted: bool
    fact: MemoryFactView
    request_id: str
    idempotency_key: str
