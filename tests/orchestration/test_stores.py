"""Tests for the execution persistence stores (Batch 1 stabilization).

Covers SQLiteExecutionStore (task lifecycle persistence) and
SQLiteCancellationStore (durable cancellation intent), plus the shared
plan-parsing module.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.infra.db import Database
from atlas.infra.execution_store import SQLiteCancellationStore, SQLiteExecutionStore
from atlas.orchestration.plan_parsing import extract_json_object, plan_from_llm_json
from atlas.orchestration.types import RiskLevel


@pytest.fixture
async def db(tmp_path):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "test.db")
    await database.start()
    yield database
    await database.stop()


async def _task_row(db: Database, task_id: str) -> dict[str, object]:
    cur = await db.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = await cur.fetchone()
    assert row is not None
    return dict(row)


class TestSQLiteExecutionStore:
    async def test_create_and_update_task(self, db: Database) -> None:
        store = SQLiteExecutionStore(db)
        ts = datetime.now(UTC)
        await store.create_task(
            task_id="t1",
            source="api",
            payload_json='{"request": "hello"}',
            idempotency_key=None,
            created_ts=ts,
        )
        row = await _task_row(db, "t1")
        assert row["state"] == "created"
        assert row["source"] == "api"

        await store.update_task_state(task_id="t1", state="reasoning", updated_ts=ts)
        row = await _task_row(db, "t1")
        assert row["state"] == "reasoning"

    async def test_create_is_idempotent(self, db: Database) -> None:
        store = SQLiteExecutionStore(db)
        ts = datetime.now(UTC)
        await store.create_task(
            task_id="t2",
            source="api",
            payload_json="{}",
            idempotency_key=None,
            created_ts=ts,
        )
        # Second insert must not raise (INSERT OR IGNORE).
        await store.create_task(
            task_id="t2",
            source="api",
            payload_json="{}",
            idempotency_key=None,
            created_ts=ts,
        )
        row = await _task_row(db, "t2")
        assert row["state"] == "created"


class TestSQLiteCancellationStore:
    async def test_request_and_check_cancellation(self, db: Database) -> None:
        store = SQLiteExecutionStore(db)
        cancels = SQLiteCancellationStore(db)
        ts = datetime.now(UTC)
        await store.create_task(
            task_id="t3",
            source="api",
            payload_json="{}",
            idempotency_key=None,
            created_ts=ts,
        )

        assert await cancels.is_cancelled("t3") is False
        assert await cancels.request_cancellation("t3") is True
        assert await cancels.is_cancelled("t3") is True

    async def test_unknown_task_returns_false(self, db: Database) -> None:
        cancels = SQLiteCancellationStore(db)
        assert await cancels.request_cancellation("nope") is False
        assert await cancels.is_cancelled("nope") is False

    async def test_terminal_task_cannot_be_cancelled(self, db: Database) -> None:
        store = SQLiteExecutionStore(db)
        cancels = SQLiteCancellationStore(db)
        ts = datetime.now(UTC)
        await store.create_task(
            task_id="t4",
            source="api",
            payload_json="{}",
            idempotency_key=None,
            created_ts=ts,
        )
        await store.update_task_state(task_id="t4", state="completed", updated_ts=ts)
        assert await cancels.request_cancellation("t4") is False

    async def test_clear_is_safe_noop(self, db: Database) -> None:
        cancels = SQLiteCancellationStore(db)
        await cancels.clear("anything")  # must not raise


class TestPlanParsing:
    def test_parses_full_plan(self) -> None:
        data = {
            "goal": "do the thing",
            "constraints": ["be safe"],
            "steps": [
                {
                    "index": 0,
                    "intent": "look",
                    "tool": "filesystem",
                    "operation": "read",
                    "args": {"path": "/x"},
                    "depends_on": [],
                    "expected_output": "content",
                },
                {"index": 1, "intent": "report", "depends_on": [0]},
            ],
            "termination_conditions": ["done"],
            "risk": "high",
            "estimated_cost_usd": 0.01,
            "confidence": 0.9,
            "unknowns": ["whether /x exists"],
        }
        plan = plan_from_llm_json(data)
        assert plan.goal == "do the thing"
        assert plan.risk is RiskLevel.HIGH
        assert len(plan.steps) == 2
        assert plan.steps[1].depends_on == (0,)
        assert plan.steps[0].tool == "filesystem"

    def test_defaults_on_garbage(self) -> None:
        plan = plan_from_llm_json({"steps": "not-a-list", "risk": "bogus"})
        assert plan.steps == ()
        assert plan.risk is RiskLevel.MEDIUM
        assert plan.confidence == 0.5
        assert plan.goal == ""

    def test_extract_json_object(self) -> None:
        text = 'Sure! Here is the plan:\n```json\n{"goal": "x"}\n```'
        assert extract_json_object(text) == '{"goal": "x"}'
        with pytest.raises(ValueError):
            extract_json_object("no json here")
