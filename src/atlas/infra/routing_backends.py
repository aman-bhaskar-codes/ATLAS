"""BackendRouter — the single seam that maps a StorageDomain to a Connection.

WHY one seam: every store obtains its backend connection here and nowhere else.
No store constructs a ``PostgresConnection`` directly (that is what today's
``bootstrap/{ide,research,agent_engine}.py`` do — T4/T5 rewire them through this
router). Centralizing resolution buys three things the scattered construction
cannot:

* **Pool dedupe** — two domains pointing at the SAME dsn share ONE asyncpg pool.
  Pools are keyed by dsn, not by domain, so co-locating six domains on one
  Supabase account opens one pool, not six.
* **Fallback chain** — per domain: explicit domain dsn -> legacy alias -> CORE
  dsn -> SQLite. This lets accounts be enabled incrementally: set CORE first to
  co-locate everything on one Postgres, then peel domains onto their own
  accounts. CORE with no dsn = SQLite (== today's zero-config behaviour).
* **Lifecycle ownership** — the router is a ``Service``; ``stop()`` closes every
  pool it opened, in reverse order, and is idempotent. The SQLite connection is
  owned by ``Database`` and is NOT closed here.

Backward compatibility (design rule 4): the legacy ``ATLAS_DATABASE_URL`` maps to
CORE and legacy ``SUPABASE_DB_CONNECTION_STRING`` maps to IDE, so existing
single-Postgres setups keep working with no config rename.

This module is persistence ROUTING only. It never touches SafetyEngine, tool
dispatch, or the audit funnel.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from atlas.infra.backends import Connection, PostgresConnection, SQLiteConnection
from atlas.infra.config import Settings
from atlas.infra.db import Database
from atlas.infra.errors import ConfigError
from atlas.infra.logging import get_logger
from atlas.infra.storage_domains import StorageDomain

_log = get_logger("atlas.infra.routing_backends")


class _RoutedSqliteConnection:
    """SQLite fallback backend: delegates to the shared ``Database`` per call.

    WHY not cache a ``SQLiteConnection`` at resolve time: ``resolve_backend`` may
    run during bootstrap, BEFORE ``Database.start()`` opens the aiosqlite
    connection. Reading ``db.conn`` per operation defers that access to call time
    (by which the db is started) and transparently tracks a db restart. The wrap
    is a thin reference holder, so per-call construction is effectively free.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def _c(self) -> SQLiteConnection:
        return SQLiteConnection(self._db.conn)

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        return await self._c().fetchone(sql, params)

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return await self._c().fetchall(sql, params)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return await self._c().execute(sql, params)

    async def commit(self) -> None:
        await self._c().commit()

    async def close(self) -> None:
        return None  # owned by Database


class BackendRouter:
    """Resolves a :class:`Connection` per :class:`StorageDomain`.

    Register with the lifecycle registry: ``start`` is a no-op (pools are lazy —
    an asyncpg pool is created on first query, not at construction), ``stop``
    closes every pool, ``health`` reports pool liveness.
    """

    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self._settings = settings
        self._db = db
        # Pools keyed by DSN (dedupe): one PostgresConnection per distinct dsn.
        self._pools: dict[str, PostgresConnection] = {}
        # Insertion order preserved by dict; stop() closes in reverse.
        self._sqlite: _RoutedSqliteConnection | None = None
        self._closed = False

    # ── resolution ────────────────────────────────────────────────────

    def effective_dsn(self, domain: StorageDomain) -> str:
        """The DSN a domain resolves to after the fallback chain, or "" for SQLite.

        Chain: explicit domain dsn -> legacy alias for that domain -> CORE dsn
        (co-locate) -> "" (SQLite). Pure/read-only — no pool is created here, so
        this is safe to call from diagnostics (``atlas doctor``, T6).
        """
        s = self._settings
        # 1. explicit per-domain dsn
        dsn = s.configured_dsn(domain)
        if dsn:
            return dsn
        # 2. legacy aliases mapped to their domain (backward compat, rule 4)
        if domain is StorageDomain.IDE and s.supabase_db_connection_string:
            return s.supabase_db_connection_string
        if domain is StorageDomain.CORE and s.database_url:
            return s.database_url
        # 3. co-locate on CORE (CORE's own dsn or its legacy alias)
        core = s.configured_dsn(StorageDomain.CORE) or s.database_url
        if core:
            return core
        # 4. SQLite
        return ""

    def resolve_backend(self, domain: StorageDomain) -> Connection:
        """Return the (deduped) :class:`Connection` for ``domain``.

        Postgres connections are shared per DSN; when the chain lands on SQLite,
        the single shared-``Database`` fallback is returned. Never opens a
        network pool eagerly — asyncpg connects on the first query.
        """
        dsn = self.effective_dsn(domain)
        if dsn:
            pool = self._pools.get(dsn)
            if pool is None:
                pool = PostgresConnection(dsn)
                self._pools[dsn] = pool
                _log.info(
                    "backend.pool.opened",
                    event_type="lifecycle",
                    domain=domain.value,
                    # dsn is a secret (embeds a password) — never log it; count only.
                    pools=len(self._pools),
                )
            return pool
        # SQLite fallback — requires the shared Database.
        if self._db is None:
            raise ConfigError(
                f"domain {domain.value!r} resolved to SQLite fallback but no Database was wired into the BackendRouter"
            )
        if self._sqlite is None:
            self._sqlite = _RoutedSqliteConnection(self._db)
        return self._sqlite

    def resolve_postgres(self, domain: StorageDomain) -> PostgresConnection:
        """Resolve ``domain`` asserting it lands on a Postgres account.

        For the Postgres-specific stores that take a concrete
        :class:`PostgresConnection` (not the :class:`Connection` seam) and whose
        SQLite sibling instead takes the shared ``Database``. Callers gate on
        :meth:`effective_dsn` being non-empty before calling this; the runtime
        check keeps the type honest and turns a routing bug into a clear error
        instead of an ``AttributeError`` deep in a store.
        """
        conn = self.resolve_backend(domain)
        if not isinstance(conn, PostgresConnection):
            raise ConfigError(
                f"domain {domain.value!r} did not resolve to a Postgres account "
                "(effective DSN is empty) — caller should use the SQLite store"
            )
        return conn

    # ── lifecycle (Service protocol) ──────────────────────────────────

    async def start(self) -> None:
        # Pools are lazy; nothing to open. Reset the closed flag so a restart
        # (Lifecycle.restart) can hand out fresh pools.
        self._closed = False

    async def stop(self) -> None:
        """Close every Postgres pool in reverse open order. Idempotent."""
        if self._closed:
            return
        for dsn in reversed(list(self._pools)):
            pool = self._pools[dsn]
            try:
                await pool.close()
            except Exception as exc:  # best-effort-complete: leaking is worse than a log
                _log.error("backend.pool.close_failed", event_type="lifecycle", error=repr(exc))
        self._pools.clear()
        self._sqlite = None
        self._closed = True

    async def close(self) -> None:
        """Alias for :meth:`stop` — the task's named teardown entry point."""
        await self.stop()

    async def health(self) -> bool:
        """Every open pool answers ``SELECT 1``. No open pools = healthy (lazy)."""
        for pool in self._pools.values():
            try:
                await pool.fetchone("SELECT 1")
            except Exception:
                return False
        return True
