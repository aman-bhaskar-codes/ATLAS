"""Catalog sources + the registry→catalog sync engine (Part 2 §20/§36-§51).

A ``CatalogSource`` is where discovery comes from; the catalog never imports
source-specific structures (§40) — an MCP source later produces plain
``UniversalToolDefinition`` objects through this exact interface.

Sync semantics, the ones that matter:

* per-source isolation (§22): one failed source leaves the others intact;
* atomic per-source apply (§23): one transaction per source, events only after
  commit (§56);
* a FAILED discovery retains previous rows (§50/§51) — absence after a failed
  refresh is not evidence of removal;
* absent after a SUCCESSFUL authoritative refresh -> STALE, never deleted
  (§19/§50); REMOVED is an explicit operator action only;
* one malformed definition is rejected into ``errors`` without blocking the
  healthy ones (§48).
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from atlas.infra.logging import get_logger
from atlas.tooling.catalog.fingerprints import (
    definition_fingerprint,
    schema_fingerprint,
    schema_size_estimate,
)
from atlas.tooling.catalog.index import CatalogIndex
from atlas.tooling.catalog.models import (
    AuthState,
    Availability,
    CatalogDiff,
    CatalogNamespaceRecord,
    CatalogOperationRecord,
    CatalogSourceInfo,
    CatalogStatus,
    CatalogSyncError,
    CatalogSyncResult,
    CatalogToolRecord,
    ToolAnnotations,
)
from atlas.tooling.catalog.store import ToolCatalogStore
from atlas.tooling.models.tool_definition import UniversalToolDefinition
from atlas.tooling.models.tool_health import ToolRuntimeState, ToolStatus

_log = get_logger("atlas.tooling.catalog.sync")


@runtime_checkable
class CatalogSource(Protocol):
    """One discovery origin (§39). Part 2 ships native + capability sources;
    Part 5 adds MCP through this same interface — no catalog changes."""

    source_id: str
    source_type: str
    display_name: str
    description: str
    version: str

    async def discover(self) -> Sequence[UniversalToolDefinition]: ...


@runtime_checkable
class StatusReportingSource(Protocol):
    """Optional source capability: map a live registry status onto catalog
    lifecycle. Sources that report runtime state get READY/etc.; others are
    cataloged as DISCOVERED (catalog existence ≠ runtime executability)."""

    def runtime_status(self, tool_id: str) -> ToolStatus | None: ...


def _status_from_runtime(state: ToolRuntimeState) -> tuple[CatalogStatus, Availability]:
    return {
        ToolRuntimeState.READY: (CatalogStatus.READY, Availability.AVAILABLE),
        ToolRuntimeState.REGISTERED: (CatalogStatus.DISCOVERED, Availability.UNAVAILABLE),
        ToolRuntimeState.DISABLED: (CatalogStatus.DISABLED, Availability.UNAVAILABLE),
        ToolRuntimeState.FAILED: (CatalogStatus.UNAVAILABLE, Availability.UNAVAILABLE),
    }[state]


@dataclass(frozen=True)
class PreparedTool:
    """A validated discovery result, normalized + fingerprinted."""

    record: CatalogToolRecord
    operations: tuple[CatalogOperationRecord, ...]
    definition_fp: str


def normalize_description(description: str) -> str:
    """Search normalization (§46/§75): collapse whitespace, bound size; the
    original description is preserved untouched on the record for display."""
    normalized = " ".join(description.split())
    return normalized[:2000]


_SECRET_SHAPE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{16,}|"
    r"(?:api[_-]?key|password|passwd|secret|authorization|bearer)[\s=:]+\S+)"
)


def _scrub(value: Any) -> Any:
    """§7/§115: hostile or careless sources must not plant secret-shaped
    strings in tool metadata. Matching strings are redacted, never persisted."""
    if isinstance(value, str):
        return _SECRET_SHAPE.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def validate_definition(definition: UniversalToolDefinition) -> str | None:
    """Return a rejection reason, or None when the definition is catalogable
    (§47/§48). Schemas must be object-shaped or absent; unknown JSON-schema
    fields are preserved, never stripped."""
    if not definition.operations:
        return "no operations declared"
    for schema in (definition.input_schema.fallback, *definition.input_schema.operations.values()):
        if schema and not isinstance(schema, dict):
            return "input schema is not a JSON object schema"
        if schema and schema.get("type") not in (None, "object"):
            return f"input schema type {schema.get('type')!r} is not object-shaped"
    return None


class CatalogSync:
    """Diff engine + transactional applier for one set of sources."""

    def __init__(
        self,
        store: ToolCatalogStore,
        *,
        metrics: object | None = None,
        publish: Any = None,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._publish = publish  # async (kind, payload) -> None; called AFTER commit

    # ── Public API ────────────────────────────────────────────────── #

    async def refresh_source(self, source: CatalogSource, index: CatalogIndex) -> CatalogSyncResult:
        started = datetime.now(UTC)
        t0 = time.perf_counter()
        run_id = f"sync-{uuid.uuid4().hex[:12]}"

        try:
            discovered = list(await source.discover())
        except Exception as exc:
            completed = datetime.now(UTC)
            duration = int((time.perf_counter() - t0) * 1000)
            error = f"{type(exc).__name__}: {exc}"
            # §51: failed discovery mutates nothing but the sync-run record.
            await self._store.record_failed_sync(
                run_id=run_id,
                source=self._source_info(source, ok=False, ts=completed),
                started_at=started,
                completed_at=completed,
                duration_ms=duration,
                error=error,
            )
            _log.warning(
                "tool.catalog.sync.failed",
                event_type="tooling",
                source_id=source.source_id,
                error=error,
            )
            if self._publish is not None:
                await self._publish(
                    "sync.failed",
                    {"source_id": source.source_id, "reason": error, "run_id": run_id},
                )
            return CatalogSyncResult(
                source_id=source.source_id,
                ok=False,
                started_at=started,
                completed_at=completed,
                duration_ms=duration,
                error=error,
            )

        # Validate + normalize; malformed tools are rejected, not fatal (§48).
        status_of = source.runtime_status if isinstance(source, StatusReportingSource) else None
        errors: list[CatalogSyncError] = []
        prepared: list[PreparedTool] = []
        for definition in discovered:
            reason = validate_definition(definition)
            if reason is not None:
                errors.append(CatalogSyncError(tool_id=definition.id, reason=reason))
                continue
            prepared.append(self._prepare(definition, runtime_status=status_of(definition.id) if status_of else None))

        diff, removed_stale_ids = self._diff(source, index, prepared)

        new_status_by_id = {p.record.tool_id: p for p in prepared}
        # §12: a detected definition change bumps the tool's definition_version
        # so old trajectory records stay interpretable against their version.
        for tool_id in diff.changed:
            entry = new_status_by_id[tool_id]
            existing = index.get(tool_id)
            if existing is not None:
                new_status_by_id[tool_id] = PreparedTool(
                    record=entry.record.model_copy(update={"definition_version": existing.definition_version + 1}),
                    operations=entry.operations,
                    definition_fp=entry.definition_fp,
                )
        tools_to_upsert: list[tuple[CatalogToolRecord, list[CatalogOperationRecord]]] = []
        for tool_id in [*diff.added, *diff.changed]:
            entry = new_status_by_id[tool_id]
            tools_to_upsert.append((entry.record, list(entry.operations)))

        now = datetime.now(UTC)
        completed = datetime.now(UTC)
        duration = int((time.perf_counter() - t0) * 1000)
        counts = {
            "run_id": run_id,
            "started_at": started,
            "completed_at": completed,
            "duration_ms": duration,
            "discovered": len(discovered),
            "added": len(diff.added),
            "updated": len(diff.changed),
            "unchanged": len(diff.unchanged),
            "stale": len(diff.stale),
            "removed": len(diff.removed),
            "rejected": len(errors),
        }

        # A runtime-state change on an unchanged definition (e.g. a tool the
        # operator disabled between syncs) is a catalog-VISIBLE change too.
        force_bump = any(
            (existing := index.get(tool_id)) is None
            or existing.status.value != new_status_by_id[tool_id].record.status.value
            or existing.availability.value != new_status_by_id[tool_id].record.availability.value
            for tool_id in diff.unchanged
        )
        counts["force_bump"] = force_bump
        new_version = await self._store.apply_source_delta(
            source=self._source_info(source, ok=True, ts=completed),
            namespace=self._namespace_record(source),
            tools_to_upsert=tools_to_upsert,
            unchanged=[
                (
                    tool_id,
                    new_status_by_id[tool_id].record.status.value,
                    new_status_by_id[tool_id].record.availability.value,
                )
                for tool_id in diff.unchanged
            ],
            stale_ids=removed_stale_ids,
            now=now,
            run=counts,
        )

        result = CatalogSyncResult(
            source_id=source.source_id,
            ok=True,
            started_at=started,
            completed_at=completed,
            duration_ms=duration,
            catalog_version=new_version,
            errors=tuple(errors),
            discovered=len(discovered),
            added=len(diff.added),
            updated=len(diff.changed),
            unchanged=len(diff.unchanged),
            stale=len(diff.stale),
            removed=len(diff.removed),
            rejected=len(errors),
        )
        # §56: events only now — the delta is durably committed.
        if self._publish is not None:
            await self._publish("sync.completed", result.model_dump(mode="json"))
            for tool_id in diff.added:
                await self._publish(
                    "tool.added",
                    {
                        "source_id": source.source_id,
                        "tool_id": tool_id,
                        "catalog_version": new_version,
                        "fingerprint": new_status_by_id[tool_id].definition_fp,
                    },
                )
            for tool_id in diff.changed:
                await self._publish(
                    "tool.updated",
                    {
                        "source_id": source.source_id,
                        "tool_id": tool_id,
                        "catalog_version": new_version,
                        "fingerprint": new_status_by_id[tool_id].definition_fp,
                    },
                )
            for tool_id in diff.stale:
                await self._publish(
                    "tool.stale",
                    {"source_id": source.source_id, "tool_id": tool_id, "catalog_version": new_version},
                )
        self._observe_metrics(result)
        _log.info(
            "tool.catalog.sync.completed",
            event_type="tooling",
            source_id=source.source_id,
            added=result.added,
            updated=result.updated,
            unchanged=result.unchanged,
            stale=result.stale,
            rejected=result.rejected,
            catalog_version=new_version,
            duration_ms=duration,
        )
        return result

    # ── Internals ─────────────────────────────────────────────────── #

    def _prepare(
        self,
        definition: UniversalToolDefinition,
        *,
        runtime_status: ToolStatus | None = None,
    ) -> PreparedTool:
        input_fp = schema_fingerprint(definition.input_schema.fallback)
        output_fp = schema_fingerprint(definition.output_schema)
        size, tokens = schema_size_estimate(definition.input_schema.fallback)
        now = datetime.now(UTC)
        definition_fp = definition_fingerprint(definition)
        annotations = ToolAnnotations(
            # Derived from OUR metadata for Part-2 sources; never treated as authorization (§45).
            read_only_hint=not definition.policy.side_effects,
            destructive_hint=False,
            idempotent_hint=definition.policy.idempotent,
            open_world_hint=definition.policy.network_required,
        )
        if runtime_status is not None:
            status, availability = _status_from_runtime(runtime_status.state)
        else:
            status, availability = CatalogStatus.DISCOVERED, Availability.UNKNOWN
        operations = tuple(
            CatalogOperationRecord(
                operation_id=f"{definition.id}:{operation}",
                tool_id=definition.id,
                name=operation,
                description="",
                input_schema=definition.input_schema.schema_for(operation),
                output_schema=definition.output_schema,
                schema_fingerprint=schema_fingerprint(definition.input_schema.schema_for(operation)),
                annotations=annotations,
            )
            for operation in definition.operations
        )
        record = CatalogToolRecord(
            tool_id=definition.id,
            tool_name=definition.name,
            namespace_id=_namespace_id(definition),
            source_id=_source_id_of(definition),
            provider=definition.provider,
            adapter=definition.adapter,
            version=definition.version,
            definition_version=definition.definition_version,
            description=definition.description,
            search_text=normalize_description(definition.description),
            capability=definition.capability,
            operations=definition.operations,
            input_schema=definition.input_schema.fallback,
            output_schema=definition.output_schema,
            input_schema_fp=input_fp,
            output_schema_fp=output_fp,
            definition_fp=definition_fp,
            execution_type=definition.execution_type,
            status=status,
            availability=availability,
            requires_auth=definition.policy.requires_auth,
            auth_state=AuthState.REQUIRED if definition.policy.requires_auth else AuthState.NONE,
            cost_class=definition.policy.cost_class.value,
            estimated_cost_usd=definition.estimated_cost_usd,
            estimated_latency_ms=definition.estimated_latency_ms,
            schema_bytes=size,
            estimated_schema_tokens=tokens,
            safety_tool=definition.safety_tool,
            default_tier=int(definition.policy.default_tier),
            side_effects=definition.policy.side_effects,
            idempotent=definition.policy.idempotent,
            rollback_support=definition.policy.supports_rollback,
            trust_level=definition.policy.trust_level,
            locality=definition.locality,
            privacy_class=definition.policy.max_privacy.value,
            network_required=definition.policy.network_required,
            tags=definition.tags,
            metadata=_scrub(dict(definition.metadata)),
            last_seen_ts=now,
        )
        return PreparedTool(record=record, operations=operations, definition_fp=definition_fp)

    def _diff(
        self,
        source: CatalogSource,
        index: CatalogIndex,
        prepared: list[PreparedTool],
    ) -> tuple[CatalogDiff, list[str]]:
        """Compare fresh discovery against the persisted snapshot for this
        source (§20/§52). Returns the diff + the ids that go STALE."""
        existing_ids = set(index.ids_by_source(source.source_id))
        fresh_ids = {p.record.tool_id for p in prepared}
        fp_by_id = {p.record.tool_id: p.definition_fp for p in prepared}

        added = sorted(fresh_ids - existing_ids)
        changed: list[str] = []
        unchanged: list[str] = []
        for tool_id in sorted(fresh_ids & existing_ids):
            existing = index.get(tool_id)
            if existing is not None and existing.definition_fp != fp_by_id[tool_id]:
                changed.append(tool_id)
            else:
                unchanged.append(tool_id)
        # §51: absence after a SUCCESSFUL refresh is STALE, never deleted.
        stale = sorted(existing_ids - fresh_ids)
        diff = CatalogDiff(
            added=tuple(added),
            changed=tuple(changed),
            unchanged=tuple(unchanged),
            stale=tuple(stale),
        )
        return diff, stale

    def _source_info(self, source: CatalogSource, *, ok: bool, ts: datetime) -> CatalogSourceInfo:
        from atlas.tooling.models.tool_definition import Locality

        locality = getattr(source, "locality", Locality.LOCAL)
        if not isinstance(locality, Locality):
            locality = Locality(str(locality))
        return CatalogSourceInfo(
            source_id=source.source_id,
            source_type=source.source_type,
            display_name=source.display_name,
            description=source.description,
            version=source.version,
            trust_level=str(getattr(source, "trust_level", "system_builtin")),
            locality=locality,
            last_sync_ts=ts,
            last_sync_ok=ok,
        )

    def _namespace_record(self, source: CatalogSource) -> CatalogNamespaceRecord:
        name = source.source_id.split(":", 1)[1] if ":" in source.source_id else source.source_id
        return CatalogNamespaceRecord(
            namespace_id=f"{source.source_id}:{name}",
            source_id=source.source_id,
            name=name,
            description=source.description or source.display_name,
            version=source.version,
        )

    def _observe_metrics(self, result: CatalogSyncResult) -> None:
        if self._metrics is None:
            return
        observe = getattr(self._metrics, "observe", None)
        counter = getattr(self._metrics, "counter", None)
        if callable(observe):
            observe("tool.catalog.sync.duration_ms", float(result.duration_ms))
        if callable(counter):
            counter("tool.catalog.sync.tools", float(result.discovered))


def _source_id_of(definition: UniversalToolDefinition) -> str:
    return f"{definition.namespace.value}:{definition.provider}"


def _namespace_id(definition: UniversalToolDefinition) -> str:
    source = _source_id_of(definition)
    return f"{source}:{definition.provider}"


__all__ = [
    "CatalogSource",
    "CatalogSync",
    "PreparedTool",
    "StatusReportingSource",
    "normalize_description",
    "validate_definition",
]
