"""Restart persistence tests (§59/§82-A): the catalog must survive the process."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.tooling.catalog.models import CatalogStatus
from tests.tooling.catalog_helpers import build_catalog, make_definition, registry_with


@pytest.mark.asyncio
async def test_catalog_survives_database_restart(tmp_path: Path) -> None:
    """register -> sync -> shutdown -> fresh Database on the same file ->
    rebuild_index -> query. Results must survive (§59)."""
    db_path = tmp_path / "catalog.db"

    db = Database(db_path)
    await db.start()
    registry = registry_with(
        make_definition("filesystem", operations=("read", "write"), description="file access"),
        make_definition("shell", operations=("run",), description="shell commands"),
    )
    from atlas.tooling.models.tool_health import ToolRuntimeState

    for tool_id in ("native:atlas:filesystem", "native:atlas:shell"):
        registry.set_status(tool_id, ToolRuntimeState.READY)  # as fabric.initialize() would
    first = build_catalog(db, registry)
    await first.initialize()
    version = first.version()
    fp = first.inspect("native:atlas:filesystem").definition_fp  # type: ignore[union-attr]
    assert version >= 1
    await db.stop()

    # Fresh process equivalent: brand-new Database + catalog over the same file.
    db2 = Database(db_path)
    await db2.start()
    second = build_catalog(db2, registry)
    await second.rebuild_index()  # persistence is authoritative, no sync yet

    assert second.version() == version
    record = second.inspect("native:atlas:filesystem")
    assert record is not None
    assert record.status == CatalogStatus.READY
    assert record.definition_fp == fp
    assert record.last_seen_ts is not None
    assert {r.tool_id for r in second.find()} >= {"native:atlas:filesystem", "native:atlas:shell"}
    # The registry sources still re-sync cleanly on top of the persisted rows.
    results = await second.refresh_all()
    assert all(r.ok for r in results)
    assert all(r.unchanged >= 1 for r in results)
    await db2.stop()


@pytest.mark.asyncio
async def test_stale_state_survives_restart(tmp_path: Path) -> None:
    from atlas.tooling.models.identity import ToolNamespace
    from tests.tooling.catalog_helpers import FakeSource

    db_path = tmp_path / "stale.db"
    db = Database(db_path)
    await db.start()
    source = FakeSource(
        "mcp:gone",
        [
            make_definition(
                "vanished", namespace=ToolNamespace.MCP, provider="gone", description="was here", network_required=True
            )
        ],
    )
    catalog = build_catalog(db, sources=[source])
    await catalog.initialize()
    source.definitions = []
    await catalog.refresh_source("mcp:gone")
    await db.stop()

    db2 = Database(db_path)
    await db2.start()
    revived = build_catalog(db2)
    await revived.rebuild_index()
    record = revived.inspect("mcp:gone:vanished")
    assert record is not None
    assert record.status == CatalogStatus.STALE
    await db2.stop()


@pytest.mark.asyncio
async def test_real_application_restart_preserves_catalog(tmp_path: Path) -> None:
    """The REAL composition root persists and re-loads the catalog across two
    boots on the same data directory (§75/§59/§82-A/§82-J)."""
    from tests.api.conftest import app_client

    async with app_client(tmp_path) as (app, client):
        atlas = app.state.atlas
        assert atlas.tooling is not None and atlas.tooling.catalog is not None
        first_summary = await client.get("/api/v1/tools/catalog")
        assert first_summary.status_code == 200
        first = first_summary.json()
        assert first["tool_count"] > 0
        assert first["catalog_version"] >= 1
        first_tool_ids = {r["tool_id"] for r in (await client.get("/api/v1/tools/catalog/candidates")).json()}

    async with app_client(tmp_path) as (app, client):
        atlas = app.state.atlas
        second_summary = (await client.get("/api/v1/tools/catalog")).json()
        assert second_summary["tool_count"] >= first["tool_count"]
        assert second_summary["catalog_version"] >= first["catalog_version"]
        second_tool_ids = {r["tool_id"] for r in (await client.get("/api/v1/tools/catalog/candidates")).json()}
        assert first_tool_ids <= second_tool_ids
        # Detail surface survived the restart too.
        assert (await client.get("/api/v1/tools/catalog/tools/native:atlas:filesystem/inspect")).status_code == 200
