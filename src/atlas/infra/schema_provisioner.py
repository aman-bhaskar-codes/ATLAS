"""SchemaProvisioner — idempotently provision each Postgres account a domain routes to.

WHY this exists: :class:`~atlas.infra.db.Database` provisions the SQLite file
(the zero-config default). When a :class:`StorageDomain` is routed to a Postgres
DSN, that account needs the same tables created before any store queries it.
This provisioner walks the domains, finds every DISTINCT Postgres DSN the router
resolves to, and applies the schema to each — exactly once, resumably.

Design rule 8 (idempotent, transactional, per-account ``schema_version``): each
account carries its own ``schema_version`` row and is brought up the SAME
migration chain SQLite uses (``infra.db._MIGRATIONS``), one step at a time with
the version stamped after each — so a partial failure resumes instead of
replaying from zero.

DDL SOURCE (per the T3 decision): the canonical SQLite ``_MIGRATIONS`` are
translated to Postgres dialect at provision time by :func:`translate_ddl`. The
translation surface is small and mechanical (``INTEGER PRIMARY KEY AUTOINCREMENT``
-> ``BIGSERIAL PRIMARY KEY``; ``REAL`` -> ``DOUBLE PRECISION``; ``ADD COLUMN`` ->
``ADD COLUMN IF NOT EXISTS`` for replay-safety). Because translation makes the
DDL idempotent (every ``CREATE``/``ADD COLUMN`` is ``IF NOT EXISTS``), a replay
after a mid-chain failure is safe — strictly better than SQLite's version-gated
non-idempotent replay.

CONSEQUENCE (documented deviation from "group DDL by domain"): under the
translate-existing approach each Postgres account receives the FULL translated
schema, not a domain-sliced subset. Domain routing still directs which account a
store's queries hit; unused tables on an account are inert. Splitting the schema
per domain would require authoring domain-grouped DDL, which this approach
deliberately does not do.

This module is persistence provisioning only — it never touches SafetyEngine,
tool dispatch, or the audit funnel.
"""

from __future__ import annotations

import re

from atlas.infra.backends import Connection
from atlas.infra.db import _MIGRATIONS
from atlas.infra.logging import get_logger
from atlas.infra.routing_backends import BackendRouter
from atlas.infra.storage_domains import ACTIVE_DOMAINS, StorageDomain

_log = get_logger("atlas.infra.schema_provisioner")

# ── dialect translation (SQLite _MIGRATIONS -> Postgres) ─────────────────────

# `INTEGER PRIMARY KEY AUTOINCREMENT` (SQLite rowid alias) -> Postgres identity.
_AUTOINC = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE)
# Standalone REAL type token -> DOUBLE PRECISION (SQLite REAL is an 8-byte float).
_REAL = re.compile(r"\bREAL\b", re.IGNORECASE)
# `ADD COLUMN <name>` -> `ADD COLUMN IF NOT EXISTS <name>` for replay-safety.
# SQLite has no ADD COLUMN IF NOT EXISTS and relies on version gating; Postgres
# does, so we make each ALTER idempotent. Skip if already guarded.
_ADD_COLUMN = re.compile(r"ADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE)


def translate_ddl(script: str) -> str:
    """Translate one SQLite migration script to portable Postgres DDL.

    Mechanical and bounded — see module docstring. Everything else in the
    migrations (``TEXT``, ``INTEGER``, ``CHECK``, ``UNIQUE``, literal ``DEFAULT``,
    ``CREATE TABLE/INDEX IF NOT EXISTS``) is already valid Postgres.
    """
    out = _AUTOINC.sub("BIGSERIAL PRIMARY KEY", script)
    out = _REAL.sub("DOUBLE PRECISION", out)
    out = _ADD_COLUMN.sub("ADD COLUMN IF NOT EXISTS ", out)
    return out


class SchemaProvisioner:
    """Provision the schema on every distinct Postgres account the router routes to.

    SQLite-resolved domains are skipped — :class:`~atlas.infra.db.Database` owns
    that file. Constructing with the default ``_MIGRATIONS`` keeps a single source
    of truth for the schema; a custom tuple is injectable for tests.
    """

    def __init__(
        self, router: BackendRouter, migrations: tuple[str, ...] = _MIGRATIONS
    ) -> None:
        self._router = router
        self._migrations = migrations

    async def provision(self) -> list[int]:
        """Bring every Postgres account up to the current schema version.

        Returns the final version reached per DISTINCT account that provisioned
        successfully (order = first domain that resolved to it). Empty list under
        zero-config (all SQLite) — a pure no-op. DSNs are secrets and are NEVER
        returned or logged.

        Resilience (design rule: CORE critical, optional domains degrade): the
        CORE account is provisioned eagerly and a failure there is FATAL — it is
        the orchestration hot path. A failure provisioning any OTHER account is
        logged and swallowed so one unreachable optional Supabase cannot brick
        startup; that domain's store will surface the error on first use, and
        ``atlas doctor`` (T6) reports it. This is why a stale/dead DSN in ``.env``
        degrades instead of crashing the runtime.
        """
        core_dsn = self._router.effective_dsn(StorageDomain.CORE)
        seen: dict[str, Connection] = {}
        for domain in ACTIVE_DOMAINS:
            dsn = self._router.effective_dsn(domain)
            if not dsn:
                continue  # SQLite fallback — owned by Database, not provisioned here
            if dsn in seen:
                continue  # dedupe: co-located domains share one account
            seen[dsn] = self._router.resolve_backend(domain)

        versions: list[int] = []
        for dsn, conn in seen.items():
            try:
                versions.append(await self._apply(conn))
            except Exception as exc:
                if dsn and dsn == core_dsn:
                    # CORE is the critical account — never start on a broken hot path.
                    raise
                _log.warning(
                    "schema.provision_degraded",
                    event_type="db",
                    # dsn is a secret — never log it; report only that AN optional
                    # account was unreachable so its domain degrades.
                    error=repr(exc),
                )
        if versions:
            _log.info(
                "schema.provisioned",
                event_type="db",
                accounts=len(versions),
                target_version=len(self._migrations),
            )
        return versions

    async def _apply(self, conn: Connection) -> int:
        """Apply pending migrations to one account, stamping version per step.

        Mirrors :meth:`Database._apply_migrations`: seed ``schema_version`` at the
        CURRENT version (0 on a fresh account), then apply ``_migrations[current:]``
        one at a time, writing the version after each so a failure is resumable.
        Translated DDL is idempotent, so a replayed step is a no-op.
        """
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = await conn.fetchone("SELECT version FROM schema_version LIMIT 1")
        current = int(row["version"]) if row else 0
        if row is None:
            await conn.execute("INSERT INTO schema_version(version) VALUES (?)", (current,))
            await conn.commit()
        for i, script in enumerate(self._migrations[current:], start=current + 1):
            await conn.execute(translate_ddl(script))
            await conn.execute("UPDATE schema_version SET version=?", (i,))
            await conn.commit()
        return len(self._migrations)
