"""End-to-end foundation test (§49) — the full governed path with
task-correlated observability through the REAL audit log:

task context -> tooling registry lookup -> universal invocation -> adapter
-> ToolDispatcher -> SafetyEngine -> fake tool -> UniversalToolResult
-> audit records correlated to the original correlation id.
"""

from __future__ import annotations

import datetime
from typing import Any

from atlas.infra.config import SafetyCfg
from atlas.infra.ids import CorrelationId
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import SafetyEngine
from atlas.safety.manifest import Manifest
from atlas.safety.policy import KillSwitchPolicy, PolicyEngine
from atlas.tooling.adapters.native import NativeToolAdapter, native_definition
from atlas.tooling.execution.executor import ToolingExecutor
from atlas.tooling.fabric import ToolingFabric
from atlas.tooling.models.tool_invocation import InvocationSource, UniversalToolInvocation
from atlas.tooling.registry.registry import ToolingRegistry
from tests.fakes import FakeClock, FakeKillSwitch, FakeTool

CORRELATION = CorrelationId("corr-e2e-foundation")
TASK_ID = "task-e2e-1"


def _build(memory_db: Any) -> tuple[ToolingFabric, FakeTool, AuditLog, ToolingRegistry]:
    manifest = Manifest(
        version=1,
        allowed_paths={},
        allowed_commands={},
        whatsapp={},
        safety={},
        rules=[{"tool": "fake.tool", "operation": "do", "tier": 0}],  # type: ignore[arg-type]
        hard_block=[],  # type: ignore[arg-type]
    )
    audit = AuditLog(memory_db)
    killswitch = FakeKillSwitch(active=False)
    safety = SafetyEngine(
        classifier=TierClassifier(manifest, 2),
        policy=PolicyEngine((KillSwitchPolicy(killswitch),)),  # type: ignore[arg-type]
        audit=audit,
        killswitch=killswitch,
        clock=FakeClock(datetime.datetime.now()),  # type: ignore[arg-type]
        cfg=SafetyCfg(),
    )

    tool = FakeTool()
    tool_registry = ToolRegistry()
    tool_registry.register(
        tool,
        ("do",),
        ToolMetadata(name="fake.tool", operations=("do",), description="a fake native tool"),
    )
    dispatcher = ToolDispatcher(tool_registry, safety)

    registry = ToolingRegistry()
    registry.register(
        native_definition("fake.tool", ("do",), tool_registry.metadata("fake.tool")),
        NativeToolAdapter(dispatcher=dispatcher, tool_registry=tool_registry),
    )
    fabric = ToolingFabric(registry=registry, executor=ToolingExecutor(registry=registry))
    return fabric, tool, audit, registry


async def test_end_to_end_governed_execution_with_task_correlated_telemetry(memory_db: Any) -> None:
    fabric, tool, audit, registry = _build(memory_db)

    # 1. registry lookup resolves the definition and its adapter
    registration = registry.require("native:atlas:fake.tool")
    assert registration.definition.safety_tool == "fake.tool"

    # 2. lifecycle: initialize -> ready
    report = await fabric.initialize()
    assert report.ok

    # 3-8. universal invocation -> adapter -> dispatcher -> SafetyEngine ->
    #      backend -> UniversalToolResult
    result = await fabric.executor.execute(
        UniversalToolInvocation(
            tool_id="native:atlas:fake.tool",
            operation="do",
            arguments={"q": "x"},
            correlation_id=CORRELATION,
            task_id=TASK_ID,
            source=InvocationSource.REASONING,
        )
    )

    assert result.ok is True
    assert result.data == "did it"
    assert result.duration_ms is not None
    assert len(tool.calls) == 1

    # 9. task-correlated telemetry: the REAL SafetyEngine audited the decision
    #    and the tool result under the original correlation id.
    records = await audit.by_correlation(str(CORRELATION))
    actions = {record["action"] for record in records}
    assert "decision" in actions, f"expected tier decision audited, got {actions}"
    assert "tool.result" in actions, f"expected tool result audited, got {actions}"
    decision = next(r for r in records if r["action"] == "decision")
    assert decision["tool"] == "fake.tool"
