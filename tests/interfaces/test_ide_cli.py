"""``atlas ide`` in-process CLI commands — thin shell over IDEService.

Patches ``build_atlas`` to yield a fake Atlas holding a REAL ``IDEService`` (with
fake safety/filesystem/ids/clock) over a temp workspace, so the command wiring —
arg parsing, sub-typer registration, disabled-path guidance, and the tree/read/
edit round-trip through the service — is exercised without a full Atlas build or
network. The engine logic itself is locked in tests/capabilities/ide.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from atlas.capabilities.ide.service import IDEService
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId
from atlas.infra.types import ToolResult
from atlas.interfaces.cli import app

runner = CliRunner()


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


def _service() -> IDEService:
    return IDEService(safety=FakeSafety(), filesystem_tool=FakeFilesystemTool(), ids=FakeIds(), clock=FakeClock())  # type: ignore[arg-type]


class FakeShellTool:
    name = "shell"

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, output={"exit_code": 0, "stdout": "ran-ok", "stderr": "", "duration_ms": 7})


def _service_with_commands() -> IDEService:
    return IDEService(
        safety=FakeSafety(),
        filesystem_tool=FakeFilesystemTool(),
        ids=FakeIds(),
        clock=FakeClock(),
        command_tool=FakeShellTool(),  # type: ignore[arg-type]
    )


def _patch_atlas(service: IDEService | None) -> Any:
    atlas = SimpleNamespace(ide_service=service)

    @asynccontextmanager
    async def fake_build_atlas() -> AsyncIterator[Any]:
        yield atlas

    return patch("atlas.interfaces.cli.build_atlas", fake_build_atlas)


def _repo(tmp_path: Path) -> str:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2\n")
    return str(tmp_path)


class TestDisabled:
    def test_tree_reports_disabled_and_exits_nonzero(self, tmp_path: Path) -> None:
        with _patch_atlas(None):
            res = runner.invoke(app, ["ide", "tree", str(tmp_path)])
        assert res.exit_code == 1
        assert "disabled" in res.stdout


class TestTreeReadEdit:
    def test_tree_lists_entries(self, tmp_path: Path) -> None:
        with _patch_atlas(_service()):
            res = runner.invoke(app, ["ide", "tree", _repo(tmp_path)])
        assert res.exit_code == 0
        assert "a.py" in res.stdout and "sub/b.py" in res.stdout

    def test_read_prints_content_and_version(self, tmp_path: Path) -> None:
        with _patch_atlas(_service()):
            res = runner.invoke(app, ["ide", "read", _repo(tmp_path), "a.py"])
        assert res.exit_code == 0
        assert "x = 1" in res.stdout and "version=" in res.stdout

    def test_edit_applies_through_funnel(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        with _patch_atlas(_service()):
            res = runner.invoke(app, ["ide", "edit", root, "a.py", "--start", "0", "--end", "1", "--text", "x = 99\n"])
        assert res.exit_code == 0
        assert "applied" in res.stdout
        assert (tmp_path / "a.py").read_text() == "x = 99\n"

    def test_read_missing_file_exits_nonzero(self, tmp_path: Path) -> None:
        with _patch_atlas(_service()):
            res = runner.invoke(app, ["ide", "read", _repo(tmp_path), "nope.py"])
        assert res.exit_code == 1


class TestProject:
    def test_project_reports_stack(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\ndependencies = ["fastapi"]\n[tool.uv]\n')
        (tmp_path / "conftest.py").write_text("")
        (tmp_path / "main.py").write_text("print('hi')\n")
        with _patch_atlas(_service()):
            res = runner.invoke(app, ["ide", "project", str(tmp_path)])
        assert res.exit_code == 0
        assert "python" in res.stdout
        assert "uv" in res.stdout
        assert "fastapi" in res.stdout


class TestRun:
    def test_run_executes_and_prints_output(self, tmp_path: Path) -> None:
        with _patch_atlas(_service_with_commands()):
            res = runner.invoke(app, ["ide", "run", _repo(tmp_path), "pytest -q"])
        assert res.exit_code == 0
        assert "ran-ok" in res.stdout and "ok" in res.stdout

    def test_run_reports_disabled_commands_as_failure(self, tmp_path: Path) -> None:
        with _patch_atlas(_service()):  # no command tool wired
            res = runner.invoke(app, ["ide", "run", _repo(tmp_path), "pytest"])
        assert res.exit_code == 1
        assert "not available" in res.stdout
