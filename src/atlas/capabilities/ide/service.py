"""IDEService — the capability-level façade over one or more open workspaces.

WHAT it is: the single object the interface layer (REST/WS routes, `atlas ide`)
talks to. It owns the live `WorkspaceEngine` (read) + `WorkspaceWriter` (governed
write) per open workspace and exposes the small verb set the ADE needs:
open a workspace, list its tree, read a document, apply a change.

WHY in capabilities/ (not interfaces/): it is pure runtime state + operations
over the domain contracts, importing only infra/safety/tools + the sibling
engines. The transport (HTTP/WS/CLI) is a thin projection on top, added in the
interfaces layer — the same split voice uses.

Session persistence (DB-backed resumability, Phase 17/42): when an
`IDESessionStore` is wired, `open_workspace` writes the workspace + session to
durable storage and the read/write verbs transparently RESUME a workspace from
the store when its id is not in the in-process registry — so a `WorkspaceId`
minted in one invocation (CLI, API request, frontend) stays addressable in the
next. Without a store the service degrades to the in-memory-only behavior; the
surface is identical either way.
"""

from __future__ import annotations

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import (
    ChangeResult,
    CommandResult,
    DocumentSnapshot,
    FileChange,
    FileNode,
    GitDiff,
    GitStatus,
    IDESession,
    IDESessionId,
    ProjectModel,
    WorkspaceId,
)
from atlas.capabilities.ide.editing import WorkspaceWriter
from atlas.capabilities.ide.git import GitEngine
from atlas.capabilities.ide.persistence import IDESessionStore
from atlas.capabilities.ide.project import analyze_project
from atlas.capabilities.ide.workspace import WorkspaceEngine, open_workspace
from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.safety.engine import SafetyEngine
from atlas.tools.base import Tool

_log = get_logger("atlas.ide.service")


class IDEServiceError(Exception):
    """A request against an unknown/closed workspace. Distinct from
    `WorkspaceError` (a bad path/file within a valid workspace)."""


class _OpenWorkspace:
    """A live workspace: its read engine, its governed writer, and the session
    aggregate the frontend renders. Bundled so the registry holds one value."""

    __slots__ = ("engine", "session", "writer")

    def __init__(self, engine: WorkspaceEngine, writer: WorkspaceWriter, session: IDESession) -> None:
        self.engine = engine
        self.writer = writer
        self.session = session


class IDEService:
    """Manages open workspaces and dispatches the ADE's core operations. Holds
    the safety funnel + filesystem tool so every write is governed identically
    to any other tool dispatch — the engine is never a side door (Constitution)."""

    def __init__(
        self,
        *,
        safety: SafetyEngine,
        filesystem_tool: Tool,
        ids: IdGenerator,
        clock: Clock,
        store: IDESessionStore | None = None,
        command_tool: Tool | None = None,
    ) -> None:
        self._safety = safety
        self._fs = filesystem_tool
        self._ids = ids
        self._clock = clock
        self._store = store
        # Command execution is optional: without a command tool the ADE still
        # reads/writes, it just cannot run tests/builds. Stateless runner, so one
        # instance serves every workspace.
        self._runner = CommandRunner(safety, command_tool) if command_tool is not None else None
        self._workspaces: dict[WorkspaceId, _OpenWorkspace] = {}

    # ---- lifecycle ------------------------------------------------------
    async def open_workspace(self, root_path: str, name: str) -> IDESession:
        """Open a workspace rooted at `root_path`. Raises `WorkspaceError` if the
        root is not a directory — an honest failure, never a silent empty session.

        Persists the workspace + session when a store is wired, so the returned
        `workspace.id` survives this process and can be resumed later."""
        now = self._clock.now().isoformat()
        workspace_id = self._ids.task_id()  # opaque unique id; reuses the id source
        engine = open_workspace(workspace_id, name, (root_path,), now_ts=now)
        writer = WorkspaceWriter(engine, self._safety, self._fs)
        session = IDESession(
            id=IDESessionId(self._ids.task_id()),
            workspace=engine.ref,
            created_ts=now,
            updated_ts=now,
        )
        self._workspaces[WorkspaceId(workspace_id)] = _OpenWorkspace(engine, writer, session)
        if self._store is not None:
            await self._store.save_workspace(engine.ref)
            await self._store.save_session(session)
        _log.info("ide.workspace.opened", event_type="lifecycle", workspace_id=workspace_id, root=str(engine.root))
        return session

    def close_workspace(self, workspace_id: str) -> bool:
        """Forget a workspace from the in-memory registry. Returns False if it was
        not resident (idempotent). Does NOT delete persisted state — a closed
        workspace can still be resumed from the store on the next request."""
        return self._workspaces.pop(WorkspaceId(workspace_id), None) is not None

    def sessions(self) -> tuple[IDESession, ...]:
        return tuple(ow.session for ow in self._workspaces.values())

    # ---- operations -----------------------------------------------------
    async def tree(self, workspace_id: str) -> tuple[FileNode, ...]:
        return (await self._require(workspace_id)).engine.tree()

    async def read_document(self, workspace_id: str, path: str) -> tuple[DocumentSnapshot, str]:
        return (await self._require(workspace_id)).engine.read_document(path)

    async def project_model(self, workspace_id: str) -> ProjectModel:
        """Analyze the workspace into a structured `ProjectModel` (languages,
        package managers, frameworks, test/build/run commands, entrypoints). Pure
        read — the reported commands are candidates, not executed here."""
        return analyze_project((await self._require(workspace_id)).engine)

    async def apply_change(
        self, workspace_id: str, change: FileChange, *, correlation_id: CorrelationId | None = None
    ) -> ChangeResult:
        """Apply a `FileChange` through the governed writer. The correlation id
        ties the write into the existing audit trail; one is minted if absent."""
        ow = await self._require(workspace_id)
        cid = correlation_id or self._ids.correlation_id()
        return await ow.writer.apply(change, correlation_id=cid)

    async def run_command(
        self,
        workspace_id: str,
        command: str,
        *,
        timeout_s: float = 120.0,
        correlation_id: CorrelationId | None = None,
    ) -> CommandResult:
        """Run one command in the workspace root through the governed funnel — the
        primitive the agentic loop uses to execute a `ProjectModel` test/build/run
        candidate. Degrades cleanly (a `CommandResult` carrying `error`) when no
        command tool is wired; a policy refusal comes back with `denied=True`. The
        workspace is resolved (and resumed if durable) before anything runs."""
        ow = await self._require(workspace_id)
        if self._runner is None:
            return CommandResult(command=command, error="command execution not available")
        cid = correlation_id or self._ids.correlation_id()
        return await self._runner.run(command, cwd=str(ow.engine.root), correlation_id=cid, timeout_s=timeout_s)

    async def git_status(self, workspace_id: str, *, correlation_id: CorrelationId | None = None) -> GitStatus | None:
        """Return the workspace's git working-tree status, or `None` when the root
        is not a git repo (or command execution is unavailable). Read-only — runs
        `git status` through the same governed funnel as any other command."""
        ow = await self._require(workspace_id)
        if self._runner is None:
            return None
        engine = GitEngine(self._runner, str(ow.engine.root))
        cid = correlation_id or self._ids.correlation_id()
        return await engine.status(correlation_id=cid)

    async def git_diff(
        self, workspace_id: str, *, staged: bool = False, correlation_id: CorrelationId | None = None
    ) -> GitDiff | None:
        """Return the workspace's working-tree diff (or the staged diff), or `None`
        when the root is not a git repo (or command execution is unavailable).
        Read-only — runs `git diff` through the same governed funnel as any command;
        an empty diff is an honest empty `GitDiff`, not `None`."""
        ow = await self._require(workspace_id)
        if self._runner is None:
            return None
        engine = GitEngine(self._runner, str(ow.engine.root))
        cid = correlation_id or self._ids.correlation_id()
        return await engine.diff(staged=staged, correlation_id=cid)

    # ---- internals ------------------------------------------------------
    async def _require(self, workspace_id: str) -> _OpenWorkspace:
        """Return the live workspace, RESUMING it from the store if it is durable
        but not currently resident. Raises `IDEServiceError` only when the id is
        neither open nor persisted anywhere."""
        wid = WorkspaceId(workspace_id)
        ow = self._workspaces.get(wid)
        if ow is not None:
            return ow
        if self._store is not None:
            resumed = await self._resume(wid)
            if resumed is not None:
                return resumed
        raise IDEServiceError(f"workspace not open: {workspace_id!r}")

    async def _resume(self, wid: WorkspaceId) -> _OpenWorkspace | None:
        """Rebuild a workspace's engine/writer/session from durable storage. The
        engine is reconstructed from the stored `WorkspaceRef` (still validated
        against disk — a since-deleted root raises `WorkspaceError`); the most
        recent persisted session is reattached, or a fresh one minted."""
        assert self._store is not None
        ref = await self._store.load_workspace(wid)
        if ref is None:
            return None
        engine = WorkspaceEngine(ref)
        writer = WorkspaceWriter(engine, self._safety, self._fs)
        sessions = await self._store.sessions_for_workspace(wid)
        if sessions:
            session = sessions[0]
        else:
            now = self._clock.now().isoformat()
            session = IDESession(id=IDESessionId(self._ids.task_id()), workspace=ref, created_ts=now, updated_ts=now)
        ow = _OpenWorkspace(engine, writer, session)
        self._workspaces[wid] = ow
        _log.info("ide.workspace.resumed", event_type="lifecycle", workspace_id=str(wid), root=str(engine.root))
        return ow
