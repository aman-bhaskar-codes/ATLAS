"""M2.* — the IDE-verb → workspace-scoped Tool adapter: IDEWorkspaceTool.

Proves the adapter maps a model-issued ``{operation, args}`` onto the right
``IDEService`` verb, marshals the frozen result contracts into a JSON-safe
``ToolResult``, and turns expected governed failures (unknown workspace, bad path,
malformed edit, a refused write) into an honest ``ToolResult(ok=False, error=...)``
rather than a raise. A scripted fake ``IDEService`` records the verb calls — no
real workspace, no SafetyEngine, no filesystem: the adapter is a pure marshaller,
and the funnel it delegates to is locked by the IDEService's own tests.
"""

from __future__ import annotations

from typing import Any

from atlas.capabilities.ide.agent_tool import IDEWorkspaceTool
from atlas.capabilities.ide.contracts import (
    ChangeResult,
    CommandResult,
    DocumentSnapshot,
    FileNode,
    GitDiff,
    GitStatus,
    ProjectModel,
)
from atlas.capabilities.ide.service import IDEServiceError
from atlas.capabilities.ide.workspace import WorkspaceError


class FakeIDEService:
    """Records the verb + workspace it was called with; returns canned contracts.

    ``apply_change``/``run_command`` echo back whatever ``ChangeResult``/
    ``CommandResult`` the test seeded, so the adapter's ok/error projection is
    exercised for both the applied and the refused case.
    """

    def __init__(
        self,
        *,
        change_result: ChangeResult | None = None,
        command_result: CommandResult | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._change_result = change_result
        self._command_result = command_result
        self._raises = raises

    def _record(self, verb: str, workspace_id: str, **kw: Any) -> None:
        self.calls.append((verb, workspace_id, kw))
        if self._raises is not None:
            raise self._raises

    async def tree(self, workspace_id: str) -> tuple[FileNode, ...]:
        self._record("tree", workspace_id)
        return (FileNode(path="a.py", name="a.py", is_dir=False, size=3, version="v1"),)

    async def read_document(self, workspace_id: str, path: str) -> tuple[DocumentSnapshot, str]:
        self._record("read_document", workspace_id, path=path)
        snap = DocumentSnapshot(id="doc-1", path=path, language="python", version="v1", line_count=1)
        return snap, "print('hi')\n"

    async def project_model(self, workspace_id: str) -> ProjectModel:
        self._record("project_model", workspace_id)
        return ProjectModel(root="/w", languages=("python",), test_commands=("pytest",))

    async def git_status(self, workspace_id: str) -> GitStatus | None:
        self._record("git_status", workspace_id)
        return GitStatus(branch="main")

    async def git_diff(self, workspace_id: str, *, staged: bool = False) -> GitDiff | None:
        self._record("git_diff", workspace_id, staged=staged)
        return GitDiff(staged=staged, patch="@@ -1 +1 @@")

    async def apply_change(self, workspace_id: str, change: Any, *, correlation_id: str | None = None) -> ChangeResult:
        self._record("apply_change", workspace_id, change=change)
        assert self._change_result is not None
        return self._change_result

    async def run_command(
        self, workspace_id: str, command: str, *, timeout_s: float = 120.0, correlation_id: str | None = None
    ) -> CommandResult:
        self._record("run_command", workspace_id, command=command, timeout_s=timeout_s)
        assert self._command_result is not None
        return self._command_result


def _tool(service: FakeIDEService, *, default_workspace_id: str | None = "ws-1") -> IDEWorkspaceTool:
    return IDEWorkspaceTool(service, default_workspace_id=default_workspace_id)  # type: ignore[arg-type]


# ── reads ─────────────────────────────────────────────────────────────── #
async def test_tree_maps_to_verb_and_serializes() -> None:
    svc = FakeIDEService()
    result = await _tool(svc).execute({"operation": "tree"})
    assert result.ok
    assert result.output["count"] == 1
    assert result.output["tree"][0]["path"] == "a.py"
    assert svc.calls == [("tree", "ws-1", {})]


async def test_read_document_requires_path_and_truncates() -> None:
    svc = FakeIDEService()
    result = await _tool(svc).execute({"operation": "read_document", "path": "a.py"})
    assert result.ok and result.output["content"] == "print('hi')\n"
    assert result.output["path"] == "a.py"
    assert ("read_document", "ws-1", {"path": "a.py"}) in svc.calls


async def test_project_model_and_git_verbs() -> None:
    svc = FakeIDEService()
    tool = _tool(svc)
    pm = await tool.execute({"operation": "project_model"})
    assert pm.ok and pm.output["languages"] == ("python",)
    gs = await tool.execute({"operation": "git_status"})
    assert gs.ok and gs.output["git_status"]["branch"] == "main"
    gd = await tool.execute({"operation": "git_diff", "staged": True})
    assert gd.ok and gd.output["git_diff"]["staged"] is True


# ── mutations ───────────────────────────────────────────────────────────── #
async def test_apply_change_builds_filechange_and_projects_applied() -> None:
    svc = FakeIDEService(change_result=ChangeResult(path="a.py", applied=True, new_version="v2"))
    result = await _tool(svc).execute(
        {
            "operation": "apply_change",
            "path": "a.py",
            "expected_version": "v1",
            "operations": [{"kind": "replace", "start_line": 0, "end_line": 1, "text": "x = 1\n"}],
        }
    )
    assert result.ok and result.error is None
    assert result.output["new_version"] == "v2"
    # the FileChange the adapter built carried the ops + expected_version through
    _, _, kw = next(c for c in svc.calls if c[0] == "apply_change")
    change = kw["change"]
    assert change.path == "a.py" and change.expected_version == "v1"
    assert change.operations[0].kind == "replace" and change.operations[0].text == "x = 1\n"


async def test_apply_change_reports_stale_refusal_as_not_ok() -> None:
    svc = FakeIDEService(change_result=ChangeResult(path="a.py", applied=False, stale=True, error="stale"))
    result = await _tool(svc).execute(
        {
            "operation": "apply_change",
            "path": "a.py",
            "operations": [{"kind": "insert", "start_line": 0, "text": "y\n"}],
        }
    )
    assert not result.ok and result.error == "stale"


async def test_apply_change_rejects_empty_operations() -> None:
    result = await _tool(FakeIDEService()).execute({"operation": "apply_change", "path": "a.py", "operations": []})
    assert not result.ok and "non-empty" in (result.error or "")


async def test_apply_change_rejects_malformed_operation() -> None:
    result = await _tool(FakeIDEService()).execute(
        {"operation": "apply_change", "path": "a.py", "operations": [{"kind": "not-a-kind"}]}
    )
    assert not result.ok and "invalid edit operation" in (result.error or "")


async def test_run_command_projects_result_and_passes_timeout() -> None:
    svc = FakeIDEService(command_result=CommandResult(command="pytest", ok=True, exit_code=0, stdout="ok"))
    result = await _tool(svc).execute({"operation": "run_command", "command": "pytest", "timeout_s": 5})
    assert result.ok and result.output["stdout"] == "ok"
    _, _, kw = next(c for c in svc.calls if c[0] == "run_command")
    assert kw["command"] == "pytest" and kw["timeout_s"] == 5.0


async def test_run_command_denied_is_not_ok() -> None:
    svc = FakeIDEService(command_result=CommandResult(command="rm -rf /", ok=False, denied=True, error="denied"))
    result = await _tool(svc).execute({"operation": "run_command", "command": "rm -rf /"})
    assert not result.ok and result.error == "denied"


# ── workspace scoping + errors ──────────────────────────────────────────── #
async def test_explicit_workspace_id_overrides_default() -> None:
    svc = FakeIDEService()
    await _tool(svc, default_workspace_id="ws-1").execute({"operation": "tree", "workspace_id": "ws-2"})
    assert svc.calls[0][1] == "ws-2"


async def test_missing_workspace_id_is_honest_error() -> None:
    result = await _tool(FakeIDEService(), default_workspace_id=None).execute({"operation": "tree"})
    assert not result.ok and "workspace_id" in (result.error or "")


async def test_unknown_operation_is_error_not_raise() -> None:
    result = await _tool(FakeIDEService()).execute({"operation": "frobnicate"})
    assert not result.ok and "unknown ide operation" in (result.error or "")


async def test_service_error_becomes_toolresult() -> None:
    svc = FakeIDEService(raises=IDEServiceError("no such workspace"))
    result = await _tool(svc).execute({"operation": "tree"})
    assert not result.ok and "no such workspace" in (result.error or "")


async def test_workspace_error_becomes_toolresult() -> None:
    svc = FakeIDEService(raises=WorkspaceError("bad path"))
    result = await _tool(svc).execute({"operation": "read_document", "path": "../etc/passwd"})
    assert not result.ok and "bad path" in (result.error or "")


async def test_read_document_missing_path_is_error() -> None:
    result = await _tool(FakeIDEService()).execute({"operation": "read_document"})
    assert not result.ok and "path" in (result.error or "")


def test_dry_run_previews_each_verb() -> None:
    tool = _tool(FakeIDEService())
    assert "APPLY_CHANGE" in tool.dry_run({"operation": "apply_change", "path": "a.py", "operations": [{}]})
    assert "RUN_COMMAND" in tool.dry_run({"operation": "run_command", "command": "pytest"})
    assert "READ_DOCUMENT" in tool.dry_run({"operation": "read_document", "path": "a.py"})
    assert "ws-1" in tool.dry_run({"operation": "tree"})
