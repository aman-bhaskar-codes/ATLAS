"""Sandbox abstraction. WHY NullSandbox refuses: Phase 1 must make host command
execution IMPOSSIBLE, not merely unimplemented. Phase 2 ships DockerSandbox
implementing this exact protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from atlas.infra.errors import SystemError_


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


@dataclass(frozen=True)
class SandboxChunk:
    """One incremental slice of a running command's output. Emitted by the
    streaming path (`run_stream`) as bytes arrive, BEFORE the process exits — the
    substrate beneath the interactive terminal (Slice 6). ``stream`` is
    ``"stdout"`` or ``"stderr"``; ``data`` is already-decoded text."""

    stream: str  # "stdout" | "stderr"
    data: str


class Sandbox(Protocol):
    """Runs a command under isolation.

    ``cwd`` is the HOST directory the command must run in. The ADE runs every
    workspace command in the workspace root (a confirmed `git commit` must commit
    THAT repository), so a sandbox that silently ran it somewhere else would act on
    the wrong tree. A Docker sandbox maps a cwd under a mount into the container and
    falls back to its own workdir when the path is not representable — the isolation
    boundary wins over the request, and it is never silently substituted elsewhere.
    """

    async def run(
        self,
        command: list[str],
        *,
        mounts: dict[str, str],
        network: bool = False,
        timeout_s: float = 60.0,
        stdin: bytes | None = None,
        cwd: str | None = None,
    ) -> SandboxResult: ...


@runtime_checkable
class StreamingSandbox(Protocol):
    """A sandbox that can also emit output INCREMENTALLY while a command runs.

    WHY a separate protocol (not a new method on ``Sandbox``): streaming is an
    added capability, not a replacement — one-shot ``run`` stays the primitive the
    agent loop and every existing tool depend on, and fakes that only implement
    ``run`` remain valid ``Sandbox``es. A caller that wants incremental output
    checks ``isinstance(sandbox, StreamingSandbox)`` and degrades honestly (falls
    back to one-shot ``run``) when it is absent.

    ``run_stream`` yields zero or more :class:`SandboxChunk`s and then EXACTLY ONE
    terminal :class:`SandboxResult` as its final item — the same authorization and
    isolation policy as ``run``, only the delivery is incremental."""

    def run_stream(
        self,
        command: list[str],
        *,
        mounts: dict[str, str],
        network: bool = False,
        timeout_s: float = 60.0,
        stdin: bytes | None = None,
        cwd: str | None = None,
    ) -> AsyncIterator[SandboxChunk | SandboxResult]: ...


class NullSandbox:
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
        raise SystemError_(
            "NullSandbox cannot execute commands — the Docker sandbox arrives in "
            "Phase 2. No host access is permitted until then."
        )

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
        # Refuse identically to `run`: streaming is delivery, not a policy bypass.
        raise SystemError_(
            "NullSandbox cannot execute commands — the Docker sandbox arrives in "
            "Phase 2. No host access is permitted until then."
        )
        yield  # pragma: no cover — makes this an async generator, never reached
