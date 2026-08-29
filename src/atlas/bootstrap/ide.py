"""IDE bootstrap — build the optional IDEService from config + wired deps.

Optional-subsystem template (like ``bootstrap/voice.py`` /
``bootstrap/computer_use.py``): returns ``IDEComponents`` whose ``service`` is
``None`` when the ADE is disabled. Never raises — the IDE is a subsystem, not a
startup dependency.

Unlike voice, the ADE needs no API keys: it reuses already-wired runtime
components — the SAME ``SafetyEngine`` funnel and filesystem tool every tool
dispatch uses — so every workspace mutation is governed identically. The IDE
cannot become a side door around ATLAS policy (Constitution).
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.capabilities.ide.persistence import SqliteIDESessionStore
from atlas.capabilities.ide.service import IDEService
from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.safety.engine import SafetyEngine
from atlas.tools.base import Tool

_log = get_logger("atlas.bootstrap.ide")


@dataclass
class IDEComponents:
    service: IDEService | None


def build_ide(
    config: AppConfig,
    *,
    safety: SafetyEngine,
    filesystem_tool: Tool | None,
    ids: IdGenerator,
    clock: Clock,
    db: Database | None = None,
    command_tool: Tool | None = None,
) -> IDEComponents:
    if not config.ide.enabled:
        _log.info("ide.disabled", event_type="lifecycle")
        return IDEComponents(service=None)
    if filesystem_tool is None:
        # The ADE governs every write through the filesystem tool; without it the
        # engine could only read, which is not a usable IDE. Degrade cleanly.
        _log.warning(
            "ide.no_filesystem_tool",
            event_type="lifecycle",
            detail="ide.enabled but no filesystem tool wired — ADE unavailable",
        )
        return IDEComponents(service=None)

    # Durable, resumable workspaces (Phase 17/42) when the shared DB is wired —
    # the SAME SQLite substrate the rest of the runtime uses (Constitution: one
    # persistence layer). Without a db the service runs in-memory-only. A future
    # Neon/Supabase backend is a second `IDESessionStore` impl behind this seam.
    store = SqliteIDESessionStore(db) if db is not None else None
    service = IDEService(
        safety=safety,
        filesystem_tool=filesystem_tool,
        ids=ids,
        clock=clock,
        store=store,
        command_tool=command_tool,
    )
    _log.info(
        "ide.ready",
        event_type="lifecycle",
        allowed_roots=list(config.ide.allowed_roots) or ["<any>"],
        persistence="sqlite" if store is not None else "memory",
        commands="enabled" if command_tool is not None else "disabled",
    )
    return IDEComponents(service=service)
