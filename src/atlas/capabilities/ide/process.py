"""Managed long-lived development processes (Slice 7 — Run/Debug).

WHAT: a dev server (`npm run dev`, `uvicorn ...`) is a command that does NOT
exit — it runs until stopped and serves on a port. `CommandRunner` (one-shot) and
the interactive `TerminalSessionManager` (a command that finishes) both assume a
terminal exit; this supervisor layers *lifecycle* on top of the terminal service:
start a long-lived command, watch its output for the port(s) it binds, expose its
status, and STOP it on demand.

WHY it is not a second execution path: the supervisor owns NO subprocess and never
touches the sandbox. It calls `TerminalSessionManager.start`/`stop`, so every
process still runs through the SAME `SafetyEngine.guard` + `shell` funnel
(deny-by-default allowlist, tiering, audit, kill-switch). The only additions are a
longer wall-clock budget, output-scanning for ports, and cancellation — all
delivery/observation concerns, never a policy bypass (Constitution). Output is
streamed to the frontend over the EXISTING terminal SSE endpoint (a process id IS
its terminal id).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from atlas.capabilities.ide.contracts import DevProcess, ProcessId, ProcessStatus
from atlas.capabilities.ide.terminal import TerminalSessionManager
from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger

_log = get_logger("atlas.ide.process")

# A dev server has no natural exit; `stop` is the normal end. A large but finite
# budget still guarantees nothing leaks forever if a session is abandoned.
PROCESS_TIMEOUT_S = 86_400.0

# Ports a running server advertises in its own stdout. Conservative on purpose —
# we only surface a port we can turn into a real localhost URL, never a guess.
_PORT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d{2,5})"),
    re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})"),
    re.compile(r"\bport[:\s]+(\d{2,5})\b", re.IGNORECASE),
    re.compile(r"\blistening on\b[^\d]*?(\d{2,5})", re.IGNORECASE),
)


def detect_ports(text: str) -> set[int]:
    """Extract plausible TCP ports a server announces in `text`. Bounded to the
    valid range so log noise ('build 12345') can't masquerade as a port."""
    found: set[int] = set()
    for pat in _PORT_PATTERNS:
        for m in pat.finditer(text):
            port = int(m.group(1))
            if 1 <= port <= 65_535:
                found.add(port)
    return found


@dataclass
class _Proc:
    """Mutable runtime record for one managed process. Projected to the frozen
    `DevProcess` contract on read."""

    id: ProcessId
    workspace_id: str
    command: str
    cwd: str
    status: ProcessStatus
    started_ts: str
    ports: set[int] = field(default_factory=set)
    exit_code: int | None = None
    exited_ts: str | None = None
    scanner: asyncio.Task[None] | None = None

    def snapshot(self) -> DevProcess:
        return DevProcess(
            id=self.id,
            command=self.command,
            cwd=self.cwd,
            status=self.status,
            detected_ports=tuple(sorted(self.ports)),
            exit_code=self.exit_code,
            started_ts=self.started_ts,
            exited_ts=self.exited_ts,
        )


class ProcessSupervisor:
    """Owns managed dev processes, running each through the terminal funnel and
    watching its output for lifecycle + ports. Stateless beyond the per-process
    records; authorization/execution are delegated wholesale to the terminal
    manager (and thus `SafetyEngine.guard`)."""

    def __init__(self, terminals: TerminalSessionManager, *, clock: Clock) -> None:
        self._terminals = terminals
        self._clock = clock
        self._procs: dict[ProcessId, _Proc] = {}

    def start(self, workspace_id: str, cwd: str, command: str, *, correlation_id: CorrelationId) -> DevProcess | None:
        """Open a terminal session, launch `command` as a long-lived process, and
        begin scanning its output for ports/exit. Returns the `DevProcess`, or None
        if the command could not be started (funnel/session refusal surfaces later
        as a terminal exit on the stream)."""
        tid = self._terminals.open(cwd)
        ok = self._terminals.start(str(tid), command, correlation_id=correlation_id, timeout_s=PROCESS_TIMEOUT_S)
        if not ok:
            return None
        proc = _Proc(
            id=ProcessId(str(tid)),
            workspace_id=workspace_id,
            command=command,
            cwd=cwd,
            status=ProcessStatus.RUNNING,
            started_ts=self._clock.now().isoformat(),
        )
        self._procs[proc.id] = proc
        proc.scanner = asyncio.create_task(self._watch(proc))
        _log.info("ide.process.start", event_type="lifecycle", process_id=str(proc.id), command=command)
        return proc.snapshot()

    async def _watch(self, proc: _Proc) -> None:
        """Consume the process's own terminal stream: accumulate detected ports as
        output arrives, and record the terminal exit. This is a SECOND consumer of
        the session buffer — independent of the frontend's SSE tail — so it never
        steals output from the UI."""
        try:
            async for event in self._terminals.stream(str(proc.id)):
                if event.kind == "chunk":
                    proc.ports |= detect_ports(event.data)
                    if proc.ports and proc.status == ProcessStatus.RUNNING:
                        proc.status = ProcessStatus.HEALTHY
                else:  # terminal exit
                    proc.exit_code = event.exit_code
                    proc.exited_ts = self._clock.now().isoformat()
                    if event.denied:
                        proc.status = ProcessStatus.FAILED
                    elif event.error == "stopped":
                        proc.status = ProcessStatus.KILLED
                    elif (event.exit_code or 0) != 0:
                        proc.status = ProcessStatus.FAILED
                    else:
                        proc.status = ProcessStatus.EXITED
        except asyncio.CancelledError:
            raise

    def stop(self, workspace_id: str, process_id: str) -> bool:
        """Stop a managed process. Returns False when it is unknown to this
        workspace or nothing is running."""
        proc = self._procs.get(ProcessId(process_id))
        if proc is None or proc.workspace_id != workspace_id:
            return False
        return self._terminals.stop(process_id)

    def list(self, workspace_id: str) -> tuple[DevProcess, ...]:
        """All managed processes for a workspace, newest first."""
        procs = [p for p in self._procs.values() if p.workspace_id == workspace_id]
        procs.sort(key=lambda p: p.started_ts, reverse=True)
        return tuple(p.snapshot() for p in procs)

    def get(self, workspace_id: str, process_id: str) -> DevProcess | None:
        proc = self._procs.get(ProcessId(process_id))
        if proc is None or proc.workspace_id != workspace_id:
            return None
        return proc.snapshot()
