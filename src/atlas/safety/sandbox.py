"""Sandbox abstraction. WHY NullSandbox refuses: Phase 1 must make host command
execution IMPOSSIBLE, not merely unimplemented. Phase 2 ships DockerSandbox
implementing this exact protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from atlas.infra.errors import SystemError_


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


class Sandbox(Protocol):
    async def run(
        self, command: list[str], *, mounts: dict[str, str],
        network: bool = False, timeout_s: float = 60.0,
        stdin: bytes | None = None,
    ) -> SandboxResult: ...


class NullSandbox:
    async def run(
        self, command: list[str], *, mounts: dict[str, str],
        network: bool = False, timeout_s: float = 60.0,
        stdin: bytes | None = None,
    ) -> SandboxResult:
        raise SystemError_(
            "NullSandbox cannot execute commands — the Docker sandbox arrives in "
            "Phase 2. No host access is permitted until then."
        )
