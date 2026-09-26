"""Immutable step records for the agent engine (M0.2), plus the durable run
envelope + result serializer (M0.5).

WHY records, not just logs: an append-only, typed trace of every model turn and
tool result is what makes a run replayable and resumable, and it is the payload
the SSE frontend and trajectory memory consume. Records are frozen — a step, once
appended, never mutates.

M0.5 adds two things that belong with the trace, not with the loop: the single
tool-result serializer the engine and the rehydrator MUST share (so a persisted
run reconstructs byte-for-byte the TOOL turns the engine fed the model), and
`AgentRunRecord`, the durable envelope a run is persisted as.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from atlas.intelligence.contracts import Message


def serialize_tool_payload(*, ok: bool, output: Any, error: str | None) -> str:
    """Render a tool observation as the TOOL turn's text content.

    The single source of truth for how a tool result becomes model-visible text.
    The engine calls this live while looping; `context.reconstruct_conversation`
    calls it while rehydrating a persisted run — sharing it is what guarantees a
    resumed conversation is identical to the one the engine originally built.
    `default=str` keeps non-JSON outputs (e.g. objects) serializable; the fallback
    guards the pathological case where even that raises.
    """
    payload = {"ok": True, "output": output} if ok else {"ok": False, "error": error}
    try:
        return json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return json.dumps({"ok": ok, "output": str(output), "error": error})


class StopReason(StrEnum):
    FINISHED = "finished"  # model returned a turn with no tool calls
    LIMIT = "limit"  # an ExecutionLimits bound was hit (graceful, audited)
    ERROR = "error"  # an unexpected failure aborted the loop


class RunStatus(StrEnum):
    """A persisted run's lifecycle state — the engine's terminal ``StopReason`` set
    plus the non-terminal ``RUNNING`` a backgrounded run sits in until its result
    lands. Stored (and indexed) on ``AgentRunRecord`` so the run-list can show and
    filter in-flight runs without opening the payload."""

    RUNNING = "running"
    FINISHED = "finished"
    LIMIT = "limit"
    ERROR = "error"


# The states in which a run has a final result and its live stream can close.
TERMINAL_RUN_STATUSES = frozenset({RunStatus.FINISHED.value, RunStatus.LIMIT.value, RunStatus.ERROR.value})


class ToolCallRecord(BaseModel):
    """One tool call the model made and the observation it got back."""

    model_config = {"frozen": True}
    call_id: str
    tool: str
    operation: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = False
    output: Any = None
    error: str | None = None
    latency_ms: int = 0


class AgentStep(BaseModel):
    """One iteration of the loop: a model turn plus any tools it drove."""

    model_config = {"frozen": True}
    index: int
    assistant_text: str = ""
    tool_calls: tuple[ToolCallRecord, ...] = ()


class AgentEngineResult(BaseModel):
    """The terminal outcome of an agent-engine run."""

    model_config = {"frozen": True}
    ok: bool
    stop_reason: StopReason
    final_text: str = ""
    steps: tuple[AgentStep, ...] = ()
    model_calls: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    error: str | None = None


class AgentRunRecord(BaseModel):
    """The durable envelope one `AgentEngine.run(...)` is persisted as (M0.5).

    Stores the SEED conversation the run started from and its terminal result
    trace — not the fully expanded conversation, which is reconstructed on demand
    from ``seed_messages`` + ``result`` (see ``agent_engine.context``). That keeps
    the row small and the engine loop stateless: a run minted in one process is
    addressable and CONTINUABLE in the next, which is what makes the substrate
    resumable for the API/frontend.

    ``workspace_id``/``session_id`` tie a run to an IDE workspace/session when one
    exists (they are the run-list foreign keys), and are ``None`` for a bare
    engine run. ``parent_run_id`` is set when a run CONTINUES another (it carries
    the prior run's full rehydrated history as its seed), giving continuations a
    traceable lineage.

    ``result`` is ``None`` while a backgrounded run is still executing and is set
    once the engine loop terminates. ``status`` is the run's lifecycle state
    (``running`` until then, else the result's terminal stop reason); it is a
    stored, indexed field so the store can list/filter runs without opening the
    payload, and it is derived from ``result`` on construction when not passed
    explicitly (so every terminal-record call site stays unchanged).
    """

    model_config = {"frozen": True}
    run_id: str
    task_id: str = ""
    correlation_id: str = ""
    workspace_id: str | None = None
    session_id: str | None = None
    parent_run_id: str | None = None
    request: str = ""
    tool_names: tuple[str, ...] = ()
    seed_messages: tuple[Message, ...] = ()
    result: AgentEngineResult | None = None
    status: str = ""
    created_ts: str = ""
    updated_ts: str = ""

    @model_validator(mode="after")
    def _derive_status(self) -> AgentRunRecord:
        """Fill ``status`` from ``result`` when it was not set explicitly.

        Fires only for freshly built records that pass ``result`` but no
        ``status`` (every M0.5/M2.1 terminal call site) or a running stub (no
        result → ``running``). A loaded record already carries ``status`` in its
        payload, so this is a no-op on rehydration — the round-trip stays exact.
        """
        if not self.status:
            derived = RunStatus.RUNNING.value if self.result is None else self.result.stop_reason.value
            object.__setattr__(self, "status", derived)
        return self
