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


class TestNativeSandboxStreaming:
    @pytest.mark.asyncio
    async def test_run_stream_yields_chunks_then_result(self, tmp_path: Path) -> None:
        from atlas.safety.sandbox import SandboxChunk, SandboxResult

        sandbox = NativeSandbox(env="dev")
        chunks: list[SandboxChunk] = []
        final: SandboxResult | None = None
        async for item in sandbox.run_stream(["printf", "a\\nb\\nc\\n"], mounts={}, network=False, timeout_s=5.0):
            if isinstance(item, SandboxChunk):
                chunks.append(item)
            else:
                final = item
        assert final is not None and final.exit_code == 0
        # Output arrived as one-or-more chunks, and the joined stdout is complete.
        assert chunks, "expected at least one streamed chunk"
        joined = "".join(c.data for c in chunks if c.stream == "stdout")
        assert "a" in joined and "b" in joined and "c" in joined

    @pytest.mark.asyncio
    async def test_run_stream_timeout_yields_124(self, tmp_path: Path) -> None:
        from atlas.safety.sandbox import SandboxResult

        sandbox = NativeSandbox(env="dev")
        final: SandboxResult | None = None
        async for item in sandbox.run_stream(["sleep", "10"], mounts={}, network=False, timeout_s=0.1):
            if isinstance(item, SandboxResult):
                final = item
        assert final is not None and final.exit_code == 124

    @pytest.mark.asyncio
    async def test_run_stream_early_close_kills_child(self, tmp_path: Path) -> None:
        """Breaking out of the stream (a `stop`) must kill the child, not let it
        run to completion in the background — the Slice 7 cancellation contract."""
        import asyncio

        sandbox = NativeSandbox(env="dev")
        marker = tmp_path / "done.txt"
        gen = sandbox.run_stream(
            ["sh", "-c", f"echo started; sleep 0.5; echo done > {marker}"],
            mounts={},
            network=False,
            timeout_s=5.0,
        )
        # Enter the generator far enough to spawn the child (first chunk = "started"),
        # then close it early.
        await gen.__anext__()
        await gen.aclose()
        # Past the child's own delay: if it were still alive it would have written.
        await asyncio.sleep(1.0)
        assert not marker.exists(), "child kept running after the stream was closed"


class TestRequestedCwd:
    """The ADE runs every workspace command in the workspace ROOT. A sandbox that
    dropped `cwd` would run a confirmed `git commit` / `pytest` in whatever directory
    the server was started in — the wrong repository. Pins the REAL behaviour."""

    @pytest.mark.asyncio
    async def test_run_honors_the_requested_cwd(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        result = await sandbox.run(["pwd"], mounts={}, network=False, timeout_s=5.0, cwd=str(workdir))
        assert result.exit_code == 0
        assert Path(result.stdout_tail.strip()).resolve() == workdir.resolve()

    @pytest.mark.asyncio
    async def test_run_remaps_a_container_cwd_to_its_host_mount(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        host = tmp_path / "work"
        host.mkdir()
        result = await sandbox.run(["pwd"], mounts={str(host): "/work"}, network=False, timeout_s=5.0, cwd="/work")
        assert result.exit_code == 0
        assert Path(result.stdout_tail.strip()).resolve() == host.resolve()

    @pytest.mark.asyncio
    async def test_run_without_a_cwd_still_inherits_the_process_cwd(self, tmp_path: Path) -> None:
        sandbox = NativeSandbox(env="dev")
        result = await sandbox.run(["pwd"], mounts={}, network=False, timeout_s=5.0)
        assert result.exit_code == 0
        assert Path(result.stdout_tail.strip()).resolve() == Path.cwd().resolve()

    @pytest.mark.asyncio
    async def test_run_stream_honors_the_requested_cwd(self, tmp_path: Path) -> None:
        from atlas.safety.sandbox import SandboxChunk, SandboxResult

        sandbox = NativeSandbox(env="dev")
        workdir = tmp_path / "streamed"
        workdir.mkdir()
        final: SandboxResult | None = None
        async for item in sandbox.run_stream(["pwd"], mounts={}, network=False, timeout_s=5.0, cwd=str(workdir)):
            if not isinstance(item, SandboxChunk):
                final = item
        assert final is not None and final.exit_code == 0
        assert Path(final.stdout_tail.strip()).resolve() == workdir.resolve()
