"""Notification Platform — the one facade every subsystem calls.

notify(): route -> (interrupt: dispatch now | batch: enqueue to digest). Returns a
receipt or a queued marker. request_approval(): delegate to ApprovalRequestManager.
Every send flows the full pipeline; nothing bypasses it. This facade is what the
Safety Engine, Planner, Reflection, Supervisor, etc. depend on — never a provider.
"""

from __future__ import annotations

from atlas.capabilities.notification.approval import ApprovalRequestManager
from atlas.capabilities.notification.dispatcher import NotificationDispatcher
from atlas.capabilities.notification.domain.models import (
    ApprovalDecision,
    ApprovalRequest,
    DeliveryReceipt,
    Notification,
)
from atlas.capabilities.notification.queue import NotificationQueue
from atlas.capabilities.notification.router import NotificationRouter


class NotificationPlatform:
    def __init__(self, *, router: NotificationRouter, dispatcher: NotificationDispatcher,
                 queue: NotificationQueue, approvals: ApprovalRequestManager) -> None:
        self._router = router
        self._dispatcher = dispatcher
        self._queue = queue
        self._approvals = approvals

    async def notify(self, n: Notification) -> DeliveryReceipt | None:
        decision = self._router.route(n)
        if not decision.interrupt_now:
            await self._queue.enqueue(n, digest=True)   # batched into digest
            return None
        return await self._dispatcher.deliver(
            n, decision.channels, multi=decision.multi_channel, retry=decision.retry)

    async def request_approval(self, req: ApprovalRequest,
                               channels: tuple[str, ...]) -> ApprovalDecision:
        return await self._approvals.request(req, channels)
