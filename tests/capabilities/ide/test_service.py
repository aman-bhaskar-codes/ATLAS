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
