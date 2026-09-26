"""ToolCatalog — the durable discovery/index facade (Part 2).

Answers "what is ATLAS aware of?" (search/filter/inspect) while the Part-1
``ToolingRegistry`` answers "what can this process execute right now?" (§2).
All queries are served from an immutable in-memory index (§53); all writes go
through per-source transactional sync (§23); ``refresh_source``/``refresh_all``
are the programmatic re-discovery API (§38).

Router preparation lives here but routing does NOT (§71/§72):
``find_candidates`` returns deterministic candidate sets; ranking is Part 3.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from atlas.infra.logging import get_logger
from atlas.infra.types import ToolCallSpec
from atlas.tooling.catalog.index import CatalogIndex
from atlas.tooling.catalog.models import (
    CatalogMatch,
    CatalogNamespaceRecord,
    CatalogOperationRecord,
    CatalogSnapshot,
    CatalogStatus,
    CatalogSyncResult,
    CatalogToolRecord,
)
from atlas.tooling.catalog.search import lexical_search
from atlas.tooling.catalog.sources import default_sources
from atlas.tooling.catalog.store import ToolCatalogStore
from atlas.tooling.catalog.sync import CatalogSource, CatalogSync
from atlas.tooling.errors import ToolNotFound
from atlas.tooling.registry.registry import ToolingRegistry

_log = get_logger("atlas.tooling.catalog")


class ToolCatalog:
    def __init__(
        self,
        *,
        store: ToolCatalogStore,
        registry: ToolingRegistry,
        metrics: object | None = None,
        publish: Any = None,
        sources: list[CatalogSource] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._injected_sources = sources
        self._sync = CatalogSync(store, metrics=metrics, publish=publish)
        self._index = CatalogIndex.build(catalog_version=0, sources=[], namespaces=[], tools=[], operations={})

    # ── Lifecycle ─────────────────────────────────────────────────── #

    async def initialize(self) -> None:
        """Load the persisted catalog into memory, then reconcile with the
        live registry (§36/§37). Persistence is authoritative: what survived
        from previous runs is visible BEFORE any sync runs."""
        await self.rebuild_index()
        await self.refresh_all()

    async def refresh_all(self) -> list[CatalogSyncResult]:
        """Sync every registered source. SEQUENTIAL on purpose: the sources
        share one SQLite connection, and per-source error isolation (§22) is
        what matters — a failed source logs its failure and the loop moves on,
        exactly like gather(return_exceptions=True) would, without interleaving
        transactions on the shared connection."""
        results: list[CatalogSyncResult] = []
        for source in self.sources():
            try:
                results.append(await self.refresh_source(source.source_id))
            except Exception as exc:
                _log.error(
                    "tool.catalog.sync.isolated_failure",
                    event_type="tooling",
                    source_id=source.source_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
        return results

    async def refresh_source(self, source_id: str) -> CatalogSyncResult:
        source = self._require_source(source_id)
        result = await self._sync.refresh_source(source, self._index)
        await self.rebuild_index()  # atomic snapshot swap after commit (§55)
        return result

    async def rebuild_index(self) -> None:
        """Reconstruct the in-memory index from the database — proves
        persistence is authoritative (§58) and powers restart recovery (§59)."""
        version = await self._store.get_catalog_version()
        sources = await self._store.load_sources()
        namespaces = await self._store.load_namespaces()
        tools = await self._store.load_tools()
        operations = await self._store.load_operations()
        self._index = CatalogIndex.build(
            catalog_version=version,
            sources=sources,
            namespaces=namespaces,
            tools=tools,
            operations=operations,
        )

    # ── Sources / lifecycle metadata ──────────────────────────────── #

    def sources(self) -> list[CatalogSource]:
        if self._injected_sources is not None:
            return self._injected_sources
        return default_sources(self._registry)

    def _require_source(self, source_id: str) -> CatalogSource:
        for source in self.sources():
            if source.source_id == source_id:
                return source
        raise ToolNotFound(f"catalog source {source_id!r} is not registered")

    # ── Structured queries (§25/§71) ──────────────────────────────── #

    def find(
        self,
        *,
        capability: str | None = None,
        operation: str | None = None,
        namespace: str | None = None,
        source_id: str | None = None,
        execution_type: str | None = None,
        status: str | None = None,
        enabled: bool | None = None,
        tag: str | None = None,
    ) -> list[CatalogToolRecord]:
        out: list[CatalogToolRecord] = []
        for record in self._index.all_tools():
            if capability is not None and record.capability != capability:
                continue
            if operation is not None and operation not in record.operations:
                continue
            if namespace is not None and record.namespace_id != namespace:
                continue
            if source_id is not None and record.source_id != source_id:
                continue
            if execution_type is not None and record.execution_type.value != execution_type:
                continue
            if status is not None and record.status.value != status:
                continue
            if enabled is not None and record.enabled != enabled:
                continue
            if tag is not None and tag not in record.tags:
                continue
            out.append(record)
        return out

    def find_candidates(
        self,
        *,
        capability: str | None = None,
        operation: str | None = None,
        namespace: str | None = None,
        execution_type: str | None = None,
        status: str | None = None,
        tags: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[CatalogToolRecord]:
        """Deterministic candidate discovery for the Part-3 router (§71).
        NO ranking here (§72): results are ordered by tool_id. Default status
        filter is READY — candidates are tools the source currently reports
        live; callers can widen explicitly."""
        effective_status = status if status is not None else CatalogStatus.READY.value
        candidates = self.find(
            capability=capability,
            operation=operation,
            namespace=namespace,
            execution_type=execution_type,
            status=effective_status,
        )
        if tags:
            wanted = set(tags)
            candidates = [c for c in candidates if wanted & set(c.tags)]
        return candidates[:limit] if limit is not None else candidates

    # ── Lexical search (§25-§28) ──────────────────────────────────── #

    def search(self, query: str, *, limit: int = 20, statuses: tuple[str, ...] | None = None) -> list[CatalogMatch]:
        effective = frozenset(statuses) if statuses is not None else None
        return lexical_search(self._index, query, limit=limit, statuses=effective)

    # ── Inspection (§30/§31) ──────────────────────────────────────── #

    def inspect(self, tool_id: str) -> CatalogToolRecord | None:
        return self._index.get(tool_id)

    def inspect_operations(self, tool_id: str) -> tuple[CatalogOperationRecord, ...]:
        return self._index.operations_of(tool_id)

    def inspect_operation(self, tool_id: str, operation: str) -> CatalogOperationRecord | None:
        return self._index.operation(tool_id, operation)

    def require(self, tool_id: str) -> CatalogToolRecord:
        record = self._index.get(tool_id)
        if record is None:
            raise ToolNotFound(f"tool {tool_id!r} is not in the tool catalog")
        return record

    # ── Namespaces (§29) ──────────────────────────────────────────── #

    def list_namespaces(self) -> list[CatalogNamespaceRecord]:
        counts = self._index.namespace_tool_counts()
        return [
            namespace.model_copy(update={"tool_count": counts.get(namespace.namespace_id, 0)})
            for namespace in self._index.namespaces
        ]

    def get_namespace(self, namespace_id: str) -> CatalogNamespaceRecord | None:
        for namespace in self.list_namespaces():
            if namespace.namespace_id == namespace_id:
                return namespace
        return None

    def search_namespaces(self, query: str) -> list[CatalogNamespaceRecord]:
        tokens = set(query.lower().replace(":", " ").split())
        out: list[CatalogNamespaceRecord] = []
        for namespace in self.list_namespaces():
            haystack = f"{namespace.namespace_id} {namespace.name} {namespace.description}".lower()
            if tokens & set(haystack.split()):
                out.append(namespace)
        return out

    def namespace_tools(self, namespace_id: str) -> list[CatalogToolRecord]:
        return [r for r in self._index.all_tools() if r.namespace_id == namespace_id]

    def namespace_health_summary(self, namespace_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.namespace_tools(namespace_id):
            counts[record.status.value] = counts.get(record.status.value, 0) + 1
        return counts

    def namespace_auth_summary(self, namespace_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.namespace_tools(namespace_id):
            counts[record.auth_state.value] = counts.get(record.auth_state.value, 0) + 1
        return counts

    # ── Model-surface preparation (§73) ───────────────────────────── #

    def to_tool_specs(self, tool_ids: list[str]) -> tuple[ToolCallSpec, ...]:
        """Provider-native schemas for a SELECTED shortlist only — never the
        whole catalog (§73)."""
        specs = []
        for tool_id in tool_ids:
            record = self._index.get(tool_id)
            if record is not None:
                specs.append(record.to_tool_spec())
        return tuple(specs)

    # ── Snapshot / version (§32/§33) ──────────────────────────────── #

    def version(self) -> int:
        return self._index.catalog_version

    def snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot(
            snapshot_id=f"cat-{uuid.uuid4().hex[:12]}",
            created_at=datetime.now(UTC),
            catalog_version=self._index.catalog_version,
            tool_count=len(self._index.tools),
            namespace_count=len(self._index.namespaces),
            source_count=len(self._index.sources),
            operation_count=sum(len(self._index.operations_of(r.tool_id)) for r in self._index.tools),
            status_counts=self._index.status_counts(),
        )

    def summary(self) -> dict[str, object]:
        """Catalog overview for CLI/API (§69)."""
        sources = self._index.sources
        last_sync = max((s.last_sync_ts for s in sources if s.last_sync_ts is not None), default=None)
        return {
            "catalog_version": self._index.catalog_version,
            "sources": [s.model_dump(mode="json") for s in sources],
            "namespace_count": len(self._index.namespaces),
            "tool_count": len(self._index.tools),
            "operation_count": sum(len(self._index.operations_of(r.tool_id)) for r in self._index.tools),
            "status_counts": self._index.status_counts(),
            "last_sync": last_sync.isoformat() if last_sync else None,
        }


__all__ = ["ToolCatalog"]
