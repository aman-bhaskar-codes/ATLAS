"""Tests for native sandbox."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.safety.sandbox_native import NativeSandbox


async def test_cleanup_deadline_survives_inherited_pipe_without_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.safety.sandbox_native import _kill_quietly

    reader = asyncio.StreamReader()
    closed = asyncio.Event()

    def close() -> None:
        reader.feed_eof()
        closed.set()

    async def wait() -> int:
        return -9

    monkeypatch.setattr(os, "killpg", lambda *_: None)
    proc = SimpleNamespace(
        pid=123, returncode=-9, stdout=reader, stderr=None, wait=wait, _transport=SimpleNamespace(close=close)
    )
    async with asyncio.timeout(0.1):
        await _kill_quietly(proc, cleanup_timeout_s=0.01)
    assert closed.is_set()


def test_native_nested_mount_uses_same_specific_target_as_credential_guard() -> None:
    mounts = {"/host/private": "/work", "/host/project": "/work/project"}
    assert NativeSandbox._remap_path("/work/project/config", mounts) == "/host/project/config"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_run_reaps_process_on_timeout_or_cancellation(monkeypatch: pytest.MonkeyPatch, cancel: bool) -> None:
    spawned: list[asyncio.subprocess.Process] = []
    started = asyncio.Event()
    create = asyncio.create_subprocess_exec

    async def capture(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        proc = await create(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(proc)
        started.set()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    task = asyncio.create_task(
        NativeSandbox().run([sys.executable, "-c", "import time; time.sleep(10)"], mounts={}, timeout_s=0.1)
    )
    try:
        await started.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).exit_code == 124
        assert spawned[0].returncode is not None, "sandbox returned without reaping its process"
    finally:
        if spawned and spawned[0].returncode is None:
            spawned[0].kill()
            await spawned[0].communicate()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("stream", [False, True])
async def test_timeout_stops_descendant_processes(tmp_path: Path, stream: bool) -> None:
    marker = tmp_path / "descendant.txt"
    child = f"import time; from pathlib import Path; time.sleep(.3); Path({str(marker)!r}).touch()"
    script = f"import subprocess,time; subprocess.Popen([{sys.executable!r}, '-c', {child!r}]); time.sleep(10)"
    sandbox = NativeSandbox()
    command = [sys.executable, "-c", script]
    if stream:
        async for _ in sandbox.run_stream(command, mounts={}, timeout_s=0.1):
            pass
    else:
        assert (await sandbox.run(command, mounts={}, timeout_s=0.1)).exit_code == 124
    await asyncio.sleep(0.4)
    assert not marker.exists(), "descendant survived the sandbox timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_child_environment_omits_credentials(monkeypatch: pytest.MonkeyPatch, stream: bool) -> None:
    for key in ("OPENAI_API_KEY", "ATLAS_MASTER_KEY", "DATABASE_URL", "UNRECOGNIZED_SECRET"):
        monkeypatch.setenv(key, "synthetic-test-credential")
    monkeypatch.setenv("LANG", "C")
    script = (
        "import os; keys=('OPENAI_API_KEY','ATLAS_MASTER_KEY','DATABASE_URL','UNRECOGNIZED_SECRET'); "
        "print(any(k in os.environ for k in keys)); print(os.environ.get('LANG')); "
        "print(bool(os.environ.get('PATH')))"
    )
    sandbox = NativeSandbox()
    command = [sys.executable, "-c", script]
    if stream:
        items = [item async for item in sandbox.run_stream(command, mounts={})]
        result = items[-1]
    else:
        result = await sandbox.run(command, mounts={})
    from atlas.safety.sandbox import SandboxResult

    assert isinstance(result, SandboxResult)
    assert result.exit_code == 0
    assert result.stdout_tail.splitlines() == ["False", "C", "True"]


@pytest.mark.asyncio
async def test_stream_deadline_includes_wait_after_pipe_eof() -> None:
    from atlas.safety.sandbox import SandboxResult

    script = "import os,time; os.close(1); os.close(2); time.sleep(.4)"

    async def collect() -> list[object]:
        return [
            item async for item in NativeSandbox().run_stream([sys.executable, "-c", script], mounts={}, timeout_s=0.05)
        ]

    items = await asyncio.wait_for(collect(), timeout=0.2)
    assert isinstance(items[-1], SandboxResult)
    assert items[-1].exit_code == 124


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_large_unbroken_output_is_drained_with_bounded_tail(stream: bool) -> None:
    from atlas.safety.sandbox import SandboxResult

    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"]
    sandbox = NativeSandbox()
    if stream:
        items = [item async for item in sandbox.run_stream(command, mounts={}, timeout_s=0.2)]
        result = items[-1]
    else:
        result = await sandbox.run(command, mounts={}, timeout_s=0.2)
    assert isinstance(result, SandboxResult)
    assert result.exit_code == 0
    assert result.stdout_tail == "x" * 16000


@pytest.mark.asyncio
async def test_stream_close_does_not_hang_when_output_queue_is_full() -> None:
    stream = NativeSandbox().run_stream(
        [sys.executable, "-c", "import sys,time; sys.stdout.write('x'*1000000); sys.stdout.flush(); time.sleep(10)"],
        mounts={},
        timeout_s=5.0,
    )
    await anext(stream)
    # Let the bounded queue fill while the consumer is suspended at yield.
    await asyncio.sleep(0.05)
    await asyncio.wait_for(stream.aclose(), timeout=0.3)


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
