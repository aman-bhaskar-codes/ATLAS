from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.app import Atlas
from atlas.infra.db import Database
from atlas.interfaces.api.dependencies import get_trust_plane
from atlas.interfaces.api.routes_trust import router
from atlas.interfaces.api.trust_facade import DefaultAtlasTrustPlane


async def add_event(db: Database, *, tier: int, ts: str, action: str = "read") -> None:
    await db.conn.execute(
        "INSERT INTO audit_events(correlation_id, ts, actor, action, tier) VALUES (?, ?, ?, ?, ?)",
        ("correlation", ts, "user", action, tier),
    )
    await db.conn.commit()


def plane_for(db: Database) -> DefaultAtlasTrustPlane:
    return DefaultAtlasTrustPlane(cast(Atlas, SimpleNamespace(db=db)))


@pytest.mark.asyncio
async def test_filters_search_full_log_and_preserve_cursor(memory_db: Database) -> None:
    await add_event(memory_db, tier=2, ts="2026-10-06T09:00:00+00:00", action="older match")
    await add_event(memory_db, tier=2, ts="2026-10-06T10:00:00+00:00", action="newer match")
    for _ in range(60):
        await add_event(memory_db, tier=1, ts="2026-10-05T12:00:00+00:00")
    plane = plane_for(memory_db)
    filters = dict(task_id=None, correlation_id=None, execution_id=None, limit=1,
                   since=datetime(2026, 10, 6, tzinfo=UTC), tier=2)
    first = await plane.audit(cursor=None, **filters)
    assert [event.action for event in first.items] == ["newer match"]
    assert first.next_cursor is not None
    second = await plane.audit(cursor=first.next_cursor, **filters)
    assert [event.action for event in second.items] == ["older match"]
    assert second.next_cursor is None


@pytest.mark.asyncio
async def test_date_filter_compares_instants_including_offset_and_boundary(memory_db: Database) -> None:
    await add_event(memory_db, tier=0, ts="2026-10-06T05:30:00+05:30", action="boundary")
    await add_event(memory_db, tier=0, ts="2026-10-06T00:30:00+02:00", action="previous day")
    page = await plane_for(memory_db).audit(
        task_id=None, correlation_id=None, execution_id=None, cursor=None, limit=50,
        since=datetime(2026, 10, 6, tzinfo=UTC), tier=0,
    )
    assert [event.action for event in page.items] == ["boundary"]


@pytest.mark.asyncio
async def test_export_is_filtered_bounded_and_omits_raw_payloads(memory_db: Database) -> None:
    await memory_db.conn.execute("INSERT INTO payloads(body) VALUES (?)", ('{"password":"synthetic-secret"}',))
    for number in range(4):
        await add_event(memory_db, tier=2, ts="2026-10-06T12:00:00Z", action=f"match {number}")
    await add_event(memory_db, tier=1, ts="2026-10-06T12:00:00Z")
    await memory_db.conn.execute("UPDATE audit_events SET payload_id=1")
    result = await plane_for(memory_db).export_audit(since=None, tier=2, max_events=3)
    assert len(result.items) == 3
    assert result.truncated is True
    assert result.max_events == 3
    assert result.filters.tier == 2
    assert all(item.tier == 2 for item in result.items)
    assert "synthetic-secret" not in result.model_dump_json()
    assert "payload_id" not in result.model_dump_json()
    result = await plane_for(memory_db).export_audit(since=None, tier=2, max_events=4)
    assert len(result.items) == 4
    assert result.truncated is False


def test_routes_validate_filter_and_export_bounds() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_trust_plane] = lambda: SimpleNamespace()
    client = TestClient(app)
    for query in ("tier=5", "tier=-1", "since=not-a-date", "since=2026-10-06T00:00:00", "cursor=bad"):
        assert client.get(f"/api/v1/audit?{query}").status_code == 422
    assert client.get("/api/v1/audit/export?max_events=1001").status_code == 422
