"""Research REST API routes (Phase 1, Slice R2) — start, fetch, list, follow up.

A thin projection over ``ResearchService`` (orchestration/research), exactly like
``routes_agent.py`` is over ``AgentRunService``. The service owns all state,
grounding, and governance; these handlers only translate HTTP <-> contracts. Every
research action the service drives still flows through the SafetyEngine funnel via
the governed ``knowledge`` tool — nothing here bypasses policy or invents a second
retrieval path.

Mounted only when ``config.research.enabled`` and the service was built; otherwise
the routes return 503 (subsystem disabled), mirroring the agent/ADE/voice surfaces.
Runs execute synchronously in R2: the response IS the full persisted, grounded
session. Live streaming of a backgrounded run (SSE) is the next slice (R3).
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
from atlas.orchestration.research.records import (
    TERMINAL_RESEARCH_STATUSES,
    ResearchAnswer,
    ResearchSessionRecord,
    ResearchSource,
)
from atlas.orchestration.research.service import ResearchError

router = APIRouter(prefix="/api/v1/research", tags=["research"])

# Live-stream cadence: how often to sweep the durable phase trace for new events, and
# how long to wait idle before a keep-alive heartbeat. The store commits synchronously,
# so a short poll surfaces events promptly without a wake queue.
_POLL_INTERVAL_SECONDS = 0.5
_HEARTBEAT_SECONDS = 15.0


def _service(atlas: Atlas) -> Any:
    """The ResearchService, or 503 when the research surface is disabled/unavailable."""
    svc = getattr(atlas, "research", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="research subsystem disabled")
    return svc


# ── Request/response models ──────────────────────────────────────────── #
class StartSessionRequest(BaseModel):
    question: str
    mode: str | None = None
    background: bool = False


class FollowUpRequest(BaseModel):
    question: str
    mode: str | None = None
    background: bool = False


class SessionResponse(BaseModel):
    """A full research session: metadata + the grounded answer + source rail + trace.

    ``answer`` is ``None`` for a FAILED session (a denied/halted dispatch or tool
    error) — the ``error`` carries why. An unanswered answer (``answered=false``) is
    an HONEST refusal, never a fabricated result (§54/§69).
    """

    session_id: str
    status: str
    question: str
    mode: str
    correlation_id: str
    parent_session_id: str | None = None
    answer: ResearchAnswer | None = None
    sources: list[ResearchSource] = []
    stop_reason: str = ""
    total_rounds: int = 0
    total_discovered: int = 0
    open_questions: int = 0
    error: str | None = None
    created_ts: str
    updated_ts: str


class SessionSummary(BaseModel):
    """A compact session row for lists — no answer body or source rail."""

    session_id: str
    status: str
    question: str
    mode: str
    parent_session_id: str | None = None
    answered: bool
    source_count: int
    created_ts: str
    updated_ts: str


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


def _to_response(rec: ResearchSessionRecord) -> SessionResponse:
    return SessionResponse(
        session_id=rec.session_id,
        status=rec.status,
        question=rec.question,
        mode=rec.mode,
        correlation_id=rec.correlation_id,
        parent_session_id=rec.parent_session_id,
        answer=rec.answer,
        sources=list(rec.sources),
        stop_reason=rec.stop_reason,
        total_rounds=rec.total_rounds,
        total_discovered=rec.total_discovered,
        open_questions=rec.open_questions,
        error=rec.error,
        created_ts=rec.created_ts,
        updated_ts=rec.updated_ts,
    )


def _to_summary(rec: ResearchSessionRecord) -> SessionSummary:
    return SessionSummary(
        session_id=rec.session_id,
        status=rec.status,
        question=rec.question,
        mode=rec.mode,
        parent_session_id=rec.parent_session_id,
        answered=rec.answer.answered if rec.answer else False,
        source_count=len(rec.sources),
        created_ts=rec.created_ts,
        updated_ts=rec.updated_ts,
    )


# ── Routes ───────────────────────────────────────────────────────────── #
@router.post("/sessions", response_model=SessionResponse)
async def start_session(req: StartSessionRequest, atlas: Atlas = Depends(get_atlas)) -> SessionResponse:
    """Run one governed research question and return its persisted session.

    Synchronous by default: blocks until the grounded answer lands, and the response
    IS the persisted trace. When ``background`` is set, returns immediately with a
    ``running`` stub (``answer`` null) that is addressable and streamable at once —
    poll ``GET /sessions/{id}`` or stream it until terminal. A denied/halted dispatch
    or a tool error is never an HTTP error — it comes back as a session whose
    ``status`` is ``failed``. An unknown ``mode`` or empty question is a 400.
    """
    svc = _service(atlas)
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    try:
        if req.background:
            rec = await svc.start_session_background(req.question, mode=req.mode)
        else:
            rec = await svc.start_session(req.question, mode=req.mode)
    except ResearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(rec)


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(limit: int = 50, atlas: Atlas = Depends(get_atlas)) -> SessionListResponse:
    """List research sessions (most-recently-updated first)."""
    svc = _service(atlas)
    sessions = await svc.list_sessions(limit=limit)
    return SessionListResponse(sessions=[_to_summary(s) for s in sessions])


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, atlas: Atlas = Depends(get_atlas)) -> SessionResponse:
    """Fetch a single research session's full grounded result. 404 if unknown."""
    svc = _service(atlas)
    rec = await svc.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"research session {session_id!r} not found")
    return _to_response(rec)


@router.post("/sessions/{session_id}/follow-up", response_model=SessionResponse)
async def follow_up(session_id: str, req: FollowUpRequest, atlas: Atlas = Depends(get_atlas)) -> SessionResponse:
    """Ask a follow-up against ``session_id`` — a fresh, linked session.

    The new session carries ``parent_session_id=session_id`` for thread lineage and
    inherits the parent's mode unless overridden. 404 if the parent is unknown; 400
    for an empty question or unknown mode.
    """
    svc = _service(atlas)
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    try:
        if req.background:
            rec = await svc.follow_up_background(session_id, req.question, mode=req.mode)
        else:
            rec = await svc.follow_up(session_id, req.question, mode=req.mode)
    except ResearchError as exc:
        # An unknown parent session is a 404; an invalid mode is a client error too,
        # but both surface as ResearchError — disambiguate on the message.
        status = 404 if "unknown research session" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _to_response(rec)


# ── Live research stream (SSE) ───────────────────────────────────────── #
def _frame(event: Any) -> str:
    """Render one phase event as an SSE ``research_event`` frame (``id:`` = cursor)."""
    return f"id: {event.sequence}\nevent: research_event\ndata: {event.model_dump_json()}\n\n"


async def _drain(svc: Any, session_id: str, after: int) -> AsyncIterator[Any]:
    """Yield every phase event past ``after``, walking the cursor until the trace is
    empty. Looping until a batch comes back empty drains a trace of any size."""
    seq = after
    while True:
        batch = await svc.session_events(session_id, after_sequence=seq)
        if not batch:
            return
        for event in batch:
            seq = event.sequence
            yield event


async def _run_event_generator(
    session_id: str, request: Request, svc: Any, start_after: int
) -> AsyncGenerator[str]:
    """Stream a session's durable phase trace as SSE, resuming from ``start_after``.

    Emits ``connected``, then the REAL phase trace in ``sequence`` order (snapshot +
    live tail), ``heartbeat`` while idle, and ``stream_closed`` once the session
    reaches a terminal ``ResearchStatus``. The terminal record is persisted before
    its terminal phase event, so a final drain on terminal detection never loses a
    late event.
    """
    last_seq = start_after
    idle = 0.0
    yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'session_id': session_id})}\n\n"

    while True:
        if await request.is_disconnected():
            return
        emitted = False
        async for event in _drain(svc, session_id, last_seq):
            if await request.is_disconnected():
                return
            yield _frame(event)
            last_seq = event.sequence
            emitted = True

        rec = await svc.get_session(session_id)
        if rec is None or rec.status in TERMINAL_RESEARCH_STATUSES:
            # Final sweep: catch anything written between the last drain and the
            # terminal transition (safe — all events precede/accompany the terminal phase).
            async for event in _drain(svc, session_id, last_seq):
                yield _frame(event)
                last_seq = event.sequence
            status = rec.status if rec is not None else "unknown"
            yield f"event: stream_closed\ndata: {json.dumps({'reason': 'session_terminal', 'status': status})}\n\n"
            return

        idle = 0.0 if emitted else idle + _POLL_INTERVAL_SECONDS
        if idle >= _HEARTBEAT_SECONDS:
            yield f"event: heartbeat\ndata: {json.dumps({'last_sequence': last_seq})}\n\n"
            idle = 0.0
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request, atlas: Atlas = Depends(get_atlas)) -> StreamingResponse:
    """Stream a research session's live phase trace via SSE — the Perplexity feed.

    Sends ``connected``, then every persisted REAL phase event in order (a snapshot
    for a finished session, a live tail for one still ``running``), heartbeats while
    idle, and ``stream_closed`` on terminal status. Resumable via the ``Last-Event-ID``
    header (the last ``sequence`` seen). 404 if the session is unknown.
    """
    svc = _service(atlas)
    # Validate BEFORE returning the StreamingResponse: an error inside the body
    # generator lands after the response head is on the wire (past CORS), so the
    # browser would see a truncated stream instead of a real 404.
    if await svc.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"research session {session_id!r} not found")

    start_after = 0
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id is not None:
        try:
            start_after = int(last_event_id)
        except ValueError:
            start_after = 0

    return StreamingResponse(
        _run_event_generator(session_id, request, svc, start_after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
