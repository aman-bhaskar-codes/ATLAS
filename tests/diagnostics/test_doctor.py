from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

from atlas.app import Atlas
from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.identity.secret_store import SecretStore
from atlas.diagnostics.doctor import _check_domain_backends, _verify_encrypted_store
from atlas.infra.db import _MIGRATIONS, Database
from atlas.infra.storage_domains import StorageDomain


async def _noop_audit(**kwargs: object) -> None:
    return None


def _identity(memory_db: Database) -> tuple[IdentityPlatform, SecretStore]:
    store = SecretStore(memory_db, "doctor-test-key")
    identity = IdentityPlatform(store=store, db=memory_db, strategies={}, audit=_noop_audit)
    return identity, store


async def test_verify_encrypted_store_accepts_decryptable_ciphertext(memory_db: Database) -> None:
    identity, store = _identity(memory_db)
    await store.put("credential-1", "secret-value")
    atlas = cast(Atlas, SimpleNamespace(identity=identity))

    ok, detail = await _verify_encrypted_store(atlas)

    assert ok
    assert detail == "1 encrypted secret rows verified"


async def test_verify_encrypted_store_rejects_corrupt_ciphertext(memory_db: Database) -> None:
    identity, _store = _identity(memory_db)
    await memory_db.conn.execute(
        "INSERT INTO secrets(id, ciphertext) VALUES (?, ?)",
        ("credential-1", "not-a-fernet-token"),
    )
    await memory_db.conn.commit()
    atlas = cast(Atlas, SimpleNamespace(identity=identity))

    ok, detail = await _verify_encrypted_store(atlas)

    assert not ok
    assert detail == "vault verification failed: DecryptionError"


# ── T6: domain-routed backend health ─────────────────────────────────────────


class _StubConn:
    """A backend conn that answers SELECT 1 and returns a fixed schema_version."""

    def __init__(self, *, version: int | None, reachable: bool = True, has_schema: bool = True) -> None:
        self._version = version
        self._reachable = reachable
        self._has_schema = has_schema

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        if not self._reachable:
            raise OSError("nodename nor servname provided, or not known")
        if "schema_version" in sql.lower():
            if not self._has_schema:
                raise RuntimeError('relation "schema_version" does not exist')
            return {"version": self._version} if self._version is not None else None
        return {"?column?": 1}  # SELECT 1


class _StubRouter:
    """Maps domains -> dsn; hands out a caller-supplied conn per distinct dsn."""

    def __init__(self, dsns: dict[StorageDomain, str], conns: dict[str, _StubConn]) -> None:
        self._dsns = dsns
        self._conns = conns

    def effective_dsn(self, domain: StorageDomain) -> str:
        return self._dsns.get(domain, "")

    def resolve_backend(self, domain: StorageDomain) -> _StubConn:
        return self._conns[self._dsns[domain]]


def _atlas_with_router(dsns: dict[StorageDomain, str], conns: dict[str, _StubConn]) -> Atlas:
    return cast(Atlas, SimpleNamespace(router=_StubRouter(dsns, conns)))


async def test_backends_zero_config_reports_sqlite() -> None:
    atlas = _atlas_with_router({}, {})  # every domain -> "" (SQLite)
    results = await _check_domain_backends(atlas)
    assert len(results) == 1
    assert results[0].name == "backends.routing"
    assert results[0].status == "pass"
    assert "SQLite" in results[0].detail


async def test_backends_reachable_provisioned_account_passes() -> None:
    dsns = {d: "postgres://core/db" for d in StorageDomain}
    conns = {"postgres://core/db": _StubConn(version=len(_MIGRATIONS))}
    results = await _check_domain_backends(_atlas_with_router(dsns, conns))
    assert len(results) == 1  # one deduped account
    assert results[0].status == "pass"
    assert f"{len(_MIGRATIONS)}/{len(_MIGRATIONS)}" in results[0].detail


async def test_backends_core_unreachable_fails() -> None:
    dsns = {d: "postgres://core/db" for d in StorageDomain}
    conns = {"postgres://core/db": _StubConn(version=None, reachable=False)}
    results = await _check_domain_backends(_atlas_with_router(dsns, conns))
    assert results[0].status == "fail"
    assert "CORE" in results[0].detail


async def test_backends_optional_unreachable_warns() -> None:
    dsns = {d: "postgres://core/db" for d in StorageDomain}
    dsns[StorageDomain.MEMORY] = "postgres://mem/db"  # a distinct, dead optional account
    conns = {
        "postgres://core/db": _StubConn(version=len(_MIGRATIONS)),
        "postgres://mem/db": _StubConn(version=None, reachable=False),
    }
    results = await _check_domain_backends(_atlas_with_router(dsns, conns))
    by_status = {r.status for r in results}
    assert "pass" in by_status  # CORE healthy
    warn = next(r for r in results if r.status == "warn")
    assert "optional" in warn.detail and "memory" in warn.name


async def test_backends_reachable_but_unprovisioned_warns() -> None:
    dsns = {d: "postgres://core/db" for d in StorageDomain}
    conns = {"postgres://core/db": _StubConn(version=None, has_schema=False)}
    results = await _check_domain_backends(_atlas_with_router(dsns, conns))
    assert results[0].status == "warn"
    assert "not provisioned" in results[0].detail


async def test_backends_stale_schema_warns() -> None:
    dsns = {d: "postgres://core/db" for d in StorageDomain}
    conns = {"postgres://core/db": _StubConn(version=len(_MIGRATIONS) - 1)}
    results = await _check_domain_backends(_atlas_with_router(dsns, conns))
    assert results[0].status == "warn"
    assert "stale" in results[0].detail
