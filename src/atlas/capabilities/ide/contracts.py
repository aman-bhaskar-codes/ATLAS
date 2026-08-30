"""ADE (Agentic Development Environment) domain contracts — Phase 1.

The stable, UI-agnostic vocabulary the whole ADE speaks: workspaces, documents,
editors, terminals, processes, git, debug/browser sessions, the project model,
and the agent task. Everything else in the IDE — engines, REST routes, the
Next.js workbench, the coding agents — is a projection of, or an operation over,
these types.

WHY here (capabilities/ide) and not interfaces: these are pure state + operation
value objects. They import only `infra` and pydantic, so every higher layer
(orchestration agents, interfaces/api routes, the frontend's generated client)
can share ONE shape and never disagree. Mirrors the voice capability split
recorded in project memory: engine + contracts low, task loop high.

INVARIANTS baked into the types (not left to callers):
  * Frozen models — a contract crossing a boundary is never mutated in place.
  * A document/file carries a content hash (`version`). An agent write declares
    the `expected_version` it planned against; the engine refuses a stale write
    (Phase 3/13/25) rather than clobbering human edits.
  * No Monaco / React / browser-engine types leak in here (Phase 1 rule).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field

# ── Identifiers ─────────────────────────────────────────────────────────── #
# NewType over str: cheap at runtime, but mypy stops a TerminalId being passed
# where a DocumentId is wanted. All are opaque, allocated by the IdGenerator.
WorkspaceId = NewType("WorkspaceId", str)
IDESessionId = NewType("IDESessionId", str)
DocumentId = NewType("DocumentId", str)
TerminalId = NewType("TerminalId", str)
ProcessId = NewType("ProcessId", str)
DebugSessionId = NewType("DebugSessionId", str)
BrowserSessionId = NewType("BrowserSessionId", str)
IDEAgentTaskId = NewType("IDEAgentTaskId", str)


class _Frozen(BaseModel):
    """Immutable value object. Consumers project/derive, never mutate."""

    model_config = ConfigDict(frozen=True)


# ── Enumerations ────────────────────────────────────────────────────────── #
class DocumentStatus(StrEnum):
    CLEAN = "clean"  # on-disk content == editor content
    DIRTY = "dirty"  # unsaved editor edits
    CONFLICT = "conflict"  # disk changed under an open/dirty buffer (Phase 25)


class TerminalStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    EXITED = "exited"
    KILLED = "killed"


class ProcessStatus(StrEnum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    EXITED = "exited"
    FAILED = "failed"
    KILLED = "killed"


class DebugStatus(StrEnum):
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    TERMINATED = "terminated"


class GitFileState(StrEnum):
    UNTRACKED = "untracked"
    MODIFIED = "modified"
    STAGED = "staged"
    DELETED = "deleted"
    RENAMED = "renamed"
    CONFLICTED = "conflicted"


class EditOpKind(StrEnum):
    CREATE = "create"
    INSERT = "insert"
    REPLACE = "replace"
    DELETE = "delete"
    RENAME = "rename"
    MOVE = "move"


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    BLOCKED = "blocked"  # awaiting approval / clarification
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Workspace & files ───────────────────────────────────────────────────── #
class WorkspaceRef(_Frozen):
    """A workspace = one or more root paths on disk under a single session."""

    id: WorkspaceId
    name: str
    root_paths: tuple[str, ...]  # multi-root (Phase 3)
    created_ts: str
    last_opened_ts: str


class FileNode(_Frozen):
    """One entry in the file tree. Directories carry no hash/size."""

    path: str  # workspace-relative, posix separators
    name: str
    is_dir: bool
    size: int | None = None
    version: str | None = None  # content hash for files (Phase 3 stale-write guard)
    language: str | None = None


class DocumentSnapshot(_Frozen):
    """An open document's server-truth state (Phase 2: backend owns runtime state)."""

    id: DocumentId
    path: str
    language: str
    version: str  # hash of on-disk content the buffer is based on
    status: DocumentStatus = DocumentStatus.CLEAN
    line_count: int = 0


class EditorGroup(_Frozen):
    """A split pane: an ordered set of open tabs with one active."""

    id: str
    document_ids: tuple[DocumentId, ...] = ()
    active_document_id: DocumentId | None = None


# ── Terminals & processes ───────────────────────────────────────────────── #
class TerminalSession(_Frozen):
    """A PTY-backed terminal. The backend owns the process; the frontend streams
    bytes over the existing WebSocket transport (Phase 6)."""

    id: TerminalId
    cwd: str
    shell: str
    status: TerminalStatus = TerminalStatus.STARTING
    cols: int = 80
    rows: int = 24
    pid: int | None = None
    exit_code: int | None = None
    created_ts: str = ""


class DevProcess(_Frozen):
    """A managed development process / dev-server (Phase 7). Structured runtime
    state an agent can reason about — not just a raw pipe."""

    id: ProcessId
    command: str
    cwd: str
    env: dict[str, str] = Field(default_factory=dict)
    status: ProcessStatus = ProcessStatus.PENDING
    pid: int | None = None
    detected_ports: tuple[int, ...] = ()
    health_url: str | None = None
    exit_code: int | None = None
    started_ts: str | None = None
    exited_ts: str | None = None


# ── Git / SCM (Phase 8) ─────────────────────────────────────────────────── #
class GitFileChange(_Frozen):
    path: str
    state: GitFileState
    staged: bool = False
    old_path: str | None = None  # for renames


class GitStatus(_Frozen):
    branch: str
    ahead: int = 0
    behind: int = 0
    detached: bool = False
    changes: tuple[GitFileChange, ...] = ()
    has_conflicts: bool = False


class DiffStat(_Frozen):
    """Per-file line-delta from `git diff --numstat`. `binary` files report no
    counts (git prints `-`/`-`); a rename carries the pre-rename `old_path`."""

    path: str
    added: int = 0
    removed: int = 0
    binary: bool = False
    old_path: str | None = None


class GitDiff(_Frozen):
    """A working-tree (or staged) diff: structured per-file stats plus the raw
    unified patch text. `staged` distinguishes `git diff` from `git diff --staged`
    so the review loop can present index vs worktree changes separately."""

    staged: bool = False
    files: tuple[DiffStat, ...] = ()
    patch: str = ""


# ── Debug (Phase 9) — DAP-shaped, adapter-agnostic ──────────────────────── #
class DebugSession(_Frozen):
    id: DebugSessionId
    adapter: str  # e.g. "debugpy", "node"
    status: DebugStatus = DebugStatus.INITIALIZING
    program: str | None = None
    paused_reason: str | None = None
    current_frame: int | None = None


# ── Browser QA (Phase 21) — a handle into the existing browser capability ── #
class BrowserSessionRef(_Frozen):
    """The IDE does NOT own browser automation (Phase 21: reuse the existing
    capability). This is only the IDE-side lifecycle handle onto one."""

    id: BrowserSessionId
    target_url: str
    process_id: ProcessId | None = None  # dev-server it is pointed at
    active: bool = True


# ── Project intelligence (Phases 10-11) ─────────────────────────────────── #
class ProjectModel(_Frozen):
    """Incremental, cached model of the repo. Populated by the project-intelligence
    engine (which reuses `engineering/`), never by dumping the tree into a prompt."""

    root: str
    languages: tuple[str, ...] = ()
    package_managers: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    entrypoints: tuple[str, ...] = ()
    test_commands: tuple[str, ...] = ()
    build_commands: tuple[str, ...] = ()
    run_commands: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    file_count: int = 0
    indexed_symbols: int = 0
    fingerprint: str = ""  # cheap change-detector so re-analysis is incremental


# ── Structured editing (Phases 13/25) ───────────────────────────────────── #
class EditOperation(_Frozen):
    """One structured edit. Line/col are 0-based; None means "not applicable to
    this kind" (e.g. CREATE carries only `text`, RENAME only `new_path`)."""

    kind: EditOpKind
    start_line: int | None = None
    start_col: int | None = None
    end_line: int | None = None
    end_col: int | None = None
    text: str | None = None  # inserted/replacement content, or full body for CREATE
    new_path: str | None = None  # for RENAME/MOVE


class FileChange(_Frozen):
    """A proposed change to ONE file. The engine applies it ONLY if the file's
    current on-disk hash still equals `expected_version` — otherwise the write is
    a stale-write conflict and is refused (never silently clobbered)."""

    path: str
    expected_version: str | None  # None == file is expected NOT to exist yet (CREATE)
    operations: tuple[EditOperation, ...]
    resulting_version: str | None = None  # filled in after a successful apply
    rationale: str = ""


class ChangeResult(_Frozen):
    """Outcome of applying a FileChange — honest about what actually happened."""

    path: str
    applied: bool
    stale: bool = False  # expected_version != on-disk version
    new_version: str | None = None
    error: str | None = None


# ── Command execution (Phases 6-7 foundation) ───────────────────────────── #
class CommandResult(_Frozen):
    """Outcome of one governed command run inside a workspace. Honest about the
    three outcomes the agentic repair loop must tell apart: the process ran (`ok`
    true on exit 0, false on non-zero), the funnel refused it (`denied` — never
    executed), or the runner had no tool wired / the tool itself errored (`error`
    with `exit_code` None). stdout/stderr are TAILS, bounded by the tool."""

    command: str
    ok: bool = False  # process ran AND exited 0
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    denied: bool = False  # SafetyEngine refused on policy — nothing was executed
    error: str | None = None  # runner/tool error, or a non-zero-exit summary


# ── Agent task (Phases 17/42) ───────────────────────────────────────────── #
class IDEAgentTask(_Frozen):
    """Persistent state of one agentic development task. Carries enough to
    reconstruct/resume the task (Phase 17) and to reconstruct it for audit
    (Phase 42). The orchestrator/agents own execution; this is the record."""

    id: IDEAgentTaskId
    workspace_id: WorkspaceId
    parent_task_id: IDEAgentTaskId | None
    correlation_id: str  # ties into the existing audit/event trail
    objective: str
    status: TaskStatus = TaskStatus.PENDING
    plan: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    executed_commands: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    recovery_attempts: int = 0
    verification: str | None = None
    outcome: str | None = None
    created_ts: str = ""
    updated_ts: str = ""


# ── Session aggregate (Phase 2) — backend is the source of truth ────────── #
class IDESession(_Frozen):
    """The whole runtime state of one IDE session. The frontend renders a
    projection of this; it never holds authority over it."""

    id: IDESessionId
    workspace: WorkspaceRef
    active_document_id: DocumentId | None = None
    open_documents: tuple[DocumentSnapshot, ...] = ()
    editor_groups: tuple[EditorGroup, ...] = ()
    terminals: tuple[TerminalSession, ...] = ()
    processes: tuple[DevProcess, ...] = ()
    debug_sessions: tuple[DebugSession, ...] = ()
    browser_sessions: tuple[BrowserSessionRef, ...] = ()
    active_agent_task_ids: tuple[IDEAgentTaskId, ...] = ()
    ui_state: dict[str, Any] = Field(default_factory=dict)  # opaque view prefs
    created_ts: str = ""
    updated_ts: str = ""


# Re-exported operation verbs used across IDE routes/agents. Kept as a Literal so
# a typo in a route handler is a type error, not a 404 at runtime.
IDEOperation = Literal[
    "workspace_open",
    "workspace_tree",
    "file_read",
    "file_write",
    "file_create",
    "file_rename",
    "file_delete",
    "terminal_open",
    "terminal_input",
    "process_start",
    "process_stop",
    "git_status",
    "git_commit",
    "project_model",
    "agent_task_run",
]
