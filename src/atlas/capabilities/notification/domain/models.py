"""Notification domain models — strongly typed, provider-neutral.

WHY frozen models: a Notification flows through many stages (route/queue/dispatch)
and, for the queue's durability, is serialized. Immutability makes stage
transitions explicit and safe. Provider payloads NEVER appear here.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, Field

from atlas.infra.ids import CorrelationId


class NotificationPriority(IntEnum):
    LOW = 0        # Tier-0 routine
    NORMAL = 1     # Tier-1 notify
    HIGH = 2       # Tier-2 confirm/approve
    CRITICAL = 3   # Tier-3 safety


class NotificationKind(StrEnum):
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    WARNING = "warning"
    REMINDER = "reminder"
    APPROVAL = "approval"
    SAFETY_ALERT = "safety_alert"
    RESEARCH_SUMMARY = "research_summary"
    AI_NEWS = "ai_news"
    SYSTEM_HEALTH = "system_health"


class Channel(BaseModel):
    model_config = {"frozen": True}
    name: str                       # 'telegram:primary', 'ntfy:atlas', 'desktop'
    provider: str                   # provider adapter name
    address: str                    # chat id / topic / webhook / email
    priority_floor: NotificationPriority = NotificationPriority.LOW


class Notification(BaseModel):
    model_config = {"frozen": True}
    id: str
    correlation_id: CorrelationId
    kind: NotificationKind
    priority: NotificationPriority
    title: str
    body: str
    urgent: bool = False            # Tier-1 escape hatch from digest
    template: str | None = None
    template_vars: dict[str, Any] = Field(default_factory=dict)
    channels: tuple[str, ...] = ()  # explicit channel names; empty = router decides
    dedup_key: str | None = None
    expires_at: datetime | None = None
    deliver_in_digest: bool = False
    created_ts: datetime


class DeliveryAttempt(BaseModel):
    model_config = {"frozen": True}
    attempt: int
    provider: str
    channel: str
    ts: datetime
    ok: bool
    latency_ms: int
    error: str | None = None


class DeliveryReceipt(BaseModel):
    model_config = {"frozen": True}
    notification_id: str
    delivered: bool
    provider: str
    channel: str
    attempts: tuple[DeliveryAttempt, ...] = ()
    final_error: str | None = None


class ApprovalRequest(BaseModel):
    model_config = {"frozen": True}
    id: str
    correlation_id: CorrelationId
    prompt: str
    detail: str
    timeout_s: float = 300.0
    default_on_timeout: bool = False   # deny by default (safety)


class ApprovalDecision(BaseModel):
    model_config = {"frozen": True}
    request_id: str
    approved: bool
    decided_ts: datetime
    timed_out: bool = False
