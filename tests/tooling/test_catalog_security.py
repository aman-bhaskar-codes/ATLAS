"""Security + safety tests for the catalog (§65/§66/§7)."""

from __future__ import annotations

import datetime
import re
from typing import Any

import pytest

from atlas.tooling.models.identity import ToolNamespace
from tests.tooling.catalog_helpers import build_catalog, make_definition, registry_with

# Patterns that would indicate a secret leaked into the durable catalog.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),  # API key shape
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)password\s*[:=]"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S"),
    re.compile(r"(?i)authorization\s*[:=]"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),  # GitHub PAT shape
)


@pytest.mark.asyncio
async def test_catalog_never_persists_secret_material(memory_db: Any) -> None:
    """§65/§7: credential REFERENCES are fine; secret VALUES must never appear
    in any serialized catalog record, even if a source slips one into
    description/metadata fields."""
    poisoned = make_definition(
        "leaky",
        namespace=ToolNamespace.MCP,
        provider="github",
        operations=("call",),
        description="calls the GitHub API",
        network_required=True,
        requires_auth=True,
    )
    # Simulate a careless upstream source embedding a secret in free-text metadata.
    poisoned = poisoned.model_copy(
        update={
            "metadata": {"note": "config says api_key=sk-SUPERSECRET123 and password=hunter2"},
            "tags": ("github",),
        }
    )
    registry = registry_with(poisoned)
    catalog = build_catalog(memory_db, registry)
    await catalog.initialize()

    payload = "\n".join(record.model_dump_json() for record in catalog.find())
    for pattern in _SECRET_PATTERNS:
        assert pattern.search(payload) is None, f"secret pattern {pattern.pattern!r} leaked into catalog"


@pytest.mark.asyncio
async def test_credential_reference_is_stored_not_the_secret(memory_db: Any) -> None:
    catalog = build_catalog(memory_db)
    registry = registry_with(
        make_definition(
            "email",
            namespace=ToolNamespace.CAPABILITY,
            operations=("send",),
            description="send email",
            capability="email",
            network_required=True,
            requires_auth=True,
        )
    )
    catalog = build_catalog(memory_db, registry)
    await catalog.initialize()

    record = catalog.inspect("capability:atlas:email")
    assert record is not None
    assert record.requires_auth is True
    assert record.auth_state.value == "REQUIRED"
    # The reference field (when populated) holds an ID like "github:default" —
    # never a secret value. Part 1 tools carry no credential reference at all.
    assert record.credential_reference is None


@pytest.mark.asyncio
async def test_read_only_hint_does_not_bypass_safety_engine(memory_db: Any) -> None:
    """§66: a tool the catalog annotates readOnly=true is STILL denied by the
    SafetyEngine when the manifest says BLOCK. Catalog metadata is descriptive
    only — the engine remains the authority."""

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
    from atlas.tooling.models.tool_invocation import UniversalToolInvocation
    from atlas.tooling.models.tool_result import FailureKind
    from atlas.tooling.registry.registry import ToolingRegistry as UniversalRegistry
    from tests.fakes import FakeClock, FakeKillSwitch, FakeTool

    # A "read-only-looking" tool the manifest HARD-BLOCKS.
    manifest = Manifest(
        version=1,
        allowed_paths={},
        allowed_commands={},
        whatsapp={},
        safety={},
        rules=[{"tool": "quasi_readonly", "operation": "peek", "tier": 4}],  # type: ignore[arg-type]
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
    tool.name = "quasi_readonly"  # FakeTool's class default is "fake.tool"
    tool_registry = ToolRegistry()
    tool_registry.register(tool, ("peek",), ToolMetadata(name="quasi_readonly", operations=("peek",)))

    # The tool has NO side effects -> its catalog annotations will claim read-only.
    universal_registry = UniversalRegistry()
    definition = native_definition("quasi_readonly", ("peek",), tool_registry.metadata("quasi_readonly"))
    assert definition.policy.side_effects is False
    universal_registry.register(
        definition,
        NativeToolAdapter(dispatcher=ToolDispatcher(tool_registry, safety), tool_registry=tool_registry),
    )
    from atlas.tooling.models.tool_health import ToolRuntimeState

    universal_registry.set_status("native:atlas:quasi_readonly", ToolRuntimeState.READY)
    fabric = ToolingFabric(registry=universal_registry, executor=ToolingExecutor(registry=universal_registry))
    await fabric.initialize()

    result = await fabric.executor.execute(
        UniversalToolInvocation(
            tool_id="native:atlas:quasi_readonly",
            operation="peek",
            correlation_id=CorrelationId("c-safety"),
        )
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.kind == FailureKind.POLICY_DENIED
    assert tool.calls == []  # the hint never reached the backend
