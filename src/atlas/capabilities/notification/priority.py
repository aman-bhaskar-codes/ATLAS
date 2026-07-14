"""Notification priority engine — notification kind -> priority + routing behavior.

WHY centralize: the mapping from 'what happened' to 'how loud' must be one table,
not scattered. Safety alerts are CRITICAL/multi-channel; task-complete is LOW/
single-channel/no-retry; approvals are HIGH with a dedicated channel + timeout.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.capabilities.notification.domain.models import NotificationKind, NotificationPriority


@dataclass(frozen=True)
class RoutingBehavior:
    priority: NotificationPriority
    multi_channel: bool
    retry: bool
    digest_default: bool


_BEHAVIOR: dict[NotificationKind, RoutingBehavior] = {
    NotificationKind.SAFETY_ALERT: RoutingBehavior(NotificationPriority.CRITICAL, True, True, False),
    NotificationKind.APPROVAL: RoutingBehavior(NotificationPriority.HIGH, False, True, False),
    NotificationKind.TASK_FAILED: RoutingBehavior(NotificationPriority.NORMAL, False, True, False),
    NotificationKind.WARNING: RoutingBehavior(NotificationPriority.NORMAL, False, False, False),
    NotificationKind.REMINDER: RoutingBehavior(NotificationPriority.NORMAL, False, False, False),
    NotificationKind.TASK_COMPLETE: RoutingBehavior(NotificationPriority.LOW, False, False, True),
    NotificationKind.RESEARCH_SUMMARY: RoutingBehavior(NotificationPriority.LOW, False, False, True),
    NotificationKind.AI_NEWS: RoutingBehavior(NotificationPriority.LOW, False, False, True),
    NotificationKind.SYSTEM_HEALTH: RoutingBehavior(NotificationPriority.LOW, False, False, True),
}


class PriorityEngine:
    def behavior(self, kind: NotificationKind) -> RoutingBehavior:
        return _BEHAVIOR.get(kind, RoutingBehavior(NotificationPriority.NORMAL, False, True, False))
