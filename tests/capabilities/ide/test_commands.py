"""Governed command execution — `CommandRunner` against a fake command tool.

Pure, offline: no real subprocess/sandbox. A fake `Tool` returns a shell-shaped
``output`` dict and a fake `SafetyEngine` either passes the request through or
refuses it, so the test locks the CONTRACT the agentic repair loop relies on:
  * a clean run maps exit_code/stdout/stderr/duration onto `CommandResult`;
  * a non-zero exit is `ok=False` with the tool's error, NOT a raise;
  * a policy refusal is `denied=True` (nothing executed), NOT a raise;
  * an empty command is rejected before the funnel is ever touched.
"""

from __future__ import annotations

from typing import Any

from atlas.capabilities.ide.commands import CommandRunner
from atlas.infra.ids import CorrelationId
from atlas.infra.types import SafetyDecision, Tier, ToolResult
from atlas.safety.engine import DeniedError

_CID = CorrelationId("cid-1")


class FakeShellTool:
    name = "shell"

    def __init__(self, result: ToolResult) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return f"RUN {args.get('command')!r}"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls.append(args)
        return self._result


class PassSafety:
    """Funnel that executes the tool — the ALLOW path."""

    async def guard(self, req: Any, tool: Any) -> ToolResult:
        return await tool.execute(req.args)


class DenySafety:
    """Funnel that refuses on policy — nothing ever executes."""

    async def guard(self, req: Any, tool: Any) -> ToolResult:
        raise DeniedError(SafetyDecision(decision="deny", tier=Tier.CONFIRM, reason="not allowlisted"))


def _ok_result() -> ToolResult:
    return ToolResult(
        ok=True,
        output={"exit_code": 0, "stdout": "3 passed", "stderr": "", "duration_ms": 42},
    )


class TestCommandRunner:
    async def test_clean_run_maps_output(self) -> None:
        tool = FakeShellTool(_ok_result())
        runner = CommandRunner(PassSafety(), tool)  # type: ignore[arg-type]
        res = await runner.run("pytest -q", cwd="/repo", correlation_id=_CID)
        assert res.ok is True and res.exit_code == 0
        assert res.stdout == "3 passed" and res.duration_ms == 42
        assert res.denied is False and res.error is None
        # The workspace cwd + command reach the tool via the request args.
        assert tool.calls[0]["command"] == "pytest -q" and tool.calls[0]["cwd"] == "/repo"

    async def test_non_zero_exit_is_not_ok_but_not_raised(self) -> None:
        failing = ToolResult(
            ok=False,
            output={"exit_code": 1, "stdout": "", "stderr": "1 failed", "duration_ms": 10},
            error="non-zero exit",
        )
        runner = CommandRunner(PassSafety(), FakeShellTool(failing))  # type: ignore[arg-type]
        res = await runner.run("pytest", cwd="/repo", correlation_id=_CID)
        assert res.ok is False and res.exit_code == 1
        assert res.stderr == "1 failed" and res.error == "non-zero exit"
        assert res.denied is False

    async def test_policy_refusal_is_denied(self) -> None:
        runner = CommandRunner(DenySafety(), FakeShellTool(_ok_result()))  # type: ignore[arg-type]
        res = await runner.run("rm -rf /", cwd="/repo", correlation_id=_CID)
        assert res.denied is True and res.ok is False
        assert "not allowlisted" in (res.error or "")

    async def test_empty_command_rejected_before_funnel(self) -> None:
        tool = FakeShellTool(_ok_result())
        runner = CommandRunner(PassSafety(), tool)  # type: ignore[arg-type]
        res = await runner.run("   ", cwd="/repo", correlation_id=_CID)
        assert res.ok is False and res.error == "empty command"
        assert tool.calls == []  # the funnel was never engaged
