import asyncio
from datetime import UTC, datetime

import pytest

from atlas.infra.types import SafetyDecision, Tier, ToolRequest
from atlas.safety.approvals import ApprovalCodeError, ApprovalConflict, ApprovalQueue
from tests.fakes import FakeClock


async def wait_pending(queue):
    for _ in range(100):
        rows = await queue.pending()
        if rows:
            return rows[0]
        await asyncio.sleep(0.001)
    raise AssertionError("approval not queued")


@pytest.mark.asyncio
async def test_durable_decision_single_waiter_and_replay(memory_db):
    queue = ApprovalQueue(memory_db, FakeClock(datetime.now(UTC)))
    await queue.start()
    wait = asyncio.create_task(
        queue.confirm(
            "secret dry-run token=raw-secret",
            SafetyDecision(decision="require_confirm", tier=Tier.CONFIRM, reason="raw-secret"),
            ToolRequest(correlation_id="cid", tool="fs", operation="write", args={"token": "raw-secret"}),
        )
    )
    ticket = await wait_pending(queue)
    assert await queue.count_pending() == 1
    assert "raw-secret" not in ticket.model_dump_json()
    row = await (await memory_db.conn.execute("SELECT * FROM approval_tickets")).fetchone()
    assert "raw-secret" not in str(dict(row))
    result = await queue.decide(ticket.id, "approve", "idempotency-key-1234")
    assert result.status == "approved" and await wait is True
    assert await queue.count_pending() == 0
    assert (await queue.decide(ticket.id, "approve", "idempotency-key-1234")).status == "approved"
    with pytest.raises(ApprovalConflict):
        await queue.decide(ticket.id, "deny", "other-idempotency-key")
    await queue.close()


@pytest.mark.asyncio
async def test_dangerous_requires_last_engine_challenge(memory_db):
    queue = ApprovalQueue(memory_db, FakeClock(datetime.now(UTC)))
    await queue.start()
    wait = asyncio.create_task(
        queue.confirm(
            "Type confirmation code [1111]\nType confirmation code [4321] to proceed.",
            SafetyDecision(decision="require_confirm", tier=Tier.DANGEROUS, reason="danger"),
            ToolRequest(correlation_id="cid", tool="fs", operation="delete"),
        )
    )
    ticket = await wait_pending(queue)
    assert ticket.confirmation_code == "4321"
    for bad in [None, "y", "1111", "\uff11\uff12\uff13\uff14"]:
        with pytest.raises(ApprovalCodeError):
            await queue.decide(ticket.id, "approve", "idempotency-key-1234", bad)
    assert not wait.done()
    await queue.decide(ticket.id, "approve", "idempotency-key-1234", "4321")
    assert await wait is True
    await queue.close()


@pytest.mark.asyncio
async def test_expiry_cancellation_restart_fail_closed(memory_db):
    clock = FakeClock(datetime.now(UTC))
    queue = ApprovalQueue(memory_db, clock, timeout_s=0.04)
    await queue.start()
    decision = SafetyDecision(decision="require_confirm", tier=Tier.CONFIRM, reason="confirm")
    req = ToolRequest(correlation_id="cid", tool="fs", operation="write")
    wait = asyncio.create_task(queue.confirm("", decision, req))
    ticket = await wait_pending(queue)
    assert await wait is False
    assert (await queue.get(ticket.id)).status == "expired"
    wait = asyncio.create_task(queue.confirm("", decision, req))
    ticket = await wait_pending(queue)
    wait.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wait
    assert (await queue.get(ticket.id)).status == "expired"
    wait = asyncio.create_task(queue.confirm("", decision, req))
    ticket = await wait_pending(queue)
    restarted = ApprovalQueue(memory_db, clock)
    with pytest.raises(ApprovalConflict, match="owner"):
        await restarted.start()
    # A second live HTTP queue is not a restart and cannot invalidate the waiter.
    assert (await queue.get(ticket.id)).status == "pending" and not wait.done()
    await queue.close()
    await restarted.start()
    assert (await restarted.get(ticket.id)).status == "expired"
    assert await wait is False
    await restarted.close()
    await queue.close()


@pytest.mark.asyncio
async def test_block_never_enqueues_and_race_has_one_winner(memory_db):
    queue = ApprovalQueue(memory_db, FakeClock(datetime.now(UTC)))
    await queue.start()
    req = ToolRequest(correlation_id="cid", tool="fs", operation="delete")
    assert (
        await queue.confirm("", SafetyDecision(decision="require_confirm", tier=Tier.BLOCK, reason="block"), req)
        is False
    )
    assert await queue.pending() == []
    wait = asyncio.create_task(
        queue.confirm("", SafetyDecision(decision="require_confirm", tier=Tier.CONFIRM, reason="confirm"), req)
    )
    ticket = await wait_pending(queue)
    results = await asyncio.gather(
        queue.decide(ticket.id, "approve", "race-idempotency-key1"),
        queue.decide(ticket.id, "deny", "race-idempotency-key2"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ApprovalConflict) for r in results) == 1
    assert await wait is True
    await queue.close()


@pytest.mark.asyncio
async def test_sanitized_preview_keeps_action_context_not_credentials(memory_db):
    from atlas.safety.approvals import safe_preview

    req = ToolRequest(
        correlation_id="safe-cid",
        tool="shell",
        operation="exec",
        args={
            "command": 'curl https://user:pass@host/path?token=custom-secret -H "Authorization: Bearer custom-secret"',
            "path": "/tmp/output.txt",
            "recipient": "owner@example.test",
            "token": "custom-secret",
            "headers": {"api_key": "header-secret"},
            "content": "private file body",
        },
    )
    preview = safe_preview(req)
    assert "/tmp/output.txt" in preview and "owner@example.test" in preview and "curl" in preview
    assert all(value not in preview for value in ("user:pass", "custom-secret", "header-secret", "private file body"))


@pytest.mark.asyncio
async def test_cli_dangerous_cannot_use_yes_or_wrong_code(monkeypatch):
    from atlas.interfaces.notify import CliConfirmer

    confirmer = CliConfirmer(allow_stdin=True)
    decision = SafetyDecision(decision="require_confirm", tier=Tier.DANGEROUS, reason="high-impact")
    req = ToolRequest(correlation_id="cli-cid", tool="fs", operation="delete")
    prompt = "Type confirmation code [8765] to proceed."
    for answer in ["y", "yes", "0000"]:
        monkeypatch.setattr("builtins.input", lambda _, reply=answer: reply)
        assert await confirmer.confirm(prompt, decision, req) is False
    monkeypatch.setattr("builtins.input", lambda _: "8765")
    assert await confirmer.confirm(prompt, decision, req) is True


@pytest.mark.asyncio
async def test_run_cancellation_rejects_late_approvals_and_later_confirmations(memory_db):
    queue = ApprovalQueue(memory_db, FakeClock(datetime.now(UTC)))
    await queue.start()
    req = ToolRequest(correlation_id="cancelled-run", tool="fs", operation="write")
    decision = SafetyDecision(decision="require_confirm", tier=Tier.CONFIRM, reason="confirm")
    wait = asyncio.create_task(queue.confirm("", decision, req))
    ticket = await wait_pending(queue)
    assert await queue.cancel(req.correlation_id) == 1
    assert await wait is False
    with pytest.raises(ApprovalConflict):
        await queue.decide(ticket.id, "approve", "late-idempotency-key1")
    assert await queue.confirm("", decision, req) is False
    assert await queue.pending() == []
    await queue.close()
