"""CapabilityAdapter — bridges the existing CapabilityDispatcher into the
universal fabric WITHOUT duplicating provider routing (Part 1 §15/§16).

The flow is the spec's, verbatim:

    UniversalToolInvocation
        -> CapabilityRequest(capability, operation, args)
        -> CapabilityDispatcher.execute   (provider selection + fallback chain)
        -> SafetyEngine.guard             (inside the dispatcher)
        -> provider execution
        -> CapabilityResult
        -> UniversalToolResult

Provider selection, fallback, retry, health and telemetry remain owned by the
capability layer — the adapter adds only normalization. A Safety denial (raised
by the dispatcher as ``CapabilityDenied``) becomes a structured ``policy_denied``
failure result.
"""

from __future__ import annotations

from atlas.capabilities.dispatcher import CapabilityDispatcher
from atlas.capabilities.domain.common import CapabilityResult, SourceKind
from atlas.capabilities.errors import CapabilityDenied, CapabilityError, NoProviderAvailable
from atlas.capabilities.providers.base import CapabilityRequest
from atlas.capabilities.registry.capability import Capability, CapabilityRegistry, CapabilitySpec
from atlas.capabilities.registry.provider_registry import ProviderRegistry
from atlas.infra.logging import get_logger
from atlas.infra.types import CostClass, PrivacyClass
from atlas.tooling.errors import ToolValidationError
from atlas.tooling.models.identity import ToolNamespace, build_tool_id
from atlas.tooling.models.tool_definition import ExecutionType, Locality, UniversalToolDefinition
from atlas.tooling.models.tool_invocation import UniversalToolInvocation
from atlas.tooling.models.tool_policy import ToolPolicyMetadata
from atlas.tooling.models.tool_provenance import ToolProvenance
from atlas.tooling.models.tool_result import FailureKind, UniversalToolResult
from atlas.tooling.models.tool_schema import ToolInputSchema

_log = get_logger("atlas.tooling.capability")


def capability_definition(spec: CapabilitySpec) -> UniversalToolDefinition:
    """Derive a UniversalToolDefinition from a REAL CapabilitySpec registration.

    Provenance/trust are honest descriptors of the current platform set: every
    registered capability is served by external services (vendor APIs or web
    sources), so locality is REMOTE and the origin is vendor-official except
    knowledge, which federates general web sources.
    """
    source_kind = SourceKind.WEB if spec.capability == Capability.KNOWLEDGE else SourceKind.OFFICIAL
    writes = "send" in spec.operations or "create" in spec.operations or "update" in spec.operations
    policy = ToolPolicyMetadata(
        # Declared spec tier — descriptor only; the SafetyEngine remains the
        # authority for every invocation (Part 1 §12).
        default_tier=spec.default_tier,
        side_effects=writes,
        supports_rollback=False,
        idempotent=not writes,
        requires_auth=spec.requires_auth,
        max_privacy=PrivacyClass.PUBLIC,
        network_required=True,
        cost_class=CostClass.FREE,
        trust_level="system_builtin",
    )
    return UniversalToolDefinition(
        id=build_tool_id(ToolNamespace.CAPABILITY, "atlas", spec.capability.value),
        name=spec.capability.value,
        namespace=ToolNamespace.CAPABILITY,
        provider="atlas",
        capability=spec.capability.value,
        description=spec.description or f"ATLAS {spec.capability.value} capability.",
        operations=spec.operations,
        input_schema=ToolInputSchema.from_operations(spec.operations),
        execution_type=ExecutionType.CAPABILITY,
        adapter="capability",
        provenance=ToolProvenance(source_kind=source_kind, provider="atlas"),
        locality=Locality.REMOTE,
        safety_tool=spec.safety_tool,
        policy=policy,
        tags=("capability",),
    )


class CapabilityAdapter:
    """Serves every capability registered in the CapabilityRegistry."""

    kind = "capability"

    def __init__(
        self,
        *,
        dispatcher: CapabilityDispatcher,
        registry: CapabilityRegistry,
        providers: ProviderRegistry,
    ) -> None:
        self._dispatcher = dispatcher
        self._registry = registry
        self._providers = providers

    async def initialize(self) -> None:
        return None  # providers initialize through the existing capability lifecycle

    async def validate(self, definition: UniversalToolDefinition) -> None:
        if definition.capability is None:
            raise ToolValidationError(f"capability tool {definition.id!r} does not name its capability")
        try:
            self._registry.get(Capability(definition.capability))
        except CapabilityError as exc:
            raise ToolValidationError(
                f"capability {definition.capability!r} is not registered in the CapabilityRegistry"
            ) from exc

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
                    f"operation {invocation.operation!r} is not declared by capability "
                    f"{definition.capability!r} (declared: {', '.join(definition.operations)})"
                ),
            )
        assert definition.capability is not None  # validated above
        request = CapabilityRequest(
            capability=Capability(definition.capability),
            operation=invocation.operation,
            args=dict(invocation.arguments),
        )
        try:
            result = await self._dispatcher.execute(
                request,
                invocation.correlation_id,
                task_id=invocation.task_id,
            )
        except CapabilityDenied as exc:
            return UniversalToolResult.failure(
                tool_id=invocation.tool_id,
                kind=FailureKind.POLICY_DENIED,
                message=str(exc),
            )
        except NoProviderAvailable as exc:
            return UniversalToolResult.failure(
                tool_id=invocation.tool_id,
                kind=FailureKind.UNAVAILABLE,
                message=str(exc),
            )
        except CapabilityError as exc:
            return UniversalToolResult.failure(
                tool_id=invocation.tool_id,
                kind=FailureKind.EXECUTION_ERROR,
                message=str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
            )
        return self._normalize(invocation.tool_id, result)

    def _normalize(self, tool_id: str, result: CapabilityResult[object]) -> UniversalToolResult:
        provenance = tuple(ToolProvenance.from_domain(p) for p in result.provenance)
        if result.ok:
            return UniversalToolResult(
                ok=True,
                tool_id=tool_id,
                provider=result.provider,
                data=result.payload,
                duration_ms=result.latency_ms or None,
                provenance=provenance,
                metadata={
                    "cost_usd": result.cost_usd,
                    "confidence": result.confidence.score,
                },
            )
        message = result.error or "capability execution failed"
        kind = (
            FailureKind.UNAVAILABLE
            if "no provider" in message.lower() or "all providers failed" in message.lower()
            else FailureKind.EXECUTION_ERROR
        )
        return UniversalToolResult.failure(
            tool_id=tool_id,
            kind=kind,
            message=message,
            provider=result.provider,
            duration_ms=result.latency_ms or None,
        )

    async def health(self) -> bool:
        return True  # per-provider health is owned by CapabilityHealth/circuit breakers

    async def shutdown(self) -> None:
        return None


__all__ = ["CapabilityAdapter", "capability_definition"]
