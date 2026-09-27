"""IDEService tests — the capability façade over open workspaces.

Real `WorkspaceEngine` on tmp_path + fake safety/filesystem/ids/clock. Locks the
façade's contract the interface layer depends on:
  * open → the workspace is addressable; tree/read work against it;
  * apply_change routes through the (fake) funnel and returns an honest result;
  * an unknown workspace id raises IDEServiceError, never returns empty.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas.capabilities.ide.contracts import EditOperation, EditOpKind, FileChange
from atlas.capabilities.ide.service import IDEService, IDEServiceError
from atlas.capabilities.ide.workspace import hash_content
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
    def __init__(self) -> None:
        self.guarded: list[Any] = []

    async def guard(self, req: Any, tool: Any) -> ToolResult:
        self.guarded.append(req)
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


def _service() -> IDEService:
    return IDEService(safety=FakeSafety(), filesystem_tool=FakeFilesystemTool(), ids=FakeIds(), clock=FakeClock())  # type: ignore[arg-type]


class FakeShellTool:
    name = "shell"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls.append(args)
        return ToolResult(ok=True, output={"exit_code": 0, "stdout": "ok", "stderr": "", "duration_ms": 5})


def _service_with_commands(tool: FakeShellTool) -> IDEService:
    return IDEService(
        safety=FakeSafety(),
        filesystem_tool=FakeFilesystemTool(),
        ids=FakeIds(),
        clock=FakeClock(),
        command_tool=tool,  # type: ignore[arg-type]
    )


def _repo(tmp_path: Path) -> str:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2\n")
    return str(tmp_path)


class TestLifecycle:
    async def test_open_then_tree_and_read(self, tmp_path: Path) -> None:
        svc = _service()
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        wid = session.workspace.id
        paths = {n.path for n in await svc.tree(wid)}
        assert {"a.py", "sub", "sub/b.py"} <= paths
        snap, content = await svc.read_document(wid, "a.py")
        assert content == "x = 1\n"
        assert snap.version == hash_content("x = 1\n")

    async def test_close_is_idempotent(self, tmp_path: Path) -> None:
        svc = _service()
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert svc.close_workspace(session.workspace.id) is True
        assert svc.close_workspace(session.workspace.id) is False

    async def test_unknown_workspace_raises(self) -> None:
        svc = _service()
        with pytest.raises(IDEServiceError):
            await svc.tree("nope")


class TestApplyChange:
    async def test_edit_routes_through_funnel(self, tmp_path: Path) -> None:
        svc = _service()
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        wid = session.workspace.id
        change = FileChange(
            path="a.py",
            expected_version=hash_content("x = 1\n"),
            operations=(EditOperation(kind=EditOpKind.REPLACE, start_line=0, end_line=1, text="x = 99\n"),),
        )
        result = await svc.apply_change(wid, change)
        assert result.applied is True
        assert (tmp_path / "a.py").read_text() == "x = 99\n"

    async def test_stale_edit_refused(self, tmp_path: Path) -> None:
        svc = _service()
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        change = FileChange(
            path="a.py",
            expected_version="wrong",
            operations=(EditOperation(kind=EditOpKind.REPLACE, start_line=0, end_line=1, text="x = 99\n"),),
        )
        result = await svc.apply_change(session.workspace.id, change)
        assert result.applied is False and result.stale is True


class TestRunCommand:
    async def test_run_command_routes_through_funnel_in_workspace_cwd(self, tmp_path: Path) -> None:
        tool = FakeShellTool()
        svc = _service_with_commands(tool)
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        result = await svc.run_command(session.workspace.id, "pytest -q")
        assert result.ok is True and result.exit_code == 0 and result.stdout == "ok"
        # Command ran in the workspace root, through the funnel (recorded by the tool).
        assert tool.calls[0]["command"] == "pytest -q"
        assert tool.calls[0]["cwd"] == str(tmp_path)

    async def test_run_command_degrades_when_no_tool(self, tmp_path: Path) -> None:
        svc = _service()  # no command_tool wired
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        result = await svc.run_command(session.workspace.id, "pytest")
        assert result.ok is False and result.error == "command execution not available"

    async def test_run_command_unknown_workspace_raises(self) -> None:
        svc = _service_with_commands(FakeShellTool())
        with pytest.raises(IDEServiceError):
            await svc.run_command("nope", "pytest")


class _GitShellTool:
    """Shell tool that answers the git-status command with canned porcelain."""

    name = "shell"

    def __init__(self, *, ok: bool, stdout: str = "") -> None:
        self._ok = ok
        self._stdout = stdout
        self.calls: list[dict[str, Any]] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls.append(args)
        return ToolResult(
            ok=self._ok,
            output={"exit_code": 0 if self._ok else 128, "stdout": self._stdout, "stderr": "", "duration_ms": 3},
            error=None if self._ok else "not a git repository",
        )


class TestGitStatus:
    async def test_repo_returns_parsed_status_through_funnel(self, tmp_path: Path) -> None:
        tool = _GitShellTool(ok=True, stdout="## main...origin/main [ahead 1]\n M a.py\n")
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=tool,  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        status = await svc.git_status(session.workspace.id)
        assert status is not None and status.branch == "main" and status.ahead == 1
        assert len(status.changes) == 1
        assert tool.calls[0]["cwd"] == str(tmp_path)
        assert tool.calls[0]["command"].startswith("git status")

    async def test_non_repo_returns_none(self, tmp_path: Path) -> None:
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=_GitShellTool(ok=False),  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.git_status(session.workspace.id) is None

    async def test_git_status_degrades_when_no_tool(self, tmp_path: Path) -> None:
        svc = _service()  # no command_tool wired
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.git_status(session.workspace.id) is None


class _DiffShellTool:
    """Answers git-diff numstat + raw patch (and non-repo) for git_diff tests."""

    name = "shell"

    def __init__(self, *, ok: bool, numstat: str = "", patch: str = "") -> None:
        self._ok = ok
        self._numstat = numstat
        self._patch = patch

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        cmd = str(args.get("command", ""))
        stdout = self._numstat if "--numstat" in cmd else self._patch
        return ToolResult(
            ok=self._ok,
            output={"exit_code": 0 if self._ok else 128, "stdout": stdout, "stderr": "", "duration_ms": 1},
            error=None if self._ok else "not a git repository",
        )


class TestGitDiff:
    async def test_repo_returns_parsed_diff(self, tmp_path: Path) -> None:
        tool = _DiffShellTool(ok=True, numstat="2\t1\ta.py\n", patch="diff --git a/a.py b/a.py\n")
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=tool,  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        diff = await svc.git_diff(session.workspace.id)
        assert diff is not None and len(diff.files) == 1 and diff.files[0].added == 2
        assert diff.patch.startswith("diff --git")

    async def test_non_repo_returns_none(self, tmp_path: Path) -> None:
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=_DiffShellTool(ok=False),  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.git_diff(session.workspace.id) is None

    async def test_git_diff_degrades_when_no_tool(self, tmp_path: Path) -> None:
        svc = _service()
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.git_diff(session.workspace.id) is None


class _GitWriteShellTool:
    """Shell tool for git WRITE verbs: canned ok/stdout, records the command+cwd
    so we can assert the write went through the funnel in the workspace root."""

    name = "shell"

    def __init__(self, *, ok: bool = True, stdout: str = "", exit_code: int = 0) -> None:
        self._ok = ok
        self._stdout = stdout
        self._exit = exit_code
        self.calls: list[dict[str, Any]] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls.append(args)
        return ToolResult(
            ok=self._ok,
            output={"exit_code": self._exit, "stdout": self._stdout, "stderr": "", "duration_ms": 2},
            error=None if self._ok else "git failed",
        )


class TestGitWriteOps:
    async def test_stage_routes_through_funnel(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=True)
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=tool,  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        res = await svc.git_stage(session.workspace.id, ["a.py"])
        assert res is not None and res.ok is True and res.action == "stage"
        assert tool.calls[0]["command"] == "git add -- a.py"
        assert tool.calls[0]["cwd"] == str(tmp_path)

    async def test_commit_parses_sha(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=True, stdout="[main 1a2b3c4] msg\n")
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=tool,  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        res = await svc.git_commit(session.workspace.id, "msg")
        assert res is not None and res.ok is True and res.commit == "1a2b3c4" and res.branch == "main"

    async def test_branch_create(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=True)
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=tool,  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        res = await svc.git_branch(session.workspace.id, "feature/x")
        assert res is not None and res.branch == "feature/x"
        assert tool.calls[0]["command"] == "git checkout -b feature/x"

    async def test_git_write_degrades_when_no_tool(self, tmp_path: Path) -> None:
        svc = _service()  # no command_tool wired
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.git_stage(session.workspace.id, ["a.py"]) is None
        assert await svc.git_commit(session.workspace.id, "m") is None
        assert await svc.git_branch(session.workspace.id, "b") is None

    async def test_git_write_unknown_workspace_raises(self) -> None:
        svc = _service_with_commands(FakeShellTool())
        with pytest.raises(IDEServiceError):
            await svc.git_stage("nope", ["a.py"])


class TestCheckpoints:
    async def test_snapshot_routes_through_funnel(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=True, stdout="deadbeefcafe\n")
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=tool,  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        res = await svc.checkpoint_snapshot(session.workspace.id, label="before edit")
        assert res is not None and res.ok is True and res.action == "snapshot"
        assert res.checkpoint_id is not None and res.commit == "deadbeefca"
        assert tool.calls[0]["command"] == "git stash create"
        assert tool.calls[0]["cwd"] == str(tmp_path)

    async def test_restore_routes_through_funnel(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=True, stdout="deadbeefcafe\n")
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=tool,  # type: ignore[arg-type]
        )
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        res = await svc.checkpoint_restore(session.workspace.id, "cp1")
        assert res is not None and res.ok is True and res.action == "restore"
        assert tool.calls[-1]["command"] == "git checkout deadbeefcafe -- ."

    async def test_checkpoint_degrades_when_no_tool(self, tmp_path: Path) -> None:
        svc = _service()  # no command_tool wired
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.checkpoint_snapshot(session.workspace.id) is None
        assert await svc.checkpoint_restore(session.workspace.id, "cp1") is None
        assert await svc.checkpoint_list(session.workspace.id) is None

    async def test_checkpoint_unknown_workspace_raises(self) -> None:
        svc = _service_with_commands(FakeShellTool())
        with pytest.raises(IDEServiceError):
            await svc.checkpoint_snapshot("nope")


class _StreamingShellTool:
    """A shell tool that honors the ambient output sink (the terminal path).

    Reads the contextvar sink the `TerminalSessionManager` binds and pushes a
    couple of `SandboxChunk`s before returning — proving output is delivered
    incrementally through the SAME `execute`, not a second path."""

    name = "shell"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        from atlas.safety.sandbox import SandboxChunk
        from atlas.tools.command_stream import current_sink

        self.calls.append(args)
        sink = current_sink()
        if sink is not None:
            await sink(SandboxChunk(stream="stdout", data="line one\n"))
            await sink(SandboxChunk(stream="stdout", data="line two\n"))
        return ToolResult(ok=True, output={"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 7})


class TestTerminal:
    async def test_open_run_and_stream_delivers_chunks_then_exit(self, tmp_path: Path) -> None:
        tool = _StreamingShellTool()
        svc = _service_with_commands(tool)  # type: ignore[arg-type]
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        wid = session.workspace.id

        tid = await svc.open_terminal(wid)
        assert tid is not None

        collected: list[Any] = []

        async def _drain() -> None:
            async for event in svc.terminal_stream(wid, str(tid)):
                collected.append(event)

        import asyncio

        reader = asyncio.create_task(_drain())
        started = await svc.run_terminal_command(wid, str(tid), "pytest -q")
        assert started is True
        await asyncio.wait_for(reader, timeout=5.0)

        kinds = [e.kind for e in collected]
        assert kinds[-1] == "exit"
        chunks = [e for e in collected if e.kind == "chunk"]
        assert [c.data for c in chunks] == ["line one\n", "line two\n"]
        exit_event = collected[-1]
        assert exit_event.exit_code == 0 and exit_event.denied is False
        # Ran in the workspace root, through the funnel (recorded by the tool).
        assert tool.calls[0]["cwd"] == str(tmp_path)
        assert tool.calls[0]["command"] == "pytest -q"

    async def test_run_terminal_unknown_session_is_false(self, tmp_path: Path) -> None:
        svc = _service_with_commands(_StreamingShellTool())  # type: ignore[arg-type]
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.run_terminal_command(session.workspace.id, "nope", "pytest") is False

    async def test_open_terminal_degrades_when_no_tool(self, tmp_path: Path) -> None:
        svc = _service()  # no command_tool wired
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.open_terminal(session.workspace.id) is None

    async def test_terminal_exists_in(self, tmp_path: Path) -> None:
        svc = _service_with_commands(_StreamingShellTool())  # type: ignore[arg-type]
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        wid = session.workspace.id
        tid = await svc.open_terminal(wid)
        assert tid is not None
        assert await svc.terminal_exists_in(wid, str(tid)) is True
        assert await svc.terminal_exists_in(wid, "nope") is False
        assert await svc.terminal_exists_in("badws", str(tid)) is False

    async def test_stream_resumes_after_cursor(self, tmp_path: Path) -> None:
        import asyncio

        tool = _StreamingShellTool()
        svc = _service_with_commands(tool)  # type: ignore[arg-type]
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        wid = session.workspace.id
        tid = await svc.open_terminal(wid)
        assert tid is not None

        first: list[Any] = []

        async def _drain_all() -> None:
            async for event in svc.terminal_stream(wid, str(tid)):
                first.append(event)

        reader = asyncio.create_task(_drain_all())
        await svc.run_terminal_command(wid, str(tid), "pytest -q")
        await asyncio.wait_for(reader, timeout=5.0)

        # Reattach after the first chunk's seq — must NOT replay it, and must still
        # deliver the terminal exit so the consumer closes honestly.
        after = first[0].seq
        resumed: list[Any] = []
        async for event in svc.terminal_stream(wid, str(tid), after_seq=after):
            resumed.append(event)
        assert all(e.seq > after for e in resumed)
        assert resumed[-1].kind == "exit"


class _PortShellTool:
    """A streaming tool that announces a dev-server URL then exits cleanly —
    exercises the supervisor's port detection over the terminal funnel."""

    name = "shell"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        from atlas.safety.sandbox import SandboxChunk
        from atlas.tools.command_stream import current_sink

        self.calls.append(args)
        sink = current_sink()
        if sink is not None:
            await sink(SandboxChunk(stream="stdout", data="  ➜  Local: http://localhost:5173/\n"))
        return ToolResult(ok=True, output={"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 3})


class _ServerShellTool:
    """A streaming tool that binds a port then blocks forever — a stand-in for a
    dev server, so a `stop` can cancel it mid-run."""

    name = "shell"

    def __init__(self) -> None:
        self.started = __import__("asyncio").Event()

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        import asyncio

        from atlas.safety.sandbox import SandboxChunk
        from atlas.tools.command_stream import current_sink

        sink = current_sink()
        if sink is not None:
            await sink(SandboxChunk(stream="stdout", data="listening on 8080\n"))
        self.started.set()
        await asyncio.Event().wait()  # never returns; a stop cancels this task
        return ToolResult(ok=True, output={"exit_code": 0})  # pragma: no cover


class TestProcessSupervisor:
    async def test_start_detects_ports_and_records_exit(self, tmp_path: Path) -> None:
        import asyncio

        svc = _service_with_commands(_PortShellTool())  # type: ignore[arg-type]
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        wid = session.workspace.id

        proc = await svc.start_process(wid, "npm run dev")
        assert proc is not None
        # The scanner consumes the session stream; give it a beat to fold events.
        for _ in range(50):
            procs = await svc.list_processes(wid)
            if procs and procs[0].detected_ports and procs[0].status.value == "exited":
                break
            await asyncio.sleep(0.01)
        procs = await svc.list_processes(wid)
        assert procs[0].detected_ports == (5173,)
        assert procs[0].status.value == "exited"
        assert procs[0].exit_code == 0

    async def test_stop_cancels_a_running_process(self, tmp_path: Path) -> None:
        import asyncio

        tool = _ServerShellTool()
        svc = _service_with_commands(tool)  # type: ignore[arg-type]
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        wid = session.workspace.id

        proc = await svc.start_process(wid, "npm run dev")
        assert proc is not None
        await asyncio.wait_for(tool.started.wait(), timeout=5.0)

        assert await svc.stop_process(wid, str(proc.id)) is True
        for _ in range(50):
            got = (await svc.list_processes(wid))[0]
            if got.status.value == "killed":
                break
            await asyncio.sleep(0.01)
        assert (await svc.list_processes(wid))[0].status.value == "killed"

    async def test_stop_unknown_process_is_false(self, tmp_path: Path) -> None:
        svc = _service_with_commands(_PortShellTool())  # type: ignore[arg-type]
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        assert await svc.stop_process(session.workspace.id, "nope") is False

    async def test_processes_degrade_when_no_tool(self, tmp_path: Path) -> None:
        svc = _service()  # no command tool wired
        session = await svc.open_workspace(_repo(tmp_path), "demo")
        wid = session.workspace.id
        assert await svc.start_process(wid, "npm run dev") is None
        assert await svc.list_processes(wid) == ()
        assert await svc.stop_process(wid, "x") is False

    async def test_list_scoped_to_workspace(self, tmp_path: Path) -> None:
        import asyncio

        svc = _service_with_commands(_PortShellTool())  # type: ignore[arg-type]
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        s1 = await svc.open_workspace(_repo(tmp_path / "a"), "a")
        s2 = await svc.open_workspace(_repo(tmp_path / "b"), "b")
        p1 = await svc.start_process(s1.workspace.id, "npm run dev")
        assert p1 is not None
        await asyncio.sleep(0.05)
        ids2 = [p.id for p in await svc.list_processes(s2.workspace.id)]
        assert str(p1.id) not in [str(i) for i in ids2]
        assert await svc.stop_process(s2.workspace.id, str(p1.id)) is False


def test_detect_ports_is_conservative() -> None:
    from atlas.capabilities.ide.process import detect_ports

    assert detect_ports("Local: http://localhost:5173/") == {5173}
    assert detect_ports("listening on 8080") == {8080}
    assert detect_ports("Port: 3000 ready") == {3000}
    assert detect_ports("running at http://127.0.0.1:8000") == {8000}
    # No bare five-digit log noise without a port cue.
    assert detect_ports("built 123456 modules in 900ms") == set()


class TestRunCommandCwd:
    """The IDE's "run in the workspace root" contract must survive the LAST hop too: the
    cwd rides the audited request and is forwarded by the real `ShellTool`. A fake
    command tool cannot catch a broken hop — this wires the real one."""

    async def test_the_workspace_root_reaches_the_real_sandbox(self, tmp_path: Path) -> None:
        from atlas.safety.sandbox import SandboxResult
        from atlas.tools.shell import ShellTool

        class _RecordingSandbox:
            def __init__(self) -> None:
                self.cwds: list[str | None] = []

            async def run(
                self,
                command: list[str],
                *,
                mounts: dict[str, str],
                network: bool = False,
                timeout_s: float = 60.0,
                stdin: bytes | None = None,
                cwd: str | None = None,
            ) -> SandboxResult:
                self.cwds.append(cwd)
                return SandboxResult(exit_code=0, stdout_tail="ok", stderr_tail="", duration_ms=1)

        sandbox = _RecordingSandbox()
        shell = ShellTool(read_only=["git status"], side_effect=[], sandbox=sandbox, mounts={})  # type: ignore[arg-type]
        svc = IDEService(
            safety=FakeSafety(),  # type: ignore[arg-type]
            filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
            ids=FakeIds(),
            clock=FakeClock(),
            command_tool=shell,
        )
        root = _repo(tmp_path)
        session = await svc.open_workspace(root, "demo")

        result = await svc.run_command(session.workspace.id, "git status")

        assert result.ok is True
        assert sandbox.cwds == [str(Path(root).resolve())]
