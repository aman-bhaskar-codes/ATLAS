"""Batch 7 tests — execution checkpoints, crash recovery, API auth."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.interfaces.api.auth import parse_api_keys, require_admin, require_principal
from atlas.orchestration.checkpoint import (
    CheckpointStore,
    ExecutionCheckpoint,
    SQLiteCheckpointBackend,
)
from atlas.orchestration.recovery import recover_interrupted_tasks


@pytest.fixture
async def db(tmp_path: Path):
    d = Database(tmp_path / "durability.db")
    await d.start()
    yield d
    await d.stop()


def _ckpt(task_id: str = "t1", step: int = 3) -> ExecutionCheckpoint:
    return ExecutionCheckpoint(
        task_id=task_id,
        step=step,
        goal={"objective": "do the thing", "confidence": 0.7},
        plan={"goal": "do the thing", "steps": [{"index": 0, "intent": "i"}]},
        history_summary="T: thought\nO: [ok] result",
        created_ts=datetime.now(UTC),
    )


class TestCheckpoints:
    async def test_save_and_latest(self, db: Database) -> None:
        store = CheckpointStore(SQLiteCheckpointBackend(db, UuidGenerator()), SystemClock())
        await store.save(_ckpt(step=1))
        await store.save(_ckpt(step=2))
        latest = await store.latest("t1")
        assert latest is not None
        assert latest.step == 2
        assert latest.goal["objective"] == "do the thing"
        assert latest.plan["steps"][0]["intent"] == "i"

    async def test_prune(self, db: Database) -> None:
        store = CheckpointStore(SQLiteCheckpointBackend(db, UuidGenerator()), SystemClock())
        await store.save(_ckpt())
        assert await store.prune("t1") == 1
        assert await store.latest("t1") is None
        assert await store.prune("t1") == 0

    async def test_latest_missing_task(self, db: Database) -> None:
        store = CheckpointStore(SQLiteCheckpointBackend(db, UuidGenerator()), SystemClock())
        assert await store.latest("never") is None


class TestCrashRecovery:
    async def test_orphaned_running_task_marked_failed(self, db: Database) -> None:
        now = datetime.now(UTC).isoformat()
        await db.conn.execute(
            "INSERT INTO tasks (id, source, state, payload, created_ts, updated_ts) "
            "VALUES ('t1','api','reasoning','{}',?,?)",
            (now, now),
        )
        await db.conn.commit()
        store = CheckpointStore(SQLiteCheckpointBackend(db, UuidGenerator()), SystemClock())
        await store.save(_ckpt(task_id="t1"))

        recovered = await recover_interrupted_tasks(db, store, SystemClock())
        assert recovered == ["t1"]
        cur = await db.conn.execute("SELECT state FROM tasks WHERE id='t1'")
        row = await cur.fetchone()
        assert row is not None and row["state"] == "failed"
        # Checkpoints pruned after resolution.
        assert await store.latest("t1") is None

    async def test_live_tasks_excluded(self, db: Database) -> None:
        now = datetime.now(UTC).isoformat()
        await db.conn.execute(
            "INSERT INTO tasks (id, source, state, payload, created_ts, updated_ts) "
            "VALUES ('t2','api','reasoning','{}',?,?)",
            (now, now),
        )
        await db.conn.commit()
        store = CheckpointStore(SQLiteCheckpointBackend(db, UuidGenerator()), SystemClock())
        recovered = await recover_interrupted_tasks(db, store, SystemClock(), live_task_ids=frozenset({"t2"}))
        assert recovered == []

    async def test_terminal_tasks_untouched(self, db: Database) -> None:
        now = datetime.now(UTC).isoformat()
        await db.conn.execute(
            "INSERT INTO tasks (id, source, state, payload, created_ts, updated_ts) "
            "VALUES ('t3','api','completed','{}',?,?)",
            (now, now),
        )
        await db.conn.commit()
        store = CheckpointStore(SQLiteCheckpointBackend(db, UuidGenerator()), SystemClock())
        recovered = await recover_interrupted_tasks(db, store, SystemClock())
        assert recovered == []


class TestApiAuth:
    def test_parse_api_keys(self) -> None:
        keys = parse_api_keys("secret1, ro:reader ,,  ")
        assert keys == {"secret1": "admin", "reader": "readonly"}
        assert parse_api_keys(None) == {}
        assert parse_api_keys("") == {}

    def test_no_keys_open_local_mode(self) -> None:
        app = FastAPI()
        app.state.api_keys = {}

        @app.get("/ping")
        async def ping(p: object = Depends(require_principal)) -> dict[str, str]:
            return {"role": getattr(p, "role", "?")}

        resp = TestClient(app).get("/ping")
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    def test_valid_key_accepted(self) -> None:
        app = FastAPI()
        app.state.api_keys = {"sekrit": "admin"}

        @app.get("/ping")
        async def ping(p: object = Depends(require_principal)) -> dict[str, str]:
            return {"role": getattr(p, "role", "?")}

        ok = TestClient(app).get("/ping", headers={"Authorization": "Bearer sekrit"})
        assert ok.status_code == 200 and ok.json()["role"] == "admin"
        bad = TestClient(app).get("/ping", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
        none = TestClient(app).get("/ping")
        assert none.status_code == 401

    def test_readonly_key_cannot_mutate(self) -> None:
        app = FastAPI()
        app.state.api_keys = {"read": "readonly"}

        @app.post("/mutate")
        async def mutate(p: object = Depends(require_admin)) -> dict[str, bool]:
            return {"ok": True}

        denied = TestClient(app).post("/mutate", headers={"Authorization": "Bearer read"})
        assert denied.status_code == 403
