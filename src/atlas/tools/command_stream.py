"""Out-of-band output sink for streaming command execution.

WHY a contextvar (not a tool arg): the interactive terminal (Slice 6) needs the
`shell` tool's output INCREMENTALLY while it runs, but the tool is invoked
through the SAME `SafetyEngine.guard` funnel as every other dispatch — and
`guard` audits `ToolRequest.args`, which must stay a plain, serializable dict. So
the streaming caller binds a sink in the ambient context right before awaiting
`guard`; the tool reads it inside `execute` and pushes chunks as they arrive. The
funnel is untouched (one authorization + execution path); a caller that binds no
sink (every agent run, every one-shot API command) gets the ordinary buffered
result. `guard` awaits `tool.execute` in the same task, so the contextvar
propagates naturally and never leaks across concurrent runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token

from atlas.safety.sandbox import SandboxChunk

# A sink consumes one output chunk. Kept async so a sink can push onto an
# asyncio.Queue / notify a condition without blocking the reader.
CommandSink = Callable[[SandboxChunk], Awaitable[None]]

_sink: ContextVar[CommandSink | None] = ContextVar("atlas_command_sink", default=None)


def current_sink() -> CommandSink | None:
    """The sink bound for the current execution context, or None (buffered)."""
    return _sink.get()


def bind_sink(sink: CommandSink) -> Token[CommandSink | None]:
    """Bind `sink` for the current context; pass the returned token to `unbind`."""
    return _sink.set(sink)


def unbind(token: Token[CommandSink | None]) -> None:
    """Restore the previously bound sink (or None)."""
    _sink.reset(token)
