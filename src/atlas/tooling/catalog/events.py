"""Catalog events on the existing MessageBus (Part 2 §34/§35/§78).

A single topic (``tool.catalog``) carries one typed event with a ``kind``
discriminator and enough context to debug a change (source, tool, fingerprints,
catalog version, reason) — and never credentials. Registered in
``bootstrap/infrastructure.py`` like every other bus topic.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from atlas.infra.bus import Event, MessageBus

TOPIC_TOOL_CATALOG = "tool.catalog"

# Event kinds (§34):
KIND_SYNC_COMPLETED = "tool.catalog.sync.completed"
KIND_SYNC_FAILED = "tool.catalog.sync.failed"
KIND_TOOL_ADDED = "tool.catalog.tool.added"
KIND_TOOL_UPDATED = "tool.catalog.tool.updated"
KIND_TOOL_STALE = "tool.catalog.tool.stale"
KIND_TOOL_REMOVED = "tool.catalog.tool.removed"
KIND_NAMESPACE_UPDATED = "tool.catalog.namespace.updated"


class ToolCatalogEvent(Event):
    model_config = ConfigDict(frozen=True)

    kind: str
    source_id: str = ""
    tool_id: str = ""
    namespace_id: str = ""
    old_fingerprint: str = ""
    new_fingerprint: str = ""
    catalog_version: int = 0
    reason: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CatalogEventPublisher:
    """Publishes catalog events AFTER commit; a bus failure is logged, never
    propagated into the sync path (events are observability, not state)."""

    def __init__(self, bus: MessageBus | None) -> None:
        self._bus = bus

    async def publish(self, kind: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        from atlas.infra.logging import get_logger

        log = get_logger("atlas.tooling.catalog.events")
        try:
            event = ToolCatalogEvent(
                correlation_id=str(payload.get("run_id") or "catalog"),
                kind=kind,
                source_id=str(payload.get("source_id", "")),
                tool_id=str(payload.get("tool_id", "")),
                namespace_id=str(payload.get("namespace_id", "")),
                old_fingerprint=str(payload.get("old_fingerprint", "")),
                new_fingerprint=str(payload.get("fingerprint", "")),
                catalog_version=int(payload.get("catalog_version", 0)),
                reason=str(payload.get("reason", "")),
                metadata={k: v for k, v in payload.items() if k not in _TYPED_FIELDS},
            )
            await self._bus.publish(TOPIC_TOOL_CATALOG, event)
        except Exception as exc:
            log.warning(
                "tool.catalog.event.failed",
                event_type="tooling",
                kind=kind,
                error=f"{type(exc).__name__}: {exc}",
            )


_TYPED_FIELDS = frozenset(
    {
        "run_id",
        "source_id",
        "tool_id",
        "namespace_id",
        "old_fingerprint",
        "fingerprint",
        "catalog_version",
        "reason",
    }
)


__all__ = [
    "KIND_NAMESPACE_UPDATED",
    "KIND_SYNC_COMPLETED",
    "KIND_SYNC_FAILED",
    "KIND_TOOL_ADDED",
    "KIND_TOOL_REMOVED",
    "KIND_TOOL_STALE",
    "KIND_TOOL_UPDATED",
    "TOPIC_TOOL_CATALOG",
    "CatalogEventPublisher",
    "ToolCatalogEvent",
]
