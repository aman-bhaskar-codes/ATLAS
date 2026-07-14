"""Approval request manager — TRANSPORT ONLY.

WHY it never decides: the platform sends the approval + awaits a decision (via a
callback future, reusing the Phase-1 ntfy action-button path). It returns the
decision to the caller (Planner/Workflow), which decides what to DO. Timeout ->
configured default (deny for safety). This is the human-in-the-loop transport, not
the policy.
"""

from __future__ import annotations

import asyncio

from atlas.capabilities.notification.dispatcher import NotificationDispatcher
from atlas.capabilities.notification.domain.models import (
    ApprovalDecision,
    ApprovalRequest,
    Notification,
    NotificationKind,
    NotificationPriority,
)
from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator


class ApprovalRequestManager:
    def __init__(self, *, dispatcher: NotificationDispatcher, ids: IdGenerator,
                 clock: Clock, callback_base: str) -> None:
        self._dispatcher = dispatcher
        self._ids = ids
        self._clock = clock
        self._cb = callback_base.rstrip("/")
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def request(self, req: ApprovalRequest, channels: tuple[str, ...]) -> ApprovalDecision:
        fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._pending[req.id] = fut
        
        actions = (("Approve", f"{self._cb}/approve/{req.id}?d=1"),
                   ("Deny", f"{self._cb}/approve/{req.id}?d=0"))
                   
        n = Notification(
            id=self._ids.execution_id(), correlation_id=req.correlation_id,
            kind=NotificationKind.APPROVAL, priority=NotificationPriority.HIGH,
            title="Approval needed", body=f"{req.prompt}\n\n{req.detail}",
            created_ts=self._clock.now())
            
        await self._dispatcher.deliver(n, channels, multi=False, retry=True, actions=actions)
        try:
            approved = await asyncio.wait_for(fut, req.timeout_s)
            return ApprovalDecision(request_id=req.id, approved=approved, decided_ts=self._clock.now())
        except TimeoutError:
            return ApprovalDecision(request_id=req.id, approved=req.default_on_timeout,
                                    decided_ts=self._clock.now(), timed_out=True)
        finally:
            self._pending.pop(req.id, None)

    def resolve(self, request_id: str, approved: bool) -> None:   # called by comms callback (6.9)
        fut = self._pending.get(request_id)
        if fut is not None and not fut.done():
            fut.set_result(approved)
