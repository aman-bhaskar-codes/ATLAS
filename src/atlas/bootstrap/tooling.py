"""Tooling bootstrap — the universal tooling fabric (Part 1 foundation).

WHY a dedicated builder: mirrors build_safety/build_orchestration — the fabric
is constructed through the composition root like every other subsystem, never
self-wired (Part 1 §18/§24). It bridges the REAL current registrations, traced
from source:

* native tools: everything registered in the orchestration ``ToolRegistry``
  (filesystem, shell, and conditionally browser/computer_use/knowledge) with
  their real operations + ToolMetadata;
* capabilities: every spec in the ``CapabilityRegistry`` (knowledge, weather,
  location, currency, email, contacts, calendar), executed through the existing
  CapabilityDispatcher -> SafetyEngine funnel.

Nothing speculative is registered (§55): a definition exists here only because
the underlying registration exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.capabilities.dispatcher import CapabilityDispatcher
from atlas.capabilities.registry.capability import CapabilityRegistry
from atlas.capabilities.registry.provider_registry import ProviderRegistry as CapProviderRegistry
from atlas.infra.bus import MessageBus
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.metrics import Metrics
from atlas.infra.types import Tier, ToolRequest
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.registry import ToolRegistry
from atlas.safety.classifier import TierClassifier
from atlas.tooling.adapters.capability import CapabilityAdapter, capability_definition
from atlas.tooling.adapters.native import NativeToolAdapter, native_definition
from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.catalog.events import CatalogEventPublisher
from atlas.tooling.catalog.store import ToolCatalogStore
from atlas.tooling.execution.executor import ToolingExecutor
from atlas.tooling.fabric import InitializationReport, ToolingFabric
from atlas.tooling.models.tool_health import ToolRuntimeState, ToolStatus
from atlas.tooling.registry.registry import ToolingRegistry

_log = get_logger("atlas.bootstrap.tooling")


@dataclass
class ToolingComponents:
    fabric: ToolingFabric
    registry: ToolingRegistry
    executor: ToolingExecutor
    initialization: InitializationReport
    catalog: ToolCatalog | None = None  # None only when built without a Database (tests)


def _descriptor_tier(
    classifier: TierClassifier | None,
    name: str,
    operations: tuple[str, ...],
) -> Tier:
    """Compute the descriptor tier for a native tool by asking the REAL
    classifier about each declared operation. This is documentation of current
    policy, never enforcement — the classifier runs again on every invocation."""
    if classifier is None or not operations:
        return Tier.AUTO
    highest = Tier.AUTO
    for operation in operations:
        decision = classifier.classify(
            ToolRequest(correlation_id=CorrelationId("tooling-descriptor"), tool=name, operation=operation)
        )
        if decision.tier > highest:
            highest = decision.tier
    return highest


def build_tooling(
    *,
    tool_registry: ToolRegistry,
    dispatcher: ToolDispatcher,
    cap_registry: CapabilityRegistry,
    cap_dispatcher: CapabilityDispatcher,
    cap_providers: CapProviderRegistry,
    metrics: Metrics | None = None,
    classifier: TierClassifier | None = None,
    db: Database | None = None,
    bus: MessageBus | None = None,
) -> ToolingComponents:
    """Build the universal tooling fabric from the real registrations."""
    fabric_registry = ToolingRegistry()

    # ── Native tools (atlas.tools -> ToolDispatcher -> SafetyEngine) ── #
    native = NativeToolAdapter(dispatcher=dispatcher, tool_registry=tool_registry)
    for name, operations in sorted(tool_registry.registered().items()):
        ops = tuple(operations)
        definition = native_definition(
            name,
            ops,
            tool_registry.metadata(name),
            default_tier=_descriptor_tier(classifier, name, ops),
        )
        fabric_registry.register(definition, native)

    # ── Capabilities (CapabilityDispatcher -> SafetyEngine) ─────────── #
    capability = CapabilityAdapter(
        dispatcher=cap_dispatcher,
        registry=cap_registry,
        providers=cap_providers,
    )
    for spec in cap_registry.all():
        definition = capability_definition(spec)
        providers = cap_providers.for_capability(spec.capability)
        status = (
            ToolStatus(
                state=ToolRuntimeState.READY,
                detail=f"{len(providers)} provider(s) via the capability dispatcher",
            )
            if providers
            # Honest state: registered and visible, but no provider is wired
            # into the dispatcher yet (platform-managed or not yet connected).
            else ToolStatus(
                state=ToolRuntimeState.REGISTERED,
                detail="no provider registered in the capability dispatcher",
            )
        )
        fabric_registry.register(definition, capability, status=status)

    executor = ToolingExecutor(registry=fabric_registry, metrics=metrics)

    # ── Persistent Tool Catalog (Part 2) ────────────────────────────── #
    # Durable discovery index over the SAME registrations; the live registry
    # stays the authority on executability. Built here, but its DB-backed
    # initialize + registry sync run in Atlas.start() (the database and bus
    # are only usable after lifecycle.start()/bus.start()).
    catalog: ToolCatalog | None = None
    if db is not None:
        publisher = CatalogEventPublisher(bus)
        catalog = ToolCatalog(
            store=ToolCatalogStore(db),
            registry=fabric_registry,
            metrics=metrics,
            publish=publisher.publish,
        )

    fabric = ToolingFabric(registry=fabric_registry, executor=executor, catalog=catalog)
    _log.info(
        "tooling.built",
        event_type="lifecycle",
        tools=len(fabric_registry),
        native=len(fabric_registry.find_by_namespace("native")),
        capability=len(fabric_registry.find_by_namespace("capability")),
    )
    return ToolingComponents(
        fabric=fabric,
        registry=fabric_registry,
        executor=executor,
        initialization=InitializationReport(),
        catalog=catalog,
    )
