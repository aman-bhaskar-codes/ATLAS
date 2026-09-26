"""IDE (ADE) REST API routes — workspace open, tree, read, and governed write.

A thin projection over ``IDEService`` (capabilities/ide). The service owns all
state and logic; these handlers only translate HTTP <-> contracts and map engine
errors to status codes. Every write still flows through the SafetyEngine funnel
inside the service — nothing here bypasses policy.

Mounted only when ``config.ide.enabled`` and the service was built; otherwise the
routes return 503 (subsystem disabled), mirroring the voice surface.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from atlas.app import Atlas
from atlas.capabilities.ide.contracts import (
    CheckpointRef,
    CheckpointResult,
    EditOperation,
    EditOpKind,
    FileChange,
    GitOpResult,
)
from atlas.capabilities.ide.service import IDEServiceError
from atlas.capabilities.ide.workspace import WorkspaceError
from atlas.interfaces.api.dependencies import get_atlas

router = APIRouter(prefix="/api/v1/ide", tags=["ide"])


def _service(atlas: Atlas) -> Any:
    """The IDEService, or 503 when the ADE is disabled/unavailable."""
    svc = getattr(atlas, "ide_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="ADE (IDE) subsystem disabled")
    return svc


# ── Request/response models ──────────────────────────────────────────── #
class OpenWorkspaceRequest(BaseModel):
    root_path: str
    name: str = "workspace"


class WorkspaceResponse(BaseModel):
    workspace_id: str
    session_id: str
    name: str
    root_paths: list[str]


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceResponse]


class FileNodeResponse(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int | None = None
    version: str | None = None
    language: str | None = None


class TreeResponse(BaseModel):
    workspace_id: str
    nodes: list[FileNodeResponse]


class DocumentResponse(BaseModel):
    id: str
    path: str
    language: str
    version: str
    status: str
    line_count: int
    content: str


class EditOperationRequest(BaseModel):
    kind: str  # one of EditOpKind values
    start_line: int | None = None
    start_col: int | None = None
    end_line: int | None = None
    end_col: int | None = None
    text: str | None = None
    new_path: str | None = None


class ApplyChangeRequest(BaseModel):
    path: str
    expected_version: str | None = None  # None == expect file absent (CREATE)
    operations: list[EditOperationRequest]
    rationale: str = ""


class ChangeResultResponse(BaseModel):
    path: str
    applied: bool
    stale: bool
    new_version: str | None = None
    error: str | None = None


class RunCommandRequest(BaseModel):
    command: str
    timeout_s: float = 120.0


class CommandResultResponse(BaseModel):
    command: str
    ok: bool
    exit_code: int | None = None
    stdout: str
    stderr: str
    duration_ms: int
    denied: bool
    error: str | None = None


class RunTestsRequest(BaseModel):
    command: str
    timeout_s: float = 300.0


class TestCaseResponse(BaseModel):
    name: str
    outcome: str
    file: str | None = None
    line: int | None = None
    message: str | None = None


class TestReportResponse(BaseModel):
    command: str
    framework: str | None = None
    ok: bool
    exit_code: int | None = None
    duration_ms: int
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    total: int | None = None
    failures: list[TestCaseResponse]
    denied: bool
    error: str | None = None
    output_tail: str


class CollectDiagnosticsRequest(BaseModel):
    command: str
    timeout_s: float = 180.0


class DiagnosticResponse(BaseModel):
    file: str
    line: int | None = None
    col: int | None = None
    severity: str
    message: str
    code: str | None = None
    source: str | None = None


class DiagnosticReportResponse(BaseModel):
    command: str
    ok: bool
    exit_code: int | None = None
    duration_ms: int
    diagnostics: list[DiagnosticResponse]
    errors: int
    warnings: int
    denied: bool
    error: str | None = None
    output_tail: str


class OpenTerminalResponse(BaseModel):
    terminal_id: str


class StartProcessRequest(BaseModel):
    command: str


class DevProcessResponse(BaseModel):
    id: str
    command: str
    cwd: str
    status: str
    detected_ports: list[int] = []
    exit_code: int | None = None
    started_ts: str | None = None
    exited_ts: str | None = None


class ProcessListResponse(BaseModel):
    processes: list[DevProcessResponse]


class StopProcessResponse(BaseModel):
    stopped: bool


class RunTerminalRequest(BaseModel):
    command: str


class TerminalRunResponse(BaseModel):
    started: bool


class GitFileChangeResponse(BaseModel):
    path: str
    state: str
    staged: bool
    old_path: str | None = None


class GitStatusResponse(BaseModel):
    is_git_repo: bool
    branch: str = ""
    ahead: int = 0
    behind: int = 0
    detached: bool = False
    has_conflicts: bool = False
    changes: list[GitFileChangeResponse] = []


class DiffStatResponse(BaseModel):
    path: str
    added: int = 0
    removed: int = 0
    binary: bool = False
    old_path: str | None = None


class GitDiffResponse(BaseModel):
    is_git_repo: bool
    staged: bool = False
    files: list[DiffStatResponse] = []
    patch: str = ""


class GitStageRequest(BaseModel):
    paths: list[str] = []
    all_changes: bool = False


class GitCommitRequest(BaseModel):
    message: str


class GitBranchRequest(BaseModel):
    name: str
    checkout_existing: bool = False


class GitOpResultResponse(BaseModel):
    action: str
    ok: bool = False
    detail: str = ""
    commit: str | None = None
    branch: str | None = None
    denied: bool = False
    error: str | None = None


class SnapshotRequest(BaseModel):
    label: str = ""


class CheckpointResultResponse(BaseModel):
    action: str
    ok: bool = False
    checkpoint_id: str | None = None
    commit: str | None = None
    label: str = ""
    clean: bool = False
    detail: str = ""
    denied: bool = False
    error: str | None = None


class CheckpointRefResponse(BaseModel):
    checkpoint_id: str
    commit: str
    label: str = ""


class CheckpointListResponse(BaseModel):
    checkpoints: list[CheckpointRefResponse]


class ProjectModelResponse(BaseModel):
    root: str
    languages: list[str]
    package_managers: list[str]
    frameworks: list[str]
    entrypoints: list[str]
    test_commands: list[str]
    build_commands: list[str]
    run_commands: list[str]
    dependencies: list[str]
    file_count: int
    indexed_symbols: int
    fingerprint: str


# ── Routes ───────────────────────────────────────────────────────────── #
@router.get("/workspaces", response_model=WorkspaceListResponse)
async def list_workspaces(atlas: Atlas = Depends(get_atlas)) -> WorkspaceListResponse:
    """List all durable workspaces."""
    svc = _service(atlas)
    workspaces = await svc.list_workspaces()
    return WorkspaceListResponse(
        workspaces=[
            WorkspaceResponse(
                workspace_id=w.id,
                session_id="",  # List doesn't strictly need session ID or we can populate it if needed
                name=w.name,
                root_paths=list(w.root_paths),
            )
            for w in workspaces
        ]
    )


@router.post("/workspaces", response_model=WorkspaceResponse)
async def open_workspace(req: OpenWorkspaceRequest, atlas: Atlas = Depends(get_atlas)) -> WorkspaceResponse:
    """Open a workspace at ``root_path``. 400 if the root is not a directory."""
    svc = _service(atlas)
    try:
        session = await svc.open_workspace(req.root_path, req.name)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceResponse(
        workspace_id=session.workspace.id,
        session_id=session.id,
        name=session.workspace.name,
        root_paths=list(session.workspace.root_paths),
    )


@router.get("/workspaces/{workspace_id}/tree", response_model=TreeResponse)
async def get_tree(workspace_id: str, atlas: Atlas = Depends(get_atlas)) -> TreeResponse:
    svc = _service(atlas)
    try:
        nodes = await svc.tree(workspace_id)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TreeResponse(
        workspace_id=workspace_id,
        nodes=[
            FileNodeResponse(
                path=n.path, name=n.name, is_dir=n.is_dir, size=n.size, version=n.version, language=n.language
            )
            for n in nodes
        ],
    )


@router.get("/workspaces/{workspace_id}/document", response_model=DocumentResponse)
async def read_document(workspace_id: str, path: str, atlas: Atlas = Depends(get_atlas)) -> DocumentResponse:
    """Read a document. ``path`` is workspace-relative (query param)."""
    svc = _service(atlas)
    try:
        snap, content = await svc.read_document(workspace_id, path)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DocumentResponse(
        id=snap.id,
        path=snap.path,
        language=snap.language,
        version=snap.version,
        status=snap.status.value,
        line_count=snap.line_count,
        content=content,
    )


@router.post("/workspaces/{workspace_id}/change", response_model=ChangeResultResponse)
async def apply_change(
    workspace_id: str, req: ApplyChangeRequest, atlas: Atlas = Depends(get_atlas)
) -> ChangeResultResponse:
    """Apply a structured change through the governed writer (funnel-routed)."""
    svc = _service(atlas)
    try:
        operations = tuple(_to_operation(op) for op in req.operations)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    change = FileChange(
        path=req.path,
        expected_version=req.expected_version,
        operations=operations,
        rationale=req.rationale,
    )
    try:
        result = await svc.apply_change(workspace_id, change)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChangeResultResponse(
        path=result.path,
        applied=result.applied,
        stale=result.stale,
        new_version=result.new_version,
        error=result.error,
    )


@router.post("/workspaces/{workspace_id}/command", response_model=CommandResultResponse)
async def run_command(
    workspace_id: str, req: RunCommandRequest, atlas: Atlas = Depends(get_atlas)
) -> CommandResultResponse:
    """Run a command in the workspace root through the governed funnel (funnel-routed;
    deny-by-default). A policy refusal comes back as ``denied=True``, a non-zero exit as
    ``ok=False`` — never a raised error for expected outcomes."""
    svc = _service(atlas)
    try:
        result = await svc.run_command(workspace_id, req.command, timeout_s=req.timeout_s)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CommandResultResponse(
        command=result.command,
        ok=result.ok,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        denied=result.denied,
        error=result.error,
    )


# ── Tests & diagnostics (Slice 8) ─────────────────────────────────────── #
@router.post("/workspaces/{workspace_id}/tests", response_model=TestReportResponse)
async def run_tests(workspace_id: str, req: RunTestsRequest, atlas: Atlas = Depends(get_atlas)) -> TestReportResponse:
    """Run a test command in the workspace root through the governed funnel and
    return a structured report (framework, pass/fail/skip counts, parsed failures).
    503 when command execution is not wired; 404 for an unknown workspace. Counts
    are honestly ``null`` when the framework summary could not be parsed."""
    svc = _service(atlas)
    try:
        report = await svc.run_tests(workspace_id, req.command, timeout_s=req.timeout_s)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if report is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return TestReportResponse(
        command=report.command,
        framework=report.framework,
        ok=report.ok,
        exit_code=report.exit_code,
        duration_ms=report.duration_ms,
        passed=report.passed,
        failed=report.failed,
        skipped=report.skipped,
        total=report.total,
        failures=[
            TestCaseResponse(name=f.name, outcome=str(f.outcome), file=f.file, line=f.line, message=f.message)
            for f in report.failures
        ],
        denied=report.denied,
        error=report.error,
        output_tail=report.output_tail,
    )


@router.post("/workspaces/{workspace_id}/diagnostics", response_model=DiagnosticReportResponse)
async def collect_diagnostics(
    workspace_id: str, req: CollectDiagnosticsRequest, atlas: Atlas = Depends(get_atlas)
) -> DiagnosticReportResponse:
    """Run a lint/type-check command in the workspace root through the governed
    funnel and return normalized diagnostics. 503 when command execution is not
    wired; 404 for an unknown workspace. A linter that exits non-zero WITH findings
    is not an error — the counts reflect the parsed diagnostics, not the exit."""
    svc = _service(atlas)
    try:
        report = await svc.collect_diagnostics(workspace_id, req.command, timeout_s=req.timeout_s)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if report is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return DiagnosticReportResponse(
        command=report.command,
        ok=report.ok,
        exit_code=report.exit_code,
        duration_ms=report.duration_ms,
        diagnostics=[
            DiagnosticResponse(
                file=d.file,
                line=d.line,
                col=d.col,
                severity=str(d.severity),
                message=d.message,
                code=d.code,
                source=d.source,
            )
            for d in report.diagnostics
        ],
        errors=report.errors,
        warnings=report.warnings,
        denied=report.denied,
        error=report.error,
        output_tail=report.output_tail,
    )


# ── Interactive streaming terminal (Slice 6) ──────────────────────────── #
@router.post("/workspaces/{workspace_id}/terminal", response_model=OpenTerminalResponse)
async def open_terminal(workspace_id: str, atlas: Atlas = Depends(get_atlas)) -> OpenTerminalResponse:
    """Open a streaming terminal session rooted at the workspace. 503 when command
    execution is not wired; 404 for an unknown workspace."""
    svc = _service(atlas)
    try:
        tid = await svc.open_terminal(workspace_id)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if tid is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return OpenTerminalResponse(terminal_id=str(tid))


@router.post("/workspaces/{workspace_id}/terminal/{terminal_id}/run", response_model=TerminalRunResponse)
async def run_terminal_command(
    workspace_id: str, terminal_id: str, req: RunTerminalRequest, atlas: Atlas = Depends(get_atlas)
) -> TerminalRunResponse:
    """Start a command in a terminal session as a background task; its output
    streams over the session's SSE endpoint. Every command re-enters the same
    governed funnel (deny-by-default). ``started=false`` when the session is
    unknown or already busy."""
    svc = _service(atlas)
    try:
        started = await svc.run_terminal_command(workspace_id, terminal_id, req.command)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TerminalRunResponse(started=started)


def _terminal_frame(event: Any) -> str:
    """Serialize one TerminalEvent as an SSE frame. Chunks carry the seq as the
    ``id:`` line so a reconnecting browser resumes via ``Last-Event-ID``."""
    if event.kind == "exit":
        payload = json.dumps({"exit_code": event.exit_code, "denied": event.denied, "error": event.error})
        return f"id: {event.seq}\nevent: exit\ndata: {payload}\n\n"
    payload = json.dumps({"stream": event.stream, "data": event.data})
    return f"id: {event.seq}\nevent: chunk\ndata: {payload}\n\n"


async def _terminal_generator(workspace_id: str, terminal_id: str, svc: Any, start_after: int) -> AsyncGenerator[str]:
    yield f"event: connected\ndata: {json.dumps({'terminal_id': terminal_id})}\n\n"
    async for event in svc.terminal_stream(workspace_id, terminal_id, after_seq=start_after):
        yield _terminal_frame(event)
    # The service generator returns once a terminal `exit` is delivered (or on
    # resume when the session already ended); close the SSE stream honestly.
    yield f"event: stream_closed\ndata: {json.dumps({'reason': 'terminal_exited'})}\n\n"


@router.get("/workspaces/{workspace_id}/terminal/{terminal_id}/stream")
async def stream_terminal(
    workspace_id: str, terminal_id: str, request: Request, after: int = 0, atlas: Atlas = Depends(get_atlas)
) -> StreamingResponse:
    """Stream a terminal session's output as SSE — ``connected``, then ``chunk``
    frames as output arrives (each carrying its ``id:`` sequence), then ``exit``
    and ``stream_closed`` when the command finishes. Resumable two ways: the
    ``Last-Event-ID`` header (browser-driven reconnect) or an explicit ``after``
    query cursor (a client opening a fresh stream for the next command in a session
    whose seq counter kept advancing). The header wins when both are present.
    Validated before the response head so an unknown workspace/terminal is an
    honest 404, not a truncated stream."""
    svc = _service(atlas)
    if not await svc.terminal_exists_in(workspace_id, terminal_id):
        raise HTTPException(status_code=404, detail=f"terminal {terminal_id!r} not found")

    start_after = after if after > 0 else 0
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id is not None:
        try:
            start_after = int(last_event_id)
        except ValueError:
            pass

    return StreamingResponse(
        _terminal_generator(workspace_id, terminal_id, svc, start_after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Managed dev processes (Slice 7 — Run/Debug) ───────────────────────── #
@router.post("/workspaces/{workspace_id}/processes", response_model=DevProcessResponse)
async def start_process(
    workspace_id: str, req: StartProcessRequest, atlas: Atlas = Depends(get_atlas)
) -> DevProcessResponse:
    """Launch a long-lived dev process (e.g. a dev server) in the workspace root
    through the governed funnel; its output streams over the terminal SSE endpoint
    at the returned id. 503 when command execution is unavailable; 404 for an
    unknown workspace."""
    svc = _service(atlas)
    try:
        proc = await svc.start_process(workspace_id, req.command)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if proc is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return _process_response(proc)


@router.get("/workspaces/{workspace_id}/processes", response_model=ProcessListResponse)
async def list_processes(workspace_id: str, atlas: Atlas = Depends(get_atlas)) -> ProcessListResponse:
    """All managed dev processes for the workspace (empty when execution is
    unavailable) — real runtime state, never a fabricated list."""
    svc = _service(atlas)
    try:
        procs = await svc.list_processes(workspace_id)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProcessListResponse(processes=[_process_response(p) for p in procs])


@router.post("/workspaces/{workspace_id}/processes/{process_id}/stop", response_model=StopProcessResponse)
async def stop_process(workspace_id: str, process_id: str, atlas: Atlas = Depends(get_atlas)) -> StopProcessResponse:
    """Stop a managed dev process. ``stopped=false`` when the process is unknown or
    already finished — never a raised error for that expected case."""
    svc = _service(atlas)
    try:
        stopped = await svc.stop_process(workspace_id, process_id)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StopProcessResponse(stopped=stopped)


def _process_response(proc: Any) -> DevProcessResponse:
    return DevProcessResponse(
        id=str(proc.id),
        command=proc.command,
        cwd=proc.cwd,
        status=proc.status.value,
        detected_ports=list(proc.detected_ports),
        exit_code=proc.exit_code,
        started_ts=proc.started_ts,
        exited_ts=proc.exited_ts,
    )


@router.get("/workspaces/{workspace_id}/git/status", response_model=GitStatusResponse)
async def git_status(workspace_id: str, atlas: Atlas = Depends(get_atlas)) -> GitStatusResponse:
    """Working-tree status through the governed funnel (read-only). A non-git root (or
    no command tool wired) comes back as ``is_git_repo=False`` — never a fabricated
    clean status, never a raised error for that expected case."""
    svc = _service(atlas)
    try:
        status = await svc.git_status(workspace_id)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if status is None:
        return GitStatusResponse(is_git_repo=False)
    return GitStatusResponse(
        is_git_repo=True,
        branch=status.branch,
        ahead=status.ahead,
        behind=status.behind,
        detached=status.detached,
        has_conflicts=status.has_conflicts,
        changes=[
            GitFileChangeResponse(path=c.path, state=c.state.value, staged=c.staged, old_path=c.old_path)
            for c in status.changes
        ],
    )


@router.get("/workspaces/{workspace_id}/git/diff", response_model=GitDiffResponse)
async def git_diff(workspace_id: str, staged: bool = False, atlas: Atlas = Depends(get_atlas)) -> GitDiffResponse:
    """Working-tree diff (or the staged diff when ``staged=true``) through the
    governed funnel (read-only). A non-git root comes back as ``is_git_repo=False``;
    a clean tree is an honest empty diff, not a 404."""
    svc = _service(atlas)
    try:
        diff = await svc.git_diff(workspace_id, staged=staged)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if diff is None:
        return GitDiffResponse(is_git_repo=False, staged=staged)
    return GitDiffResponse(
        is_git_repo=True,
        staged=diff.staged,
        files=[
            DiffStatResponse(path=f.path, added=f.added, removed=f.removed, binary=f.binary, old_path=f.old_path)
            for f in diff.files
        ],
        patch=diff.patch,
    )


# ── Git write operations (Slice 9) ────────────────────────────────────── #
def _git_op_response(result: GitOpResult) -> GitOpResultResponse:
    return GitOpResultResponse(
        action=result.action,
        ok=result.ok,
        detail=result.detail,
        commit=result.commit,
        branch=result.branch,
        denied=result.denied,
        error=result.error,
    )


@router.post("/workspaces/{workspace_id}/git/stage", response_model=GitOpResultResponse)
async def git_stage(workspace_id: str, req: GitStageRequest, atlas: Atlas = Depends(get_atlas)) -> GitOpResultResponse:
    """Stage paths (or all changes) through the governed funnel. 503 when command
    execution is not wired; 404 for an unknown workspace. A policy refusal returns
    ``denied=true``; a git failure returns an honest ``error`` — never a fake ok."""
    svc = _service(atlas)
    try:
        result = await svc.git_stage(workspace_id, req.paths, all_changes=req.all_changes)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return _git_op_response(result)


@router.post("/workspaces/{workspace_id}/git/unstage", response_model=GitOpResultResponse)
async def git_unstage(
    workspace_id: str, req: GitStageRequest, atlas: Atlas = Depends(get_atlas)
) -> GitOpResultResponse:
    """Unstage paths (or all) through the governed funnel — index only, worktree
    untouched. 503 when command execution is not wired; 404 for an unknown workspace."""
    svc = _service(atlas)
    try:
        result = await svc.git_unstage(workspace_id, req.paths, all_changes=req.all_changes)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return _git_op_response(result)


@router.post("/workspaces/{workspace_id}/git/commit", response_model=GitOpResultResponse)
async def git_commit(
    workspace_id: str, req: GitCommitRequest, atlas: Atlas = Depends(get_atlas)
) -> GitOpResultResponse:
    """Commit the staged index through the governed funnel. 503 when command
    execution is not wired; 404 for an unknown workspace. Committing with nothing
    staged is git's own non-zero exit surfaced honestly (``ok=false``)."""
    svc = _service(atlas)
    try:
        result = await svc.git_commit(workspace_id, req.message)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return _git_op_response(result)


@router.post("/workspaces/{workspace_id}/git/branch", response_model=GitOpResultResponse)
async def git_branch(
    workspace_id: str, req: GitBranchRequest, atlas: Atlas = Depends(get_atlas)
) -> GitOpResultResponse:
    """Create-and-switch to a new branch (default) or switch to an existing one
    (``checkout_existing=true``) through the governed funnel. 503 when command
    execution is not wired; 404 for an unknown workspace."""
    svc = _service(atlas)
    try:
        result = await svc.git_branch(workspace_id, req.name, checkout_existing=req.checkout_existing)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return _git_op_response(result)


# ── Checkpoints (Slice 10) ─────────────────────────────────────────────── #
def _checkpoint_response(result: CheckpointResult) -> CheckpointResultResponse:
    return CheckpointResultResponse(
        action=result.action,
        ok=result.ok,
        checkpoint_id=result.checkpoint_id,
        commit=result.commit,
        label=result.label,
        clean=result.clean,
        detail=result.detail,
        denied=result.denied,
        error=result.error,
    )


@router.post("/workspaces/{workspace_id}/checkpoints", response_model=CheckpointResultResponse)
async def create_checkpoint(
    workspace_id: str, req: SnapshotRequest, atlas: Atlas = Depends(get_atlas)
) -> CheckpointResultResponse:
    """Snapshot the workspace's working tree through the governed funnel. 503 when
    command execution is not wired; 404 for an unknown workspace. A policy refusal
    comes back ``denied=true``; a git failure returns an honest ``error``."""
    svc = _service(atlas)
    try:
        result = await svc.checkpoint_snapshot(workspace_id, label=req.label)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return _checkpoint_response(result)


@router.get("/workspaces/{workspace_id}/checkpoints", response_model=CheckpointListResponse)
async def list_checkpoints(workspace_id: str, atlas: Atlas = Depends(get_atlas)) -> CheckpointListResponse:
    """List the workspace's stored checkpoints through the governed funnel. 503 when
    command execution is not wired; 404 for an unknown workspace. A non-git root /
    no checkpoints is an honest empty list."""
    svc = _service(atlas)
    try:
        refs: tuple[CheckpointRef, ...] | None = await svc.checkpoint_list(workspace_id)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if refs is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return CheckpointListResponse(
        checkpoints=[CheckpointRefResponse(checkpoint_id=r.checkpoint_id, commit=r.commit, label=r.label) for r in refs]
    )


@router.post(
    "/workspaces/{workspace_id}/checkpoints/{checkpoint_id}/restore",
    response_model=CheckpointResultResponse,
)
async def restore_checkpoint(
    workspace_id: str, checkpoint_id: str, atlas: Atlas = Depends(get_atlas)
) -> CheckpointResultResponse:
    """Restore the workspace's working tree to a checkpoint through the governed
    funnel. 503 when command execution is not wired; 404 for an unknown workspace.
    An unknown checkpoint id is an honest ``ok=false``, never a fabricated success."""
    svc = _service(atlas)
    try:
        result = await svc.checkpoint_restore(workspace_id, checkpoint_id)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=503, detail="command execution not available")
    return _checkpoint_response(result)


@router.get("/workspaces/{workspace_id}/project", response_model=ProjectModelResponse)
async def get_project_model(workspace_id: str, atlas: Atlas = Depends(get_atlas)) -> ProjectModelResponse:
    """Analyze the workspace into a `ProjectModel` (languages, managers, frameworks,
    test/build/run commands). Read-only — reported commands are candidates."""
    svc = _service(atlas)
    try:
        pm = await svc.project_model(workspace_id)
    except IDEServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectModelResponse(
        root=pm.root,
        languages=list(pm.languages),
        package_managers=list(pm.package_managers),
        frameworks=list(pm.frameworks),
        entrypoints=list(pm.entrypoints),
        test_commands=list(pm.test_commands),
        build_commands=list(pm.build_commands),
        run_commands=list(pm.run_commands),
        dependencies=list(pm.dependencies),
        file_count=pm.file_count,
        indexed_symbols=pm.indexed_symbols,
        fingerprint=pm.fingerprint,
    )


def _to_operation(op: EditOperationRequest) -> EditOperation:
    try:
        kind = EditOpKind(op.kind)
    except ValueError as exc:
        raise ValueError(f"unknown edit op kind: {op.kind!r}") from exc
    return EditOperation(
        kind=kind,
        start_line=op.start_line,
        start_col=op.start_col,
        end_line=op.end_line,
        end_col=op.end_col,
        text=op.text,
        new_path=op.new_path,
    )
