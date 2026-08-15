"""Infrastructure bootstrap — DB, bus, IDs, clock, audit, killswitch."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atlas.infra.bus import MessageBus
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.config import AppConfig, Settings
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator, UuidGenerator
from atlas.infra.lifecycle import Lifecycle
from atlas.infra.logging import get_logger
from atlas.infra.metrics import Metrics
from atlas.infra.registry import ServiceRegistry
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


def build_infrastructure(settings: Settings, config: AppConfig) -> InfraComponents:
    """Build all infrastructure primitives. No async needed — pure construction."""
    ids: IdGenerator = UuidGenerator()
    clock: Clock = SystemClock()
    metrics = Metrics()
    tracer = Tracer(config.tracing)

    db = Database(settings.db_path())
    registry = ServiceRegistry()
    registry.register("db", db)
    lifecycle = Lifecycle(registry)

    bus = MessageBus(db)
    from atlas.infra.bus import MemoryBusEvent
    from atlas.orchestration.events import (
        MemoryEvent, OrchestratorEvent, PlanningEvent, SafetyEvent, ToolEvent,
    )
    bus.register_type("orchestrator", OrchestratorEvent)
    bus.register_type("safety", SafetyEvent)
    bus.register_type("planning", PlanningEvent)
    bus.register_type("memory", MemoryBusEvent)  # use infra type; memory layer publishes this
    bus.register_type("tool", ToolEvent)

    audit = AuditLog(db)
    killswitch = KillSwitch(config.safety.stop_flag_path)

    return InfraComponents(
        ids=ids, clock=clock, metrics=metrics, tracer=tracer,
        db=db, registry=registry, lifecycle=lifecycle,
        bus=bus, audit=audit, killswitch=killswitch,
    )
