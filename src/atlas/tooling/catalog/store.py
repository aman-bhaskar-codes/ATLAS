"""SQLite persistence for the tool catalog.

WHY the existing SQLite substrate (Part 2 §3/§15/§76): single-user local-first
ATLAS already runs one aiosqlite connection with WAL and numbered forward
migrations; the catalog adds tables, not a database engine. The durable
representation is explicit, normalized columns (§16/§76) — no pickles, no
blobs-of-JSON-per-row except the JSON schemas that ARE data (§17).

Transaction model (§23/§56/§57): one source sync = one Python-level write
batch on the shared connection followed by a single commit; a failure rolls
back to the previous durable catalog. Catalog READS never touch SQL at query
time — they come from an immutable in-memory index rebuilt after each commit
and swapped atomically (§53-§55), so no reader can observe a half-applied
delta. If the process crashes mid-refresh, the previous commit stands.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from atlas.infra.db import Database
from atlas.tooling.catalog.models import (
    AuthState,
    Availability,
    CatalogNamespaceRecord,
    CatalogOperationRecord,
    CatalogSourceInfo,
    CatalogStatus,
    CatalogToolRecord,
    ToolAnnotations,
)


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _from_json(raw: object, kind: type) -> Any:
    if raw is None:
        return None
    try:
        parsed = json.loads(str(raw))
    except ValueError:
        return None
    return parsed if isinstance(parsed, kind) else None


def _hint_out(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _hint_in(value: object) -> bool | None:
    return None if value is None else bool(value)


class ToolCatalogStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ── Source / namespace upserts ────────────────────────────────── #

    async def upsert_source(self, info: CatalogSourceInfo, now: datetime) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO tool_sources
                (source_id, source_type, display_name, description, version,
                 trust_level, locality, last_sync_ts, last_sync_ok, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                source_type=excluded.source_type,
                display_name=excluded.display_name,
                description=excluded.description,
                version=excluded.version,
                trust_level=excluded.trust_level,
                locality=excluded.locality,
                last_sync_ts=excluded.last_sync_ts,
                last_sync_ok=excluded.last_sync_ok,
                updated_ts=excluded.updated_ts
            """,
            (
                info.source_id,
                info.source_type,
                info.display_name,
                info.description,
                info.version,
                info.trust_level,
                info.locality.value,
                _iso(info.last_sync_ts) or _iso(now),
                None if info.last_sync_ok is None else int(info.last_sync_ok),
                _iso(now),
                _iso(now),
            ),
        )

    async def upsert_namespace(self, record: CatalogNamespaceRecord, now: datetime) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO tool_namespaces
                (namespace_id, source_id, name, description, version, status,
                 tags_json, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, name) DO UPDATE SET
                namespace_id=excluded.namespace_id,
                description=excluded.description,
                version=excluded.version,
                status=excluded.status,
                tags_json=excluded.tags_json,
                updated_ts=excluded.updated_ts
            """,
            (
                record.namespace_id,
                record.source_id,
                record.name,
                record.description,
                record.version,
                record.status.value,
                _to_json(list(record.tags)),
                _iso(record.created_ts) or _iso(now),
                _iso(now),
            ),
        )

    # ── Tool upsert + operation replacement ───────────────────────── #

    async def upsert_tool(
        self,
        record: CatalogToolRecord,
        operations: list[CatalogOperationRecord],
        now: datetime,
    ) -> None:
        conn = self._db.conn
        await conn.execute(
            """
            INSERT INTO tool_definitions (
                tool_id, source_id, namespace_id, name, version, definition_version,
                description, search_text, capability, operations_json,
                input_schema_json, output_schema_json,
                input_schema_fp, output_schema_fp, definition_fp,
                execution_type, adapter, status, enabled, availability,
                requires_auth, credential_reference, auth_state,
                cost_class, estimated_cost_usd, estimated_latency_ms,
                schema_bytes, estimated_schema_tokens,
                safety_tool, default_tier, side_effects, idempotent, rollback_support,
                trust_level, locality, privacy_class, network_required,
                tags_json, metadata_json,
                created_ts, updated_ts, last_seen_ts, last_validated_ts
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?
            )
            ON CONFLICT(tool_id) DO UPDATE SET
                source_id=excluded.source_id,
                namespace_id=excluded.namespace_id,
                name=excluded.name,
                version=excluded.version,
                definition_version=excluded.definition_version,
                description=excluded.description,
                search_text=excluded.search_text,
                capability=excluded.capability,
                operations_json=excluded.operations_json,
                input_schema_json=excluded.input_schema_json,
                output_schema_json=excluded.output_schema_json,
                input_schema_fp=excluded.input_schema_fp,
                output_schema_fp=excluded.output_schema_fp,
                definition_fp=excluded.definition_fp,
                execution_type=excluded.execution_type,
                adapter=excluded.adapter,
                status=excluded.status,
                enabled=excluded.enabled,
                availability=excluded.availability,
                requires_auth=excluded.requires_auth,
                credential_reference=excluded.credential_reference,
                auth_state=excluded.auth_state,
                cost_class=excluded.cost_class,
                estimated_cost_usd=excluded.estimated_cost_usd,
                estimated_latency_ms=excluded.estimated_latency_ms,
                schema_bytes=excluded.schema_bytes,
                estimated_schema_tokens=excluded.estimated_schema_tokens,
                safety_tool=excluded.safety_tool,
                default_tier=excluded.default_tier,
                side_effects=excluded.side_effects,
                idempotent=excluded.idempotent,
                rollback_support=excluded.rollback_support,
                trust_level=excluded.trust_level,
                locality=excluded.locality,
                privacy_class=excluded.privacy_class,
                network_required=excluded.network_required,
                tags_json=excluded.tags_json,
                metadata_json=excluded.metadata_json,
                updated_ts=excluded.updated_ts,
                last_seen_ts=excluded.last_seen_ts,
                last_validated_ts=excluded.last_validated_ts
            """,
            (
                record.tool_id,
                record.source_id,
                record.namespace_id,
                record.tool_name,
                record.version,
                record.definition_version,
                record.description,
                record.search_text,
                record.capability,
                _to_json(list(record.operations)),
                _to_json(record.input_schema),
                _to_json(record.output_schema) if record.output_schema is not None else None,
                record.input_schema_fp,
                record.output_schema_fp,
                record.definition_fp,
                record.execution_type.value,
                record.adapter,
                record.status.value,
                int(record.enabled),
                record.availability.value,
                int(record.requires_auth),
                record.credential_reference,
                record.auth_state.value,
                record.cost_class,
                record.estimated_cost_usd,
                record.estimated_latency_ms,
                record.schema_bytes,
                record.estimated_schema_tokens,
                record.safety_tool,
                record.default_tier,
                int(record.side_effects),
                int(record.idempotent),
                int(record.rollback_support),
                record.trust_level,
                record.locality.value,
                record.privacy_class,
                int(record.network_required),
                _to_json(list(record.tags)),
                _to_json(record.metadata),
                _iso(record.created_ts) or _iso(now),
                _iso(now),
                _iso(record.last_seen_ts) or _iso(now),
                _iso(record.last_validated_ts),
            ),
        )
        # §16: operations are their own rows; replacing the set for an
        # upserted tool keeps the operation table authoritative (no stale
        # leftovers when a source drops an operation).
        await conn.execute("DELETE FROM tool_operations WHERE tool_id = ?", (record.tool_id,))
        for op in operations:
            await conn.execute(
                """
                INSERT INTO tool_operations (
                    operation_id, tool_id, name, description,
                    input_schema_json, output_schema_json, schema_fingerprint,
                    read_only_hint, destructive_hint, idempotent_hint, open_world_hint,
                    status, created_ts, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    description=excluded.description,
                    input_schema_json=excluded.input_schema_json,
                    output_schema_json=excluded.output_schema_json,
                    schema_fingerprint=excluded.schema_fingerprint,
                    read_only_hint=excluded.read_only_hint,
                    destructive_hint=excluded.destructive_hint,
                    idempotent_hint=excluded.idempotent_hint,
                    open_world_hint=excluded.open_world_hint,
                    status=excluded.status,
                    updated_ts=excluded.updated_ts
                """,
                (
                    op.operation_id,
                    op.tool_id,
                    op.name,
                    op.description,
                    _to_json(op.input_schema),
                    _to_json(op.output_schema) if op.output_schema is not None else None,
                    op.schema_fingerprint,
                    _hint_out(op.annotations.read_only_hint),
                    _hint_out(op.annotations.destructive_hint),
                    _hint_out(op.annotations.idempotent_hint),
                    _hint_out(op.annotations.open_world_hint),
                    op.status.value,
                    _iso(op.created_ts) or _iso(now),
                    _iso(now),
                ),
            )

    async def update_tool_status(
        self,
        tool_ids: list[str],
        status: CatalogStatus,
        now: datetime,
        *,
        availability: Availability | None = None,
        touch_last_seen: bool = False,
    ) -> None:
        """Lifecycle transitions on existing rows (stale/removed/disable)."""
        if not tool_ids:
            return
        sets = ["status=?", "updated_ts=?"]
        params: list[Any] = [status.value, _iso(now)]
        if availability is not None:
            sets.append("availability=?")
            params.append(availability.value)
        if touch_last_seen:
            sets.append("last_seen_ts=?")
            params.append(_iso(now))
        placeholders = ",".join("?" * len(tool_ids))
        params.extend(tool_ids)
        await self._db.conn.execute(
            f"UPDATE tool_definitions SET {', '.join(sets)} WHERE tool_id IN ({placeholders})",
            params,
        )

    # ── Reads (index rebuild + inspection; never per-search-query) ── #

    async def load_sources(self) -> list[CatalogSourceInfo]:
        out: list[CatalogSourceInfo] = []
        cur = await self._db.conn.execute("SELECT * FROM tool_sources ORDER BY source_id")
        async for row in cur:
            out.append(
                CatalogSourceInfo(
                    source_id=row["source_id"],
                    source_type=row["source_type"],
                    display_name=row["display_name"],
                    description=row["description"],
                    version=row["version"],
                    trust_level=row["trust_level"],
                    locality=row["locality"],
                    last_sync_ts=_dt(row["last_sync_ts"]),
                    last_sync_ok=None if row["last_sync_ok"] is None else bool(row["last_sync_ok"]),
                )
            )
        return out

    async def load_namespaces(self) -> list[CatalogNamespaceRecord]:
        out: list[CatalogNamespaceRecord] = []
        cur = await self._db.conn.execute("SELECT * FROM tool_namespaces ORDER BY namespace_id")
        async for row in cur:
            out.append(
                CatalogNamespaceRecord(
                    namespace_id=row["namespace_id"],
                    source_id=row["source_id"],
                    name=row["name"],
                    description=row["description"],
                    version=row["version"],
                    status=CatalogStatus(row["status"]),
                    tags=tuple(_from_json(row["tags_json"], list) or ()),
                    created_ts=_dt(row["created_ts"]),
                    updated_ts=_dt(row["updated_ts"]),
                )
            )
        return out

    async def load_tools(self, *, source_id: str | None = None) -> list[CatalogToolRecord]:
        sql = "SELECT * FROM tool_definitions"
        params: tuple[Any, ...] = ()
        if source_id is not None:
            sql += " WHERE source_id = ?"
            params = (source_id,)
        sql += " ORDER BY tool_id"
        out: list[CatalogToolRecord] = []
        cur = await self._db.conn.execute(sql, params)
        async for row in cur:
            out.append(self._tool_from_row(row))
        return out

    async def load_operations(self) -> dict[str, list[CatalogOperationRecord]]:
        ops: dict[str, list[CatalogOperationRecord]] = {}
        cur = await self._db.conn.execute("SELECT * FROM tool_operations ORDER BY operation_id")
        async for row in cur:
            ops.setdefault(row["tool_id"], []).append(
                CatalogOperationRecord(
                    operation_id=row["operation_id"],
                    tool_id=row["tool_id"],
                    name=row["name"],
                    description=row["description"],
                    input_schema=_from_json(row["input_schema_json"], dict) or {},
                    output_schema=_from_json(row["output_schema_json"], dict),
                    schema_fingerprint=row["schema_fingerprint"],
                    annotations=ToolAnnotations(
                        read_only_hint=_hint_in(row["read_only_hint"]),
                        destructive_hint=_hint_in(row["destructive_hint"]),
                        idempotent_hint=_hint_in(row["idempotent_hint"]),
                        open_world_hint=_hint_in(row["open_world_hint"]),
                    ),
                    status=CatalogStatus(row["status"]),
                    created_ts=_dt(row["created_ts"]),
                    updated_ts=_dt(row["updated_ts"]),
                )
            )
        return ops

    def _tool_from_row(self, row: Any) -> CatalogToolRecord:
        parts = str(row["tool_id"]).split(":")
        provider = parts[1] if len(parts) == 3 else ""
        return CatalogToolRecord(
            tool_id=row["tool_id"],
            tool_name=row["name"],
            namespace_id=row["namespace_id"],
            source_id=row["source_id"],
            provider=provider,
            adapter=row["adapter"],
            version=row["version"],
            definition_version=int(row["definition_version"]),
            description=row["description"],
            search_text=row["search_text"],
            capability=row["capability"],
            operations=tuple(_from_json(row["operations_json"], list) or ()),
            input_schema=_from_json(row["input_schema_json"], dict) or {},
            output_schema=_from_json(row["output_schema_json"], dict),
            input_schema_fp=row["input_schema_fp"],
            output_schema_fp=row["output_schema_fp"],
            definition_fp=row["definition_fp"],
            execution_type=row["execution_type"],
            status=CatalogStatus(row["status"]),
            enabled=bool(row["enabled"]),
            availability=Availability(row["availability"]),
            requires_auth=bool(row["requires_auth"]),
            credential_reference=row["credential_reference"],
            auth_state=AuthState(row["auth_state"]),
            cost_class=row["cost_class"],
            estimated_cost_usd=float(row["estimated_cost_usd"]),
            estimated_latency_ms=int(row["estimated_latency_ms"]),
            schema_bytes=int(row["schema_bytes"]),
            estimated_schema_tokens=int(row["estimated_schema_tokens"]),
            safety_tool=row["safety_tool"],
            default_tier=int(row["default_tier"]),
            side_effects=bool(row["side_effects"]),
            idempotent=bool(row["idempotent"]),
            rollback_support=bool(row["rollback_support"]),
            trust_level=row["trust_level"],
            locality=row["locality"],
            privacy_class=row["privacy_class"],
            network_required=bool(row["network_required"]),
            tags=tuple(_from_json(row["tags_json"], list) or ()),
            metadata=_from_json(row["metadata_json"], dict) or {},
            created_ts=_dt(row["created_ts"]),
            updated_ts=_dt(row["updated_ts"]),
            last_seen_ts=_dt(row["last_seen_ts"]),
            last_validated_ts=_dt(row["last_validated_ts"]),
        )

    # ── Catalog version + sync runs (§21/§33) ─────────────────────── #

    async def get_catalog_version(self) -> int:
        cur = await self._db.conn.execute("SELECT value FROM catalog_state WHERE key = 'catalog_version'")
        row = await cur.fetchone()
        return int(row["value"]) if row else 0

    async def _bump_catalog_version(self) -> int:
        version = await self.get_catalog_version()
        new_version = version + 1
        await self._db.conn.execute(
            "INSERT INTO catalog_state (key, value) VALUES ('catalog_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(new_version),),
        )
        return new_version

    async def record_sync_run(
        self,
        *,
        run_id: str,
        source_id: str,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: int,
        discovered: int,
        added: int,
        updated: int,
        unchanged: int,
        stale: int,
        removed: int,
        rejected: int,
        ok: bool,
        error: str | None,
        catalog_version: int,
    ) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO tool_sync_runs (
                id, source_id, started_ts, completed_ts, duration_ms,
                discovered, added, updated, unchanged, stale, removed, rejected,
                ok, error, catalog_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_id,
                _iso(started_at),
                _iso(completed_at),
                duration_ms,
                discovered,
                added,
                updated,
                unchanged,
                stale,
                removed,
                rejected,
                int(ok),
                error,
                catalog_version,
            ),
        )

    # ── Atomic source sync transaction (§23/§56) ──────────────────── #

    async def apply_source_delta(
        self,
        *,
        source: CatalogSourceInfo,
        namespace: CatalogNamespaceRecord,
        tools_to_upsert: list[tuple[CatalogToolRecord, list[CatalogOperationRecord]]],
        unchanged: list[tuple[str, str, str]],
        stale_ids: list[str],
        now: datetime,
        run: dict[str, Any],
    ) -> int:
        """Apply one source's full delta as a single transaction and return
        the new catalog version. ``unchanged`` carries (tool_id, status,
        availability) so a runtime state change between syncs (e.g. a tool
        disabled in the live registry) still lands even when the definition
        fingerprint is identical. On any exception the batch rolls back and
        the previous committed catalog remains valid (§57). Events are
        published by the caller ONLY after this commit succeeded (§56)."""
        conn = self._db.conn
        try:
            await self.upsert_source(source, now)
            await self.upsert_namespace(namespace, now)
            for record, ops in tools_to_upsert:
                await self.upsert_tool(record, ops, now)
            # Unchanged definitions: refresh lifecycle state + last_seen (§19).
            for tool_id, status, availability in unchanged:
                await conn.execute(
                    "UPDATE tool_definitions SET status=?, availability=?, last_seen_ts=?, updated_ts=? "
                    "WHERE tool_id=?",
                    (status, availability, _iso(now), _iso(now), tool_id),
                )
            if stale_ids:
                await self.update_tool_status(
                    stale_ids, CatalogStatus.STALE, now, availability=Availability.UNAVAILABLE
                )
            # §33: the version is an epoch of catalog-VISIBLE change — an
            # entirely unchanged re-sync does not move it.
            changed_catalog = bool(tools_to_upsert or stale_ids or run.get("force_bump"))
            new_version = await self._bump_catalog_version() if changed_catalog else (await self.get_catalog_version())
            await self.record_sync_run(
                run_id=run["run_id"],
                source_id=source.source_id,
                started_at=run["started_at"],
                completed_at=run["completed_at"],
                duration_ms=run["duration_ms"],
                discovered=run["discovered"],
                added=run["added"],
                updated=run["updated"],
                unchanged=run["unchanged"],
                stale=run["stale"],
                removed=run["removed"],
                rejected=run["rejected"],
                ok=True,
                error=None,
                catalog_version=new_version,
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        return new_version

    async def record_failed_sync(
        self,
        *,
        run_id: str,
        source: CatalogSourceInfo,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: int,
        error: str,
    ) -> None:
        """A FAILED discovery is NOT evidence that tools were removed (§51):
        record the failure, touch nothing else, keep the previous rows."""
        conn = self._db.conn
        try:
            await self.upsert_source(
                source.model_copy(update={"last_sync_ts": completed_at, "last_sync_ok": False}),
                completed_at,
            )
            await self.record_sync_run(
                run_id=run_id,
                source_id=source.source_id,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                discovered=0,
                added=0,
                updated=0,
                unchanged=0,
                stale=0,
                removed=0,
                rejected=0,
                ok=False,
                error=error,
                catalog_version=await self.get_catalog_version(),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


__all__ = ["ToolCatalogStore"]
