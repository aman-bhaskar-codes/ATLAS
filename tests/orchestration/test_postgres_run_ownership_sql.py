"""Verify the reserved PostgreSQL adapter's parameterized ownership/locking seam."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from atlas.infra.backends import PostgresConnection, translate_placeholders
from atlas.orchestration.agent_engine.persistence import PostgresAgentRunStore
from atlas.orchestration.agent_engine.records import AgentRunRecord
from atlas.orchestration.research.persistence import PostgresResearchSessionStore
from atlas.orchestration.research.records import ResearchSessionRecord


class RecordingConnection(PostgresConnection):
    def __init__(self, payload: str = "") -> None:
        super().__init__("unused-test-connection")
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.payload = payload

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        self.statements.append((sql, tuple(params)))
        return 1

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any]:
        self.statements.append((sql, tuple(params)))
        return {"sequence": 1, "payload": self.payload}


@pytest.mark.parametrize("research", [False, True])
async def test_postgres_owned_upsert_locks_owner_in_both_mutation_arms(research: bool) -> None:
    connection = RecordingConnection()
    store = PostgresResearchSessionStore(connection) if research else PostgresAgentRunStore(connection)
    record = ResearchSessionRecord(session_id="r") if research else AgentRunRecord(run_id="r")
    await store.save_owned(record, "owner", now="2026-10-11T00:00:00+00:00")
    sql, params = connection.statements[-1]
    assert sql.count("FOR SHARE") == 2
    translated = translate_placeholders(sql)
    assert f"${len(params)}" in translated and f"${len(params) + 1}" not in translated
    assert params[-6:] == ("research:r" if research else "agent:r", "owner", "2026-10-11T00:00:00+00:00") * 2


async def test_postgres_research_event_locks_owner_through_insert() -> None:
    connection = RecordingConnection()
    event = await PostgresResearchSessionStore(connection).append_owned_event(
        "r", "retrieving", {}, "owner", ts="2026-10-11T00:00:00+00:00"
    )
    sql, params = connection.statements[-1]
    assert "FOR SHARE" in sql and "RETURNING sequence" in sql
    assert len(params) == 7 and event.sequence == 1


@pytest.mark.parametrize("research", [False, True])
async def test_postgres_orphan_settlement_locks_existing_lease_before_expiry_check(research: bool) -> None:
    before = ResearchSessionRecord(session_id="r") if research else AgentRunRecord(run_id="r")
    connection = RecordingConnection(before.model_dump_json())
    store = PostgresResearchSessionStore(connection) if research else PostgresAgentRunStore(connection)
    after = before.model_copy(update={"status": "failed" if research else "error"})
    assert await store.settle_orphan(before, after, now="2026-10-11T00:00:00+00:00")
    sql, params = connection.statements[-1]
    assert "AS MATERIALIZED" in sql and "FOR SHARE" in sql
    assert params[0] == ("research:r" if research else "agent:r")
    assert f"${len(params)}" in translate_placeholders(sql)
