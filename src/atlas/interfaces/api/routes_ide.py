"""IDE (ADE) REST API routes — workspace open, tree, read, and governed write.

A thin projection over ``IDEService`` (capabilities/ide). The service owns all
state and logic; these handlers only translate HTTP <-> contracts and map engine
errors to status codes. Every write still flows through the SafetyEngine funnel
inside the service — nothing here bypasses policy.

Mounted only when ``config.ide.enabled`` and the service was built; otherwise the
routes return 503 (subsystem disabled), mirroring the voice surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from atlas.app import Atlas
from atlas.capabilities.ide.contracts import EditOperation, EditOpKind, FileChange
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
