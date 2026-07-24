"""shell_tool — allowlisted commands, sandboxed.

WHY binary-name validation before launch: the classifier already tiers
read_only vs side_effect commands, but the tool ALSO refuses anything whose
binary isn't in the manifest allowlist (deny-by-default, defense in depth). No
raw shell string from the model is ever executed unsplit.
"""

from __future__ import annotations

import shlex
from typing import Any

from atlas.infra.logging import get_logger
from atlas.infra.types import SideEffect, ToolResult
from atlas.safety.sandbox import Sandbox

_log = get_logger("atlas.tools.shell")

# Shell operators that MUST be rejected before execution.
_SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", "`", "$(", ">>", ">", "<"})


class ShellTool:
    name = "shell"

    def __init__(
        self, *, read_only: list[str], side_effect: list[str],
        sandbox: Sandbox, mounts: dict[str, str],
    ) -> None:
        self._read_only = read_only
        self._side_effect = side_effect
        self._sandbox = sandbox
        self._mounts = mounts  # permitted host->container mounts for shell work

    def dry_run(self, args: dict[str, Any]) -> str:
        return f"RUN (sandboxed) {args.get('command')!r}"

    def _allowed(self, command: str) -> tuple[bool, str]:
        """Parse command with shlex, validate executable exactly against allowlist.
        
        Returns (allowed, reason).
        """
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return False, f"unparseable command: {exc}"
        if not argv:
            return False, "empty command"

        # Reject shell operators in raw command text
        for op in _SHELL_OPERATORS:
            if op in command:
                return False, f"shell operator {op!r} is not permitted"

        executable = argv[0]
        allow = self._read_only + self._side_effect
        if executable not in allow:
            return False, f"executable {executable!r} not in allowlist"

        return True, ""

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        command = str(args.get("command", ""))
        if not command:
            return ToolResult(ok=False, error="no command")

        allowed, reason = self._allowed(command)
        if not allowed:
            return ToolResult(ok=False, error=f"command not allowlisted: {reason}")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return ToolResult(ok=False, error=f"unparseable command: {exc}")

        network = argv[0] in {"npm", "pip"} or " ".join(argv[:2]) in {"git clone", "git pull"}
        result = await self._sandbox.run(
            argv, mounts=self._mounts, network=network, timeout_s=120.0,
        )
        is_side_effect = argv[0] in self._side_effect
        effects: tuple[SideEffect, ...] = ()
        if is_side_effect:
            effects = (SideEffect(kind="command", target=argv[0],
                                  detail=command, reversible=False),)
        return ToolResult(
            ok=result.exit_code == 0,
            output={"exit_code": result.exit_code, "stdout": result.stdout_tail,
                    "stderr": result.stderr_tail, "duration_ms": result.duration_ms},
            side_effects=effects,
            error=None if result.exit_code == 0 else (result.stderr_tail or "non-zero exit"),
        )

