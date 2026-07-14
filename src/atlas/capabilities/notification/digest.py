"""Digest engine — aggregate batched items into one summary per window.

WHY reusable: Reflection (P8), Evaluation (P10), and knowledge (6.3 AI-news) push
into digest CATEGORIES instead of pinging directly. The engine drains the digest
queue, groups by category, and optionally summarizes prose via the P5 gateway,
then emits ONE notification. Deterministic windows come from the Scheduler.
"""

from __future__ import annotations

from collections import defaultdict

from atlas.capabilities.notification.domain.models import (
    Notification,
    NotificationKind,
    NotificationPriority,
)
from atlas.capabilities.notification.queue import NotificationQueue
from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway


class DigestEngine:
    def __init__(self, *, queue: NotificationQueue, gateway: ModelGateway,
                 ids: IdGenerator, clock: Clock) -> None:
        self._queue = queue
        self._gw = gateway
        self._ids = ids
        self._clock = clock

    async def build(self, correlation_id: str) -> Notification | None:
        batched = await self._queue.claim_ready(digest=True, limit=200)
        if not batched:
            return None
            
        groups: dict[str, list[str]] = defaultdict(list)
        for n in batched:
            groups[n.kind.value].append(f"{n.title}: {n.body[:160]}")
            
        sections = []
        for kind, items in groups.items():
            sections.append(f"## {kind} ({len(items)})\n" + "\n".join(f"- {i}" for i in items[:20]))
            
        raw = "\n\n".join(sections)
        
        # optional: summarize prose-heavy categories via P5
        resp = await self._gw.complete(ModelRequest(
            correlation_id=correlation_id, system="Summarize this digest crisply, keep all headers.",
            prompt=raw, max_tokens=600))
            
        for n in batched:
            await self._queue.complete(n.id)
            
        return Notification(
            id=self._ids.execution_id(), correlation_id=correlation_id,
            kind=NotificationKind.SYSTEM_HEALTH, priority=NotificationPriority.LOW,
            title=f"ATLAS digest ({len(batched)} items)", body=resp.text,
            created_ts=self._clock.now())
