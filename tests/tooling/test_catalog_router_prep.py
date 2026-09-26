"""Router-preparation + model-surface tests (§71/§73/§74) and CLI contract."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from atlas.infra.types import ToolCallSpec
from atlas.tooling.models.identity import ToolNamespace
from tests.tooling.catalog_helpers import build_catalog, make_definition, registry_with


def _catalog(memory_db: Any) -> Any:
    registry = registry_with(
        make_definition(
            "knowledge",
            namespace=ToolNamespace.CAPABILITY,
            operations=("search", "sources"),
            description="knowledge and research over web sources",
            capability="knowledge",
            network_required=True,
            tags=("research",),
        ),
        make_definition(
            "filesystem",
            operations=("read", "write", "search"),
            description="read and write files",
            tags=("filesystem",),
        ),
    )
    from atlas.tooling.models.tool_health import ToolRuntimeState

    for tool_id in ("native:atlas:filesystem", "capability:atlas:knowledge"):
        registry.set_status(tool_id, ToolRuntimeState.READY)  # as fabric.initialize() would
    catalog = build_catalog(memory_db, registry)
    return catalog


@pytest.mark.asyncio
async def test_find_candidates_is_deterministic_and_filtered(memory_db: Any) -> None:
    """§71/§72: candidates are a deterministic SET — no ranking, no 'best'."""
    catalog = _catalog(memory_db)
    await catalog.initialize()

    by_operation = catalog.find_candidates(operation="search")
    assert [c.tool_id for c in by_operation] == [
        "capability:atlas:knowledge",
        "native:atlas:filesystem",
    ]  # sorted by tool_id, not by preference (§72)
    by_capability = catalog.find_candidates(capability="knowledge")
    assert [c.tool_id for c in by_capability] == ["capability:atlas:knowledge"]
    limited = catalog.find_candidates(limit=1)
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_to_tool_specs_serves_only_the_shortlist(memory_db: Any) -> None:
    """§73: the model sees a selected shortlist, never the whole catalog."""
    catalog = _catalog(memory_db)
    await catalog.initialize()

    shortlist = ["capability:atlas:knowledge"]
    specs = catalog.to_tool_specs(shortlist)
    assert len(specs) == 1
    spec = specs[0]
    assert isinstance(spec, ToolCallSpec)
    assert spec.name == "capability:atlas:knowledge"
    assert spec.parameters["required"] == ["operation"]  # type: ignore[index]
    operation_enum = spec.parameters["properties"]["operation"]["enum"]  # type: ignore[index]
    assert operation_enum == ["search", "sources"]

    # Unknown ids are skipped, not invented (§84 of Part 1: no hallucinated tools).
    empty = catalog.to_tool_specs(["mcp:nowhere:missing"])
    assert empty == ()


@pytest.mark.asyncio
async def test_schema_size_tracked_for_surface_budgeting(memory_db: Any) -> None:
    """§74: schema size is measured (bytes + crude token estimate)."""
    catalog = _catalog(memory_db)
    await catalog.initialize()
    record = catalog.inspect("native:atlas:filesystem")
    assert record is not None
    assert record.schema_bytes > 0
    assert record.estimated_schema_tokens >= 1


@pytest.mark.asyncio
async def test_snapshot_and_version_are_consistent(memory_db: Any) -> None:
    """§32/§33: snapshot records the epoch + shape; version is monotonic."""
    catalog = _catalog(memory_db)
    await catalog.initialize()
    version = catalog.version()
    snap = catalog.snapshot()
    assert snap.catalog_version == version
    assert snap.tool_count == 2
    assert snap.namespace_count == 2
    assert snap.source_count == 2
    assert snap.operation_count == 5
    assert snap.status_counts.get("READY") == 2

    await catalog.refresh_all()  # unchanged resync
    assert catalog.version() == version  # no visible change -> same epoch


@pytest.mark.asyncio
async def test_to_definition_round_trips_to_part1_contract(memory_db: Any) -> None:
    """The catalog record reconstructs a valid Part-1 UniversalToolDefinition
    with the same stable identity — the Part-3 router's contract bridge."""
    catalog = _catalog(memory_db)
    await catalog.initialize()
    record = catalog.inspect("capability:atlas:knowledge")
    assert record is not None
    definition = record.to_definition()
    assert definition.id == record.tool_id
    assert definition.operations == record.operations
    assert definition.safety_tool == record.safety_tool


# ── CLI (§68/§69/§82-I) ────────────────────────────────────────────────── #


class StubClient:
    def __init__(self, routes: dict[str, Any]) -> None:
        self._routes = routes

    async def _get(self, path: str) -> Any:
        from urllib.parse import unquote

        return self._routes[unquote(path)]


def _cli_payload() -> dict[str, Any]:
    return {
        "catalog_version": 7,
        "namespace_count": 2,
        "tool_count": 2,
        "operation_count": 5,
        "status_counts": {"READY": 2},
        "last_sync": "2026-09-25T00:00:00+00:00",
        "sources": [
            {
                "source_id": "native:atlas",
                "source_type": "native",
                "trust_level": "system_builtin",
                "last_sync_ok": True,
            },
        ],
    }


def test_cli_catalog_command_renders_summary(monkeypatch: Any) -> None:
    from atlas_cli.main import app

    monkeypatch.setattr("atlas_cli.main.client", StubClient({"/api/v1/tools/catalog": _cli_payload()}))
    monkeypatch.setenv("COLUMNS", "250")
    result = CliRunner().invoke(app, ["tools", "catalog"])
    assert result.exit_code == 0, result.output
    assert "Catalog Version" in result.output and "native:atlas" in result.output


def test_cli_search_command_renders_matches(monkeypatch: Any) -> None:
    from atlas_cli.main import app

    payload = [{"tool_id": "native:atlas:filesystem", "score": 0.62, "matched_fields": ["name", "operation"]}]
    monkeypatch.setattr("atlas_cli.main.client", StubClient({"/api/v1/tools/catalog/search?q=files&limit=20": payload}))
    monkeypatch.setenv("COLUMNS", "250")
    result = CliRunner().invoke(app, ["tools", "search", "files"])
    assert result.exit_code == 0, result.output
    assert "native:atlas:filesystem" in result.output


def test_cli_inspect_command_renders_tool(monkeypatch: Any) -> None:
    from atlas_cli.main import app

    payload = {
        "tool_id": "native:atlas:filesystem",
        "namespace_id": "native:atlas:atlas",
        "source_id": "native:atlas",
        "provider": "atlas",
        "adapter": "native",
        "version": "1",
        "definition_version": 1,
        "capability": None,
        "operations": ["read", "write"],
        "execution_type": "native",
        "status": "READY",
        "availability": "AVAILABLE",
        "auth_state": "NONE",
        "credential_reference": None,
        "cost_class": "local",
        "estimated_latency_ms": 50,
        "safety_tool": "filesystem",
        "default_tier": 1,
        "side_effects": True,
        "idempotent": False,
        "trust_level": "system_builtin",
        "locality": "local",
        "privacy_class": "secret",
        "tags": ["filesystem"],
        "definition_fp": "abc123",
        "schema_bytes": 120,
        "estimated_schema_tokens": 30,
        "created_ts": "2026-09-25T00:00:00+00:00",
        "updated_ts": None,
        "last_seen_ts": None,
        "last_validated_ts": None,
        "description": "read and write files",
    }
    monkeypatch.setattr(
        "atlas_cli.main.client",
        StubClient({"/api/v1/tools/catalog/tools/native:atlas:filesystem/inspect": payload}),
    )
    monkeypatch.setenv("COLUMNS", "250")
    result = CliRunner().invoke(app, ["tools", "inspect", "native:atlas:filesystem"])
    assert result.exit_code == 0, result.output
    assert "native:atlas:filesystem" in result.output
    assert "abc123" in result.output  # fingerprint visible for debugging


def test_cli_namespaces_command(monkeypatch: Any) -> None:
    from atlas_cli.main import app

    payload = [{"namespace_id": "native:atlas:atlas", "source_id": "native:atlas", "tool_count": 2, "status": "READY"}]
    monkeypatch.setattr("atlas_cli.main.client", StubClient({"/api/v1/tools/catalog/namespaces": payload}))
    monkeypatch.setenv("COLUMNS", "250")
    result = CliRunner().invoke(app, ["tools", "namespaces"])
    assert result.exit_code == 0, result.output
    assert "native:atlas:atlas" in result.output
