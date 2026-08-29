"""Governed command execution — the run side of the workspace (Phases 6-7 foundation).

The agentic loop's repair cycle (edit → TEST → read failures → edit → re-test)
must actually RUN a project's commands — the test/build/run candidates
`analyze_project` surfaces. `CommandRunner` is that primitive, and it routes every
command through the SAME `SafetyEngine.guard` + tool funnel `WorkspaceWriter` uses
for byte writes: the IDE never spawns a subprocess itself, so it cannot become a
side door around ATLAS policy (Constitution). Deny-by-default is preserved — WHAT
may run is decided by the funnel + the command tool's own allowlist, never here.

Expected outcomes never raise past this boundary. A refusal (`DeniedError`), a
halt, a non-zero exit, or an absent tool all come back as an honest
`CommandResult` the caller can branch on — the same discipline `WorkspaceWriter`
uses for stale/denied writes.

One-shot, bounded execution only. Interactive PTY terminals (Phase 6) and
long-lived dev processes / servers (Phase 7) are separate later slices; this is
the shared synchronous building block beneath them.
"""

from __future__ import annotations

from typing import Any

from atlas.capabilities.ide.contracts import CommandResult
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ToolRequest, ToolResult
from atlas.safety.engine import DeniedError, HaltedError, SafetyEngine
from atlas.tools.base import Tool

_log = get_logger("atlas.ide.commands")


class CommandRunner:
    """Governed one-shot command execution over a command tool. Stateless: it
    holds only the funnel and the tool, so one instance serves every workspace."""

    def __init__(self, safety: SafetyEngine, command_tool: Tool) -> None:
        self._safety = safety
        self._tool = command_tool

    async def run(
        self,
        command: str,
        *,
        cwd: str,
        correlation_id: CorrelationId,
        timeout_s: float = 120.0,
    ) -> CommandResult:
        """Run `command` (in `cwd`) through the funnel, returning a structured
        result. Never raises for expected outcomes — denial/halt/non-zero come
        back on the `CommandResult` itself."""
        command = command.strip()
        if not command:
            return CommandResult(command=command, error="empty command")

        req = ToolRequest(
            correlation_id=correlation_id,
            tool=self._tool.name,
            operation="run",
            args={"command": command, "cwd": cwd, "timeout_s": timeout_s},
        )
        try:
            result: ToolResult = await self._safety.guard(req, self._tool)
        except DeniedError as exc:
            _log.info("ide.command.denied", event_type="safety", command=command, reason=exc.decision.reason)
            return CommandResult(command=command, denied=True, error=f"denied: {exc.decision.reason}")
        except HaltedError as exc:
            return CommandResult(command=command, error=f"halted: {exc}")

        out: dict[str, Any] = result.output if isinstance(result.output, dict) else {}
        exit_code = out.get("exit_code")
        return CommandResult(
            command=command,
            ok=result.ok,
            exit_code=int(exit_code) if isinstance(exit_code, int) else None,
            stdout=str(out.get("stdout", "")),
            stderr=str(out.get("stderr", "")),
            duration_ms=int(out.get("duration_ms") or result.duration_ms or 0),
            error=result.error,
        )
