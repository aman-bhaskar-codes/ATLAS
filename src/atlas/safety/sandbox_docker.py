"""Docker-backed execution sandbox.

WHY the flags are the security policy: --network none, --read-only,
--cap-drop ALL, --user non-root, --pids-limit, memory/cpu caps, and ONLY the
explicitly requested bind mounts. Even a buggy tool cannot escape the mounted
dirs. WHY shell out to `docker` instead of the SDK: the exact argv is auditable
and the runner is injectable for tests (no Docker needed in CI).
"""

from __future__ import annotations

import asyncio
import shlex
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from atlas.infra.circuit_breaker import CircuitBreaker
from atlas.infra.logging import get_logger
from atlas.safety.sandbox import SandboxChunk, SandboxResult

_log = get_logger("atlas.sandbox.docker")

_MAX_OUTPUT = 16_000  # chars; tool output is structured + truncated, never a raw dump


def _container_cwd(cwd: str | None, mounts: dict[str, str]) -> str | None:
    """Translate a HOST working directory into its path INSIDE the container. `None`
    when there is no cwd or it does not live under a mount — the container then keeps
    its own workdir, so a directory it cannot see can never be conjured into being."""
    if not cwd:
        return None
    for host, container in mounts.items():
        if cwd == host:
            return container
        if cwd.startswith(host.rstrip("/") + "/"):
            return container.rstrip("/") + cwd[len(host) :]
    return None


@dataclass(frozen=True)
class SandboxSpec:
    """Everything needed to launch one locked-down container run."""

    image: str
    cpus: float = 1.0
    memory: str = "512m"
    pids_limit: int = 128
    network: bool = False
    workdir: str = "/work"


class DockerRunner(Protocol):
    """Injectable process boundary. Real impl runs `docker`; tests fake it."""

    async def run(
        self, argv: Sequence[str], *, timeout_s: float, stdin: bytes | None = None
    ) -> tuple[int, str, str]: ...


class SubprocessDockerRunner:
    async def run(self, argv: Sequence[str], *, timeout_s: float, stdin: bytes | None = None) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(input=stdin), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            return 124, "", f"timed out after {timeout_s}s"
        code = proc.returncode if proc.returncode is not None else -1
        return code, out.decode(errors="replace"), err.decode(errors="replace")

    async def run_stream(
        self, argv: Sequence[str], *, timeout_s: float, stdin: bytes | None = None
    ) -> AsyncIterator[tuple[str, str] | int]:
        """Spawn `docker run` and yield (stream, text) as output arrives, then the
        integer exit code as the final item. 124 on timeout."""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if stdin is not None and proc.stdin is not None:
            proc.stdin.write(stdin)
            proc.stdin.close()

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
        start = time.perf_counter()
        deadline = start + timeout_s
        timed_out = False
        try:
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
                yield item
        finally:
            for p in pumps:
                p.cancel()
        if timed_out:
            proc.kill()
            await proc.wait()
            yield 124
            return
        code = await proc.wait()
        yield code if code is not None else -1


class DockerSandbox:
    """Implements the Sandbox protocol with a hardened `docker run`."""

    def __init__(self, spec: SandboxSpec, runner: DockerRunner | None = None) -> None:
        self._spec = spec
        self._runner = runner or SubprocessDockerRunner()
        self._breaker = CircuitBreaker(fail_threshold=3, cooldown_s=30.0)

    def _build_argv(
        self,
        command: list[str],
        mounts: dict[str, str],
        *,
        network: bool,
        stdin: bytes | None = None,
        cwd: str | None = None,
    ) -> list[str]:
        argv: list[str] = [
            "docker",
            "run",
            "--rm",
        ]

        # In Docker run, -i keeps stdin open even if not attached.
        # We need it if we're sending stdin.
        if stdin is not None:
            argv.append("-i")

        argv += [
            "--user",
            "65534:65534",  # nobody:nogroup, never root
            "--read-only",  # root fs is immutable
            "--cap-drop",
            "ALL",  # no Linux capabilities
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self._spec.pids_limit),
            "--cpus",
            str(self._spec.cpus),
            "--memory",
            self._spec.memory,
            "--network",
            "bridge" if network else "none",
            "--tmpfs",
            "/tmp:rw,size=64m,noexec",  # scratch, non-executable
            "--workdir",
            _container_cwd(cwd, mounts) or self._spec.workdir,
        ]
        # Only the explicitly permitted host paths are visible in the container.
        for host, container in mounts.items():
            argv += ["--volume", f"{host}:{container}"]
        argv.append(self._spec.image)
        argv += command
        return argv

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
        if not self._breaker.allow():
            _log.error("sandbox.circuit_open", event_type="sandbox")
            return SandboxResult(
                exit_code=-1, stdout_tail="", stderr_tail="Sandbox circuit breaker OPEN", duration_ms=0
            )

        argv = self._build_argv(command, mounts, network=network, stdin=stdin, cwd=cwd)
        _log.info(
            "sandbox.run", event_type="sandbox", cmd=shlex.join(command), mounts=list(mounts.values()), network=network
        )
        start = time.perf_counter()
        code, out, err = await self._runner.run(argv, timeout_s=timeout_s, stdin=stdin)
        dur = int((time.perf_counter() - start) * 1000)

        # 125 is Docker daemon error (failed to run container).
        # Anything else is either container exit code or timeout (124) which is normal behavior.
        if code == 125:
            self._breaker.record_failure()
        else:
            self._breaker.record_success()

        return SandboxResult(
            exit_code=code,
            stdout_tail=out[-_MAX_OUTPUT:],
            stderr_tail=err[-_MAX_OUTPUT:],
            duration_ms=dur,
        )

    async def health(self) -> bool:
        try:
            code, _, _ = await self._runner.run(["docker", "version", "--format", "{{.Server.Version}}"], timeout_s=5.0)
        except (FileNotFoundError, OSError):
            return False  # docker not installed or not on PATH
        return code == 0

    async def run_stream(
        self,
        command: list[str],
        *,
        mounts: dict[str, str],
        network: bool = False,
        timeout_s: float = 60.0,
        stdin: bytes | None = None,
        cwd: str | None = None,
    ) -> AsyncIterator[SandboxChunk | SandboxResult]:
        """Incremental variant of `run` with the SAME hardened container flags. If
        the injected runner streams (`run_stream`), output is yielded as it
        arrives; a runner that only exposes one-shot `run` (e.g. a test fake) is
        honestly degraded — its buffered output is emitted as chunks, then the
        result — so the streaming contract holds either way. Ends with exactly one
        terminal SandboxResult."""
        if not self._breaker.allow():
            _log.error("sandbox.circuit_open", event_type="sandbox")
            yield SandboxResult(exit_code=-1, stdout_tail="", stderr_tail="Sandbox circuit breaker OPEN", duration_ms=0)
            return

        argv = self._build_argv(command, mounts, network=network, stdin=stdin, cwd=cwd)
        _log.info(
            "sandbox.run_stream",
            event_type="sandbox",
            cmd=shlex.join(command),
            mounts=list(mounts.values()),
            network=network,
        )
        start = time.perf_counter()
        stream_fn = getattr(self._runner, "run_stream", None)
        out_parts: list[str] = []
        err_parts: list[str] = []

        if stream_fn is None:
            # Runner has no streaming path — degrade to one-shot, still honest.
            code, out, err = await self._runner.run(argv, timeout_s=timeout_s, stdin=stdin)
            if out:
                yield SandboxChunk(stream="stdout", data=out)
            if err:
                yield SandboxChunk(stream="stderr", data=err)
        else:
            code = -1
            async for item in stream_fn(argv, timeout_s=timeout_s, stdin=stdin):
                if isinstance(item, int):
                    code = item
                    break
                stream, text = item
                (out_parts if stream == "stdout" else err_parts).append(text)
                yield SandboxChunk(stream=stream, data=text)
            out, err = "".join(out_parts), "".join(err_parts)

        if code == 125:
            self._breaker.record_failure()
        else:
            self._breaker.record_success()
        yield SandboxResult(
            exit_code=code,
            stdout_tail=out[-_MAX_OUTPUT:],
            stderr_tail=err[-_MAX_OUTPUT:],
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
