"""Shared helpers for catalog tests: definitions, fake sources, bootstrapping."""

from __future__ import annotations

from typing import Any

from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.catalog.store import ToolCatalogStore
from atlas.tooling.models.identity import ToolNamespace, build_tool_id
from atlas.tooling.models.tool_definition import (
    ExecutionType,
    Locality,
    UniversalToolDefinition,
)
from atlas.tooling.models.tool_policy import ToolPolicyMetadata
from atlas.tooling.models.tool_provenance import ToolProvenance
from atlas.tooling.models.tool_schema import ToolInputSchema
from atlas.tooling.registry.registry import ToolingRegistry


class StubAdapter:
    kind = "stub"

    async def initialize(self) -> None: ...
    async def validate(self, definition: UniversalToolDefinition) -> None: ...
    async def execute(self, invocation: Any, definition: UniversalToolDefinition) -> Any: ...
    async def health(self) -> bool:
        return True

    async def shutdown(self) -> None: ...


def make_definition(
    name: str,
    *,
    namespace: ToolNamespace = ToolNamespace.NATIVE,
    provider: str = "atlas",
    operations: tuple[str, ...] = ("op",),
    description: str = "",
    capability: str | None = None,
    tags: tuple[str, ...] = (),
    side_effects: bool = False,
    network_required: bool = False,
    requires_auth: bool = False,
    input_fallback: dict[str, Any] | None = None,
) -> UniversalToolDefinition:
    """Build a definition through the REAL pydantic model — no shortcuts."""
    schema = ToolInputSchema(
        fallback=input_fallback
        if input_fallback is not None
        else {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": list(operations)},
                "args": {"type": "object"},
            },
            "required": ["operation"],
        }
    )
    execution = ExecutionType.NATIVE if namespace == ToolNamespace.NATIVE else ExecutionType.CAPABILITY
    return UniversalToolDefinition(
        id=build_tool_id(namespace, provider, name),
        name=name,
        namespace=namespace,
        provider=provider,
        description=description or f"{name} tool for testing",
        operations=operations,
        input_schema=schema,
        execution_type=execution,
        adapter="stub",
        provenance=ToolProvenance(source_kind="local" if not network_required else "web", provider=provider),
        locality=Locality.REMOTE if network_required else Locality.LOCAL,
        safety_tool=name,
        capability=capability,
        policy=ToolPolicyMetadata(
            side_effects=side_effects,
            network_required=network_required,
            requires_auth=requires_auth,
            idempotent=not side_effects,
        ),
        tags=tags,
    )


class FakeSource:
    """Configurable CatalogSource double: mutable discovery set + failure flag."""

    def __init__(
        self,
        source_id: str = "mcp:test",
        definitions: list[UniversalToolDefinition] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.source_id = source_id
        self.source_type = source_id.split(":", 1)[0]
        self.display_name = source_id
        self.description = f"test source {source_id}"
        self.version = "1"
        self.definitions = definitions or []
        self.fail = fail
        self.discover_calls = 0

    async def discover(self) -> list[UniversalToolDefinition]:
        self.discover_calls += 1
        if self.fail:
            raise RuntimeError("source down")
        return list(self.definitions)


class EventCollector:
    """Records published catalog events (kind, payload)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]


def build_catalog(
    db: Any,
    registry: ToolingRegistry | None = None,
    *,
    publish: Any = None,
    sources: Any = None,
) -> ToolCatalog:
    """A real ToolCatalog over a started test Database."""
    return ToolCatalog(
        store=ToolCatalogStore(db),
        registry=registry or ToolingRegistry(),
        publish=publish,
        sources=sources,
    )


def registry_with(*definitions: UniversalToolDefinition) -> ToolingRegistry:
    registry = ToolingRegistry()
    for definition in definitions:
        registry.register(definition, StubAdapter())  # type: ignore[arg-type]
    return registry
