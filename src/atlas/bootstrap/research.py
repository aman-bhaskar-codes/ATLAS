"""Research bootstrap (Phase 1) — build the optional ResearchService.

Optional-subsystem template (like ``bootstrap/agent_engine.py`` / ``bootstrap/
ide.py``): returns ``ResearchComponents`` whose ``service`` is ``None`` when the
subsystem is disabled or its durable dependency (a session store) is missing.
Never raises — the research surface is a subsystem, not a startup dependency.

It reuses already-wired runtime seams: the ORCHESTRATION ``ToolDispatcher``
(funnel-aligned — it drives the governed ``knowledge`` tool over the SAME
``SafetyEngine`` funnel as any other dispatch) and the SAME shared SQLite
substrate the rest of the runtime uses. There is no separate retrieval system and
no separate safety path — the service only adds session persistence on top of the
existing governed pipeline (Constitution: one funnel, one fabric).
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.infra.backends import PostgresConnection
from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig, Settings
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.orchestration.agent_engine.engine import SupportsDispatch
from atlas.orchestration.research.persistence import (
    PostgresResearchSessionStore,
    ResearchSessionStore,
    SqliteResearchSessionStore,
)
from atlas.orchestration.research.service import ResearchService

_log = get_logger("atlas.bootstrap.research")


@dataclass
class ResearchComponents:
    service: ResearchService | None


def build_research(
    settings: Settings,
    config: AppConfig,
    *,
    dispatcher: SupportsDispatch,
    ids: IdGenerator,
    clock: Clock,
    db: Database | None = None,
) -> ResearchComponents:
    cfg = config.research
    if not cfg.enabled:
        _log.info("research.disabled", event_type="lifecycle")
        return ResearchComponents(service=None)

    # Durable, resumable sessions on the SAME substrate everything else uses.
    # Postgres only if a Supabase/Neon connection is actually configured (reserved
    # seam — the placeholder is empty by default), else the shared SQLite DB.
    store: ResearchSessionStore | None = None
    if settings.supabase_db_connection_string:
        store = PostgresResearchSessionStore(PostgresConnection(settings.supabase_db_connection_string))
    elif db is not None:
        store = SqliteResearchSessionStore(db)
    if store is None:
        # A durable session store is the whole point of the surface (persisted,
        # resumable, list). Without the shared DB there is nowhere to put sessions
        # — degrade cleanly rather than serve an amnesiac research surface.
        _log.warning(
            "research.no_store",
            event_type="lifecycle",
            detail="research.enabled but no db/connection wired — surface unavailable",
        )
        return ResearchComponents(service=None)

    service = ResearchService(
        dispatcher=dispatcher,
        store=store,
        ids=ids,
        clock=clock,
        default_mode=cfg.default_mode,
        max_question_chars=cfg.max_question_chars,
        max_sessions_listed=cfg.max_sessions_listed,
    )
    _log.info(
        "research.ready",
        event_type="lifecycle",
        persistence=("postgres" if isinstance(store, PostgresResearchSessionStore) else "sqlite"),
        default_mode=cfg.default_mode,
    )
    return ResearchComponents(service=service)
