"""Storage domains — the routing keys for domain-routed persistence.

Each :class:`StorageDomain` names one logical persistence account. A domain maps
to at most one Postgres DSN (via :meth:`Settings.configured_dsn`); the
``BackendRouter`` (infra.routing_backends) turns that into a concrete
:class:`~atlas.infra.backends.Connection`, applying the fallback chain
(domain DSN -> CORE DSN -> SQLite) and pool dedupe.

WHY a standalone module: both ``infra.config`` and the router import these keys.
Keeping the enum free of any config import avoids an import cycle and keeps the
domain vocabulary in one place. The string values are stable slugs — they form
the ``ATLAS_SUPABASE_<DOMAIN>_DSN`` env names and the ``schema_version`` account
tags, so do not rename them without a migration.
"""

from __future__ import annotations

from enum import Enum


class StorageDomain(str, Enum):
    """A logical persistence account. Six active, two reserved."""

    CORE = "core"  # Acct 1 (critical): orchestration hot path, event bus, safety/audit, tooling, routing
    IDENTITY = "identity"  # Acct 2 (high): secrets, identities — blast-radius isolation
    MEMORY = "memory"  # Acct 3: episodes, semantic facts, user_model, curated memory, trajectories
    IDE = "ide"  # Acct 4: ide_sessions, ide_workspaces, dev-agent ledger/checkpoints
    RESEARCH = "research"  # Acct 5: research sessions/feedback, fabric_*, knowledge docs/chunks, rag
    TELEMETRY = "telemetry"  # Acct 6: llm_calls, cognitive/eval/adaptation/canary telemetry
    ANALYTICS = "analytics"  # Acct 7: RESERVED — future replica/warehouse or telemetry overflow
    ENVIRONMENT = "environment"  # Acct 8: RESERVED — future prod/dev split or MCP registry DB


# The six domains that carry live data now. The reserved pair stays dormant
# until explicitly enabled — the router treats an unset reserved DSN like any
# other unset domain (fall back), so listing them here is documentation, not
# activation.
ACTIVE_DOMAINS: tuple[StorageDomain, ...] = (
    StorageDomain.CORE,
    StorageDomain.IDENTITY,
    StorageDomain.MEMORY,
    StorageDomain.IDE,
    StorageDomain.RESEARCH,
    StorageDomain.TELEMETRY,
)

RESERVED_DOMAINS: tuple[StorageDomain, ...] = (
    StorageDomain.ANALYTICS,
    StorageDomain.ENVIRONMENT,
)
