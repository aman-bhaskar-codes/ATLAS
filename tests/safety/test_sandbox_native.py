"""Tests for native sandbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.safety.sandbox_native import NativeSandbox


class TestNativeSandbox:
    def test_rejects_non_dev_environment(self) -> None:
        with pytest.raises(RuntimeError, match="dev"):
            NativeSandbox(env="production")

    def test_accepts_dev_environment(self) -> None:
        sandbox = NativeSandbox(env="dev")
        assert sandbox is not None

    @pytest.mark.asyncio
    async def test_run_simple_command(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        result = await sandbox.run(["echo", "hello"], mounts={}, network=False, timeout_s=5.0)
        assert result.exit_code == 0
        assert "hello" in result.stdout_tail

    @pytest.mark.asyncio
    async def test_run_with_mount_remap(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        host_dir = tmp_path / "data"
        host_dir.mkdir()
        result = await sandbox.run(
            ["ls", "/work/data"],
            mounts={str(host_dir): "/work/data"},
            network=False,
            timeout_s=5.0,
        )
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_run_with_stdin(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        result = await sandbox.run(
            ["cat"],
            mounts={},
            network=False,
            timeout_s=5.0,
            stdin=b"test input",
        )
        assert result.exit_code == 0
        assert "test input" in result.stdout_tail

    @pytest.mark.asyncio
    async def test_run_timeout(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        result = await sandbox.run(
            ["sleep", "10"],
            mounts={},
            network=False,
            timeout_s=0.1,
        )
        assert result.exit_code == 124
        assert "timed out" in result.stderr_tail

    @pytest.mark.asyncio
    async def test_run_invalid_command(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        result = await sandbox.run(
            ["nonexistent_command_xyz"],
            mounts={},
            network=False,
            timeout_s=5.0,
        )
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_health_returns_true(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        assert await sandbox.health() is True
