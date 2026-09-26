"""Catalog search tests: structured + lexical with explanations (§25-§27, §60)."""

from __future__ import annotations

from typing import Any

import pytest

from atlas.tooling.catalog.models import CatalogStatus
from atlas.tooling.models.identity import ToolNamespace
from tests.tooling.catalog_helpers import make_definition, registry_with


@pytest.fixture
async def catalog(memory_db: Any) -> Any:
    """A real registry-backed catalog synced once (§82-B/§82-C)."""
    from tests.tooling.catalog_helpers import build_catalog

    registry = registry_with(
        make_definition(
            "filesystem",
            operations=("read", "list", "search", "write"),
            description="read and write files on local disk",
            tags=("filesystem", "local"),
        ),
        make_definition(
            "shell",
            operations=("read_only", "side_effect"),
            description="run allowlisted shell commands",
            tags=("shell", "local"),
        ),
        make_definition(
            "weather",
            namespace=ToolNamespace.CAPABILITY,
            operations=("forecast",),
            description="weather forecast for a location",
            capability="weather",
            network_required=True,
            tags=("weather", "remote"),
        ),
        make_definition(
            "email",
            namespace=ToolNamespace.CAPABILITY,
            operations=("read", "search", "send"),
            description="read search and send email",
            capability="email",
            network_required=True,
            requires_auth=True,
            tags=("email", "communication"),
        ),
    )
    from atlas.tooling.models.tool_health import ToolRuntimeState

    for tool_id in (
        "native:atlas:filesystem",
        "native:atlas:shell",
        "capability:atlas:weather",
        "capability:atlas:email",
    ):
        registry.set_status(tool_id, ToolRuntimeState.READY)  # as fabric.initialize() would
    catalog = build_catalog(memory_db, registry)
    await catalog.initialize()
    return catalog


@pytest.mark.asyncio
async def test_exact_name_match_explains_itself(catalog: Any) -> None:
    matches = catalog.search("filesystem")
    assert matches, "expected at least one hit"
    assert matches[0].tool_id == "native:atlas:filesystem"
    assert matches[0].score > 0.5
    assert "name" in matches[0].matched_fields
    assert matches[0].record is not None


@pytest.mark.asyncio
async def test_operation_match(catalog: Any) -> None:
    matches = catalog.search("forecast")
    assert matches
    assert matches[0].tool_id == "capability:atlas:weather"
    assert "operation" in matches[0].matched_fields


@pytest.mark.asyncio
async def test_capability_match(catalog: Any) -> None:
    matches = catalog.search("email")
    assert any(m.tool_id == "capability:atlas:email" for m in matches)
    email_match = next(m for m in matches if m.tool_id == "capability:atlas:email")
    assert "capability" in email_match.matched_fields or "name" in email_match.matched_fields


@pytest.mark.asyncio
async def test_description_match(catalog: Any) -> None:
    matches = catalog.search("allowlisted commands")
    assert matches
    assert matches[0].tool_id == "native:atlas:shell"
    assert "description" in matches[0].matched_fields


@pytest.mark.asyncio
async def test_no_match_returns_empty_not_noise(catalog: Any) -> None:
    assert catalog.search("xylophone quantum blockchain") == []


@pytest.mark.asyncio
async def test_multiple_matches_ranked_deterministically(catalog: Any) -> None:
    matches = catalog.search("search")
    assert len(matches) >= 2  # filesystem (op) + email (op)
    scores = [m.score for m in matches]
    assert scores == sorted(scores, reverse=True)
    ids = [m.tool_id for m in matches]
    same = catalog.search("search")
    assert [m.tool_id for m in same] == ids  # deterministic (§27)


@pytest.mark.asyncio
async def test_structured_find_combined_filters(catalog: Any) -> None:
    hits = catalog.find(capability="email", operation="send", status="READY")
    assert [h.tool_id for h in hits] == ["capability:atlas:email"]
    assert catalog.find(capability="email", operation="delete") == []
    assert catalog.find(capability="missing-capability") == []


@pytest.mark.asyncio
async def test_find_candidates_default_excludes_non_ready(catalog: Any) -> None:
    """§82-K: candidates are deterministic and default to READY only."""
    candidates = catalog.find_candidates(capability="email")
    assert [c.tool_id for c in candidates] == ["capability:atlas:email"]

    # Mark one tool stale in the underlying store, rebuild, and confirm the
    # candidate set reflects catalog state.
    await catalog._store.update_tool_status(
        ["native:atlas:shell"], CatalogStatus.STALE, __import__("datetime").datetime.now(__import__("datetime").UTC)
    )
    await catalog.rebuild_index()
    all_ids = [c.tool_id for c in catalog.find_candidates()]
    assert "native:atlas:shell" not in all_ids
    stale = catalog.find(status="STALE")
    assert [r.tool_id for r in stale] == ["native:atlas:shell"]


@pytest.mark.asyncio
async def test_disabled_tool_is_queryable_but_not_a_candidate(catalog: Any) -> None:

    catalog._registry.disable("native:atlas:filesystem")
    await catalog.refresh_source("native:atlas")

    record = catalog.inspect("native:atlas:filesystem")
    assert record is not None and record.status == CatalogStatus.DISABLED  # inspectable (§57)
    assert "native:atlas:filesystem" not in [c.tool_id for c in catalog.find_candidates()]


@pytest.mark.asyncio
async def test_search_normalization_preserves_display_description(catalog: Any) -> None:
    """§75: search_text is normalized; description keeps display quality."""
    record = catalog.inspect("native:atlas:shell")
    assert record is not None
    assert record.description == "run allowlisted shell commands"
    assert record.search_text == record.description  # already normalized form


@pytest.mark.asyncio
async def test_namespace_queries(catalog: Any) -> None:
    """§29: namespaces are first-class with tool counts + health/auth summaries."""
    namespaces = catalog.list_namespaces()
    assert {n.namespace_id for n in namespaces} == {
        "native:atlas:atlas",
        "capability:atlas:atlas",
    }
    native_ns = catalog.get_namespace("native:atlas:atlas")
    assert native_ns is not None and native_ns.tool_count == 2
    assert catalog.search_namespaces("native")[0].namespace_id == "native:atlas:atlas"
    health = catalog.namespace_health_summary("capability:atlas:atlas")
    assert health == {"READY": 2}
    auth = catalog.namespace_auth_summary("capability:atlas:atlas")
    assert auth.get("REQUIRED") == 1 and auth.get("NONE") == 1
