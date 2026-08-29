"""Structured editing — the write side of the workspace (Phases 13/25).

Two pieces, deliberately split:

  * `apply_operations` — PURE. Turns a file's current text + a tuple of
    `EditOperation`s into the resulting text. No IO, no funnel: trivially
    unit-testable, and the single place edit semantics live.

  * `WorkspaceWriter.apply` — the governed mutation. It (1) recomputes the
    on-disk `version` and refuses if it no longer equals the change's
    `expected_version` (STALE — a human edited under the agent, never
    clobbered, §preserve-human-edits), then (2) routes the actual byte write
    through `SafetyEngine.guard` + the filesystem tool — the SAME funnel every
    tool dispatch uses. The engine never writes bytes itself; it cannot become
    a side door around ATLAS (Constitution).

Line/col are 0-based; ranges are half-open `[start_line, end_line)`. Multi-op
changes are applied bottom-up (highest line first) so earlier edits never
invalidate the indices of later ones.
"""

from __future__ import annotations

from atlas.capabilities.ide.contracts import (
    ChangeResult,
    EditOperation,
    EditOpKind,
    FileChange,
)
from atlas.capabilities.ide.workspace import WorkspaceEngine, WorkspaceError, hash_content
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ToolRequest, ToolResult
from atlas.safety.engine import DeniedError, HaltedError, SafetyEngine
from atlas.tools.base import Tool

_log = get_logger("atlas.ide.editing")


class EditError(Exception):
    """A structurally impossible edit (bad kind mix, out-of-range line). Raised
    before any IO so a malformed change never reaches the funnel."""


def apply_operations(current: str | None, operations: tuple[EditOperation, ...]) -> str:
    """Apply `operations` to `current` text, returning the new full content.

    CREATE must stand alone (it IS the whole body and expects no prior content).
    RENAME/MOVE are file-level, not content-level, and are rejected here — the
    writer handles them separately. Everything else is line-range surgery.
    """
    if not operations:
        raise EditError("no operations")

    kinds = {op.kind for op in operations}
    if EditOpKind.CREATE in kinds:
        if len(operations) != 1:
            raise EditError("CREATE must be the only operation in a change")
        return operations[0].text or ""
    if kinds & {EditOpKind.RENAME, EditOpKind.MOVE}:
        raise EditError("RENAME/MOVE are file-level ops, not content edits")

    lines = (current or "").splitlines(keepends=True)

    # Bottom-up: sort by start_line descending so index-shifting edits compose.
    for op in sorted(operations, key=lambda o: o.start_line or 0, reverse=True):
        start = op.start_line or 0
        if start < 0 or start > len(lines):
            raise EditError(f"start_line {start} out of range (0..{len(lines)})")
        if op.kind is EditOpKind.INSERT:
            lines[start:start] = _as_lines(op.text or "")
        elif op.kind is EditOpKind.DELETE:
            end = op.end_line if op.end_line is not None else start + 1
            _check_end(start, end, len(lines))
            del lines[start:end]
        elif op.kind is EditOpKind.REPLACE:
            end = op.end_line if op.end_line is not None else start + 1
            _check_end(start, end, len(lines))
            lines[start:end] = _as_lines(op.text or "")
        else:  # pragma: no cover - kinds are exhaustively handled above
            raise EditError(f"unsupported op kind: {op.kind}")

    return "".join(lines)


def _as_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _check_end(start: int, end: int, n: int) -> None:
    if end < start or end > n:
        raise EditError(f"end_line {end} out of range (start={start}, len={n})")


class WorkspaceWriter:
    """Governed write side over one `WorkspaceEngine`. Holds the safety funnel
    and the filesystem tool so every mutation is classified/audited/confirmed
    exactly like any other tool dispatch."""

    def __init__(self, engine: WorkspaceEngine, safety: SafetyEngine, filesystem_tool: Tool) -> None:
        self._engine = engine
        self._safety = safety
        self._fs = filesystem_tool

    async def apply(self, change: FileChange, *, correlation_id: CorrelationId) -> ChangeResult:
        """Apply one `FileChange`, refusing a stale write and routing the byte
        write through the funnel. Never raises for expected outcomes (stale /
        denied / tool error) — they come back as an honest `ChangeResult`."""
        # RENAME/MOVE handled in a later slice (needs a filesystem move op).
        if any(op.kind in {EditOpKind.RENAME, EditOpKind.MOVE} for op in change.operations):
            return ChangeResult(path=change.path, applied=False, error="rename/move not yet supported")

        is_create = any(op.kind is EditOpKind.CREATE for op in change.operations)

        # 1) Stale-write guard — compare planned-against version to disk truth.
        current: str | None
        try:
            _, current = self._engine.read_document(change.path)
            current_version: str | None = hash_content(current)
        except WorkspaceError:
            current = None
            current_version = None  # file absent

        if is_create:
            if current is not None:
                return ChangeResult(path=change.path, applied=False, error="file already exists")
        else:
            if current is None:
                return ChangeResult(path=change.path, applied=False, error="file not found")
            if change.expected_version is not None and change.expected_version != current_version:
                # A human (or another agent) moved the file under us. Refuse.
                return ChangeResult(path=change.path, applied=False, stale=True, new_version=current_version)

        # 2) Compute the resulting content (pure).
        try:
            new_content = apply_operations(current, change.operations)
        except EditError as exc:
            return ChangeResult(path=change.path, applied=False, error=str(exc))

        # 3) Route the write through the SAME funnel as any tool dispatch.
        abs_path = str(self._engine.root / change.path)
        req = ToolRequest(
            correlation_id=correlation_id,
            tool=self._fs.name,
            operation="write",
            args={"operation": "write", "path": abs_path, "content": new_content},
        )
        try:
            result: ToolResult = await self._safety.guard(req, self._fs)
        except DeniedError as exc:
            return ChangeResult(path=change.path, applied=False, error=f"denied: {exc.decision.reason}")
        except HaltedError as exc:
            return ChangeResult(path=change.path, applied=False, error=f"halted: {exc}")

        if not result.ok:
            return ChangeResult(path=change.path, applied=False, error=result.error or "write failed")
        return ChangeResult(path=change.path, applied=True, new_version=hash_content(new_content))
