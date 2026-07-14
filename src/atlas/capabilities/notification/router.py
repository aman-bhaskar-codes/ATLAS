"""Notification router — decide channels + immediacy.

WHY: given a notification, pick channels (explicit > kind-preferred > default) and
ask the quiet-hours engine whether to interrupt or digest. Never talks to a
provider; outputs a RoutingDecision the dispatcher/queue act on.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.capabilities.notification.domain.models import Notification
from atlas.capabilities.notification.priority import PriorityEngine
from atlas.capabilities.notification.quiet_hours import QuietHoursEngine
from atlas.capabilities.notification.resolver import ChannelResolver


@dataclass(frozen=True)
class RoutingDecision:
    channels: tuple[str, ...]
    interrupt_now: bool
    multi_channel: bool
    retry: bool


class NotificationRouter:
    def __init__(self, *, priority: PriorityEngine, quiet: QuietHoursEngine,
                 channels: ChannelResolver) -> None:
        self._priority = priority
        self._quiet = quiet
        self._channels = channels

    def route(self, n: Notification) -> RoutingDecision:
        behavior = self._priority.behavior(n.kind)
        chosen = self._channels.resolve(n, multi=behavior.multi_channel)
        interrupt = self._quiet.should_interrupt(n) and not n.deliver_in_digest
        return RoutingDecision(channels=chosen, interrupt_now=interrupt,
                               multi_channel=behavior.multi_channel, retry=behavior.retry)
