"""ADE / IDE REST surface — thin projection over IDEService.

A real `IDEService` (with fake safety/filesystem/ids/clock) drives a temp-dir
workspace, wired onto `app.state.atlas` as a SimpleNamespace — the routes touch
only `atlas.ide_service`, so a full `Atlas` build is unnecessary. The point is
the SEAM, not the engine logic (locked in tests/capabilities/ide):
  * open → tree → read → change round-trips through the HTTP layer;
  * a stale edit surfaces as applied=False/stale=True (never a clobber);
  * an unknown workspace is 404; a disabled subsystem (ide_service=None) is 503.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.capabilities.ide.service import IDEService
from atlas.capabilities.ide.workspace import hash_content
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId
from atlas.infra.types import ToolResult
from atlas.interfaces.api.dependencies import get_atlas
from atlas.interfaces.api.routes_ide import router as ide_router


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

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, output={"exit_code": 0, "stdout": "ran", "stderr": "", "duration_ms": 3})


def _service_with_commands() -> IDEService:
    return IDEService(
        safety=FakeSafety(),
        filesystem_tool=FakeFilesystemTool(),
        ids=FakeIds(),
        clock=FakeClock(),
        command_tool=FakeShellTool(),  # type: ignore[arg-type]
    )


def _client(*, service: IDEService | None) -> TestClient:
    app = FastAPI()
    app.include_router(ide_router, prefix="")  # router carries its own /api/v1/ide prefix
    atlas = SimpleNamespace(ide_service=service)
    app.dependency_overrides[get_atlas] = lambda: atlas
    return TestClient(app)


def _repo(tmp_path: Path) -> str:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2\n")
    return str(tmp_path)


BASE = "/api/v1/ide"


class TestDisabled:
    def test_open_is_503_when_service_absent(self) -> None:
        client = _client(service=None)
        resp = client.post(f"{BASE}/workspaces", json={"root_path": "/tmp", "name": "x"})
        assert resp.status_code == 503
        assert "disabled" in resp.json()["detail"]


class TestReadVertical:
    def test_open_then_tree_and_read(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        opened = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"})
        assert opened.status_code == 200
        wid = opened.json()["workspace_id"]

        tree = client.get(f"{BASE}/workspaces/{wid}/tree")
        assert tree.status_code == 200
        paths = {n["path"] for n in tree.json()["nodes"]}
        assert {"a.py", "sub", "sub/b.py"} <= paths

        doc = client.get(f"{BASE}/workspaces/{wid}/document", params={"path": "a.py"})
        assert doc.status_code == 200
        assert doc.json()["content"] == "x = 1\n"
        assert doc.json()["version"] == hash_content("x = 1\n")

    def test_open_non_directory_is_400(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        missing = str(tmp_path / "does-not-exist")
        resp = client.post(f"{BASE}/workspaces", json={"root_path": missing, "name": "demo"})
        assert resp.status_code == 400

    def test_tree_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service())
        resp = client.get(f"{BASE}/workspaces/nope/tree")
        assert resp.status_code == 404


class TestApplyChange:
    def test_edit_routes_through_funnel_and_writes(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(
            f"{BASE}/workspaces/{wid}/change",
            json={
                "path": "a.py",
                "expected_version": hash_content("x = 1\n"),
                "operations": [{"kind": "replace", "start_line": 0, "end_line": 1, "text": "x = 99\n"}],
                "rationale": "bump",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] is True
        assert (tmp_path / "a.py").read_text() == "x = 99\n"

    def test_stale_edit_refused_never_clobbers(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(
            f"{BASE}/workspaces/{wid}/change",
            json={
                "path": "a.py",
                "expected_version": "stale-hash",
                "operations": [{"kind": "replace", "start_line": 0, "end_line": 1, "text": "x = 99\n"}],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"] is False and body["stale"] is True
        assert (tmp_path / "a.py").read_text() == "x = 1\n"  # untouched

    def test_unknown_edit_op_kind_is_400(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(
            f"{BASE}/workspaces/{wid}/change",
            json={"path": "a.py", "expected_version": None, "operations": [{"kind": "teleport"}]},
        )
        assert resp.status_code == 400

    def test_change_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service())
        resp = client.post(
            f"{BASE}/workspaces/nope/change",
            json={"path": "a.py", "expected_version": None, "operations": [{"kind": "create", "text": "z\n"}]},
        )
        assert resp.status_code == 404


class TestProjectModel:
    def test_project_model_reports_stack(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\ndependencies = ["fastapi"]\n[tool.uv]\n')
        (tmp_path / "conftest.py").write_text("")
        (tmp_path / "main.py").write_text("print('hi')\n")
        client = _client(service=_service())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": str(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.get(f"{BASE}/workspaces/{wid}/project")
        assert resp.status_code == 200
        body = resp.json()
        assert body["languages"][0] == "python"
        assert "uv" in body["package_managers"]
        assert "fastapi" in body["frameworks"]
        assert "pytest" in body["test_commands"]
        assert "main.py" in body["entrypoints"]
        assert body["fingerprint"]

    def test_project_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service())
        assert client.get(f"{BASE}/workspaces/nope/project").status_code == 404


class TestRunCommand:
    def test_command_runs_through_funnel(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_commands())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(f"{BASE}/workspaces/{wid}/command", json={"command": "pytest -q"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["exit_code"] == 0 and body["stdout"] == "ran"
        assert body["denied"] is False

    def test_command_degrades_when_no_tool(self, tmp_path: Path) -> None:
        client = _client(service=_service())  # no command tool wired
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(f"{BASE}/workspaces/{wid}/command", json={"command": "pytest"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False and "not available" in resp.json()["error"]

    def test_command_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service_with_commands())
        assert client.post(f"{BASE}/workspaces/nope/command", json={"command": "pytest"}).status_code == 404


class _OutputShellTool:
    """Shell tool that answers every command with a fixed exit + stdout — lets the
    tests/diagnostics routes exercise real parsing without a subprocess."""

    name = "shell"

    def __init__(self, *, ok: bool, exit_code: int, stdout: str) -> None:
        self._ok, self._exit, self._stdout = ok, exit_code, stdout

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=self._ok,
            output={"exit_code": self._exit, "stdout": self._stdout, "stderr": "", "duration_ms": 6},
            error=None if self._ok else "exit 1",
        )


def _service_with_output(tool: _OutputShellTool) -> IDEService:
    return IDEService(
        safety=FakeSafety(),  # type: ignore[arg-type]
        filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
        ids=FakeIds(),  # type: ignore[arg-type]
        clock=FakeClock(),  # type: ignore[arg-type]
        command_tool=tool,  # type: ignore[arg-type]
    )


_PYTEST_OUT = "==== 1 failed, 2 passed in 0.1s ====\nFAILED tests/t.py::test_x - AssertionError: nope\n"
_MYPY_OUT = "src/app.py:10: error: bad thing  [assignment]\nFound 1 error in 1 file\n"


class TestTestsRoute:
    def test_tests_route_parses_pass_fail(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_output(_OutputShellTool(ok=False, exit_code=1, stdout=_PYTEST_OUT)))
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(f"{BASE}/workspaces/{wid}/tests", json={"command": "pytest -q"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["framework"] == "pytest"
        assert body["ok"] is False and body["exit_code"] == 1
        assert body["passed"] == 2 and body["failed"] == 1
        assert body["failures"][0]["name"] == "test_x"
        assert body["output_tail"]

    def test_tests_route_503_without_command_tool(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        assert client.post(f"{BASE}/workspaces/{wid}/tests", json={"command": "pytest"}).status_code == 503

    def test_tests_route_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service_with_output(_OutputShellTool(ok=True, exit_code=0, stdout="")))
        assert client.post(f"{BASE}/workspaces/nope/tests", json={"command": "pytest"}).status_code == 404


class TestDiagnosticsRoute:
    def test_diagnostics_route_normalizes_findings(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_output(_OutputShellTool(ok=False, exit_code=1, stdout=_MYPY_OUT)))
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(f"{BASE}/workspaces/{wid}/diagnostics", json={"command": "mypy ."})
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is None  # non-zero-with-findings is not a runner error
        assert body["errors"] == 1 and len(body["diagnostics"]) == 1
        d = body["diagnostics"][0]
        assert d["file"] == "src/app.py" and d["line"] == 10 and d["source"] == "mypy"

    def test_diagnostics_route_503_without_command_tool(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        assert client.post(f"{BASE}/workspaces/{wid}/diagnostics", json={"command": "mypy ."}).status_code == 503


class _GitShellTool:
    name = "shell"

    def __init__(self, *, ok: bool, stdout: str = "") -> None:
        self._ok = ok
        self._stdout = stdout

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=self._ok,
            output={"exit_code": 0 if self._ok else 128, "stdout": self._stdout, "stderr": "", "duration_ms": 2},
            error=None if self._ok else "not a git repository",
        )


def _service_with_git(tool: _GitShellTool) -> IDEService:
    return IDEService(
        safety=FakeSafety(),  # type: ignore[arg-type]
        filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
        ids=FakeIds(),
        clock=FakeClock(),
        command_tool=tool,  # type: ignore[arg-type]
    )


class TestGitStatus:
    def test_repo_reports_branch_and_changes(self, tmp_path: Path) -> None:
        tool = _GitShellTool(ok=True, stdout="## main...origin/main [ahead 2]\n M a.py\n?? b.py\n")
        client = _client(service=_service_with_git(tool))
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.get(f"{BASE}/workspaces/{wid}/git/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_git_repo"] is True and body["branch"] == "main" and body["ahead"] == 2
        assert len(body["changes"]) == 2

    def test_non_repo_reports_is_git_repo_false(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_git(_GitShellTool(ok=False)))
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.get(f"{BASE}/workspaces/{wid}/git/status")
        assert resp.status_code == 200
        assert resp.json()["is_git_repo"] is False

    def test_git_status_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service_with_git(_GitShellTool(ok=True)))
        assert client.get(f"{BASE}/workspaces/nope/git/status").status_code == 404


class _DiffShellTool:
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
            output={"exit_code": 0 if self._ok else 128, "stdout": stdout, "stderr": "", "duration_ms": 2},
            error=None if self._ok else "not a git repository",
        )


class TestGitDiff:
    def test_repo_reports_files_and_patch(self, tmp_path: Path) -> None:
        tool = _DiffShellTool(ok=True, numstat="2\t1\ta.py\n", patch="diff --git a/a.py b/a.py\n")
        client = _client(service=_service_with_git(tool))  # type: ignore[arg-type]
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.get(f"{BASE}/workspaces/{wid}/git/diff")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_git_repo"] is True and body["staged"] is False
        assert len(body["files"]) == 1 and body["files"][0]["added"] == 2
        assert body["patch"].startswith("diff --git")

    def test_staged_flag_threads_through(self, tmp_path: Path) -> None:
        tool = _DiffShellTool(ok=True, numstat="", patch="")
        client = _client(service=_service_with_git(tool))  # type: ignore[arg-type]
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        body = client.get(f"{BASE}/workspaces/{wid}/git/diff", params={"staged": True}).json()
        assert body["is_git_repo"] is True and body["staged"] is True and body["files"] == []

    def test_non_repo_reports_is_git_repo_false(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_git(_DiffShellTool(ok=False)))  # type: ignore[arg-type]
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.get(f"{BASE}/workspaces/{wid}/git/diff")
        assert resp.status_code == 200 and resp.json()["is_git_repo"] is False

    def test_git_diff_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service_with_git(_DiffShellTool(ok=True)))  # type: ignore[arg-type]
        assert client.get(f"{BASE}/workspaces/nope/git/diff").status_code == 404


class _GitWriteShellTool:
    """Canned git-write shell tool for the write-op routes (stage/commit/branch)."""

    name = "shell"

    def __init__(self, *, ok: bool = True, stdout: str = "", exit_code: int = 0) -> None:
        self._ok = ok
        self._stdout = stdout
        self._exit = exit_code

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=self._ok,
            output={"exit_code": self._exit, "stdout": self._stdout, "stderr": "", "duration_ms": 2},
            error=None if self._ok else "git failed",
        )


class TestGitWriteRoutes:
    def _wid(self, client: TestClient, tmp_path: Path) -> str:
        return client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]

    def test_stage_route(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_git(_GitWriteShellTool(ok=True)))  # type: ignore[arg-type]
        wid = self._wid(client, tmp_path)
        resp = client.post(f"{BASE}/workspaces/{wid}/git/stage", json={"paths": ["a.py"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "stage" and body["ok"] is True and body["denied"] is False

    def test_commit_route_returns_sha(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=True, stdout="[main 1a2b3c4] msg\n")
        client = _client(service=_service_with_git(tool))  # type: ignore[arg-type]
        wid = self._wid(client, tmp_path)
        resp = client.post(f"{BASE}/workspaces/{wid}/git/commit", json={"message": "msg"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["commit"] == "1a2b3c4" and body["branch"] == "main"

    def test_commit_nothing_staged_is_honest_failure(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=False, exit_code=1, stdout="nothing to commit, working tree clean")
        client = _client(service=_service_with_git(tool))  # type: ignore[arg-type]
        wid = self._wid(client, tmp_path)
        body = client.post(f"{BASE}/workspaces/{wid}/git/commit", json={"message": "x"}).json()
        assert body["ok"] is False and body["commit"] is None

    def test_branch_route(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_git(_GitWriteShellTool(ok=True)))  # type: ignore[arg-type]
        wid = self._wid(client, tmp_path)
        body = client.post(f"{BASE}/workspaces/{wid}/git/branch", json={"name": "feature/x"}).json()
        assert body["ok"] is True and body["branch"] == "feature/x"

    def test_stage_503_without_command_tool(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        wid = self._wid(client, tmp_path)
        assert client.post(f"{BASE}/workspaces/{wid}/git/stage", json={"paths": ["a.py"]}).status_code == 503

    def test_commit_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service_with_git(_GitWriteShellTool(ok=True)))  # type: ignore[arg-type]
        assert client.post(f"{BASE}/workspaces/nope/git/commit", json={"message": "x"}).status_code == 404


class TestCheckpointRoutes:
    def _wid(self, client: TestClient, tmp_path: Path) -> str:
        return client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]

    def test_snapshot_route(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=True, stdout="deadbeefcafe\n")
        client = _client(service=_service_with_git(tool))  # type: ignore[arg-type]
        wid = self._wid(client, tmp_path)
        resp = client.post(f"{BASE}/workspaces/{wid}/checkpoints", json={"label": "before edit"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "snapshot" and body["ok"] is True
        assert body["checkpoint_id"] and body["commit"] == "deadbeefca"

    def test_restore_route(self, tmp_path: Path) -> None:
        tool = _GitWriteShellTool(ok=True, stdout="deadbeefcafe\n")
        client = _client(service=_service_with_git(tool))  # type: ignore[arg-type]
        wid = self._wid(client, tmp_path)
        resp = client.post(f"{BASE}/workspaces/{wid}/checkpoints/cp1/restore")
        assert resp.status_code == 200
        assert resp.json()["action"] == "restore" and resp.json()["ok"] is True

    def test_list_route(self, tmp_path: Path) -> None:
        out = "refs/atlas/checkpoints/cp1\tdeadbee\tbefore edit\n"
        tool = _GitWriteShellTool(ok=True, stdout=out)
        client = _client(service=_service_with_git(tool))  # type: ignore[arg-type]
        wid = self._wid(client, tmp_path)
        body = client.get(f"{BASE}/workspaces/{wid}/checkpoints").json()
        assert body["checkpoints"][0]["checkpoint_id"] == "cp1"
        assert body["checkpoints"][0]["label"] == "before edit"

    def test_snapshot_503_without_command_tool(self, tmp_path: Path) -> None:
        client = _client(service=_service())
        wid = self._wid(client, tmp_path)
        assert client.post(f"{BASE}/workspaces/{wid}/checkpoints", json={}).status_code == 503

    def test_restore_unknown_workspace_is_404(self) -> None:
        client = _client(service=_service_with_git(_GitWriteShellTool(ok=True)))  # type: ignore[arg-type]
        assert client.post(f"{BASE}/workspaces/nope/checkpoints/cp1/restore").status_code == 404


class _StreamingShellTool:
    """A shell tool that honors the ambient sink so the SSE route emits chunks."""

    name = "shell"

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        from atlas.safety.sandbox import SandboxChunk
        from atlas.tools.command_stream import current_sink

        sink = current_sink()
        if sink is not None:
            await sink(SandboxChunk(stream="stdout", data="hello\n"))
        return ToolResult(ok=True, output={"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 2})


def _service_with_streaming() -> IDEService:
    return IDEService(
        safety=FakeSafety(),  # type: ignore[arg-type]
        filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
        ids=FakeIds(),
        clock=FakeClock(),
        command_tool=_StreamingShellTool(),  # type: ignore[arg-type]
    )


class TestTerminalRoutes:
    def test_open_terminal_503_without_command_tool(self, tmp_path: Path) -> None:
        client = _client(service=_service())  # no command tool
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(f"{BASE}/workspaces/{wid}/terminal")
        assert resp.status_code == 503

    def test_open_terminal_unknown_workspace_404(self) -> None:
        client = _client(service=_service_with_streaming())
        resp = client.post(f"{BASE}/workspaces/nope/terminal")
        assert resp.status_code == 404

    def test_stream_unknown_terminal_404(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_streaming())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.get(f"{BASE}/workspaces/{wid}/terminal/nope/stream")
        assert resp.status_code == 404

    def test_open_run_and_stream_end_to_end(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_streaming())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        tid = client.post(f"{BASE}/workspaces/{wid}/terminal").json()["terminal_id"]

        started = client.post(f"{BASE}/workspaces/{wid}/terminal/{tid}/run", json={"command": "pytest -q"})
        assert started.status_code == 200 and started.json()["started"] is True

        # Read the SSE body: connected → chunk(s) → exit → stream_closed.
        body = client.get(f"{BASE}/workspaces/{wid}/terminal/{tid}/stream").text
        assert "event: connected" in body
        assert "event: chunk" in body and "hello" in body
        assert "event: exit" in body
        assert "event: stream_closed" in body

    def test_run_unknown_terminal_started_false(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_streaming())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(f"{BASE}/workspaces/{wid}/terminal/nope/run", json={"command": "pytest"})
        assert resp.status_code == 200 and resp.json()["started"] is False

    def test_stream_after_cursor_skips_prior_output(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_streaming())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        tid = client.post(f"{BASE}/workspaces/{wid}/terminal").json()["terminal_id"]
        client.post(f"{BASE}/workspaces/{wid}/terminal/{tid}/run", json={"command": "pytest -q"})

        # Resume past the first chunk (seq 1): its "hello" must not replay, but the
        # terminal exit still arrives so the consumer closes honestly.
        body = client.get(f"{BASE}/workspaces/{wid}/terminal/{tid}/stream", params={"after": 1}).text
        assert "hello" not in body
        assert "event: exit" in body and "event: stream_closed" in body


class _PortStreamingShellTool:
    """Announces a dev-server port then exits — drives the process supervisor's
    port detection through the same governed sink the terminal uses."""

    name = "shell"

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        from atlas.safety.sandbox import SandboxChunk
        from atlas.tools.command_stream import current_sink

        sink = current_sink()
        if sink is not None:
            await sink(SandboxChunk(stream="stdout", data="  ➜  Local: http://localhost:5173/\n"))
        return ToolResult(ok=True, output={"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 2})


def _service_with_ports() -> IDEService:
    return IDEService(
        safety=FakeSafety(),  # type: ignore[arg-type]
        filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
        ids=FakeIds(),
        clock=FakeClock(),
        command_tool=_PortStreamingShellTool(),  # type: ignore[arg-type]
    )


class TestProcessRoutes:
    def test_start_process_503_without_command_tool(self, tmp_path: Path) -> None:
        client = _client(service=_service())  # no command tool
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(f"{BASE}/workspaces/{wid}/processes", json={"command": "npm run dev"})
        assert resp.status_code == 503

    def test_start_process_unknown_workspace_404(self) -> None:
        client = _client(service=_service_with_ports())
        resp = client.post(f"{BASE}/workspaces/nope/processes", json={"command": "npm run dev"})
        assert resp.status_code == 404

    def test_start_list_detects_port_end_to_end(self, tmp_path: Path) -> None:
        import time

        client = _client(service=_service_with_ports())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        started = client.post(f"{BASE}/workspaces/{wid}/processes", json={"command": "npm run dev"})
        assert started.status_code == 200
        pid = started.json()["id"]
        assert started.json()["command"] == "npm run dev"

        # The scanner folds the streamed port asynchronously; poll the honest state.
        detected: list[int] = []
        status = ""
        for _ in range(50):
            procs = client.get(f"{BASE}/workspaces/{wid}/processes").json()["processes"]
            match = next((p for p in procs if p["id"] == pid), None)
            assert match is not None
            detected, status = match["detected_ports"], match["status"]
            if detected and status == "exited":
                break
            time.sleep(0.02)
        assert detected == [5173]
        assert status == "exited"

    def test_stop_unknown_process_is_false(self, tmp_path: Path) -> None:
        client = _client(service=_service_with_ports())
        wid = client.post(f"{BASE}/workspaces", json={"root_path": _repo(tmp_path), "name": "demo"}).json()[
            "workspace_id"
        ]
        resp = client.post(f"{BASE}/workspaces/{wid}/processes/nope/stop")
        assert resp.status_code == 200 and resp.json()["stopped"] is False

    def test_list_processes_unknown_workspace_404(self) -> None:
        client = _client(service=_service_with_ports())
        assert client.get(f"{BASE}/workspaces/nope/processes").status_code == 404
