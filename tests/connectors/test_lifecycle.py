"""Connector lifecycle tests — DISCOVERED never executes; promotion needs evidence."""

from __future__ import annotations

import pytest

from atlas.capabilities.public_api.catalog import CatalogEntry, PublicAPICatalog
from atlas.capabilities.public_api.connector import ConnectorRegistry, ConnectorStatus


def _registry() -> ConnectorRegistry:
    return ConnectorRegistry(PublicAPICatalog.load_default())


def test_catalog_seeds_every_connector_as_discovered() -> None:
    registry = _registry()
    assert registry.all(), "expected seeded connectors"
    assert all(rec.status is ConnectorStatus.DISCOVERED for rec in registry.all())
    assert all(not rec.executable for rec in registry.all())
    assert registry.executable_ids() == ()


def test_promote_to_executable_without_note_is_refused() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="requires validation evidence"):
        registry.promote("open_meteo", ConnectorStatus.VALIDATED)
    with pytest.raises(ValueError, match="requires validation evidence"):
        registry.promote("open_meteo", ConnectorStatus.AVAILABLE, note="")


def test_promote_with_evidence_grants_execution_rights() -> None:
    registry = _registry()
    record = registry.promote("open_meteo", ConnectorStatus.VALIDATED, note="probe → HTTP 200")
    assert record.executable is True
    assert record.note == "probe → HTTP 200"
    assert record.promoted_ts is not None
    assert registry.executable_ids() == ("open_meteo",)


def test_non_executable_promotions_do_not_require_note() -> None:
    registry = _registry()
    record = registry.promote("open_meteo", ConnectorStatus.CANDIDATE)
    assert record.status is ConnectorStatus.CANDIDATE
    assert record.executable is False


def test_promote_unknown_connector_raises() -> None:
    registry = _registry()
    with pytest.raises(KeyError, match="unknown connector"):
        registry.promote("does_not_exist", ConnectorStatus.VALIDATED, note="evidence")


def test_register_discovered_enters_without_execution_rights() -> None:
    registry = _registry()
    entry = CatalogEntry(api_id="brand_new", name="Brand New", description="", category="X", url="https://new.test")
    record = registry.register_discovered(entry)
    assert record.status is ConnectorStatus.DISCOVERED
    assert record.executable is False
    # idempotent: registering twice returns the same record
    assert registry.register_discovered(entry) is record


def test_disabled_connector_is_not_executable() -> None:
    registry = _registry()
    registry.promote("open_meteo", ConnectorStatus.VALIDATED, note="probe → HTTP 200")
    registry.promote("open_meteo", ConnectorStatus.DISABLED)
    assert registry.get("open_meteo").executable is False  # type: ignore[union-attr]
    assert registry.executable_ids() == ()
