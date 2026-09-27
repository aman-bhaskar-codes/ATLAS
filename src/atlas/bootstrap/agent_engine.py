"""Agent-engine bootstrap (M2.1) — build the optional AgentRunService.

Optional-subsystem template (like ``bootstrap/ide.py`` / ``bootstrap/voice.py``):
returns ``AgentEngineComponents`` whose ``service`` is ``None`` when the subsystem
is disabled or its one durable dependency (a run store) is missing. Never raises —
the agent surface is a subsystem, not a startup dependency.

It reuses already-wired runtime seams: the ORCHESTRATION ``ToolDispatcher`` and
``ToolRouter`` (funnel-aligned — NOT the universal tooling fabric), the shared
event-bus ``EventPublisher``, and the SAME shared SQLite substrate the rest of the
runtime uses. Every tool the agent drives is therefore governed by the SAME
``SafetyEngine`` funnel as any other dispatch (Constitution: one execution funnel).
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig, Settings
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.routing_backends import BackendRouter
from atlas.infra.storage_domains import StorageDomain
from atlas.orchestration.agent_engine.engine import SupportsDispatch, SupportsInfer
from atlas.orchestration.agent_engine.event_reader import AgentEventReader, SqliteAgentEventReader
from atlas.orchestration.agent_engine.persistence import (
    AgentRunStore,
    PostgresAgentRunStore,
    SqliteAgentRunStore,
)
from atlas.orchestration.agent_engine.service import DEFAULT_AGENT_SYSTEM_PROMPT, AgentRunService
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.tool_routing import ToolRouter

_log = get_logger("atlas.bootstrap.agent_engine")


@dataclass
class AgentEngineComponents:
    service: AgentRunService | None


def build_agent_engine(
    settings: Settings,
    config: AppConfig,
    *,
    gateway: SupportsInfer,
    dispatcher: SupportsDispatch,
    tool_router: ToolRouter,
    events: EventPublisher | None,
    ids: IdGenerator,
    clock: Clock,
    db: Database | None = None,
    router: BackendRouter | None = None,
) -> AgentEngineComponents:
    cfg = config.agent_engine
    if not cfg.enabled:
        _log.info("agent_engine.disabled", event_type="lifecycle")
        return AgentEngineComponents(service=None)

    # Durable, resumable runs on the SAME substrate everything else uses. Agent
    # runs are orchestration state -> the CORE domain. The BackendRouter applies
    # the fallback chain (CORE dsn / legacy ATLAS_DATABASE_URL -> SQLite) and
    # dedupes the pool; construction stays out of bootstrap (T4). Zero-config
    # resolves CORE to SQLite, so this is the shared DB exactly as before.
    store: AgentRunStore | None = None
    if router is not None and router.effective_dsn(StorageDomain.CORE):
        store = PostgresAgentRunStore(router.resolve_postgres(StorageDomain.CORE))
    elif db is not None:
        store = SqliteAgentRunStore(db)
    if store is None:
        # A durable run store is the whole point of the surface (persisted,
        # resumable, list/continue). Without the shared DB there is nowhere to put
        # runs — degrade cleanly rather than serve an amnesiac agent.
        _log.warning(
            "agent_engine.no_store",
            event_type="lifecycle",
            detail="agent_engine.enabled but no db/connection wired — surface unavailable",
        )
        return AgentEngineComponents(service=None)

    limits = ExecutionLimits(
        max_steps=cfg.max_steps,
        max_tool_calls=cfg.max_tool_calls,
        max_tokens=cfg.max_tokens,
    )
    # The live-console trace reader is always over the shared SQLite bus DB (that is
    # where every event is persisted), independent of the run store's backend. Wired
    # only when the shared DB is present; without it the surface degrades to snapshots.
    event_reader: AgentEventReader | None = SqliteAgentEventReader(db) if db is not None else None
    service = AgentRunService(
        gateway=gateway,
        dispatcher=dispatcher,
        tool_router=tool_router,
        store=store,
        ids=ids,
        clock=clock,
        publisher=events,
        event_reader=event_reader,
        limits=limits,
        system_prompt=cfg.system_prompt or DEFAULT_AGENT_SYSTEM_PROMPT,
        max_tools=cfg.max_tools,
        max_output_tokens=cfg.max_output_tokens,
    )
    _log.info(
        "agent_engine.ready",
        event_type="lifecycle",
        persistence=("postgres" if isinstance(store, PostgresAgentRunStore) else "sqlite"),
        max_steps=cfg.max_steps,
        max_tool_calls=cfg.max_tool_calls,
        max_tools=cfg.max_tools,
    )
    return AgentEngineComponents(service=service)
