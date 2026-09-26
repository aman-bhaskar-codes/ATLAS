"""Interactive, streaming terminal sessions (Slice 6).

WHAT: a governed terminal whose output arrives INCREMENTALLY. `CommandRunner`
(sibling module) is the one-shot primitive the agent repair loop uses; this is
its streaming counterpart for the human at the workbench, who must see a build or
test scroll live rather than wait for a buffered tail.

WHY it is not a second execution path: every command still goes through the SAME
`SafetyEngine.guard` + `shell` tool funnel — deny-by-default allowlist, tiering,
audit, kill-switch all unchanged. The only addition is delivery: the manager
binds an output *sink* (see `tools.command_stream`) in the ambient context right
before awaiting `guard`, and the `shell` tool forwards each `SandboxChunk` to it
while the sandbox streams. The manager never spawns a subprocess itself and never
touches the sandbox directly, so it cannot become a side door around policy
(Constitution).

Each session buffers its ordered output events in memory so an SSE consumer can
(re)attach and resume from a cursor — the same durable-cursor discipline the
agent-run stream uses, scoped to a live terminal rather than a persisted run.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from atlas.capabilities.ide.contracts import TerminalId, TerminalStatus
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ToolRequest, ToolResult
from atlas.safety.engine import DeniedError, HaltedError, SafetyEngine
from atlas.safety.sandbox import SandboxChunk
from atlas.tools.base import Tool
from atlas.tools.command_stream import bind_sink, unbind

_log = get_logger("atlas.ide.terminal")


@dataclass(frozen=True)
class TerminalEvent:
    """One ordered item in a session's output stream. `kind` is ``"chunk"`` (live
    output) or ``"exit"`` (the command finished — terminal for that command)."""

    seq: int
    kind: Literal["chunk", "exit"]
    stream: str = ""  # "stdout" | "stderr" for chunks
    data: str = ""
    exit_code: int | None = None
    denied: bool = False
    error: str | None = None


@dataclass
class _Session:
    id: TerminalId
    cwd: str
    status: TerminalStatus = TerminalStatus.STARTING
    events: list[TerminalEvent] = field(default_factory=list)
    seq: int = 0
    running: bool = False
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None


class TerminalSessionManager:
    """Owns live terminal sessions and runs commands in them through the funnel.

    Stateful only in the buffered output per session; authorization + execution
    are delegated wholesale to `SafetyEngine.guard` over the `shell` tool."""

    def __init__(self, safety: SafetyEngine, command_tool: Tool, *, ids: IdGenerator) -> None:
        self._safety = safety
        self._tool = command_tool
        self._ids = ids
        self._sessions: dict[TerminalId, _Session] = {}

    def open(self, cwd: str) -> TerminalId:
        """Allocate a new terminal session rooted at `cwd`. No process starts yet —
        a session is a durable output buffer that commands run into."""
        tid = TerminalId(self._ids.task_id())
        self._sessions[tid] = _Session(id=tid, cwd=cwd)
        _log.info("ide.terminal.open", event_type="lifecycle", terminal_id=str(tid), cwd=cwd)
        return tid

    def exists(self, terminal_id: str) -> bool:
        return TerminalId(terminal_id) in self._sessions

    async def _append(self, sess: _Session, event: TerminalEvent) -> None:
        async with sess.cond:
            sess.events.append(event)
            sess.cond.notify_all()

    async def run(
        self, terminal_id: str, command: str, *, correlation_id: CorrelationId, timeout_s: float = 120.0
    ) -> None:
        """Run `command` in the session, streaming its output into the buffer as it
        arrives. Awaits completion; callers wanting a live SSE tail should schedule
        this via `start` and read `stream` concurrently. Every outcome — denial,
        halt, non-zero exit, tool error, or a `stop` cancellation — lands as a
        terminal ``exit`` event, never a raised exception past this boundary.

        `timeout_s` is the wall-clock budget forwarded to the funnel; a long-lived
        dev server (Slice 7) passes a large bound while an ordinary terminal
        command keeps the 120s default."""
        sess = self._sessions.get(TerminalId(terminal_id))
        if sess is None:
            return
        command = command.strip()
        if not command:
            await self._finish(sess, TerminalEvent(seq=0, kind="exit", exit_code=None, error="empty command"))
            return

        sess.running = True
        sess.status = TerminalStatus.RUNNING

        async def _sink(chunk: SandboxChunk) -> None:
            sess.seq += 1
            await self._append(sess, TerminalEvent(seq=sess.seq, kind="chunk", stream=chunk.stream, data=chunk.data))

        req = ToolRequest(
            correlation_id=correlation_id,
            tool=self._tool.name,
            operation="run",
            args={"command": command, "cwd": sess.cwd, "timeout_s": timeout_s},
        )
        token = bind_sink(_sink)
        try:
            result: ToolResult = await self._safety.guard(req, self._tool)
            out = result.output if isinstance(result.output, dict) else {}
            code = out.get("exit_code")
            await self._finish(
                sess,
                TerminalEvent(
                    seq=0,
                    kind="exit",
                    exit_code=int(code) if isinstance(code, int) else None,
                    error=result.error,
                ),
                killed=False,
            )
        except asyncio.CancelledError:
            # A `stop` cancelled this task. The sandbox generator's early-close
            # path has already killed the child; record an honest terminal event
            # (shielded, so the append survives the in-flight cancellation) and
            # re-raise to complete the cancellation.
            await asyncio.shield(self._finish(sess, TerminalEvent(seq=0, kind="exit", error="stopped"), killed=True))
            raise
        except DeniedError as exc:
            _log.info("ide.terminal.denied", event_type="safety", command=command, reason=exc.decision.reason)
            await self._finish(
                sess,
                TerminalEvent(seq=0, kind="exit", denied=True, error=f"denied: {exc.decision.reason}"),
                killed=True,
            )
        except HaltedError as exc:
            await self._finish(sess, TerminalEvent(seq=0, kind="exit", error=f"halted: {exc}"), killed=True)
        finally:
            unbind(token)
            sess.running = False

    def start(self, terminal_id: str, command: str, *, correlation_id: CorrelationId, timeout_s: float = 120.0) -> bool:
        """Fire `run` as a background task so an SSE consumer can stream the output
        live. Returns False if the session is unknown or already busy."""
        sess = self._sessions.get(TerminalId(terminal_id))
        if sess is None or sess.running:
            return False
        sess.task = asyncio.create_task(
            self.run(terminal_id, command, correlation_id=correlation_id, timeout_s=timeout_s)
        )
        return True

    def stop(self, terminal_id: str) -> bool:
        """Cancel the command running in a session (if any). The cancellation
        unwinds through the sandbox generator, which kills the child process; the
        `run` coroutine records a terminal ``exit`` (``error="stopped"``). Returns
        False when the session is unknown or nothing is running."""
        sess = self._sessions.get(TerminalId(terminal_id))
        if sess is None or not sess.running or sess.task is None:
            return False
        sess.task.cancel()
        _log.info("ide.terminal.stop", event_type="lifecycle", terminal_id=terminal_id)
        return True

    def status(self, terminal_id: str) -> TerminalStatus | None:
        """The session's current status, or None when the session is unknown."""
        sess = self._sessions.get(TerminalId(terminal_id))
        return sess.status if sess is not None else None

    async def _finish(self, sess: _Session, event: TerminalEvent, *, killed: bool = False) -> None:
        sess.seq += 1
        final = TerminalEvent(
            seq=sess.seq,
            kind="exit",
            exit_code=event.exit_code,
            denied=event.denied,
            error=event.error,
        )
        sess.status = TerminalStatus.KILLED if killed else TerminalStatus.EXITED
        await self._append(sess, final)

    async def stream(self, terminal_id: str, after_seq: int = 0) -> AsyncIterator[TerminalEvent]:
        """Yield the session's output events in order, resuming after `after_seq`,
        then live-tail new events until a terminal ``exit`` is delivered. A
        reconnecting consumer passes the last seq it saw (its cursor) to resume
        without replaying — buffered events make that idempotent."""
        sess = self._sessions.get(TerminalId(terminal_id))
        if sess is None:
            return
        cursor = after_seq
        while True:
            async with sess.cond:
                pending = [e for e in sess.events if e.seq > cursor]
                if not pending:
                    # Nothing new. If the last buffered event is terminal, stop.
                    if sess.events and sess.events[-1].kind == "exit" and sess.events[-1].seq <= cursor:
                        return
                    await sess.cond.wait()
                    pending = [e for e in sess.events if e.seq > cursor]
            for event in pending:
                cursor = event.seq
                yield event
                if event.kind == "exit":
                    return
