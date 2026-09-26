"""IDE-verb → workspace-scoped Tool adapter (M2.* — the last Phase-2 slice).

WHAT: exposes the `IDEService` verb set (tree/read/project-model/apply-change/
run-command/git-status/git-diff) as ONE `Tool` the agent engine can call, so a
persisted agent run bound to a `workspace_id` (see `AgentRunRecord.workspace_id`)
can actually read, edit, and run code inside that IDE workspace — not merely
describe it.

WHY it is a thin adapter, not a second engine: the consequential verbs already
route through `IDEService`'s OWN `SafetyEngine.guard` funnel — `apply_change` via
`WorkspaceWriter` (→ `filesystem.write`), `run_command`/`git_*` via
`CommandRunner` (→ `shell.run`). Those inner dispatches ARE the enforcement point
(tiering, `within_write_paths`, the credential/mass-deletion hard-blocks). This
tool adds no new execution path; it only maps a model-issued `{operation, args}`
onto a verb and marshals the frozen result contract back into a JSON-safe
`ToolResult`. Reads are pure and need no funnel. The Constitution's one-funnel
invariant is preserved: the IDE tool cannot become a side door.

WHY it lives in `capabilities/ide` (not `tools/`): it depends on `IDEService`, a
capability. `tools/` sits BELOW capabilities in the layer graph, so a tool there
importing `IDEService` would invert the layering. Here it imports only the sibling
service + `tools.base.Tool` (the protocol) — both permitted. Its registration into
the orchestration `ToolRegistry` (with `ToolMetadata` + manifest tiers) is done by
bootstrap, which may see every layer.

Workspace scoping: an instance is bound to a `default_workspace_id` (the run's
workspace); a call may still name a different `workspace_id` in its args, but the
bound default means the model does not have to know the opaque id to act.
"""

from __future__ import annotations

from typing import Any

from atlas.capabilities.ide.contracts import EditOperation, FileChange
from atlas.capabilities.ide.service import IDEService, IDEServiceError
from atlas.capabilities.ide.workspace import WorkspaceError
from atlas.infra.logging import get_logger
from atlas.infra.types import ToolResult

_log = get_logger("atlas.ide.agent_tool")

# The verbs the tool advertises. Read-only first, then the governed mutations —
# the manifest tiers these (reads Tier-0, mutations Tier-1 at this outer boundary;
# the REAL enforcement is the inner filesystem/shell dispatch each verb performs).
IDE_READ_OPERATIONS: tuple[str, ...] = ("tree", "read_document", "project_model", "git_status", "git_diff")
IDE_WRITE_OPERATIONS: tuple[str, ...] = ("apply_change", "run_command")
IDE_OPERATIONS: tuple[str, ...] = IDE_READ_OPERATIONS + IDE_WRITE_OPERATIONS

IDE_TOOL_DESCRIPTION = (
    "Act inside the bound IDE workspace. Operations: tree (args: none — the file "
    "tree), read_document (args: path — a file's content), project_model (args: "
    "none — languages/frameworks/test+build+run commands), git_status (args: none), "
    "git_diff (args: staged? — the working-tree or staged patch), apply_change "
    "(args: path, operations[list of {kind: create|insert|replace|delete, "
    "start_line?, end_line?, text?}], expected_version? — a governed, stale-checked "
    "edit to ONE file), run_command (args: command, timeout_s? — run one project "
    "command, e.g. a test/build, through the governed funnel). Prefer project_model "
    "then run_command to verify edits. All writes/commands are policy-governed and "
    "may be refused."
)


class IDEWorkspaceTool:
    """Adapts `IDEService` verbs into the `Tool` protocol for the agent engine.

    Bound to one workspace (`default_workspace_id`); the mutating verbs delegate
    to `IDEService`, whose inner `SafetyEngine.guard` funnel governs the actual
    file write / command. Expected failures (unknown workspace, bad path, invalid
    edit) come back as an honest `ToolResult(ok=False, error=...)`, never a raise —
    the dispatcher turns a raise into a hard `ToolExecutionError`, but a governed
    verb's refusal is information the model should see and reason about.
    """

    name = "ide"

    def __init__(self, service: IDEService, *, default_workspace_id: str | None = None) -> None:
        self._svc = service
        self._default_workspace_id = default_workspace_id

    # ---- preview --------------------------------------------------------
    def dry_run(self, args: dict[str, Any]) -> str:
        op = str(args.get("operation", ""))
        wid = self._workspace_id(args) or "<unbound>"
        if op == "apply_change":
            ops = args.get("operations") or []
            return f"APPLY_CHANGE to {args.get('path')!r} in {wid} ({len(ops)} op(s), governed write)"
        if op == "run_command":
            return f"RUN_COMMAND {args.get('command')!r} in {wid} (governed)"
        if op == "read_document":
            return f"READ_DOCUMENT {args.get('path')!r} in {wid}"
        return f"IDE {op!r} in {wid}"

    # ---- execute --------------------------------------------------------
    async def execute(self, args: dict[str, Any]) -> ToolResult:
        op = str(args.get("operation", ""))
        workspace_id = self._workspace_id(args)
        if not workspace_id:
            return ToolResult(ok=False, error="ide tool has no workspace_id (bind a run to a workspace, or pass one)")
        try:
            return await self._dispatch(op, workspace_id, args)
        except IDEServiceError as exc:
            # Unknown/closed workspace — a use-case error the model can recover from.
            return ToolResult(ok=False, error=f"ide: {exc}")
        except WorkspaceError as exc:
            # A bad path/file WITHIN a valid workspace — likewise recoverable.
            return ToolResult(ok=False, error=f"ide: {exc}")

    async def _dispatch(self, op: str, workspace_id: str, args: dict[str, Any]) -> ToolResult:
        if op == "tree":
            nodes = await self._svc.tree(workspace_id)
            return ToolResult(ok=True, output={"tree": [n.model_dump() for n in nodes], "count": len(nodes)})
        if op == "read_document":
            path = self._require_str(args, "path")
            snapshot, content = await self._svc.read_document(workspace_id, path)
            return ToolResult(ok=True, output={**snapshot.model_dump(), "content": content[:100_000]})
        if op == "project_model":
            model = await self._svc.project_model(workspace_id)
            return ToolResult(ok=True, output=model.model_dump())
        if op == "git_status":
            status = await self._svc.git_status(workspace_id)
            return ToolResult(ok=True, output={"git_status": status.model_dump() if status else None})
        if op == "git_diff":
            diff = await self._svc.git_diff(workspace_id, staged=bool(args.get("staged", False)))
            return ToolResult(ok=True, output={"git_diff": diff.model_dump() if diff else None})
        if op == "apply_change":
            return await self._apply_change(workspace_id, args)
        if op == "run_command":
            return await self._run_command(workspace_id, args)
        return ToolResult(ok=False, error=f"unknown ide operation {op!r}")

    async def _apply_change(self, workspace_id: str, args: dict[str, Any]) -> ToolResult:
        path = self._require_str(args, "path")
        raw_ops = args.get("operations")
        if not isinstance(raw_ops, list) or not raw_ops:
            return ToolResult(ok=False, error="apply_change requires a non-empty 'operations' list")
        try:
            operations = tuple(EditOperation.model_validate(op) for op in raw_ops)
        except Exception as exc:  # pydantic ValidationError et al. — a malformed edit
            return ToolResult(ok=False, error=f"invalid edit operation: {exc}")
        change = FileChange(
            path=path,
            expected_version=args.get("expected_version"),
            operations=operations,
            rationale=str(args.get("rationale", "")),
        )
        # Governed by IDEService's inner funnel (WorkspaceWriter -> filesystem.write).
        result = await self._svc.apply_change(workspace_id, change)
        return ToolResult(ok=result.applied, output=result.model_dump(), error=result.error)

    async def _run_command(self, workspace_id: str, args: dict[str, Any]) -> ToolResult:
        command = self._require_str(args, "command")
        timeout_s = float(args.get("timeout_s", 120.0))
        # Governed by IDEService's inner funnel (CommandRunner -> shell.run).
        result = await self._svc.run_command(workspace_id, command, timeout_s=timeout_s)
        return ToolResult(ok=result.ok, output=result.model_dump(), error=result.error)

    # ---- helpers --------------------------------------------------------
    def _workspace_id(self, args: dict[str, Any]) -> str | None:
        explicit = args.get("workspace_id")
        return str(explicit) if explicit else self._default_workspace_id

    @staticmethod
    def _require_str(args: dict[str, Any], key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value:
            raise WorkspaceError(f"missing required argument {key!r}")
        return value
