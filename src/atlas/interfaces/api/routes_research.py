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

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from atlas.app import Atlas
from atlas.interfaces.api.dependencies import get_atlas
from atlas.orchestration.research.records import (
    ResearchAnswer,
    ResearchSessionRecord,
    ResearchSource,
)
from atlas.orchestration.research.service import ResearchError

router = APIRouter(prefix="/api/v1/research", tags=["research"])


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


class FollowUpRequest(BaseModel):
    question: str
    mode: str | None = None


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
    """Run one governed research question and return its full persisted session.

    Synchronous in R2: blocks until the grounded answer lands, and the response IS
    the persisted trace. A denied/halted dispatch or a tool error is never an HTTP
    error — it comes back as a session whose ``status`` is ``failed``. An unknown
    ``mode`` or empty question is a 400.
    """
    svc = _service(atlas)
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    try:
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
        rec = await svc.follow_up(session_id, req.question, mode=req.mode)
    except ResearchError as exc:
        # An unknown parent session is a 404; an invalid mode is a client error too,
        # but both surface as ResearchError — disambiguate on the message.
        status = 404 if "unknown research session" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _to_response(rec)
