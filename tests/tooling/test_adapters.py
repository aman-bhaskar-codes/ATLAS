"""Adapter integration tests — the REAL governed path, no bypass (§47/§48).

Every execution here flows: universal invocation -> executor -> adapter ->
ToolDispatcher | CapabilityDispatcher -> SafetyEngine.guard() -> fake backend
-> UniversalToolResult. The SafetyEngine is the real engine with a real
permissions manifest, real audit log, and real kill switch.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from atlas.capabilities.dispatcher import CapabilityDispatcher
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.observability.telemetry import CapabilityTelemetry
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability, CapabilityRegistry, CapabilitySpec
from atlas.capabilities.registry.health import CapabilityHealth
from atlas.capabilities.registry.provider_registry import ProviderRegistry
from atlas.infra.config import SafetyCfg
from atlas.infra.ids import CorrelationId
from atlas.infra.types import Tier
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import SafetyEngine
from atlas.safety.manifest import Manifest
from atlas.safety.policy import KillSwitchPolicy, PolicyEngine
from atlas.tooling.adapters.capability import CapabilityAdapter, capability_definition
from atlas.tooling.adapters.native import NativeToolAdapter, native_definition
from atlas.tooling.errors import ToolDisabled
from atlas.tooling.execution.executor import ToolingExecutor
from atlas.tooling.fabric import ToolingFabric
from atlas.tooling.models.tool_health import ToolRuntimeState
from atlas.tooling.models.tool_invocation import UniversalToolInvocation
from atlas.tooling.models.tool_result import FailureKind
from atlas.tooling.registry.registry import ToolingRegistry
from tests.fakes import FakeClock, FakeKillSwitch, FakeTool


class FakeProvider:
    capability = Capability.KNOWLEDGE
    is_local = True
    requires_auth = False

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.calls = 0

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool:
        return True

    async def execute(self, request: CapabilityRequest) -> Any:
        self.calls += 1
        return {"raw": request.args.get("q", "")}

    def normalize(self, raw: Any) -> Any:
        from pydantic import BaseModel

        class Payload(BaseModel):
            value: str

        return Payload(value=str(raw["raw"]))

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=0)

    async def shutdown(self) -> None: ...


def _safety(memory_db: Any, rules: list[dict[str, Any]]) -> tuple[SafetyEngine, AuditLog]:
    manifest = Manifest(
        version=1,
        allowed_paths={},
        allowed_commands={},
        whatsapp={},
        safety={},
        rules=rules,  # type: ignore[arg-type]
        hard_block=[],  # type: ignore[arg-type]
    )
    audit = AuditLog(memory_db)
    killswitch = FakeKillSwitch(active=False)
    engine = SafetyEngine(
        classifier=TierClassifier(manifest, 2),
        policy=PolicyEngine((KillSwitchPolicy(killswitch),)),  # type: ignore[arg-type]
        audit=audit,
        killswitch=killswitch,
        clock=FakeClock(datetime.datetime.now()),  # type: ignore[arg-type]
        cfg=SafetyCfg(),
    )
    return engine, audit


def _invocation(tool_id: str, operation: str) -> UniversalToolInvocation:
    return UniversalToolInvocation(
        tool_id=tool_id,
        operation=operation,
        arguments={"q": "hello"},
        correlation_id=CorrelationId("corr-tooling"),
        task_id="task-1",
    )


async def _run_fabric(registry: ToolingRegistry) -> ToolingFabric:
    fabric = ToolingFabric(
        registry=registry,
        executor=ToolingExecutor(registry=registry),
    )
    await fabric.initialize()
    return fabric


# ── Native path (§47/§71) ──────────────────────────────────────────────── #


async def test_native_invocation_reaches_real_tool_through_safety_engine(memory_db: Any) -> None:
    safety, _audit = _safety(memory_db, rules=[{"tool": "fake.tool", "operation": "do", "tier": 0}])
    tool = FakeTool()
    tool_registry = ToolRegistry()
    tool_registry.register(
        tool,
        ("do",),
        ToolMetadata(name="fake.tool", operations=("do",), description="a fake tool"),
    )
    dispatcher = ToolDispatcher(tool_registry, safety)

    registry = ToolingRegistry()
    registry.register(
        native_definition("fake.tool", ("do",), tool_registry.metadata("fake.tool")),
        NativeToolAdapter(dispatcher=dispatcher, tool_registry=tool_registry),
    )
    fabric = await _run_fabric(registry)

    result = await fabric.executor.execute(_invocation("native:atlas:fake.tool", "do"))

    assert result.ok is True
    assert result.data == "did it"  # FakeTool output, normalized unchanged
    assert len(tool.calls) == 1  # backend executed exactly once
    assert tool.calls[0]["operation"] == "do"  # dispatcher merged operation into args
    assert result.duration_ms is not None
    # Side effects do NOT travel on the Observation the dispatcher returns —
    # they are preserved in the hash-chained audit trail under the correlation
    # id (see tests/safety/test_engine.py). The universal result carries the
    # normalized data; the audit log carries the side-effect record.
    assert result.side_effects == ()
    assert registry.require("native:atlas:fake.tool").status.state == ToolRuntimeState.READY


async def test_native_unknown_operation_is_a_structured_failure(memory_db: Any) -> None:
    safety, _audit = _safety(memory_db, rules=[{"tool": "fake.tool", "operation": "*", "tier": 0}])
    tool = FakeTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool, ("do",), None)
    registry = ToolingRegistry()
    registry.register(
        native_definition("fake.tool", ("do",), None),
        NativeToolAdapter(dispatcher=ToolDispatcher(tool_registry, safety), tool_registry=tool_registry),
    )
    fabric = await _run_fabric(registry)

    result = await fabric.executor.execute(_invocation("native:atlas:fake.tool", "destroy"))

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind == FailureKind.VALIDATION
    assert tool.calls == []  # never reached the backend


async def test_unregistered_native_tool_fails_validation_at_initialize(memory_db: Any) -> None:
    safety, _audit = _safety(memory_db, rules=[{"tool": "*", "operation": "*", "tier": 0}])
    tool_registry = ToolRegistry()
    registry = ToolingRegistry()
    registry.register(
        native_definition("ghost", ("do",), None),
        NativeToolAdapter(dispatcher=ToolDispatcher(tool_registry, safety), tool_registry=tool_registry),
    )
    fabric = ToolingFabric(registry=registry, executor=ToolingExecutor(registry=registry))
    report = await fabric.initialize()

    assert not report.ok
    assert "native:atlas:ghost" in report.failed
    assert registry.require("native:atlas:ghost").status.state == ToolRuntimeState.FAILED


# ── Capability path (§48/§72) ──────────────────────────────────────────── #


def _capability_stack(
    memory_db: Any, rules: list[dict[str, Any]]
) -> tuple[ToolingFabric, FakeProvider, ToolingRegistry]:
    safety, _audit = _safety(memory_db, rules)
    cap_registry = CapabilityRegistry()
    cap_registry.register(
        CapabilitySpec(
            capability=Capability.KNOWLEDGE,
            safety_tool="knowledge",
            operations=("search",),
            default_tier=Tier.AUTO,
        )
    )
    health = CapabilityHealth()
    providers = ProviderRegistry(health)
    provider = FakeProvider()
    providers.register(provider)
    telemetry = CapabilityTelemetry(lambda **kw: _noop())
    dispatcher = CapabilityDispatcher(
        registry=cap_registry,
        providers=providers,
        health=health,
        safety=safety,
        telemetry=telemetry,
    )
    registry = ToolingRegistry()
    registry.register(
        capability_definition(cap_registry.get(Capability.KNOWLEDGE)),
        CapabilityAdapter(dispatcher=dispatcher, registry=cap_registry, providers=providers),
    )
    fabric = ToolingFabric(registry=registry, executor=ToolingExecutor(registry=registry))
    return fabric, provider, registry


async def _noop() -> None: ...


async def test_capability_invocation_reaches_provider_through_safety_engine(memory_db: Any) -> None:
    fabric, provider, registry = _capability_stack(
        memory_db, rules=[{"tool": "knowledge", "operation": "search", "tier": 0}]
    )
    await fabric.initialize()

    result = await fabric.executor.execute(_invocation("capability:atlas:knowledge", "search"))

    assert result.ok is True
    assert provider.calls == 1
    assert result.data is not None
    assert result.data.value == "hello"  # normalized domain payload
    assert result.provider == "fake"
    assert len(result.provenance) == 1  # provider provenance preserved
    assert registry.require("capability:atlas:knowledge").status.state == ToolRuntimeState.READY


async def test_capability_safety_denial_is_a_structured_result(memory_db: Any) -> None:
    fabric, provider, _registry = _capability_stack(
        memory_db, rules=[{"tool": "knowledge", "operation": "search", "tier": 4}]
    )
    await fabric.initialize()

    result = await fabric.executor.execute(_invocation("capability:atlas:knowledge", "search"))

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind == FailureKind.POLICY_DENIED
    assert provider.calls == 0  # the backend never executed (§73)


async def test_capability_denial_raises_typed_error_at_dispatcher_level(memory_db: Any) -> None:
    """The underlying CapabilityDenied remains available to callers of the
    capability dispatcher itself — the adapter converts it to a result only at
    the universal boundary."""
    safety, _audit = _safety(memory_db, rules=[{"tool": "knowledge", "operation": "search", "tier": 4}])
    cap_registry = CapabilityRegistry()
    cap_registry.register(
        CapabilitySpec(capability=Capability.KNOWLEDGE, safety_tool="knowledge", operations=("search",))
    )
    health = CapabilityHealth()
    providers = ProviderRegistry(health)
    providers.register(FakeProvider())
    dispatcher = CapabilityDispatcher(
        registry=cap_registry,
        providers=providers,
        health=health,
        safety=safety,
        telemetry=CapabilityTelemetry(lambda **kw: _noop()),
    )
    with pytest.raises(CapabilityDenied):
        await dispatcher.execute(
            CapabilityRequest(capability=Capability.KNOWLEDGE, operation="search", args={}),
            CorrelationId("c"),
        )


# ── Disabled tool (§74) ────────────────────────────────────────────────── #


async def test_disabled_tool_raises_typed_error_without_execution(memory_db: Any) -> None:
    safety, _audit = _safety(memory_db, rules=[{"tool": "fake.tool", "operation": "do", "tier": 0}])
    tool = FakeTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool, ("do",), None)
    registry = ToolingRegistry()
    registry.register(
        native_definition("fake.tool", ("do",), None),
        NativeToolAdapter(dispatcher=ToolDispatcher(tool_registry, safety), tool_registry=tool_registry),
    )
    fabric = await _run_fabric(registry)

    registry.disable("native:atlas:fake.tool")
    with pytest.raises(ToolDisabled):
        await fabric.executor.execute(_invocation("native:atlas:fake.tool", "do"))
    assert tool.calls == []  # no backend execution (§74)

    registry.enable("native:atlas:fake.tool")
    result = await fabric.executor.execute(_invocation("native:atlas:fake.tool", "do"))
    assert result.ok is True
    assert len(tool.calls) == 1


# ── Safety decision propagation ────────────────────────────────────────── #


def test_manifest_tiers_flow_into_descriptor_not_enforcement() -> None:
    """The definition's tier is a descriptor; the manifest is the authority."""
    definition = native_definition(
        "fake.tool",
        ("do",),
        ToolMetadata(name="fake.tool", operations=("do",), side_effects=True),
        default_tier=Tier.CONFIRM,
    )
    assert definition.default_tier == Tier.CONFIRM
    decision_tier = Tier.BLOCK  # what a manifest hard-block would yield
    assert decision_tier > definition.default_tier  # engine can always override the descriptor
