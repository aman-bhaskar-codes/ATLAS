"""ADE (Agentic Development Environment) capability — engine + domain contracts.

Lives LOW in the layer graph (capabilities/): may import infra/control/perception/
tools/safety/intelligence/memory, must NOT import knowledge/orchestration/
evaluation/adaptation/diagnostics/interfaces. The REST/WS surface and the
`atlas ide` launcher live in `interfaces/`; the coding agents reuse
`orchestration/agents/`. This mirrors the voice capability split.
"""

from __future__ import annotations

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import (
    BrowserSessionRef,
    ChangeResult,
    CommandResult,
    DebugSession,
    DebugSessionId,
    DebugStatus,
    DevProcess,
    DocumentId,
    DocumentSnapshot,
    DocumentStatus,
    EditOperation,
    EditOpKind,
    EditorGroup,
    FileChange,
    FileNode,
    GitFileChange,
    GitFileState,
    GitStatus,
    IDEAgentTask,
    IDEAgentTaskId,
    IDEOperation,
    IDESession,
    IDESessionId,
    ProcessId,
    ProcessStatus,
    ProjectModel,
    TaskStatus,
    TerminalId,
    TerminalSession,
    TerminalStatus,
    WorkspaceId,
    WorkspaceRef,
)
from atlas.capabilities.ide.editing import (
    EditError,
    WorkspaceWriter,
    apply_operations,
)
from atlas.capabilities.ide.persistence import IDESessionStore, SqliteIDESessionStore
from atlas.capabilities.ide.project import analyze_project
from atlas.capabilities.ide.service import IDEService, IDEServiceError
from atlas.capabilities.ide.workspace import (
    WorkspaceEngine,
    WorkspaceError,
    hash_content,
    open_workspace,
)

__all__ = [
    "BrowserSessionRef",
    "ChangeResult",
    "CommandResult",
    "CommandRunner",
    "DebugSession",
    "DebugSessionId",
    "DebugStatus",
    "DevProcess",
    "DocumentId",
    "DocumentSnapshot",
    "DocumentStatus",
    "EditError",
    "EditOpKind",
    "EditOperation",
    "EditorGroup",
    "FileChange",
    "FileNode",
    "GitFileChange",
    "GitFileState",
    "GitStatus",
    "IDEAgentTask",
    "IDEAgentTaskId",
    "IDEOperation",
    "IDEService",
    "IDEServiceError",
    "IDESession",
    "IDESessionId",
    "IDESessionStore",
    "ProcessId",
    "ProcessStatus",
    "ProjectModel",
    "SqliteIDESessionStore",
    "TaskStatus",
    "TerminalId",
    "TerminalSession",
    "TerminalStatus",
    "WorkspaceEngine",
    "WorkspaceError",
    "WorkspaceId",
    "WorkspaceRef",
    "WorkspaceWriter",
    "analyze_project",
    "apply_operations",
    "hash_content",
    "open_workspace",
]
