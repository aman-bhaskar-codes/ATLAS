"""Approval queues cannot invalidate or consume another live HTTP owner's tickets."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.infra.types import SafetyDecision, Tier, ToolRequest
from atlas.safety.approvals import ApprovalConflict, ApprovalQueue
from tests.fakes import FakeClock
from tests.safety.test_approval_queue import wait_pending


@pytest.fixture
async def owners(tmp_path: Path) -> AsyncIterator[tuple[Database, Database]]:
    one, two = Database(tmp_path / "approvals.db"), Database(tmp_path / "approvals.db")
    await one.start()
    await two.start()
    try:
        yield one, two
    finally:
        await two.stop()
        await one.stop()


def confirmation(queue: ApprovalQueue, cid: str) -> asyncio.Task[bool]:
    return asyncio.create_task(
        queue.confirm(
            "",
            SafetyDecision(decision="require_confirm", tier=Tier.CONFIRM, reason="controlled approval"),
            ToolRequest(correlation_id=cid, tool="fs", operation="write"),
        )
    )


async def test_second_live_queue_cannot_expire_first_owner_tickets(owners: tuple[Database, Database]) -> None:
    one, two = owners
    clock = FakeClock(datetime.now(UTC))
    first, second = ApprovalQueue(one, clock), ApprovalQueue(two, clock)
    await first.start()
    wait = confirmation(first, "first")
    try:
        ticket = await wait_pending(first)
        with pytest.raises(ApprovalConflict, match="owner"):
            await second.start()
        assert (await first.get(ticket.id)).status == "pending" and not wait.done()
        await second.close()
        assert (await first.get(ticket.id)).status == "pending" and not wait.done()
    finally:
        await first.close()
        await second.close()
        assert await wait is False


async def test_stale_queue_close_and_decision_cannot_touch_new_owner(owners: tuple[Database, Database]) -> None:
    one, two = owners
    instant = datetime.now(UTC)
    clock = FakeClock(instant)
    first, second = ApprovalQueue(one, clock), ApprovalQueue(two, clock)
    await first.start()
    old_wait = confirmation(first, "old")
    old_ticket = await wait_pending(first)
    # Simulate a crashed owner's expired lease without advancing approval expiry.
    await one.conn.execute(
        "UPDATE runtime_execution_leases SET lease_until=? WHERE resource_id='apiapprovals'",
        ((instant - timedelta(seconds=1)).isoformat(),),
    )
    await one.conn.commit()
    await second.start()
    new_wait = confirmation(second, "new")
    try:
        new_ticket = await wait_pending(second)
        with pytest.raises(ApprovalConflict):
            await first.decide(old_ticket.id, "approve", "stale-decision-key-123")
        await first.close()
        assert (await second.get(new_ticket.id)).status == "pending" and not new_wait.done()
        await second.decide(new_ticket.id, "approve", "new-owner-decision-123")
        assert await new_wait is True and await old_wait is False
    finally:
        await first.close()
        await second.close()
        await asyncio.gather(old_wait, new_wait, return_exceptions=True)


async def test_queue_renewal_loss_denies_waiter_without_mutating_new_tickets(owners: tuple[Database, Database]) -> None:
    one, two = owners
    instant = datetime.now(UTC)
    clock = FakeClock(instant)
    first = ApprovalQueue(one, clock, lease_ttl_s=0.3, lease_renewal_s=0.01)
    second = ApprovalQueue(two, clock)
    await first.start()
    old_wait = confirmation(first, "old")
    await wait_pending(first)
    await two.conn.execute(
        "UPDATE runtime_execution_leases SET lease_until=? WHERE resource_id='apiapprovals'",
        ((instant - timedelta(seconds=1)).isoformat(),),
    )
    await two.conn.commit()
    await second.start()
    new_wait = confirmation(second, "new")
    try:
        ticket = await wait_pending(second)
        assert await asyncio.wait_for(old_wait, timeout=0.3) is False
        assert (
            await first.confirm(
                "",
                SafetyDecision(decision="require_confirm", tier=Tier.CONFIRM, reason="closed"),
                ToolRequest(correlation_id="after-loss", tool="fs", operation="write"),
            )
            is False
        )
        assert (await second.get(ticket.id)).status == "pending" and not new_wait.done()
    finally:
        await first.close()
        await second.close()
        await asyncio.gather(old_wait, new_wait, return_exceptions=True)
