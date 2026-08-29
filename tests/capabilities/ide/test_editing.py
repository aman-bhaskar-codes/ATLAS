"""Structured-editing tests — pure applier semantics + governed write path.

Two layers, mirroring the module:
  * `apply_operations` is pure, so it is tested directly with strings — this is
    where edit correctness (insert/replace/delete, multi-op bottom-up) lives.
  * `WorkspaceWriter` is tested with a REAL `WorkspaceEngine` (tmp_path) plus a
    fake SafetyEngine + fake filesystem tool. The point is the SEAM: a stale
    change is refused WITHOUT touching the funnel; a clean change routes exactly
    one write through `guard`; a denial/failure surfaces as an honest
    `ChangeResult`, never an exception.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from atlas.capabilities.ide.contracts import EditOperation, EditOpKind, FileChange
from atlas.capabilities.ide.editing import EditError, WorkspaceWriter, apply_operations
from atlas.capabilities.ide.workspace import WorkspaceEngine, hash_content, open_workspace
from atlas.infra.ids import CorrelationId
from atlas.infra.types import ToolResult
from atlas.safety.engine import DeniedError


def _op(kind: EditOpKind, **kw: Any) -> EditOperation:
    return EditOperation(kind=kind, **kw)


class TestApplyOperations:
    def test_create_returns_full_body(self) -> None:
        out = apply_operations(None, (_op(EditOpKind.CREATE, text="hello\n"),))
        assert out == "hello\n"

    def test_create_must_be_alone(self) -> None:
        with pytest.raises(EditError):
            apply_operations(None, (_op(EditOpKind.CREATE, text="x"), _op(EditOpKind.INSERT, start_line=0, text="y")))

    def test_insert_at_line(self) -> None:
        out = apply_operations("a\nb\n", (_op(EditOpKind.INSERT, start_line=1, text="mid\n"),))
        assert out == "a\nmid\nb\n"

    def test_replace_range(self) -> None:
        out = apply_operations("a\nb\nc\n", (_op(EditOpKind.REPLACE, start_line=1, end_line=2, text="B\n"),))
        assert out == "a\nB\nc\n"

    def test_delete_range(self) -> None:
        out = apply_operations("a\nb\nc\n", (_op(EditOpKind.DELETE, start_line=1, end_line=2),))
        assert out == "a\nc\n"

    def test_multi_op_applies_bottom_up(self) -> None:
        # Two replaces on the same base; bottom-up so the first's indices hold.
        ops = (
            _op(EditOpKind.REPLACE, start_line=0, end_line=1, text="A\n"),
            _op(EditOpKind.REPLACE, start_line=2, end_line=3, text="C\n"),
        )
        assert apply_operations("a\nb\nc\n", ops) == "A\nb\nC\n"

    def test_out_of_range_raises(self) -> None:
        with pytest.raises(EditError):
            apply_operations("a\n", (_op(EditOpKind.REPLACE, start_line=5, end_line=6, text="x"),))

    def test_rename_rejected_by_pure_applier(self) -> None:
        with pytest.raises(EditError):
            apply_operations("a\n", (_op(EditOpKind.RENAME, new_path="b.py"),))


class FakeFilesystemTool:
    name = "filesystem"

    def __init__(self, *, ok: bool = True, error: str | None = None) -> None:
        self._ok = ok
        self._error = error
        self.writes: list[dict[str, Any]] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return "WRITE"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.writes.append(args)
        if self._ok:
            # Emulate the real filesystem tool's byte write so on-disk assertions hold.
            Path(str(args["path"])).write_text(str(args["content"]))
        return ToolResult(ok=self._ok, error=self._error)


class FakeSafety:
    """Stand-in SafetyEngine.guard: records the request, executes the tool (so a
    clean path really writes), or raises to model a denial."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.guarded: list[Any] = []

    async def guard(self, req: Any, tool: Any) -> ToolResult:
        self.guarded.append(req)
        if self._raises is not None:
            raise self._raises
        return await tool.execute(req.args)


def _writer(tmp_path: Path, safety: FakeSafety, fs: FakeFilesystemTool) -> WorkspaceWriter:
    (tmp_path / "a.py").write_text("x = 1\n")
    eng: WorkspaceEngine = open_workspace("ws1", "demo", (str(tmp_path),), now_ts="t")
    return WorkspaceWriter(eng, safety, fs)  # type: ignore[arg-type]


CID = CorrelationId("cid-ide-1")


class TestWorkspaceWriter:
    @pytest.mark.asyncio
    async def test_clean_edit_routes_through_funnel_once(self, tmp_path: Path) -> None:
        safety, fs = FakeSafety(), FakeFilesystemTool()
        writer = _writer(tmp_path, safety, fs)
        version = hash_content("x = 1\n")
        change = FileChange(
            path="a.py",
            expected_version=version,
            operations=(EditOperation(kind=EditOpKind.REPLACE, start_line=0, end_line=1, text="x = 2\n"),),
        )
        result = await writer.apply(change, correlation_id=CID)
        assert result.applied is True
        assert result.new_version == hash_content("x = 2\n")
        assert len(safety.guarded) == 1  # exactly one funnel dispatch
        assert (tmp_path / "a.py").read_text() == "x = 2\n"

    @pytest.mark.asyncio
    async def test_stale_write_is_refused_without_touching_funnel(self, tmp_path: Path) -> None:
        safety, fs = FakeSafety(), FakeFilesystemTool()
        writer = _writer(tmp_path, safety, fs)
        change = FileChange(
            path="a.py",
            expected_version="stale-hash-that-does-not-match",
            operations=(EditOperation(kind=EditOpKind.REPLACE, start_line=0, end_line=1, text="x = 2\n"),),
        )
        result = await writer.apply(change, correlation_id=CID)
        assert result.applied is False
        assert result.stale is True
        assert safety.guarded == []  # never routed a clobbering write
        assert (tmp_path / "a.py").read_text() == "x = 1\n"  # untouched

    @pytest.mark.asyncio
    async def test_create_new_file(self, tmp_path: Path) -> None:
        safety, fs = FakeSafety(), FakeFilesystemTool()
        writer = _writer(tmp_path, safety, fs)
        change = FileChange(
            path="new.py",
            expected_version=None,
            operations=(EditOperation(kind=EditOpKind.CREATE, text="print('new')\n"),),
        )
        result = await writer.apply(change, correlation_id=CID)
        assert result.applied is True
        assert (tmp_path / "new.py").read_text() == "print('new')\n"

    @pytest.mark.asyncio
    async def test_create_existing_file_refused(self, tmp_path: Path) -> None:
        safety, fs = FakeSafety(), FakeFilesystemTool()
        writer = _writer(tmp_path, safety, fs)
        change = FileChange(
            path="a.py",
            expected_version=None,
            operations=(EditOperation(kind=EditOpKind.CREATE, text="oops\n"),),
        )
        result = await writer.apply(change, correlation_id=CID)
        assert result.applied is False
        assert "exists" in (result.error or "")
        assert safety.guarded == []

    @pytest.mark.asyncio
    async def test_denied_write_surfaces_as_result_not_exception(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        decision = SimpleNamespace(reason="needs confirmation", tier=SimpleNamespace(name="CONFIRM"))
        safety = FakeSafety(raises=DeniedError(decision))  # type: ignore[arg-type]
        fs = FakeFilesystemTool()
        writer = _writer(tmp_path, safety, fs)
        change = FileChange(
            path="a.py",
            expected_version=hash_content("x = 1\n"),
            operations=(EditOperation(kind=EditOpKind.REPLACE, start_line=0, end_line=1, text="x = 2\n"),),
        )
        result = await writer.apply(change, correlation_id=CID)
        assert result.applied is False
        assert "denied" in (result.error or "")

    @pytest.mark.asyncio
    async def test_tool_failure_surfaces_as_result(self, tmp_path: Path) -> None:
        safety = FakeSafety()
        fs = FakeFilesystemTool(ok=False, error="disk full")
        writer = _writer(tmp_path, safety, fs)
        change = FileChange(
            path="a.py",
            expected_version=hash_content("x = 1\n"),
            operations=(EditOperation(kind=EditOpKind.REPLACE, start_line=0, end_line=1, text="x = 2\n"),),
        )
        result = await writer.apply(change, correlation_id=CID)
        assert result.applied is False
        assert "disk full" in (result.error or "")
