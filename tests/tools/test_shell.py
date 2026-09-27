"""Tests for shell tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from atlas.safety.sandbox import SandboxResult
from atlas.tools.shell import _SHELL_OPERATORS, ShellTool


class TestShellOperators:
    def test_operators_is_frozenset(self) -> None:
        assert isinstance(_SHELL_OPERATORS, frozenset)

    def test_contains_pipe(self) -> None:
        assert "|" in _SHELL_OPERATORS

    def test_contains_semicolon(self) -> None:
        assert ";" in _SHELL_OPERATORS


class TestShellTool:
    @pytest.fixture
    def mock_sandbox(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def tool(self, mock_sandbox: AsyncMock) -> ShellTool:
        return ShellTool(
            read_only=["ls", "cat", "echo"],
            side_effect=["git", "npm"],
            sandbox=mock_sandbox,
            mounts={},
        )

    def test_dry_run(self, tool: ShellTool) -> None:
        result = tool.dry_run({"command": "ls -la"})
        assert "ls -la" in result

    def test_allowed_command(self, tool: ShellTool) -> None:
        allowed, reason = tool._allowed("ls -la /tmp")
        assert allowed is True
        assert reason == ""

    def test_disallowed_executable(self, tool: ShellTool) -> None:
        allowed, reason = tool._allowed("rm -rf /")
        assert allowed is False
        assert "not in allowlist" in reason

    def test_shell_operator_rejected(self, tool: ShellTool) -> None:
        allowed, reason = tool._allowed("ls | grep foo")
        assert allowed is False
        assert "not permitted" in reason

    def test_empty_command_rejected(self, tool: ShellTool) -> None:
        allowed, reason = tool._allowed("")
        assert allowed is False
        assert "empty" in reason

    def test_unparseable_command(self, tool: ShellTool) -> None:
        allowed, reason = tool._allowed("echo 'unclosed quote")
        assert allowed is False
        assert "unparseable" in reason

    @pytest.mark.asyncio
    async def test_execute_empty_command(self, tool: ShellTool) -> None:
        result = await tool.execute({"command": ""})
        assert result.ok is False
        assert "no command" in result.error

    @pytest.mark.asyncio
    async def test_execute_disallowed_command(self, tool: ShellTool) -> None:
        result = await tool.execute({"command": "rm -rf /"})
        assert result.ok is False
        assert "not allowlisted" in result.error

    @pytest.mark.asyncio
    async def test_execute_allowed_command(self, tool: ShellTool, mock_sandbox: AsyncMock) -> None:
        mock_sandbox.run.return_value = SandboxResult(
            exit_code=0,
            stdout_tail="output",
            stderr_tail="",
            duration_ms=100,
        )
        result = await tool.execute({"command": "ls -la"})
        assert result.ok is True
        assert result.output["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_execute_network_command(self, tool: ShellTool, mock_sandbox: AsyncMock) -> None:
        mock_sandbox.run.return_value = SandboxResult(
            exit_code=0,
            stdout_tail="cloned",
            stderr_tail="",
            duration_ms=5000,
        )
        await tool.execute({"command": "git clone https://example.com/repo.git"})
        call_args = mock_sandbox.run.call_args
        assert call_args[1]["network"] is True

    @pytest.mark.asyncio
    async def test_execute_honors_args_timeout(self, tool: ShellTool, mock_sandbox: AsyncMock) -> None:
        """A long-lived dev server (Slice 7) passes a large `timeout_s`; the tool
        must forward it to the sandbox rather than the hardcoded 120s default."""
        mock_sandbox.run.return_value = SandboxResult(exit_code=0, stdout_tail="", stderr_tail="", duration_ms=1)
        await tool.execute({"command": "ls", "timeout_s": 86_400.0})
        assert mock_sandbox.run.call_args[1]["timeout_s"] == 86_400.0

    @pytest.mark.asyncio
    async def test_execute_bad_timeout_falls_back_to_default(self, tool: ShellTool, mock_sandbox: AsyncMock) -> None:
        mock_sandbox.run.return_value = SandboxResult(exit_code=0, stdout_tail="", stderr_tail="", duration_ms=1)
        await tool.execute({"command": "ls", "timeout_s": "not-a-number"})
        assert mock_sandbox.run.call_args[1]["timeout_s"] == 120.0


class TestMultiWordAllowlist:
    """Locks the token-prefix allowlist match: entries may be multi-word
    (`"git status"`), so a bare `git` must NOT satisfy `git status`, and a
    non-allowlisted subcommand (`git push`) must be denied even though `git`
    is the executable. Guards the regression where argv[0]-only matching
    denied every git subcommand (or would let all of them through)."""

    @pytest.fixture
    def tool(self) -> ShellTool:
        return ShellTool(
            read_only=["git status", "git diff", "git log"],
            side_effect=["git commit", "npm install"],
            sandbox=AsyncMock(),
            mounts={},
        )

    def test_read_only_subcommand_allowed(self, tool: ShellTool) -> None:
        assert tool._allowed("git status -s")[0] is True
        assert tool._allowed("git diff --numstat")[0] is True

    def test_side_effect_subcommand_allowed(self, tool: ShellTool) -> None:
        assert tool._allowed("git commit -m msg")[0] is True

    def test_non_allowlisted_subcommand_denied(self, tool: ShellTool) -> None:
        allowed, reason = tool._allowed("git push origin main")
        assert allowed is False and "not in allowlist" in reason

    def test_bare_executable_does_not_match_multiword(self, tool: ShellTool) -> None:
        # `git` alone must not satisfy a `git status` entry.
        assert tool._allowed("git")[0] is False

    def test_unknown_executable_denied(self, tool: ShellTool) -> None:
        assert tool._allowed("rm -rf /")[0] is False


class TestShellToolStreaming:
    """The interactive-terminal seam: when an output sink is bound in the ambient
    context AND the sandbox can stream, `execute` forwards chunks live through the
    SAME code path (no second tool, no changed args) and still returns the buffered
    ToolResult. Uses the real NativeSandbox so this is true incremental output."""

    @pytest.mark.asyncio
    async def test_bound_sink_receives_incremental_chunks(self) -> None:
        from atlas.safety.sandbox import SandboxChunk
        from atlas.safety.sandbox_native import NativeSandbox
        from atlas.tools.command_stream import bind_sink, unbind

        tool = ShellTool(
            read_only=["printf", "echo"],
            side_effect=[],
            sandbox=NativeSandbox(env="dev"),
            mounts={},
        )
        chunks: list[SandboxChunk] = []

        async def _sink(chunk: SandboxChunk) -> None:
            chunks.append(chunk)

        token = bind_sink(_sink)
        try:
            result = await tool.execute({"command": "printf 'a\\nb\\nc\\n'"})
        finally:
            unbind(token)

        assert result.ok is True and result.output["exit_code"] == 0
        # Output was delivered as chunks (incrementally), not only in the tail.
        assert chunks, "expected streamed chunks via the bound sink"
        streamed = "".join(c.data for c in chunks if c.stream == "stdout")
        assert "a" in streamed and "b" in streamed and "c" in streamed

    @pytest.mark.asyncio
    async def test_no_sink_uses_buffered_path(self) -> None:
        from atlas.safety.sandbox_native import NativeSandbox
        from atlas.tools.command_stream import current_sink

        tool = ShellTool(read_only=["echo"], side_effect=[], sandbox=NativeSandbox(env="dev"), mounts={})
        assert current_sink() is None  # no sink bound → ordinary one-shot path
        result = await tool.execute({"command": "echo hello"})
        assert result.ok is True and "hello" in result.output["stdout"]


class TestCwdForwarding:
    """The ADE's per-command cwd must reach the sandbox. The governed request carries
    the workspace root; a tool that dropped it there would run a confirmed command in
    the server's own directory — the wrong repository."""

    @pytest.mark.asyncio
    async def test_cwd_reaches_the_sandbox(self) -> None:
        sandbox = AsyncMock()
        sandbox.run.return_value = SandboxResult(exit_code=0, stdout_tail="", stderr_tail="", duration_ms=1)
        tool = ShellTool(read_only=["git status"], side_effect=[], sandbox=sandbox, mounts={})

        await tool.execute({"command": "git status", "cwd": "/tmp/workspace"})

        assert sandbox.run.call_args[1]["cwd"] == "/tmp/workspace"

    @pytest.mark.asyncio
    async def test_absent_cwd_keeps_the_historical_call_shape(self) -> None:
        # Agent/CLI dispatches pass no cwd: the call must stay byte-for-byte what it was,
        # so a duck-typed sandbox without the new `cwd` parameter keeps working.
        sandbox = AsyncMock()
        sandbox.run.return_value = SandboxResult(exit_code=0, stdout_tail="", stderr_tail="", duration_ms=1)
        tool = ShellTool(read_only=["git status"], side_effect=[], sandbox=sandbox, mounts={})

        await tool.execute({"command": "git status"})

        assert "cwd" not in sandbox.run.call_args[1]

    @pytest.mark.asyncio
    async def test_streaming_path_honors_cwd(self, tmp_path: Path) -> None:
        from atlas.safety.sandbox import SandboxChunk
        from atlas.safety.sandbox_native import NativeSandbox
        from atlas.tools.command_stream import bind_sink, unbind

        tool = ShellTool(read_only=["pwd"], side_effect=[], sandbox=NativeSandbox(env="dev"), mounts={})

        async def _sink(chunk: SandboxChunk) -> None:
            return None

        token = bind_sink(_sink)
        try:
            result = await tool.execute({"command": "pwd", "cwd": str(tmp_path)})
        finally:
            unbind(token)

        assert result.ok is True
        assert Path(str(result.output["stdout"]).strip()).resolve() == tmp_path.resolve()
