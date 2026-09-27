"""shell_tool — allowlisted commands, sandboxed.

WHY binary-name validation before launch: the classifier already tiers
read_only vs side_effect commands, but the tool ALSO refuses anything whose
binary isn't in the manifest allowlist (deny-by-default, defense in depth). No
raw shell string from the model is ever executed unsplit.
"""

from __future__ import annotations

import shlex
from typing import Any, cast

from atlas.infra.logging import get_logger
from atlas.infra.types import SideEffect, ToolResult
from atlas.safety.sandbox import Sandbox, SandboxChunk, SandboxResult, StreamingSandbox
from atlas.tools.command_stream import current_sink

_log = get_logger("atlas.tools.shell")

# Shell operators that MUST be rejected before execution.
_SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", "`", "$(", ">>", ">", "<"})


def _matches_prefix(argv: list[str], entries: list[str]) -> bool:
    """True if `argv` starts with any allowlist entry's tokens.

    Entries may be multi-word (`"git status"`, `"npm install"`); a bare `git`
    must NOT satisfy a `git status` entry, so we compare token *prefixes* rather
    than the first token alone — otherwise every git subcommand would slip
    through (or, as it did before, be wrongly denied)."""
    for entry in entries:
        toks = entry.split()
        if toks and argv[: len(toks)] == toks:
            return True
    return False


class ShellTool:
    name = "shell"

    def __init__(
        self,
        *,
        read_only: list[str],
        side_effect: list[str],
        sandbox: Sandbox,
        mounts: dict[str, str],
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
        if not (_matches_prefix(argv, self._read_only) or _matches_prefix(argv, self._side_effect)):
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

        # Callers may extend the wall-clock budget via args (the interactive
        # terminal passes 120s; a long-lived dev server passes a much larger
        # bound). Absent/invalid → the historical 120s default. The value is a
        # plain arg on the audited ToolRequest, so the funnel still sees it.
        raw_timeout = args.get("timeout_s", 120.0)
        try:
            timeout_s = float(raw_timeout)
        except (TypeError, ValueError):
            timeout_s = 120.0

        # The ADE runs every workspace command IN the workspace root. The cwd travels on
        # the audited request, so forward it when a caller supplied one; without one
        # (every agent/CLI dispatch today) the historical call shape is preserved, so a
        # duck-typed sandbox that lacks the `cwd` parameter keeps working untouched.
        raw_cwd = args.get("cwd")
        cwd = raw_cwd if isinstance(raw_cwd, str) and raw_cwd else None

        # If a streaming sink is bound in the ambient context (the interactive
        # terminal path) AND the sandbox can stream, push output as it arrives and
        # assemble the final result from the terminal SandboxResult. Otherwise the
        # ordinary one-shot path — unchanged for every agent run and API command.
        sink = current_sink()
        if sink is not None and isinstance(self._sandbox, StreamingSandbox):
            result = await self._run_streaming(argv, network=network, sink=sink, timeout_s=timeout_s, cwd=cwd)
        elif cwd is None:
            result = await self._sandbox.run(
                argv,
                mounts=self._mounts,
                network=network,
                timeout_s=timeout_s,
            )
        else:
            result = await self._sandbox.run(
                argv,
                mounts=self._mounts,
                network=network,
                timeout_s=timeout_s,
                cwd=cwd,
            )
        is_side_effect = _matches_prefix(argv, self._side_effect)
        effects: tuple[SideEffect, ...] = ()
        if is_side_effect:
            effects = (SideEffect(kind="command", target=argv[0], detail=command, reversible=False),)
        return ToolResult(
            ok=result.exit_code == 0,
            output={
                "exit_code": result.exit_code,
                "stdout": result.stdout_tail,
                "stderr": result.stderr_tail,
                "duration_ms": result.duration_ms,
            },
            side_effects=effects,
            error=None if result.exit_code == 0 else (result.stderr_tail or "non-zero exit"),
        )

    async def _run_streaming(
        self, argv: list[str], *, network: bool, sink: Any, timeout_s: float = 120.0, cwd: str | None = None
    ) -> SandboxResult:
        """Drive the streaming sandbox, forwarding each chunk to `sink`, and return
        the terminal SandboxResult. Isolation/policy is identical to one-shot `run`
        — only the delivery differs. Only reached after `execute` has narrowed the
        sandbox to a `StreamingSandbox`, so the cast is sound."""
        streaming = cast(StreamingSandbox, self._sandbox)
        if cwd is None:
            stream = streaming.run_stream(argv, mounts=self._mounts, network=network, timeout_s=timeout_s)
        else:
            stream = streaming.run_stream(argv, mounts=self._mounts, network=network, timeout_s=timeout_s, cwd=cwd)
        final: SandboxResult | None = None
        async for item in stream:
            if isinstance(item, SandboxChunk):
                await sink(item)
            else:
                final = item
        if final is None:  # defensive: a well-behaved stream always ends in a result
            final = SandboxResult(
                exit_code=-1, stdout_tail="", stderr_tail="stream ended without result", duration_ms=0
            )
        return final
