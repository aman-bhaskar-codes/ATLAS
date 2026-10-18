"""Real SQL + real SafetyEngine + real API routers; no provider or server startup."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.infra.config import SafetyCfg
from atlas.infra.types import ToolRequest
from atlas.interfaces.api.auth import require_principal
from atlas.interfaces.api.errors import register_exception_handlers
from atlas.interfaces.api.routes_approvals import router as approvals_router
from atlas.interfaces.api.routes_trust import router as trust_router
from atlas.safety.approvals import ApprovalQueue
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import DeniedError, SafetyEngine
from atlas.safety.manifest import Manifest
from atlas.safety.policy import PolicyEngine
from tests.fakes import FakeClock, FakeKillSwitch, FakeTool
from tests.safety.test_approval_queue import wait_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [2, 3])
async def test_ticket_api_readonly_code_replay_and_one_guard_execution(memory_db, tier):
    clock = FakeClock(datetime.now(UTC))
    queue = ApprovalQueue(memory_db, clock)
    await queue.start()
    manifest = Manifest(
        version=1,
        allowed_paths={},
        allowed_commands={},
        whatsapp={},
        safety={},
        rules=[{"tool": "fs", "operation": "write", "tier": tier}],
        hard_block=[],
    )
    engine = SafetyEngine(
        classifier=TierClassifier(manifest, 2),
        policy=PolicyEngine(()),
        audit=AuditLog(memory_db),
        killswitch=FakeKillSwitch(),
        clock=clock,
        cfg=SafetyCfg(),
        confirmer=queue,
    )
    tool = FakeTool()
    request = ToolRequest(
        correlation_id="approval-api-cid",
        tool="fs",
        operation="write",
        args={"path": "/tmp/reviewable.txt", "content": "never persist body", "api_key": "private-value"},
    )
    guard = asyncio.create_task(engine.guard(request, tool))
    ticket = await wait_pending(queue)
    app = FastAPI()
    app.state.atlas = SimpleNamespace(db=memory_db, approvals=queue)
    app.state.event_store = None
    app.state.api_keys = {"owner-test-key": "admin", "reader-test-key": "readonly"}
    app.include_router(approvals_router, prefix="/api/v1", dependencies=[Depends(require_principal)])
    app.include_router(trust_router, dependencies=[Depends(require_principal)])
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owner = {"Authorization": "Bearer owner-test-key"}
        reader = {"Authorization": "Bearer reader-test-key"}
        listing = await client.get("/api/v1/approvals/pending", headers=reader)
        assert listing.status_code == 200
        record = listing.json()[0]
        assert record["preview"] == record["exact_preview"]
        assert "/tmp/reviewable.txt" in record["exact_preview"]
        assert "private-value" not in listing.text and "never persist body" not in listing.text
        assert record["policy_version"] and record["created_at"]
        detail = await client.get(f"/api/v1/approvals/{ticket.id}", headers=reader)
        assert detail.status_code == 200 and detail.json()["exact_preview"] == record["preview"]
        command = {"decision": "approve", "idempotency_key": "api-idempotency-key1"}
        rejected = await client.post(f"/api/v1/approvals/{ticket.id}/decide", headers=reader, json=command)
        assert rejected.status_code == 403 and tool.calls == []
        if tier == 3:
            mismatch = await client.post(f"/api/v1/approvals/{ticket.id}/decide", headers=owner, json=command)
            assert mismatch.status_code == 422 and tool.calls == []
            command["confirmation_code"] = record["confirmation_code"]
        accepted = await client.post(f"/api/v1/approvals/{ticket.id}/decide", headers=owner, json=command)
        assert accepted.status_code == 200 and accepted.json()["status"] == "approved"
        assert (await guard).ok
        assert len(tool.calls) == 1
        replay = await client.post(f"/api/v1/approvals/{ticket.id}/decide", headers=owner, json=command)
        assert replay.status_code == 200 and len(tool.calls) == 1
        command["decision"] = "deny"
        conflict = await client.post(f"/api/v1/approvals/{ticket.id}/decide", headers=owner, json=command)
        assert conflict.status_code == 409
        assert (await client.get("/api/v1/approvals/pending", headers=owner)).json() == []
    await queue.close()


@pytest.mark.asyncio
async def test_queue_shutdown_denies_waiting_guard(memory_db):
    clock = FakeClock(datetime.now(UTC))
    queue = ApprovalQueue(memory_db, clock)
    await queue.start()
    manifest = Manifest(
        version=1,
        allowed_paths={},
        allowed_commands={},
        whatsapp={},
        safety={},
        rules=[{"tool": "fs", "operation": "write", "tier": 2}],
        hard_block=[],
    )
    tool = FakeTool()
    engine = SafetyEngine(
        classifier=TierClassifier(manifest, 2),
        policy=PolicyEngine(()),
        audit=AuditLog(memory_db),
        killswitch=FakeKillSwitch(),
        clock=clock,
        cfg=SafetyCfg(),
        confirmer=queue,
    )
    guard = asyncio.create_task(
        engine.guard(ToolRequest(correlation_id="shutdown", tool="fs", operation="write"), tool)
    )
    ticket = await wait_pending(queue)
    await queue.close()
    with pytest.raises(DeniedError):
        await guard
    assert tool.calls == []
    assert (await queue.get(ticket.id)).status == "expired"
