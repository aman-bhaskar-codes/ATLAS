"""NativeToolAdapter — bridges existing ``atlas.tools`` Tool implementations
into the universal fabric WITHOUT copying any execution logic.

The flow is exactly the spec's (Part 1 §14):

    UniversalToolInvocation
        -> Action(tool=safety_tool, operation, args)
        -> ToolDispatcher.dispatch      (the existing governed path)
        -> SafetyEngine.guard           (inside the dispatcher)
        -> tool.execute
        -> Observation
        -> UniversalToolResult

The dispatcher keeps doing what it always did: merging the operation into args,
running tier classification/policy/audit/confirmation via ``guard()``, and
recording tool health. Denials and halts surface here as structured failure
results — information, not crashes.
"""

from __future__ import annotations

import time

from atlas.capabilities.domain.common import SourceKind
from atlas.infra.logging import get_logger
from atlas.infra.types import CostClass, PrivacyClass, Tier
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.types import Action
from atlas.tooling.errors import ToolValidationError
from atlas.tooling.models.identity import ToolNamespace, build_tool_id
from atlas.tooling.models.tool_definition import ExecutionType, Locality, UniversalToolDefinition
from atlas.tooling.models.tool_invocation import UniversalToolInvocation
from atlas.tooling.models.tool_policy import ToolPolicyMetadata
from atlas.tooling.models.tool_provenance import ToolProvenance
from atlas.tooling.models.tool_result import FailureKind, UniversalToolResult
from atlas.tooling.models.tool_schema import ToolInputSchema

_log = get_logger("atlas.tooling.native")

#: Where each built-in native tool executes and where its results originate.
#: Unknown native tools default to LOCAL execution — the conservative choice,
#: because every current native tool runs on-device.
_LOCALITY_BY_TOOL: dict[str, Locality] = {
    "filesystem": Locality.LOCAL,
    "shell": Locality.LOCAL,
    "browser": Locality.REMOTE,
    "computer_use": Locality.LOCAL,
    "knowledge": Locality.REMOTE,
}
_SOURCE_KIND_BY_TOOL: dict[str, SourceKind] = {
    "filesystem": SourceKind.LOCAL,
    "shell": SourceKind.LOCAL,
    "browser": SourceKind.WEB,
    "computer_use": SourceKind.LOCAL,
    "knowledge": SourceKind.WEB,
}


def native_definition(
    name: str,
    operations: tuple[str, ...],
    metadata: ToolMetadata | None,
    *,
    default_tier: Tier | None = None,
) -> UniversalToolDefinition:
    """Derive a UniversalToolDefinition from a REAL registration (the
    orchestration ToolRegistry is the source of truth — nothing invented)."""
    locality = _LOCALITY_BY_TOOL.get(name, Locality.LOCAL)
    source_kind = _SOURCE_KIND_BY_TOOL.get(name, SourceKind.LOCAL)
    meta = metadata
    policy = ToolPolicyMetadata(
        # Descriptor only — the SafetyEngine manifest tier lookup remains the
        # authority for every invocation (Part 1 §12).
        default_tier=default_tier if default_tier is not None else Tier.AUTO,
        side_effects=meta.side_effects if meta else False,
        supports_rollback=meta.supports_rollback if meta else False,
        idempotent=meta.idempotent if meta else True,
        requires_auth=False,
        max_privacy=(
            # Local tools may process the most sensitive data; anything that
            # ships data off-machine is capped at PUBLIC until policy parts.
            PrivacyClass.SECRET if locality == Locality.LOCAL else PrivacyClass.PUBLIC
        ),
        network_required=locality == Locality.REMOTE,
        cost_class=CostClass.LOCAL if locality == Locality.LOCAL else CostClass.FREE,
        trust_level="system_builtin",
    )
    return UniversalToolDefinition(
        id=build_tool_id(ToolNamespace.NATIVE, "atlas", name),
        name=name,
        namespace=ToolNamespace.NATIVE,
        provider="atlas",
        description=meta.description if meta and meta.description else f"Native ATLAS tool {name!r}.",
        operations=operations,
        input_schema=ToolInputSchema.from_operations(operations),
        execution_type=ExecutionType.NATIVE,
        adapter="native",
        provenance=ToolProvenance(source_kind=source_kind, provider="atlas"),
        locality=locality,
        safety_tool=name,
        policy=policy,
        estimated_cost_usd=meta.estimated_cost_usd if meta else 0.0,
        estimated_latency_ms=meta.estimated_latency_ms if meta else 500,
    )


class NativeToolAdapter:
    """Serves every registered native (atlas.tools) tool."""

    kind = "native"

    def __init__(self, *, dispatcher: ToolDispatcher, tool_registry: ToolRegistry) -> None:
        self._dispatcher = dispatcher
        self._tool_registry = tool_registry

    async def initialize(self) -> None:
        return None  # nothing to open: native tools are already wired in-process

    async def validate(self, definition: UniversalToolDefinition) -> None:
        tool = self._tool_registry.get(definition.safety_tool)
        if tool is None:
            raise ToolValidationError(f"native tool {definition.safety_tool!r} is not registered in the ToolRegistry")
        if not definition.operations:
            raise ToolValidationError(f"native tool {definition.safety_tool!r} declares no operations")

    async def execute(
        self,
        invocation: UniversalToolInvocation,
        definition: UniversalToolDefinition,
    ) -> UniversalToolResult:
        if invocation.operation not in definition.operations:
            return UniversalToolResult.failure(
                tool_id=invocation.tool_id,
                kind=FailureKind.VALIDATION,
                message=(
                    f"operation {invocation.operation!r} is not declared by "
                    f"{definition.safety_tool!r} (declared: {', '.join(definition.operations)})"
                ),
            )
        action = Action(
            step=0,
            kind="tool_call",
            tool=definition.safety_tool,
            operation=invocation.operation,
            args=dict(invocation.arguments),
        )
        started = time.perf_counter()
        observation = await self._dispatcher.dispatch(action, invocation.correlation_id)
        duration_ms = int((time.perf_counter() - started) * 1000)
        if observation.ok:
            return UniversalToolResult(
                ok=True,
                tool_id=invocation.tool_id,
                data=observation.content,
                duration_ms=duration_ms,
                metadata={"adapter": self.kind, "source": invocation.source.value},
            )
        error = observation.error or "unknown native dispatch failure"
        if error.startswith("denied"):
            kind, retryable = FailureKind.POLICY_DENIED, False
        elif error.startswith("halted"):
            kind, retryable = FailureKind.HALTED, False
        elif "unknown tool" in error:
            kind, retryable = FailureKind.UNAVAILABLE, False
        else:
            kind, retryable = FailureKind.EXECUTION_ERROR, True
        return UniversalToolResult.failure(
            tool_id=invocation.tool_id,
            kind=kind,
            message=error,
            retryable=retryable,
            duration_ms=duration_ms,
        )

    async def health(self) -> bool:
        return True  # in-process; health of individual tools is tracked by ToolHealthTracker

    async def shutdown(self) -> None:
        return None


__all__ = ["NativeToolAdapter", "native_definition"]
