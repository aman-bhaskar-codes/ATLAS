"""ToolingRegistry tests — registration, lookup, filtering, state (§46/§57)."""

from __future__ import annotations

import pytest

from atlas.capabilities.domain.common import SourceKind
from atlas.tooling.adapters.base import ToolAdapter
from atlas.tooling.errors import ToolAlreadyRegistered, ToolNotFound
from atlas.tooling.models.identity import ToolNamespace, build_tool_id
from atlas.tooling.models.tool_definition import ExecutionType, Locality, UniversalToolDefinition
from atlas.tooling.models.tool_health import ToolRuntimeState, ToolStatus
from atlas.tooling.models.tool_invocation import UniversalToolInvocation
from atlas.tooling.models.tool_provenance import ToolProvenance
from atlas.tooling.models.tool_result import UniversalToolResult
from atlas.tooling.models.tool_schema import ToolInputSchema
from atlas.tooling.registry.registry import ToolingRegistry


class StubAdapter:
    kind = "stub"

    async def initialize(self) -> None: ...
    async def validate(self, definition: UniversalToolDefinition) -> None: ...
    async def execute(
        self, invocation: UniversalToolInvocation, definition: UniversalToolDefinition
    ) -> UniversalToolResult:
        return UniversalToolResult(ok=True, tool_id=invocation.tool_id)

    async def health(self) -> bool:
        return True

    async def shutdown(self) -> None: ...


def _definition(name: str, *, namespace: ToolNamespace = ToolNamespace.NATIVE) -> UniversalToolDefinition:
    return UniversalToolDefinition(
        id=build_tool_id(namespace, "atlas", name),
        name=name,
        namespace=namespace,
        provider="atlas",
        operations=("op",),
        input_schema=ToolInputSchema.from_operations(("op",)),
        execution_type=ExecutionType.NATIVE if namespace == ToolNamespace.NATIVE else ExecutionType.CAPABILITY,
        adapter="stub",
        provenance=ToolProvenance(source_kind=SourceKind.LOCAL, provider="atlas"),
        locality=Locality.LOCAL,
        safety_tool=name,
        capability=name if namespace == ToolNamespace.CAPABILITY else None,
        tags=("capability",) if namespace == ToolNamespace.CAPABILITY else (),
    )


def _registry() -> ToolingRegistry:
    registry = ToolingRegistry()
    registry.register(_definition("filesystem"), StubAdapter())  # type: ignore[arg-type]
    registry.register(_definition("shell"), StubAdapter())  # type: ignore[arg-type]
    registry.register(_definition("knowledge", namespace=ToolNamespace.CAPABILITY), StubAdapter())  # type: ignore[arg-type]
    return registry


def test_register_get_require_and_list() -> None:
    registry = _registry()
    assert len(registry) == 3
    registration = registry.require("native:atlas:filesystem")
    assert registration.definition.name == "filesystem"
    assert registration.adapter.kind == "stub"
    assert {d.name for d in registry.list_definitions()} == {"filesystem", "shell", "knowledge"}


def test_duplicate_registration_is_a_typed_error() -> None:
    registry = _registry()
    with pytest.raises(ToolAlreadyRegistered):
        registry.register(_definition("filesystem"), StubAdapter())  # type: ignore[arg-type]


def test_require_unknown_tool_raises_typed_error() -> None:
    with pytest.raises(ToolNotFound):
        _registry().require("native:atlas:nonexistent")


def test_unregister_removes_and_requires_existing() -> None:
    registry = _registry()
    removed = registry.unregister("native:atlas:shell")
    assert removed.definition.name == "shell"
    with pytest.raises(ToolNotFound):
        registry.unregister("native:atlas:shell")
    assert len(registry) == 2


def test_update_replaces_definition_and_keeps_status() -> None:
    registry = _registry()
    registry.disable("native:atlas:filesystem")
    registry.update(_definition("filesystem"))
    registration = registry.require("native:atlas:filesystem")
    assert registration.status.state == ToolRuntimeState.DISABLED
    with pytest.raises(ToolNotFound):
        registry.update(_definition("newtool"))


def test_find_by_capability_namespace_and_execution_type() -> None:
    registry = _registry()
    assert [r.definition.name for r in registry.find_by_capability("knowledge")] == ["knowledge"]
    assert {r.definition.name for r in registry.find_by_namespace("native")} == {"filesystem", "shell"}
    assert [r.definition.name for r in registry.find_by_execution_type("capability")] == ["knowledge"]


def test_find_with_combined_filters() -> None:
    registry = _registry()
    everything = registry.find()
    assert len(everything) == 3
    assert len(registry.find(namespace="capability", tag="capability")) == 1
    assert registry.find(capability="missing") == ()


def test_enable_disable_cycle_keeps_tool_inspectable() -> None:
    registry = _registry()
    tool_id = "native:atlas:filesystem"

    registry.disable(tool_id)
    assert registry.is_enabled(tool_id) is False
    # Disabled tools remain inspectable and visible to routing (§57).
    assert registry.get(tool_id) is not None
    assert registry.require(tool_id).status.state == ToolRuntimeState.DISABLED
    assert len(registry.list_definitions()) == 3

    registry.enable(tool_id)
    assert registry.is_enabled(tool_id) is True
    assert registry.require(tool_id).status.state == ToolRuntimeState.READY


def test_set_status_preserves_detail_by_default() -> None:
    registry = ToolingRegistry()
    registry.register(
        _definition("filesystem"),
        StubAdapter(),  # type: ignore[arg-type]
        status=ToolStatus(state=ToolRuntimeState.REGISTERED, detail="2 provider(s)"),
    )
    registry.set_status("native:atlas:filesystem", ToolRuntimeState.READY)
    assert registry.require("native:atlas:filesystem").status.detail == "2 provider(s)"
    registry.set_status("native:atlas:filesystem", ToolRuntimeState.FAILED, detail="boom")
    assert registry.require("native:atlas:filesystem").status.detail == "boom"


def test_registration_default_state_is_registered_not_ready() -> None:
    """No fake readiness: a plain registration has not proven anything yet."""
    registry = ToolingRegistry()
    registry.register(_definition("filesystem"), StubAdapter())  # type: ignore[arg-type]
    assert registry.require("native:atlas:filesystem").status.state == ToolRuntimeState.REGISTERED


def test_adapters_satisfy_tool_adapter_protocol() -> None:
    from atlas.tooling.adapters.capability import CapabilityAdapter
    from atlas.tooling.adapters.native import NativeToolAdapter

    assert isinstance(StubAdapter(), ToolAdapter)

    class NotAnAdapter:
        pass

    assert not isinstance(NotAnAdapter(), ToolAdapter)
    # The real adapters must satisfy the contract structurally.
    assert hasattr(NativeToolAdapter, "execute") and hasattr(CapabilityAdapter, "execute")
