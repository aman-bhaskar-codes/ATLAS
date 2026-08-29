"""IDE session persistence — durable store CRUD + service resume across processes.

Uses a REAL temp SQLite `Database` (the shared substrate) so the appended
migration is exercised end-to-end: the `ide_workspaces`/`ide_sessions` tables must
exist and round-trip full pydantic payloads. The resume test proves the point of
the whole slice — a workspace id opened by one `IDEService` is addressable by a
SECOND service with a fresh in-memory registry, because the store rehydrates it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas.capabilities.ide.contracts import (
    EditorGroup,
    IDESession,
    IDESessionId,
    WorkspaceId,
    WorkspaceRef,
)
from atlas.capabilities.ide.persistence import SqliteIDESessionStore
from atlas.capabilities.ide.service import IDEService
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId
from atlas.infra.types import ToolResult


class FakeFilesystemTool:
    name = "filesystem"

    def dry_run(self, args: dict[str, Any]) -> str:
        return "WRITE"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        Path(str(args["path"])).write_text(str(args["content"]))
        return ToolResult(ok=True)


class FakeSafety:
    async def guard(self, req: Any, tool: Any) -> ToolResult:
        return await tool.execute(req.args)


class FakeIds:
    def __init__(self) -> None:
        self._n = 0

    def _next(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def task_id(self) -> TaskId:
        return TaskId(self._next("id"))

    def correlation_id(self) -> CorrelationId:
        return CorrelationId(self._next("cid"))

    def execution_id(self) -> ExecutionId:
        return ExecutionId(self._next("exec"))


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 29, tzinfo=UTC)


@pytest.fixture
async def db(tmp_path: Path) -> Any:
    database = Database(tmp_path / "atlas.db")
    await database.start()
    try:
        yield database
    finally:
        await database.stop()


def _ref(wid: str = "ws1", root: str = "/tmp/x") -> WorkspaceRef:
    return WorkspaceRef(
        id=WorkspaceId(wid),
        name="demo",
        root_paths=(root,),
        created_ts="2026-08-29T00:00:00+00:00",
        last_opened_ts="2026-08-29T00:00:00+00:00",
    )


def _session(sid: str, ref: WorkspaceRef) -> IDESession:
    return IDESession(
        id=IDESessionId(sid),
        workspace=ref,
        editor_groups=(EditorGroup(id="g1"),),
        created_ts=ref.created_ts,
        updated_ts=ref.last_opened_ts,
    )


class TestStoreCrud:
    async def test_workspace_round_trip(self, db: Database) -> None:
        store = SqliteIDESessionStore(db)
        ref = _ref()
        await store.save_workspace(ref)
        loaded = await store.load_workspace(WorkspaceId("ws1"))
        assert loaded == ref  # frozen models compare by value

    async def test_load_missing_returns_none(self, db: Database) -> None:
        store = SqliteIDESessionStore(db)
        assert await store.load_workspace(WorkspaceId("nope")) is None
        assert await store.load_session(IDESessionId("nope")) is None

    async def test_upsert_is_idempotent(self, db: Database) -> None:
        store = SqliteIDESessionStore(db)
        await store.save_workspace(_ref())
        await store.save_workspace(_ref())  # second save must not raise/duplicate
        assert len(await store.list_workspaces()) == 1

    async def test_session_round_trip_and_by_workspace(self, db: Database) -> None:
        store = SqliteIDESessionStore(db)
        ref = _ref()
        await store.save_workspace(ref)
        sess = _session("s1", ref)
        await store.save_session(sess)
        assert await store.load_session(IDESessionId("s1")) == sess
        by_ws = await store.sessions_for_workspace(WorkspaceId("ws1"))
        assert len(by_ws) == 1 and by_ws[0].editor_groups[0].id == "g1"

    async def test_delete_workspace_cascades_sessions(self, db: Database) -> None:
        store = SqliteIDESessionStore(db)
        ref = _ref()
        await store.save_workspace(ref)
        await store.save_session(_session("s1", ref))
        await store.delete_workspace(WorkspaceId("ws1"))
        assert await store.load_workspace(WorkspaceId("ws1")) is None
        assert await store.sessions_for_workspace(WorkspaceId("ws1")) == ()


class TestServiceResume:
    async def test_workspace_resumes_in_fresh_service(self, db: Database, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        store = SqliteIDESessionStore(db)

        # Service #1 opens the workspace (persisted to the store).
        svc1 = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),  # type: ignore[arg-type]
            clock=FakeClock(),  # type: ignore[arg-type]
            store=store,
        )
        session = await svc1.open_workspace(str(tmp_path), "demo")
        wid = session.workspace.id

        # Service #2 has an EMPTY in-memory registry — a stale process. It must
        # still serve the id by resuming it from the durable store.
        svc2 = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),  # type: ignore[arg-type]
            clock=FakeClock(),  # type: ignore[arg-type]
            store=store,
        )
        paths = {n.path for n in await svc2.tree(wid)}
        assert "a.py" in paths
        _snap, content = await svc2.read_document(wid, "a.py")
        assert content == "x = 1\n"

    async def test_unknown_id_still_raises_without_store_entry(self, db: Database) -> None:
        from atlas.capabilities.ide.service import IDEServiceError

        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),  # type: ignore[arg-type]
            clock=FakeClock(),  # type: ignore[arg-type]
            store=SqliteIDESessionStore(db),
        )
        with pytest.raises(IDEServiceError):
            await svc.tree("never-opened")
