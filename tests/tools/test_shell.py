"""Tests for shell tool."""

from __future__ import annotations

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
