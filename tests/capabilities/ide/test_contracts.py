"""Phase 1 ADE contract tests — the invariants the whole ADE relies on.

Not exhaustive field-by-field checks (pydantic already enforces types); these lock
the two properties every higher layer *assumes* and would silently break on:
  * every contract is FROZEN (a value crossing a boundary is never mutated); and
  * the stale-write guard fields (`version`/`expected_version`) exist and behave
    the way the engine will key on them (Phases 3/13/25).
Enums are pinned to their wire strings because the frontend/JSON depend on them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from atlas.capabilities.ide.contracts import (
    ChangeResult,
    DocumentSnapshot,
    DocumentStatus,
    EditOperation,
    EditOpKind,
    FileChange,
    FileNode,
    IDEAgentTask,
    IDEAgentTaskId,
    IDESession,
    IDESessionId,
    TaskStatus,
    WorkspaceId,
    WorkspaceRef,
)


def _workspace() -> WorkspaceRef:
    return WorkspaceRef(
        id=WorkspaceId("ws1"),
        name="demo",
        root_paths=("/repo",),
        created_ts="2026-08-29T00:00:00Z",
        last_opened_ts="2026-08-29T00:00:00Z",
    )


class TestFrozen:
    def test_document_snapshot_is_immutable(self) -> None:
        doc = DocumentSnapshot(id="d1", path="a.py", language="python", version="h1")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            doc.status = DocumentStatus.DIRTY  # type: ignore[misc]

    def test_session_is_immutable(self) -> None:
        session = IDESession(id=IDESessionId("s1"), workspace=_workspace())
        with pytest.raises(ValidationError):
            session.active_document_id = "d1"  # type: ignore[misc]

    def test_agent_task_is_immutable(self) -> None:
        task = IDEAgentTask(
            id=IDEAgentTaskId("t1"),
            workspace_id=WorkspaceId("ws1"),
            parent_task_id=None,
            correlation_id="cid1",
            objective="build a feature",
        )
        with pytest.raises(ValidationError):
            task.status = TaskStatus.RUNNING  # type: ignore[misc]


class TestStaleWriteGuard:
    def test_create_expects_absent_file(self) -> None:
        # expected_version=None is the CREATE contract: the file must NOT exist yet.
        change = FileChange(
            path="new.py",
            expected_version=None,
            operations=(EditOperation(kind=EditOpKind.CREATE, text="print('hi')\n"),),
        )
        assert change.expected_version is None
        assert change.resulting_version is None  # only filled after a successful apply

    def test_edit_carries_expected_version(self) -> None:
        change = FileChange(
            path="a.py",
            expected_version="hash-abc",
            operations=(EditOperation(kind=EditOpKind.REPLACE, start_line=0, end_line=1, text="x = 2\n"),),
        )
        assert change.expected_version == "hash-abc"

    def test_change_result_defaults_are_honest(self) -> None:
        # A fresh result claims nothing: not applied, not stale, no new version.
        result = ChangeResult(path="a.py", applied=False)
        assert result.applied is False
        assert result.stale is False
        assert result.new_version is None

    def test_filenode_version_optional_for_dirs(self) -> None:
        d = FileNode(path="src", name="src", is_dir=True)
        f = FileNode(path="src/a.py", name="a.py", is_dir=False, version="h1", size=10)
        assert d.version is None and d.size is None
        assert f.version == "h1"


class TestEnumWireValues:
    def test_document_status_values(self) -> None:
        assert [s.value for s in DocumentStatus] == ["clean", "dirty", "conflict"]

    def test_task_status_values(self) -> None:
        assert TaskStatus.BLOCKED.value == "blocked"
        assert set(TaskStatus) >= {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }

    def test_edit_op_kinds(self) -> None:
        assert {k.value for k in EditOpKind} == {
            "create",
            "insert",
            "replace",
            "delete",
            "rename",
            "move",
        }


class TestSessionAggregate:
    def test_defaults_are_empty_not_none(self) -> None:
        # The session is the source of truth; collections default to empty tuples so
        # a fresh session renders as "nothing open", never crashes on None iteration.
        session = IDESession(id=IDESessionId("s1"), workspace=_workspace())
        assert session.open_documents == ()
        assert session.terminals == ()
        assert session.processes == ()
        assert session.active_agent_task_ids == ()
        assert session.ui_state == {}
