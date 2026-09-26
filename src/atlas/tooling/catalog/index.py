"""In-process catalog index.

WHY an index at all (Part 2 §53/§54): the catalog is read-mostly — queries
must never scan SQLite per search. The index is an immutable snapshot built
from the store and swapped atomically on refresh (§55): readers always hold a
consistent view, a running sync never partially leaks into query results.

Deliberately NOT an embedding store — semantic retrieval is Part 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas.tooling.catalog.models import (
    CatalogNamespaceRecord,
    CatalogOperationRecord,
    CatalogSourceInfo,
    CatalogStatus,
    CatalogToolRecord,
)


def _tokens(text: str) -> frozenset[str]:
    """Lowercase word tokens — the lexical search vocabulary (§25)."""
    out: set[str] = set()
    buf: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            buf.append(ch)
        elif buf:
            out.add("".join(buf))
            buf = []
    if buf:
        out.add("".join(buf))
    out.discard("")
    return frozenset(out)


@dataclass(frozen=True)
class _IndexedTool:
    record: CatalogToolRecord
    operations: tuple[CatalogOperationRecord, ...]
    name_tokens: frozenset[str]
    op_tokens: frozenset[str]
    description_tokens: frozenset[str]
    tag_tokens: frozenset[str]
    capability_tokens: frozenset[str]
    namespace_tokens: frozenset[str]


@dataclass(frozen=True)
class CatalogIndex:
    """Immutable point-in-time view of the persisted catalog."""

    catalog_version: int
    sources: tuple[CatalogSourceInfo, ...] = ()
    namespaces: tuple[CatalogNamespaceRecord, ...] = ()
    tools: tuple[CatalogToolRecord, ...] = ()
    _by_id: dict[str, _IndexedTool] = field(default_factory=dict, repr=False)
    _ops: dict[str, tuple[CatalogOperationRecord, ...]] = field(default_factory=dict, repr=False)
    _by_source: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _by_namespace: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _by_capability: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _by_status: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _by_tag: dict[str, set[str]] = field(default_factory=dict, repr=False)

    @classmethod
    def build(
        cls,
        *,
        catalog_version: int,
        sources: list[CatalogSourceInfo],
        namespaces: list[CatalogNamespaceRecord],
        tools: list[CatalogToolRecord],
        operations: dict[str, list[CatalogOperationRecord]],
    ) -> CatalogIndex:
        by_id: dict[str, _IndexedTool] = {}
        ops_map: dict[str, tuple[CatalogOperationRecord, ...]] = {}
        by_source: dict[str, list[str]] = {}
        by_namespace: dict[str, list[str]] = {}
        by_capability: dict[str, list[str]] = {}
        by_status: dict[str, list[str]] = {}
        by_tag: dict[str, set[str]] = {}

        for record in sorted(tools, key=lambda t: t.tool_id):
            tool_ops = tuple(sorted(operations.get(record.tool_id, ()), key=lambda o: o.operation_id))
            indexed = _IndexedTool(
                record=record,
                operations=tool_ops,
                name_tokens=_tokens(record.tool_name),
                op_tokens=_tokens(" ".join(record.operations)),
                description_tokens=_tokens(record.search_text),
                tag_tokens=_tokens(" ".join(record.tags)),
                capability_tokens=_tokens(record.capability or ""),
                namespace_tokens=_tokens(record.namespace_id),
            )
            by_id[record.tool_id] = indexed
            ops_map[record.tool_id] = tool_ops
            by_source.setdefault(record.source_id, []).append(record.tool_id)
            by_namespace.setdefault(record.namespace_id, []).append(record.tool_id)
            if record.capability:
                by_capability.setdefault(record.capability, []).append(record.tool_id)
            by_status.setdefault(record.status.value, []).append(record.tool_id)
            for tag in record.tags:
                by_tag.setdefault(tag, set()).add(record.tool_id)

        for ids in by_capability.values():
            ids.sort()
        for ids in by_status.values():
            ids.sort()
        for ids in by_source.values():
            ids.sort()
        for ids in by_namespace.values():
            ids.sort()

        return cls(
            catalog_version=catalog_version,
            sources=tuple(sorted(sources, key=lambda s: s.source_id)),
            namespaces=tuple(sorted(namespaces, key=lambda n: n.namespace_id)),
            tools=tuple(record for record in (by_id[k].record for k in sorted(by_id))),
            _by_id=by_id,
            _ops=ops_map,
            _by_source=by_source,
            _by_namespace=by_namespace,
            _by_capability=by_capability,
            _by_status=by_status,
            _by_tag=by_tag,
        )

    # ── Lookups ───────────────────────────────────────────────────── #

    def get(self, tool_id: str) -> CatalogToolRecord | None:
        indexed = self._by_id.get(tool_id)
        return indexed.record if indexed else None

    def operations_of(self, tool_id: str) -> tuple[CatalogOperationRecord, ...]:
        return self._ops.get(tool_id, ())

    def operation(self, tool_id: str, name: str) -> CatalogOperationRecord | None:
        for op in self.operations_of(tool_id):
            if op.name == name:
                return op
        return None

    def all_tools(self) -> tuple[CatalogToolRecord, ...]:
        return self.tools

    def namespace_tool_counts(self) -> dict[str, int]:
        return {ns_id: len(ids) for ns_id, ids in self._by_namespace.items()}

    def source_of(self, source_id: str) -> CatalogSourceInfo | None:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        return None

    def namespace_of(self, namespace_id: str) -> CatalogNamespaceRecord | None:
        for namespace in self.namespaces:
            if namespace.namespace_id == namespace_id:
                return namespace
        return None

    def status_counts(self) -> dict[str, int]:
        return {status: len(ids) for status, ids in self._by_status.items()}

    def indexed(self, tool_id: str) -> _IndexedTool | None:
        return self._by_id.get(tool_id)

    def ids_by_source(self, source_id: str) -> list[str]:
        return list(self._by_source.get(source_id, ()))

    def ids_by_tag(self, tag: str) -> set[str]:
        return set(self._by_tag.get(tag, ()))

    def ids_by_status(self, status: CatalogStatus) -> list[str]:
        return list(self._by_status.get(status.value, ()))
