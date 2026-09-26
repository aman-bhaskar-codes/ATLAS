"""Persistent, searchable, versioned Tool Catalog (Part 2).

Distinct from the live ``ToolingRegistry``: the registry answers "what can
this process execute right now?", the catalog answers "what tools is ATLAS
aware of, with which schemas and metadata, from which source, last seen when,
in which catalog state?". See docs/tooling/catalog.md.
"""

from __future__ import annotations

from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.catalog.events import TOPIC_TOOL_CATALOG, CatalogEventPublisher, ToolCatalogEvent
from atlas.tooling.catalog.fingerprints import canonical_json, definition_fingerprint, schema_fingerprint
from atlas.tooling.catalog.models import (
    AuthState,
    Availability,
    CatalogDiff,
    CatalogMatch,
    CatalogNamespaceRecord,
    CatalogOperationRecord,
    CatalogSnapshot,
    CatalogSourceInfo,
    CatalogStatus,
    CatalogSyncError,
    CatalogSyncResult,
    CatalogToolRecord,
    ToolAnnotations,
)
from atlas.tooling.catalog.sources import RegistryCatalogSource, default_sources
from atlas.tooling.catalog.store import ToolCatalogStore
from atlas.tooling.catalog.sync import CatalogSource, CatalogSync

__all__ = [
    "TOPIC_TOOL_CATALOG",
    "AuthState",
    "Availability",
    "CatalogDiff",
    "CatalogEventPublisher",
    "CatalogMatch",
    "CatalogNamespaceRecord",
    "CatalogOperationRecord",
    "CatalogSnapshot",
    "CatalogSource",
    "CatalogSourceInfo",
    "CatalogStatus",
    "CatalogSync",
    "CatalogSyncError",
    "CatalogSyncResult",
    "CatalogToolRecord",
    "RegistryCatalogSource",
    "ToolAnnotations",
    "ToolCatalog",
    "ToolCatalogEvent",
    "ToolCatalogStore",
    "canonical_json",
    "default_sources",
    "definition_fingerprint",
    "schema_fingerprint",
]
