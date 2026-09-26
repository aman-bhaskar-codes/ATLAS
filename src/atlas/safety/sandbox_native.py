"""Native (no-Docker) sandbox for dev/local use.

WHY: Docker is required for production isolation. In dev mode (ATLAS_ENV=dev)
the user often doesn't have Docker running. This sandbox runs commands directly
on the host inside the allowed mount paths — NO isolation, dev only. It is
intentionally rejected in production (env != 'dev').
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from atlas.infra.logging import get_logger
from atlas.safety.sandbox import SandboxChunk, SandboxResult

_log = get_logger("atlas.sandbox.native")
_MAX_OUTPUT = 16_000


async def _kill_quietly(proc: asyncio.subprocess.Process) -> None:
    """Force-terminate a still-running child and reap it, swallowing the races
    (already exited, already reaped). Used on the timeout and early-close paths so
    a long-lived process never leaks past its session."""
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    with contextlib.suppress(Exception):
        await proc.wait()


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
    ) -> SandboxResult:
        # Remap mount_target paths back to their host equivalents in the argv.
        # e.g. /work/answer.txt -> /Users/.../scratch/answer.txt
        remapped = []
        for arg in command:
            for host_path, container_path in mounts.items():
                if arg.startswith(container_path):
                    arg = arg.replace(container_path, host_path, 1)
                    break
            remapped.append(arg)

        _log.info("sandbox.native.run", event_type="sandbox", cmd=remapped, network=network)

        # Ensure all mount source dirs exist.
        for host_path in mounts:
            Path(host_path).mkdir(parents=True, exist_ok=True)

        start = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *remapped,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            out_bytes, err_bytes = await asyncio.wait_for(proc.communicate(input=stdin), timeout=timeout_s)
            code = proc.returncode if proc.returncode is not None else -1
        except TimeoutError:
            return SandboxResult(
                exit_code=124,
                stdout_tail="",
                stderr_tail=f"timed out after {timeout_s}s",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:
            return SandboxResult(
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(exc),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )

        dur = int((time.perf_counter() - start) * 1000)
        return SandboxResult(
            exit_code=code,
            stdout_tail=out_bytes.decode(errors="replace")[-_MAX_OUTPUT:],
            stderr_tail=err_bytes.decode(errors="replace")[-_MAX_OUTPUT:],
            duration_ms=dur,
        )

    async def health(self) -> bool:
        return True

    def _remap(self, command: list[str], mounts: dict[str, str]) -> list[str]:
        """Map container mount targets in argv back to their host paths."""
        remapped = []
        for arg in command:
            for host_path, container_path in mounts.items():
                if arg.startswith(container_path):
                    arg = arg.replace(container_path, host_path, 1)
                    break
            remapped.append(arg)
        return remapped

    async def run_stream(
        self,
        command: list[str],
        *,
        mounts: dict[str, str],
        network: bool = False,
        timeout_s: float = 60.0,
        stdin: bytes | None = None,
    ) -> AsyncIterator[SandboxChunk | SandboxResult]:
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
                env={**os.environ},
            )
        except Exception as exc:
            yield SandboxResult(
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(exc),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return

        if stdin is not None and proc.stdin is not None:
            proc.stdin.write(stdin)
            proc.stdin.close()

        # Fan both pipes into one queue so we can yield chunks in arrival order
        # from a single async generator. Each reader pushes (stream, text); a
        # None sentinel per reader signals EOF.
        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

        async def _pump(reader: asyncio.StreamReader | None, name: str) -> None:
            if reader is None:
                await queue.put(None)
                return
            while True:
                line = await reader.readline()
                if not line:
                    break
                await queue.put((name, line.decode(errors="replace")))
            await queue.put(None)

        pumps = [
            asyncio.create_task(_pump(proc.stdout, "stdout")),
            asyncio.create_task(_pump(proc.stderr, "stderr")),
        ]

        out_parts: list[str] = []
        err_parts: list[str] = []
        timed_out = False
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
                stream, text = item
                (out_parts if stream == "stdout" else err_parts).append(text)
                yield SandboxChunk(stream=stream, data=text)
        except (asyncio.CancelledError, GeneratorExit):
            # The consumer stopped iterating early — a "stop" cancelled the task
            # driving this generator. Kill the still-running child so a long-lived
            # dev server never outlives its session, then propagate the unwind.
            await _kill_quietly(proc)
            raise
        finally:
            for p in pumps:
                p.cancel()

        if timed_out:
            await _kill_quietly(proc)
            yield SandboxResult(
                exit_code=124,
                stdout_tail="".join(out_parts)[-_MAX_OUTPUT:],
                stderr_tail=(f"timed out after {timeout_s}s"),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return

        code = await proc.wait()
        yield SandboxResult(
            exit_code=code if code is not None else -1,
            stdout_tail="".join(out_parts)[-_MAX_OUTPUT:],
            stderr_tail="".join(err_parts)[-_MAX_OUTPUT:],
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
