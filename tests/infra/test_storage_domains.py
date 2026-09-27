"""T1 — StorageDomain enum + per-domain DSN config surface.

Zero-config parity is the load-bearing guarantee: with no DSNs set, every
domain reports "" (the router will therefore fall back to SQLite, exactly as
today). We construct ``Settings(_env_file=None)`` so the developer's real
``.env`` cannot leak DSNs into the assertions.
"""

from __future__ import annotations

import pytest

from atlas.infra.config import Settings
from atlas.infra.storage_domains import (
    ACTIVE_DOMAINS,
    RESERVED_DOMAINS,
    StorageDomain,
)


def _settings(**env: str) -> Settings:
    # _env_file=None ignores the real .env; env vars still applied via monkeypatch.
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_all_eight_domains_exist_with_stable_slugs() -> None:
    assert {d.value for d in StorageDomain} == {
        "core",
        "identity",
        "memory",
        "ide",
        "research",
        "telemetry",
        "analytics",
        "environment",
    }
    assert len(ACTIVE_DOMAINS) == 6
    assert set(RESERVED_DOMAINS) == {StorageDomain.ANALYTICS, StorageDomain.ENVIRONMENT}
    # Active and reserved partition the enum with no overlap.
    assert set(ACTIVE_DOMAINS).isdisjoint(RESERVED_DOMAINS)
    assert set(ACTIVE_DOMAINS) | set(RESERVED_DOMAINS) == set(StorageDomain)


def test_zero_config_every_domain_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear any domain DSNs the process env might carry (CI parity / no regression).
    for d in StorageDomain:
        monkeypatch.delenv(f"ATLAS_SUPABASE_{d.value.upper()}_DSN", raising=False)
    settings = _settings()
    for d in StorageDomain:
        assert settings.configured_dsn(d) == "", f"{d} should default to empty"


def test_setting_core_dsn_is_isolated_to_core(monkeypatch: pytest.MonkeyPatch) -> None:
    for d in StorageDomain:
        monkeypatch.delenv(f"ATLAS_SUPABASE_{d.value.upper()}_DSN", raising=False)
    monkeypatch.setenv("ATLAS_SUPABASE_CORE_DSN", "postgres://core-host/db")
    settings = _settings()
    assert settings.configured_dsn(StorageDomain.CORE) == "postgres://core-host/db"
    for d in StorageDomain:
        if d is not StorageDomain.CORE:
            assert settings.configured_dsn(d) == ""


def test_reserved_domains_read_their_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for d in StorageDomain:
        monkeypatch.delenv(f"ATLAS_SUPABASE_{d.value.upper()}_DSN", raising=False)
    monkeypatch.setenv("ATLAS_SUPABASE_ANALYTICS_DSN", "postgres://warehouse/db")
    settings = _settings()
    assert settings.configured_dsn(StorageDomain.ANALYTICS) == "postgres://warehouse/db"
    assert settings.configured_dsn(StorageDomain.ENVIRONMENT) == ""
