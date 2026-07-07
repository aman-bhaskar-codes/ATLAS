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

    def _allowed(self, command: str) -> bool:
        allow = self._read_only + self._side_effect
        return any(command.strip().startswith(a) for a in allow)

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        command = str(args.get("command", ""))
        if not command:
            return ToolResult(ok=False, error="no command")
        if not self._allowed(command):
            return ToolResult(ok=False, error=f"command not allowlisted: {command!r}")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return ToolResult(ok=False, error=f"unparseable command: {exc}")

        network = command.strip().startswith(("npm", "pip", "git clone", "git pull"))
        result = await self._sandbox.run(
            argv, mounts=self._mounts, network=network, timeout_s=120.0,
        )
        is_side_effect = any(command.strip().startswith(a) for a in self._side_effect)
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
