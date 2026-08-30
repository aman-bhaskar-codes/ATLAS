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
