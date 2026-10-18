"""Native (no-Docker) sandbox for dev/local use.

WHY: Docker is required for production isolation. In dev mode (ATLAS_ENV=dev)
the user often doesn't have Docker running. This sandbox runs commands directly
on the host inside the allowed mount paths — NO isolation, dev only. It is
intentionally rejected in production (env != 'dev').
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import signal
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from atlas.infra.command_policy import remap_mount_path
from atlas.infra.logging import get_logger
from atlas.safety.sandbox import SandboxChunk, SandboxResult

_log = get_logger("atlas.sandbox.native")
_MAX_OUTPUT = 16_000
_SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SYSTEMROOT",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "NODE_ENV",
    "ATLAS_ENV",
)


def _safe_environment() -> dict[str, str]:
    """Preserve development essentials without inheriting server credentials."""
    return {key: os.environ[key] for key in _SAFE_ENV_KEYS if key in os.environ}


async def _kill_quietly(proc: asyncio.subprocess.Process, *, cleanup_timeout_s: float = 1.0) -> None:
    """Force-terminate a still-running child and reap it, swallowing the races
    (already exited, already reaped). Used on the timeout and early-close paths so
    a long-lived process never leaks past its session."""
    try:
        if os.name == "posix":
            # The group can outlive its leader, including after pipe EOF.
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass

    async def drain(reader: asyncio.StreamReader | None) -> None:
        if reader is not None:
            while await reader.read(4096):
                pass

    # wait() can block even after SIGKILL when unread pipe buffers paused their
    # transports. Drain the bounded residual output before finishing reaping.
    try:
        async with asyncio.timeout(cleanup_timeout_s):
            await asyncio.gather(drain(proc.stdout), drain(proc.stderr), proc.wait())
    except TimeoutError:
        _log.warning("sandbox.native.cleanup_timeout", event_type="sandbox")
    finally:
        # A detached descendant may hold inherited pipes open indefinitely.
        # asyncio exposes no public Process.close(): close its transport after
        # termination so pipe backpressure cannot outlive the cleanup deadline.
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()


class NativeSandbox:
    """Runs commands directly on the host. DEV ONLY — no isolation whatsoever."""

    def __init__(self, env: str = "dev") -> None:
        if env != "dev":
            raise RuntimeError("NativeSandbox is only permitted in dev environment")

    async def run(
        self,
        command: list[str],
        *,
        mounts: dict[str, str],
        network: bool = False,
        timeout_s: float = 60.0,
        stdin: bytes | None = None,
        cwd: str | None = None,
    ) -> SandboxResult:
        # Share the bounded reader and lifecycle handling with streaming callers.
        async with contextlib.aclosing(
            self.run_stream(command, mounts=mounts, network=network, timeout_s=timeout_s, stdin=stdin, cwd=cwd)
        ) as stream:
            async for item in stream:
                if isinstance(item, SandboxResult):
                    return item
        raise RuntimeError("native sandbox returned no result")

    async def health(self) -> bool:
        return True

    def _remap(self, command: list[str], mounts: dict[str, str]) -> list[str]:
        """Map container mount targets in argv back to their host paths."""
        return [self._remap_path(arg, mounts) for arg in command]

    @staticmethod
    def _remap_path(value: str, mounts: dict[str, str]) -> str:
        """Map ONE container mount target back to its host path — argv entries and the
        command's `cwd` alike. A value with no matching mount returns unchanged."""
        return remap_mount_path(value, mounts)

    async def run_stream(
        self,
        command: list[str],
        *,
        mounts: dict[str, str],
        network: bool = False,
        timeout_s: float = 60.0,
        stdin: bytes | None = None,
        cwd: str | None = None,
    ) -> AsyncGenerator[SandboxChunk | SandboxResult]:
        """Run a command on the host, yielding output as it arrives, then a final
        SandboxResult. DEV ONLY — no isolation. Same semantics as `run`, only the
        delivery is incremental: stdout/stderr are interleaved as chunks and the
        tails are accumulated (bounded) for the terminal result."""
        remapped = self._remap(command, mounts)
        _log.info("sandbox.native.run_stream", event_type="sandbox", cmd=remapped, network=network)
        for host_path in mounts:
            Path(host_path).mkdir(parents=True, exist_ok=True)

        start = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *remapped,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_safe_environment(),
                start_new_session=os.name == "posix",
                cwd=self._remap_path(cwd, mounts) if cwd else None,
            )
        except Exception as exc:
            yield SandboxResult(
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(exc),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return

        # Fan both pipes into one queue so we can yield chunks in arrival order
        # from a single async generator. Each reader pushes (stream, text); a
        # None sentinel per reader signals EOF.
        queue: asyncio.Queue[tuple[str, str] | Exception | None] = asyncio.Queue(maxsize=16)

        async def _pump(reader: asyncio.StreamReader | None, name: str) -> None:
            if reader is None:
                await queue.put(None)
                return
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                while data := await reader.read(4096):
                    text = decoder.decode(data)
                    if text:
                        await queue.put((name, text))
                if text := decoder.decode(b"", final=True):
                    await queue.put((name, text))
            except Exception as exc:
                await queue.put(exc)
            # Cancellation must not try to enqueue EOF: a suspended consumer can
            # leave the queue full, and cleanup would wait for it indefinitely.
            await queue.put(None)

        async def _feed() -> None:
            if stdin is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

        pumps = [
            asyncio.create_task(_pump(proc.stdout, "stdout")),
            asyncio.create_task(_pump(proc.stderr, "stderr")),
        ]
        feeder = asyncio.create_task(_feed())

        out_tail = ""
        err_tail = ""
        timed_out = False
        error: str | None = None
        code = -1
        try:
            deadline = start + timeout_s
            finished = 0
            while finished < len(pumps):
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    timed_out = True
                    break
                if item is None:
                    finished += 1
                    continue
                if isinstance(item, Exception):
                    raise item
                stream, text = item
                if stream == "stdout":
                    out_tail = (out_tail + text)[-_MAX_OUTPUT:]
                else:
                    err_tail = (err_tail + text)[-_MAX_OUTPUT:]
                yield SandboxChunk(stream=stream, data=text)
            if not timed_out:
                # EOF does not imply process exit: commands can close their pipes
                # and continue running. The same deadline governs process reaping.
                async with asyncio.timeout(max(0.0, deadline - time.perf_counter())):
                    code = await proc.wait()
                    await feeder
                    await asyncio.gather(*pumps)
        except TimeoutError:
            timed_out = True
        except Exception as exc:
            error = str(exc)
        finally:
            # Also runs on cancellation and aclose(), including while yielding.
            for p in pumps:
                p.cancel()
            feeder.cancel()
            await asyncio.gather(*pumps, feeder, return_exceptions=True)
            await _kill_quietly(proc)

        if timed_out:
            yield SandboxResult(
                exit_code=124,
                stdout_tail=out_tail,
                stderr_tail=(f"timed out after {timeout_s}s"),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return

        yield SandboxResult(
            exit_code=1 if error else code,
            stdout_tail=out_tail,
            stderr_tail=error or err_tail,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
