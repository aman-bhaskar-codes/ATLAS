"""Notification + delivery status with legal transitions (mirrors P4 task SM)."""

from __future__ import annotations

from enum import StrEnum


class NotificationStatus(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    ROUTED = "routed"
    QUEUED = "queued"
    DIGEST_BATCHED = "digest_batched"
    DISPATCHING = "dispatching"
    DELIVERED = "delivered"
    RETRYING = "retrying"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_TERMINAL = {
    NotificationStatus.DELIVERED,
    NotificationStatus.DEAD_LETTER,
    NotificationStatus.EXPIRED,
    NotificationStatus.CANCELLED
}


def is_terminal(s: NotificationStatus) -> bool:
    return s in _TERMINAL
