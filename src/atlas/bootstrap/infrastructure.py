"""Infrastructure bootstrap — DB, bus, IDs, clock, audit, killswitch."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.infra.bus import MessageBus
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.config import AppConfig, Settings
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator, UuidGenerator
from atlas.infra.lifecycle import Lifecycle
from atlas.infra.logging import get_logger
from atlas.infra.metrics import Metrics
from atlas.infra.registry import ServiceRegistry
from atlas.infra.routing_backends import BackendRouter
from atlas.infra.tracing import Tracer
from atlas.safety.audit import AuditLog
from atlas.safety.killswitch import KillSwitch

_log = get_logger("atlas.bootstrap.infrastructure")


@dataclass
class InfraComponents:
    ids: IdGenerator
    clock: Clock
    metrics: Metrics
    tracer: Tracer
    db: Database
    registry: ServiceRegistry
    lifecycle: Lifecycle
    bus: MessageBus
    audit: AuditLog
    killswitch: KillSwitch
    router: BackendRouter


def build_infrastructure(settings: Settings, config: AppConfig) -> InfraComponents:
    """Build all infrastructure primitives. No async needed — pure construction."""
    ids: IdGenerator = UuidGenerator()
    clock: Clock = SystemClock()
    metrics = Metrics()
    tracer = Tracer(config.tracing)

    db = Database(settings.db_path())
    registry = ServiceRegistry()
    registry.register("db", db)
    # The single persistence seam: every store obtains its Connection here (T4).
    # Depends on db so it starts after / stops before the SQLite file it falls
    # back to; stop() closes every Postgres pool it opened. Lazy — no pool opens
    # until a store issues its first query, so zero-config pays nothing.
    router = BackendRouter(settings, db)
    registry.register("backend_router", router, deps=("db",))
    lifecycle = Lifecycle(registry)

    bus = MessageBus(db)
    from atlas.infra.bus import MemoryBusEvent
    from atlas.orchestration.events import (
        OrchestratorEvent,
        PlanningEvent,
        SafetyEvent,
        ToolEvent,
    )

    bus.register_type("orchestrator", OrchestratorEvent)
    bus.register_type("safety", SafetyEvent)
    bus.register_type("planning", PlanningEvent)
    bus.register_type("memory", MemoryBusEvent)  # use infra type; memory layer publishes this
    bus.register_type("tool", ToolEvent)
    from atlas.tooling.catalog.events import ToolCatalogEvent
    from atlas.tooling.routing.events import RouteEvent

    bus.register_type("tool.catalog", ToolCatalogEvent)  # Part 2: tool catalog lifecycle
    bus.register_type("route", RouteEvent)  # Part 3: routing lifecycle
    from atlas.tooling.execution.events import ExecutionEvent

    bus.register_type("execution", ExecutionEvent)  # Part 4: execution lifecycle
    from atlas.tooling.mcp.events import MCPEvent

    bus.register_type("mcp", MCPEvent)  # Part 5: MCP lifecycle

    audit = AuditLog(db)
    killswitch = KillSwitch(config.safety.stop_flag_path)

    return InfraComponents(
        ids=ids,
        clock=clock,
        metrics=metrics,
        tracer=tracer,
        db=db,
        registry=registry,
        lifecycle=lifecycle,
        bus=bus,
        audit=audit,
        killswitch=killswitch,
        router=router,
    )
