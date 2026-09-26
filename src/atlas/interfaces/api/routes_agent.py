"""Agent-run REST API routes (M2.1) — start, fetch, list, and continue agent runs.

A thin projection over ``AgentRunService`` (orchestration/agent_engine). The
service owns all state and governance; these handlers only translate HTTP <->
contracts. Every tool the agent drives still flows through the SafetyEngine funnel
inside the engine loop — nothing here bypasses policy.

Mounted only when ``config.agent_engine.enabled`` and the service was built;
otherwise the routes return 503 (subsystem disabled), mirroring the ADE/voice
surfaces. Runs execute synchronously: the response IS the full, persisted trace.
Live streaming of an in-flight run (background execution + SSE) is the next slice.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from atlas.app import Atlas
from atlas.interfaces.api.dependencies import get_atlas
from atlas.orchestration.agent_engine.records import (
    TERMINAL_RUN_STATUSES,
    AgentEngineResult,
    AgentRunRecord,
)
from atlas.orchestration.agent_engine.service import AgentRunError, AgentRunNotReady

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

# Live-stream cadence: how often to sweep the durable trace for new events, and how
# long to wait idle before a keep-alive heartbeat. The bus commits synchronously, so
# a short poll surfaces events promptly without a wake queue (the orchestrator-topic
# SSE waker in ``events.py`` covers neither the ``tool`` topic nor this table).
_POLL_INTERVAL_SECONDS = 0.5
_HEARTBEAT_SECONDS = 15.0


def _service(atlas: Atlas) -> Any:
    """The AgentRunService, or 503 when the agent surface is disabled/unavailable."""
    svc = getattr(atlas, "agent_engine", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="agent-run subsystem disabled")
    return svc


# ── Request/response models ──────────────────────────────────────────── #
class StartRunRequest(BaseModel):
    request: str
    workspace_id: str | None = None
    session_id: str | None = None
    max_tools: int | None = None
    background: bool = False


class ContinueRunRequest(BaseModel):
    request: str
    max_tools: int | None = None


# <<APPEND-MARKER>>
class RunResponse(BaseModel):
    """A full run: metadata + the complete engine trace (``result``).

    ``seed_messages`` is deliberately NOT exposed — it is rehydration state, not a
    client concern. The trace (steps + final text) is the authoritative artifact.
    ``result`` is ``None`` while a backgrounded run is still ``running``; poll the
    run (or stream it) until ``status`` is terminal and ``result`` is populated.
    """

    run_id: str
    status: str
    request: str
    correlation_id: str
    workspace_id: str | None = None
    session_id: str | None = None
    parent_run_id: str | None = None
    tool_names: list[str]
    created_ts: str
    updated_ts: str
    result: AgentEngineResult | None = None


class RunSummary(BaseModel):
    """A compact run row for lists — no step trace."""

    run_id: str
    status: str
    request: str
    workspace_id: str | None = None
    session_id: str | None = None
    parent_run_id: str | None = None
    created_ts: str
    updated_ts: str
    model_calls: int
    tool_calls: int
    final_text: str


class RunListResponse(BaseModel):
    runs: list[RunSummary]


def _to_response(rec: AgentRunRecord) -> RunResponse:
    return RunResponse(
        run_id=rec.run_id,
        status=rec.status,
        request=rec.request,
        correlation_id=rec.correlation_id,
        workspace_id=rec.workspace_id,
        session_id=rec.session_id,
        parent_run_id=rec.parent_run_id,
        tool_names=list(rec.tool_names),
        created_ts=rec.created_ts,
        updated_ts=rec.updated_ts,
        result=rec.result,
    )


def _to_summary(rec: AgentRunRecord) -> RunSummary:
    return RunSummary(
        run_id=rec.run_id,
        status=rec.status,
        request=rec.request,
        workspace_id=rec.workspace_id,
        session_id=rec.session_id,
        parent_run_id=rec.parent_run_id,
        created_ts=rec.created_ts,
        updated_ts=rec.updated_ts,
        model_calls=rec.result.model_calls if rec.result else 0,
        tool_calls=rec.result.tool_calls if rec.result else 0,
        final_text=rec.result.final_text if rec.result else "",
    )


# <<ROUTES-MARKER>>
@router.post("/runs", response_model=RunResponse)
async def start_run(req: StartRunRequest, atlas: Atlas = Depends(get_atlas)) -> RunResponse:
    """Start a governed agent loop for ``request`` and return its run record.

    Synchronous by default: blocks until the loop finishes or a seatbelt trips, and
    the response IS the full persisted trace. When ``background`` is set, returns
    immediately with a ``running`` stub (``result`` null) that becomes addressable
    and streamable at once — poll ``GET /runs/{id}`` or stream it until terminal. A
    limit/error is never an HTTP error; it comes back as a run whose ``status`` says so.
    """
    svc = _service(atlas)
    if not req.request.strip():
        raise HTTPException(status_code=400, detail="request must not be empty")
    if req.background:
        rec = await svc.start_run_background(
            req.request,
            workspace_id=req.workspace_id,
            session_id=req.session_id,
            max_tools=req.max_tools,
        )
    else:
        rec = await svc.start_run(
            req.request,
            workspace_id=req.workspace_id,
            session_id=req.session_id,
            max_tools=req.max_tools,
        )
    return _to_response(rec)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    workspace_id: str | None = None, limit: int = 50, atlas: Atlas = Depends(get_atlas)
) -> RunListResponse:
    """List runs (most-recently-updated first), optionally scoped to a workspace."""
    svc = _service(atlas)
    runs = await svc.list_runs(workspace_id=workspace_id, limit=limit)
    return RunListResponse(runs=[_to_summary(r) for r in runs])


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, atlas: Atlas = Depends(get_atlas)) -> RunResponse:
    """Fetch a single run's full trace. 404 if unknown."""
    svc = _service(atlas)
    rec = await svc.get_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return _to_response(rec)


@router.post("/runs/{run_id}/continue", response_model=RunResponse)
async def continue_run(run_id: str, req: ContinueRunRequest, atlas: Atlas = Depends(get_atlas)) -> RunResponse:
    """Rehydrate ``run_id`` and drive a new turn — returns a fresh, linked run.

    The new run carries ``parent_run_id=run_id`` and the full rehydrated history as
    its seed, so it is itself self-contained and further continuable. 404 if the
    parent run is unknown; 409 if it is still running (nothing to rehydrate yet).
    """
    svc = _service(atlas)
    if not req.request.strip():
        raise HTTPException(status_code=400, detail="request must not be empty")
    try:
        rec = await svc.continue_run(run_id, req.request, max_tools=req.max_tools)
    except AgentRunNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AgentRunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_response(rec)


# ── Live run console (SSE) ───────────────────────────────────────────── #
def _frame(event: Any) -> str:
    """Render one trace event as an SSE ``agent_event`` frame (``id:`` = cursor)."""
    return f"id: {event.sequence}\nevent: agent_event\ndata: {event.model_dump_json()}\n\n"


async def _drain(svc: Any, run_id: str, after: int) -> AsyncIterator[Any]:
    """Yield every event past ``after``, walking the cursor until the trace is empty.

    Batches are bounded by the reader's ``limit``; looping until a batch comes back
    empty drains a trace of any size (a snapshot larger than one page still flushes
    fully before the stream closes).
    """
    seq = after
    while True:
        batch = await svc.run_events(run_id, after_sequence=seq)
        if not batch:
            return
        for event in batch:
            seq = event.sequence
            yield event


async def _run_event_generator(run_id: str, request: Request, svc: Any, start_after: int) -> AsyncGenerator[str]:
    """Stream a run's durable trace as SSE, resuming from ``start_after``.

    Emits ``connected``, then the trace in ``rowid`` order (snapshot + live tail),
    ``heartbeat`` while idle, and ``stream_closed`` once the run reaches a terminal
    ``RunStatus``. The terminal record is persisted only AFTER every event is
    committed, so a final drain on terminal detection can never lose a late event.
    """
    last_seq = start_after
    idle = 0.0
    yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'run_id': run_id})}\n\n"

    while True:
        if await request.is_disconnected():
            return
        emitted = False
        async for event in _drain(svc, run_id, last_seq):
            if await request.is_disconnected():
                return
            yield _frame(event)
            last_seq = event.sequence
            emitted = True

        rec = await svc.get_run(run_id)
        if rec is None or rec.status in TERMINAL_RUN_STATUSES:
            # Final sweep: catch anything written between the last drain and the
            # terminal transition (safe — all events precede the terminal record).
            async for event in _drain(svc, run_id, last_seq):
                yield _frame(event)
                last_seq = event.sequence
            status = rec.status if rec is not None else "unknown"
            yield f"event: stream_closed\ndata: {json.dumps({'reason': 'run_terminal', 'status': status})}\n\n"
            return

        idle = 0.0 if emitted else idle + _POLL_INTERVAL_SECONDS
        if idle >= _HEARTBEAT_SECONDS:
            yield f"event: heartbeat\ndata: {json.dumps({'last_sequence': last_seq})}\n\n"
            idle = 0.0
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request, atlas: Atlas = Depends(get_atlas)) -> StreamingResponse:
    """Stream a run's live trace via SSE — the run console's data feed.

    Sends ``connected``, then every persisted lifecycle/tool event in order (a
    snapshot for a finished run, a live tail for one still ``running``), heartbeats
    while idle, and ``stream_closed`` on terminal status. Resumable via the
    ``Last-Event-ID`` header (the last ``rowid`` seen). 404 if the run is unknown.
    """
    svc = _service(atlas)
    # Validate BEFORE returning the StreamingResponse: an error inside the body
    # generator lands after the response head is on the wire (past CORS), so the
    # browser would see a truncated stream instead of a real 404.
    if await svc.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    start_after = 0
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id is not None:
        try:
            start_after = int(last_event_id)
        except ValueError:
            start_after = 0

    return StreamingResponse(
        _run_event_generator(run_id, request, svc, start_after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
