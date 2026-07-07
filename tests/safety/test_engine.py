from __future__ import annotations

import datetime
from typing import Any

import pytest

from atlas.infra.config import SafetyCfg
from atlas.infra.types import ToolRequest
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import DeniedError, HaltedError, SafetyEngine
from atlas.safety.manifest import Manifest
from atlas.safety.policy import KillSwitchPolicy, PolicyEngine
from tests.fakes import FakeClock, FakeConfirmer, FakeKillSwitch, FakeTool


@pytest.mark.asyncio
async def test_engine_allow(memory_db: Any) -> None:
    m = Manifest(
        version=1, allowed_paths={}, allowed_commands={}, whatsapp={}, safety={},
        rules=[{"tool": "fs", "operation": "read", "tier": 0}], hard_block=[]
    )
    audit = AuditLog(memory_db)
    ks = FakeKillSwitch(active=False)
    clf = TierClassifier(m, 2)
    engine = SafetyEngine(
        classifier=clf, policy=PolicyEngine((KillSwitchPolicy(ks),)),
        audit=audit, killswitch=ks, clock=FakeClock(datetime.datetime.now()),
        cfg=SafetyCfg()
    )

    req = ToolRequest(correlation_id="cid-1", tool="fs", operation="read")
    tool = FakeTool()

    res = await engine.guard(req, tool)
    assert res.ok

    # Verify audit
    logs = await audit.by_correlation("cid-1")
    assert len(logs) == 2
    assert logs[0]["action"] == "decision"
    assert logs[1]["action"] == "tool.result"


@pytest.mark.asyncio
async def test_engine_killswitch(memory_db: Any) -> None:
    m = Manifest(
        version=1, allowed_paths={}, allowed_commands={}, whatsapp={}, safety={},
        rules=[{"tool": "fs", "operation": "read", "tier": 0}], hard_block=[]
    )
    audit = AuditLog(memory_db)
    ks = FakeKillSwitch(active=True)
    clf = TierClassifier(m, 2)
    engine = SafetyEngine(
        classifier=clf, policy=PolicyEngine((KillSwitchPolicy(ks),)),
        audit=audit, killswitch=ks, clock=FakeClock(datetime.datetime.now()),
        cfg=SafetyCfg()
    )

    req = ToolRequest(correlation_id="cid-1", tool="fs", operation="read")
    tool = FakeTool()

    with pytest.raises(HaltedError):
        await engine.guard(req, tool)


@pytest.mark.asyncio
async def test_engine_confirm_deny(memory_db: Any) -> None:
    m = Manifest(
        version=1, allowed_paths={}, allowed_commands={}, whatsapp={}, safety={},
        rules=[{"tool": "fs", "operation": "write", "tier": 2}], hard_block=[]
    )
    audit = AuditLog(memory_db)
    ks = FakeKillSwitch(active=False)
    clf = TierClassifier(m, 2)
    engine = SafetyEngine(
        classifier=clf, policy=PolicyEngine((KillSwitchPolicy(ks),)),
        audit=audit, killswitch=ks, clock=FakeClock(datetime.datetime.now()),
        cfg=SafetyCfg(),
        confirmer=FakeConfirmer(response=False)  # User denies
    )

    req = ToolRequest(correlation_id="cid-1", tool="fs", operation="write")
    tool = FakeTool()

    with pytest.raises(DeniedError):
        await engine.guard(req, tool)

    logs = await audit.by_correlation("cid-1")
    assert len(logs) == 2
    assert logs[1]["action"] == "decision"
    assert logs[1]["outcome"] == "denied"
