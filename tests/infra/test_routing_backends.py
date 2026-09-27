"""T2 — BackendRouter: pool dedupe + fallback chain + lifecycle.

These tests never open a real pool. ``PostgresConnection`` is lazy (asyncpg
connects on first query, not at construction), so we can assert on the deduped
instances the router hands out without touching a network. ``Settings`` is built
with ``_env_file=None`` and every domain DSN env var is cleared, so the
developer's real ``.env`` cannot leak DSNs into the assertions.
"""

from __future__ import annotations

import pytest

from atlas.infra.backends import PostgresConnection
from atlas.infra.config import Settings
from atlas.infra.errors import ConfigError
from atlas.infra.routing_backends import BackendRouter, _RoutedSqliteConnection
from atlas.infra.storage_domains import StorageDomain

_DSN_ENVS = [f"ATLAS_SUPABASE_{d.value.upper()}_DSN" for d in StorageDomain] + [
    "ATLAS_DATABASE_URL",
    "SUPABASE_DB_CONNECTION_STRING",
]


def _clean_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name in _DSN_ENVS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class _FakeDB:
    """Stand-in for ``Database`` — the router only holds the reference; the SQLite
    fallback reads ``.conn`` per call, which these tests never trigger."""

    conn = object()


# ── effective DSN (pure fallback chain) ──────────────────────────────────


def test_zero_config_every_domain_is_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(_clean_settings(monkeypatch), db=_FakeDB())  # type: ignore[arg-type]
    for d in StorageDomain:
        assert router.effective_dsn(d) == "", f"{d} should fall back to SQLite"


def test_explicit_domain_dsn_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(
        _clean_settings(monkeypatch, ATLAS_SUPABASE_MEMORY_DSN="postgres://mem/db"),
        db=_FakeDB(),  # type: ignore[arg-type]
    )
    assert router.effective_dsn(StorageDomain.MEMORY) == "postgres://mem/db"
    # Other domains stay on SQLite (no CORE set to co-locate onto).
    assert router.effective_dsn(StorageDomain.RESEARCH) == ""


def test_core_colocates_unset_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(
        _clean_settings(monkeypatch, ATLAS_SUPABASE_CORE_DSN="postgres://core/db"),
        db=_FakeDB(),  # type: ignore[arg-type]
    )
    # CORE and every unset domain co-locate on the CORE dsn.
    for d in StorageDomain:
        assert router.effective_dsn(d) == "postgres://core/db"


def test_explicit_domain_overrides_core(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(
        _clean_settings(
            monkeypatch,
            ATLAS_SUPABASE_CORE_DSN="postgres://core/db",
            ATLAS_SUPABASE_MEMORY_DSN="postgres://mem/db",
        ),
        db=_FakeDB(),  # type: ignore[arg-type]
    )
    assert router.effective_dsn(StorageDomain.MEMORY) == "postgres://mem/db"
    assert router.effective_dsn(StorageDomain.RESEARCH) == "postgres://core/db"


def test_legacy_ide_alias_maps_to_ide(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(
        _clean_settings(monkeypatch, SUPABASE_DB_CONNECTION_STRING="postgres://legacy-ide/db"),
        db=_FakeDB(),  # type: ignore[arg-type]
    )
    assert router.effective_dsn(StorageDomain.IDE) == "postgres://legacy-ide/db"
    # The legacy IDE alias does NOT co-locate other domains — only CORE does.
    assert router.effective_dsn(StorageDomain.MEMORY) == ""


def test_legacy_database_url_maps_to_core_and_colocates(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(
        _clean_settings(monkeypatch, ATLAS_DATABASE_URL="postgres://legacy-core/db"),
        db=_FakeDB(),  # type: ignore[arg-type]
    )
    assert router.effective_dsn(StorageDomain.CORE) == "postgres://legacy-core/db"
    # As CORE, it co-locates unset domains too.
    assert router.effective_dsn(StorageDomain.RESEARCH) == "postgres://legacy-core/db"


# ── pool dedupe ──────────────────────────────────────────────────────────


def test_same_dsn_shares_one_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    # CORE co-locates every domain, so all resolve to ONE PostgresConnection.
    router = BackendRouter(
        _clean_settings(monkeypatch, ATLAS_SUPABASE_CORE_DSN="postgres://core/db"),
        db=_FakeDB(),  # type: ignore[arg-type]
    )
    core = router.resolve_backend(StorageDomain.CORE)
    research = router.resolve_backend(StorageDomain.RESEARCH)
    memory = router.resolve_backend(StorageDomain.MEMORY)
    assert core is research is memory
    assert isinstance(core, PostgresConnection)
    assert len(router._pools) == 1


def test_distinct_dsn_distinct_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(
        _clean_settings(
            monkeypatch,
            ATLAS_SUPABASE_CORE_DSN="postgres://core/db",
            ATLAS_SUPABASE_MEMORY_DSN="postgres://mem/db",
        ),
        db=_FakeDB(),  # type: ignore[arg-type]
    )
    core = router.resolve_backend(StorageDomain.CORE)
    memory = router.resolve_backend(StorageDomain.MEMORY)
    assert core is not memory
    assert len(router._pools) == 2


def test_sqlite_fallback_is_shared_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(_clean_settings(monkeypatch), db=_FakeDB())  # type: ignore[arg-type]
    a = router.resolve_backend(StorageDomain.CORE)
    b = router.resolve_backend(StorageDomain.MEMORY)
    assert a is b
    assert isinstance(a, _RoutedSqliteConnection)
    assert not router._pools


def test_sqlite_fallback_without_db_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(_clean_settings(monkeypatch), db=None)
    with pytest.raises(ConfigError):
        router.resolve_backend(StorageDomain.CORE)


# ── lifecycle ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_closes_pools_reverse_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = BackendRouter(
        _clean_settings(
            monkeypatch,
            ATLAS_SUPABASE_CORE_DSN="postgres://core/db",
            ATLAS_SUPABASE_MEMORY_DSN="postgres://mem/db",
        ),
        db=_FakeDB(),  # type: ignore[arg-type]
    )
    closed: list[str] = []

    core = router.resolve_backend(StorageDomain.CORE)
    memory = router.resolve_backend(StorageDomain.MEMORY)

    async def _mk(tag: str):  # type: ignore[no-untyped-def]
        async def _close() -> None:
            closed.append(tag)

        return _close

    core.close = await _mk("core")  # type: ignore[method-assign]
    memory.close = await _mk("memory")  # type: ignore[method-assign]

    await router.stop()
    assert closed == ["memory", "core"]  # reverse open order
    assert not router._pools

    await router.stop()  # idempotent — no double close
    assert closed == ["memory", "core"]


@pytest.mark.asyncio
async def test_health_true_when_no_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    router = BackendRouter(_clean_settings(monkeypatch), db=_FakeDB())  # type: ignore[arg-type]
    assert await router.health() is True
