"""T3 — SchemaProvisioner: dialect translation + per-account idempotent provisioning.

No live Postgres is touched. The translator is tested directly; the provisioner
is driven against a fake router handing out fake connections that record every
statement, so we assert on version gating, dedupe, and the SQLite skip without a
network.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from atlas.infra.schema_provisioner import SchemaProvisioner, translate_ddl
from atlas.infra.storage_domains import ACTIVE_DOMAINS, StorageDomain

_MIGS = (
    "CREATE TABLE IF NOT EXISTS t1 (id INTEGER PRIMARY KEY AUTOINCREMENT, score REAL);",
    "ALTER TABLE t1 ADD COLUMN note TEXT;",
    "CREATE INDEX IF NOT EXISTS idx_t1 ON t1(note);",
)


# ── translator ───────────────────────────────────────────────────────────


def test_autoincrement_becomes_bigserial() -> None:
    out = translate_ddl("id INTEGER PRIMARY KEY AUTOINCREMENT,")
    assert "BIGSERIAL PRIMARY KEY" in out
    assert "AUTOINCREMENT" not in out


def test_real_becomes_double_precision() -> None:
    out = translate_ddl("score REAL NOT NULL DEFAULT 0")
    assert "DOUBLE PRECISION" in out
    assert "REAL" not in out


def test_add_column_gets_if_not_exists() -> None:
    out = translate_ddl("ALTER TABLE t ADD COLUMN c TEXT;")
    assert "ADD COLUMN IF NOT EXISTS c TEXT" in out


def test_add_column_not_double_guarded() -> None:
    out = translate_ddl("ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;")
    assert out.count("IF NOT EXISTS") == 1


def test_portable_ddl_unchanged() -> None:
    ddl = "CREATE TABLE IF NOT EXISTS x (id TEXT PRIMARY KEY, n INTEGER, CHECK (n > 0));"
    assert translate_ddl(ddl) == ddl


# ── fakes ──────────────────────────────────────────────────────────────────


class _FakeConn:
    """Records executes; models schema_version so version gating is exercised."""

    def __init__(self) -> None:
        self.scripts: list[str] = []
        self._version: int | None = None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        self.scripts.append(sql)
        low = sql.lower()
        if "insert into schema_version" in low:
            self._version = int(params[0])
        elif "update schema_version set version" in low:
            self._version = int(params[0])
        return 0

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        if "from schema_version" in sql.lower():
            return {"version": self._version} if self._version is not None else None
        return None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return []

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _FailingConn(_FakeConn):
    """A connection whose first execute raises — models an unreachable account."""

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        raise OSError("nodename nor servname provided, or not known")


class _FakeRouter:
    """Maps domains -> dsn; hands out one _FakeConn per distinct dsn (dedupe).

    ``fail_dsns`` marks accounts that raise on first execute (unreachable host).
    """

    def __init__(self, dsns: dict[StorageDomain, str], fail_dsns: set[str] | None = None) -> None:
        self._dsns = dsns
        self._fail = fail_dsns or set()
        self._conns: dict[str, _FakeConn] = {}

    def effective_dsn(self, domain: StorageDomain) -> str:
        return self._dsns.get(domain, "")

    def resolve_backend(self, domain: StorageDomain) -> _FakeConn:
        dsn = self.effective_dsn(domain)
        if dsn not in self._conns:
            self._conns[dsn] = _FailingConn() if dsn in self._fail else _FakeConn()
        return self._conns[dsn]


# ── provisioner ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_config_provisions_nothing() -> None:
    router = _FakeRouter({})  # every domain -> "" (SQLite)
    prov = SchemaProvisioner(router, migrations=_MIGS)  # type: ignore[arg-type]
    assert await prov.provision() == []
    assert router._conns == {}  # no account ever resolved


@pytest.mark.asyncio
async def test_single_account_applies_all_migrations() -> None:
    router = _FakeRouter({d: "postgres://core/db" for d in ACTIVE_DOMAINS})
    prov = SchemaProvisioner(router, migrations=_MIGS)  # type: ignore[arg-type]
    versions = await prov.provision()
    assert versions == [len(_MIGS)]  # one deduped account, brought to current
    conn = router._conns["postgres://core/db"]
    # AUTOINCREMENT translated on the way in.
    assert any("BIGSERIAL PRIMARY KEY" in s for s in conn.scripts)
    assert not any("AUTOINCREMENT" in s for s in conn.scripts)
    assert conn._version == len(_MIGS)


@pytest.mark.asyncio
async def test_distinct_dsn_two_accounts() -> None:
    dsns = {d: "postgres://core/db" for d in ACTIVE_DOMAINS}
    dsns[StorageDomain.MEMORY] = "postgres://mem/db"
    prov = SchemaProvisioner(_FakeRouter(dsns), migrations=_MIGS)  # type: ignore[arg-type]
    versions = await prov.provision()
    assert versions == [len(_MIGS), len(_MIGS)]  # two distinct accounts


@pytest.mark.asyncio
async def test_resume_from_existing_version() -> None:
    router = _FakeRouter({d: "postgres://core/db" for d in ACTIVE_DOMAINS})
    conn = router.resolve_backend(StorageDomain.CORE)
    conn._version = len(_MIGS) - 1  # already migrated all but the last step
    prov = SchemaProvisioner(router, migrations=_MIGS)  # type: ignore[arg-type]
    await prov.provision()
    # Only the final migration's DDL should have been applied (index/last script).
    applied_ddl = [s for s in conn.scripts if "idx_t1" in s]
    assert applied_ddl  # the last migration ran
    assert not any("BIGSERIAL" in s for s in conn.scripts)  # earlier steps skipped
    assert conn._version == len(_MIGS)


@pytest.mark.asyncio
async def test_optional_account_failure_degrades() -> None:
    """An unreachable OPTIONAL account is logged+swallowed; startup proceeds."""
    dsns = {d: "postgres://core/db" for d in ACTIVE_DOMAINS}
    dsns[StorageDomain.MEMORY] = "postgres://mem/db"  # a distinct, dead account
    router = _FakeRouter(dsns, fail_dsns={"postgres://mem/db"})
    prov = SchemaProvisioner(router, migrations=_MIGS)  # type: ignore[arg-type]
    versions = await prov.provision()
    # CORE provisioned; the dead MEMORY account degraded rather than crashing.
    assert versions == [len(_MIGS)]
    assert router._conns["postgres://core/db"]._version == len(_MIGS)


@pytest.mark.asyncio
async def test_core_account_failure_is_fatal() -> None:
    """An unreachable CORE account is FATAL — never start on a broken hot path."""
    dsns = {d: "postgres://core/db" for d in ACTIVE_DOMAINS}
    router = _FakeRouter(dsns, fail_dsns={"postgres://core/db"})
    prov = SchemaProvisioner(router, migrations=_MIGS)  # type: ignore[arg-type]
    with pytest.raises(OSError):
        await prov.provision()
