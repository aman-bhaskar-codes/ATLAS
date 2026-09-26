"""Catalog sync tests: diff, lifecycle, isolation, malformed tools (§20-§23, §48-§52, §61, §63-§64)."""

from __future__ import annotations

from typing import Any

import pytest

from atlas.tooling.catalog.models import Availability, CatalogStatus
from atlas.tooling.models.identity import ToolNamespace
from tests.tooling.catalog_helpers import (
    EventCollector,
    FakeSource,
    build_catalog,
    make_definition,
)


@pytest.mark.asyncio
async def test_first_sync_adds_everything_and_creates_hierarchy(memory_db: Any) -> None:
    collector = EventCollector()
    source = FakeSource(
        "native:atlas",
        [make_definition("filesystem", operations=("read", "write"), description="file access")],
    )
    catalog = build_catalog(memory_db, publish=collector.publish, sources=[source])
    await catalog.initialize()

    assert catalog.version() == 1
    # Source -> namespace -> tool -> operation all queryable (§82-D).
    assert catalog.inspect("native:atlas:filesystem") is not None
    assert catalog.get_namespace("native:atlas:atlas") is not None
    assert catalog.inspect_operation("native:atlas:filesystem", "read") is not None
    assert collector.kinds()[0] == "sync.completed"
    assert "tool.added" in collector.kinds()


@pytest.mark.asyncio
async def test_unchanged_resync_keeps_version(memory_db: Any) -> None:
    source = FakeSource("native:atlas", [make_definition("shell", operations=("run",), description="shell")])
    catalog = build_catalog(memory_db, sources=[source])
    await catalog.initialize()
    version_after_first = catalog.version()

    second = await catalog.refresh_source("native:atlas")

    assert second.unchanged == 1 and second.added == 0 and second.updated == 0
    assert second.catalog_version == version_after_first  # §33: no visible change, no bump


@pytest.mark.asyncio
async def test_changed_description_bumps_update_and_definition_version(memory_db: Any) -> None:
    source = FakeSource(
        "native:atlas", [make_definition("shell", operations=("run",), description="run shell commands")]
    )
    catalog = build_catalog(memory_db, sources=[source])
    await catalog.refresh_source("native:atlas")
    before = catalog.inspect("native:atlas:shell")
    assert before is not None

    source.definitions = [make_definition("shell", operations=("run",), description="run allowlisted shell commands")]
    await catalog.refresh_source("native:atlas")
    after = catalog.inspect("native:atlas:shell")

    assert after is not None
    assert after.description == "run allowlisted shell commands"
    assert after.definition_version == before.definition_version + 1  # §12
    assert after.definition_fp != before.definition_fp


@pytest.mark.asyncio
async def test_absent_after_successful_refresh_becomes_stale_not_deleted(memory_db: Any) -> None:
    source = FakeSource(
        "mcp:github",
        [
            make_definition(
                "search_repositories",
                namespace=ToolNamespace.MCP,
                provider="github",
                description="search repos",
                network_required=True,
            )
        ],
    )
    catalog = build_catalog(memory_db, sources=[source])
    await catalog.refresh_source("mcp:github")
    assert catalog.inspect("mcp:github:search_repositories") is not None

    # The source's authoritative list no longer contains the tool.
    source.definitions = []
    result = await catalog.refresh_source("mcp:github")

    assert result.stale == 1 and result.removed == 0
    record = catalog.inspect("mcp:github:search_repositories")
    assert record is not None  # history preserved (§19)
    assert record.status == CatalogStatus.STALE
    assert record.availability == Availability.UNAVAILABLE


@pytest.mark.asyncio
async def test_failed_discovery_retains_previous_catalog(memory_db: Any) -> None:
    source = FakeSource(
        "mcp:github",
        [
            make_definition(
                "get_file",
                namespace=ToolNamespace.MCP,
                provider="github",
                description="get a file",
                network_required=True,
            )
        ],
    )
    catalog = build_catalog(memory_db, sources=[source])
    await catalog.refresh_source("mcp:github")
    version_before = catalog.version()

    source.fail = True
    result = await catalog.refresh_source("mcp:github")

    assert not result.ok
    assert result.error is not None and "source down" in result.error
    record = catalog.inspect("mcp:github:get_file")
    # FakeSource reports no runtime status -> honest DISCOVERED; retained either way (§51)
    assert record is not None and record.status == CatalogStatus.DISCOVERED
    assert catalog.version() == version_before


@pytest.mark.asyncio
async def test_source_isolation_one_failure_does_not_destroy_others(memory_db: Any) -> None:
    """§63: A succeeds, B fails, C succeeds — A and C updated, B's failure isolated."""
    source_a = FakeSource(
        "mcp:a",
        [
            make_definition(
                "tool_a", namespace=ToolNamespace.MCP, provider="a", description="alpha tool", network_required=True
            )
        ],
    )
    source_b = FakeSource(
        "mcp:b",
        [
            make_definition(
                "tool_b", namespace=ToolNamespace.MCP, provider="b", description="beta tool", network_required=True
            )
        ],
        fail=True,
    )
    source_c = FakeSource(
        "mcp:c",
        [
            make_definition(
                "tool_c", namespace=ToolNamespace.MCP, provider="c", description="gamma tool", network_required=True
            )
        ],
    )
    catalog = build_catalog(memory_db, sources=[source_a, source_b, source_c])

    results = await catalog.refresh_all()

    by_source = {r.source_id: r for r in results}
    assert by_source["mcp:a"].ok and by_source["mcp:a"].added == 1
    assert by_source["mcp:c"].ok and by_source["mcp:c"].added == 1
    assert not by_source["mcp:b"].ok
    assert by_source["mcp:b"].error is not None
    assert catalog.inspect("mcp:a:tool_a") is not None
    assert catalog.inspect("mcp:c:tool_c") is not None
    # B never reported a successful list, so nothing of B can exist to lose —
    # the isolation guarantee is that A and C survived the B failure.
    assert "mcp:a" in [s["source_id"] for s in catalog.summary()["sources"]]  # type: ignore[index]


@pytest.mark.asyncio
async def test_malformed_tool_rejected_but_healthy_ones_stored(memory_db: Any) -> None:
    """§48/§64: N valid + 1 malformed -> N stored + 1 structured rejection."""
    healthy = [
        make_definition(
            f"tool_{i:02d}",
            namespace=ToolNamespace.MCP,
            provider="x",
            operations=("go",),
            description=f"healthy tool {i}",
            network_required=True,
        )
        for i in range(3)
    ]
    malformed = make_definition(
        "broken_tool",
        namespace=ToolNamespace.MCP,
        provider="x",
        operations=("go",),
        description="bad schema",
        network_required=True,
        input_fallback={"type": "array", "items": {"type": "string"}},  # not object-shaped
    )
    source = FakeSource("mcp:x", [*healthy, malformed])
    catalog = build_catalog(memory_db, sources=[source])

    result = await catalog.refresh_source("mcp:x")

    assert result.ok
    assert result.discovered == 4 and result.added == 3 and result.rejected == 1
    assert len(result.errors) == 1
    assert result.errors[0].tool_id == "mcp:x:broken_tool"
    assert "object" in result.errors[0].reason
    assert catalog.inspect("mcp:x:tool_00") is not None
    assert catalog.inspect("mcp:x:broken_tool") is None


@pytest.mark.asyncio
async def test_runtime_status_overlays_catalog_lifecycle(memory_db: Any) -> None:
    """A StatusReportingSource maps registry state onto catalog state (§18)."""
    from atlas.tooling.models.tool_health import ToolRuntimeState, ToolStatus
    from tests.tooling.catalog_helpers import StubAdapter, registry_with

    registry = registry_with(make_definition("fs", operations=("read",), description="files"))
    registry.set_status("native:atlas:fs", ToolRuntimeState.READY)  # as fabric.initialize() would
    registry.register(
        make_definition("br", operations=("browse",), description="browser"),
        StubAdapter(),  # type: ignore[arg-type]
        status=ToolStatus(state=ToolRuntimeState.DISABLED, detail="off"),
    )
    catalog = build_catalog(memory_db, registry)
    await catalog.initialize()

    assert catalog.inspect("native:atlas:fs").status == CatalogStatus.READY  # type: ignore[union-attr]
    assert catalog.inspect("native:atlas:br").status == CatalogStatus.DISABLED  # type: ignore[union-attr]
    # Default candidate set (READY only) excludes the disabled one:
    assert [c.tool_id for c in catalog.find_candidates()] == ["native:atlas:fs"]


@pytest.mark.asyncio
async def test_capability_source_syncs_with_remote_availability(memory_db: Any) -> None:
    """Capability tools catalog as REMOTE with WEB provenance and honest
    availability from the registry's runtime status."""
    from atlas.tooling.models.tool_health import ToolRuntimeState
    from tests.tooling.catalog_helpers import registry_with

    capability_def = make_definition(
        "knowledge",
        namespace=ToolNamespace.CAPABILITY,
        operations=("search", "sources"),
        description="knowledge and research",
        capability="knowledge",
        network_required=True,
    )
    registry = registry_with(capability_def)
    registry.set_status("capability:atlas:knowledge", ToolRuntimeState.READY)
    catalog = build_catalog(memory_db, registry)
    await catalog.initialize()

    record = catalog.inspect("capability:atlas:knowledge")
    assert record is not None
    assert record.source_id == "capability:atlas"
    assert record.namespace_id == "capability:atlas:atlas"
    assert record.execution_type.value == "capability"
    assert record.status == CatalogStatus.READY
    assert "search" in record.operations


@pytest.mark.asyncio
async def test_sync_run_is_recorded_per_refresh(memory_db: Any) -> None:
    source = FakeSource("native:atlas", [make_definition("fs", operations=("read",), description="files")])
    catalog = build_catalog(memory_db, sources=[source])
    await catalog.initialize()
    cur = await memory_db.conn.execute("SELECT COUNT(*) AS n FROM tool_sync_runs WHERE source_id='native:atlas'")
    row = await cur.fetchone()
    assert row["n"] == 1
