"""Research REST surface — thin projection over ResearchService (Slice R2).

A fake service (no knowledge fabric, no store, no network) wired onto
``app.state.atlas`` as a SimpleNamespace — the routes touch only ``atlas.research``,
so a full Atlas build is unnecessary. The point is the HTTP SEAM, not the grounded
run (locked in tests/orchestration/test_research_service.py):
  * a disabled subsystem (research=None) is 503; an empty question is 400;
  * a synchronous session returns its full grounded answer + source rail;
  * a FAILED session serializes with a null ``answer`` and a real ``error``;
  * a follow-up links to its parent; an unknown parent is 404; a bad mode is 400.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api.dependencies import get_atlas
from atlas.interfaces.api.routes_research import router as research_router
from atlas.orchestration.research.records import (
    ResearchAnswer,
    ResearchCitation,
    ResearchEvent,
    ResearchSessionRecord,
    ResearchSource,
)
from atlas.orchestration.research.service import ResearchError

BASE = "/api/v1/research"


def _completed(session_id: str, question: str, *, parent: str | None = None) -> ResearchSessionRecord:
    return ResearchSessionRecord(
        session_id=session_id,
        correlation_id="cid",
        parent_session_id=parent,
        question=question,
        mode="deep_research",
        answer=ResearchAnswer(
            text="a grounded answer [1].",
            answered=True,
            confidence=0.8,
            mode="deep",
            citations=(ResearchCitation(index=1, title="T", uri="https://example.com", quote="q"),),
        ),
        sources=(ResearchSource(title="T", uri="https://example.com", quote="q"),),
        stop_reason="converged",
        total_rounds=2,
        created_ts="2026-09-26T00:00:00+00:00",
        updated_ts="2026-09-26T00:00:01+00:00",
    )


def _failed(session_id: str, question: str) -> ResearchSessionRecord:
    return ResearchSessionRecord(
        session_id=session_id,
        correlation_id="cid",
        question=question,
        mode="deep_research",
        status="failed",
        error="denied (tier NOTIFY): web egress blocked",
        created_ts="2026-09-26T00:00:00+00:00",
        updated_ts="2026-09-26T00:00:00+00:00",
    )


class FakeResearchService:
    """Records seam calls and returns canned records. ``start_session`` keys off the
    question so one fake exercises every mapping: ``"fail"`` → a FAILED session,
    ``"bad mode"`` → a ResearchError (400); ``follow_up`` keys off the parent id:
    ``"missing"`` → unknown-session ResearchError (404)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def start_session(self, question: str, *, mode: str | None = None) -> ResearchSessionRecord:
        self.calls.append(("start_session", question))
        if question == "bad mode":
            raise ResearchError("unknown research mode 'nonsense'")
        if question == "fail":
            return _failed("sess-fail", question)
        return _completed("sess-sync", question)

    async def start_session_background(self, question: str, *, mode: str | None = None) -> ResearchSessionRecord:
        self.calls.append(("start_session_background", question))
        return ResearchSessionRecord(
            session_id="sess-bg",
            correlation_id="cid",
            question=question,
            mode="deep_research",
            status="running",
            created_ts="2026-09-26T00:00:00+00:00",
            updated_ts="2026-09-26T00:00:00+00:00",
        )

    async def follow_up(self, session_id: str, question: str, *, mode: str | None = None) -> ResearchSessionRecord:
        self.calls.append(("follow_up", session_id))
        if session_id == "missing":
            raise ResearchError(f"cannot follow up unknown research session {session_id!r}")
        return _completed("sess-follow", question, parent=session_id)

    async def get_session(self, session_id: str) -> ResearchSessionRecord | None:
        return None if session_id == "missing" else _completed(session_id, "prior")

    async def session_events(self, session_id: str, *, after_sequence: int = 0) -> tuple[ResearchEvent, ...]:
        events = (
            ResearchEvent(sequence=1, session_id=session_id, phase="started", ts="t"),
            ResearchEvent(sequence=2, session_id=session_id, phase="sources_found", payload={"count": 1}, ts="t"),
            ResearchEvent(sequence=3, session_id=session_id, phase="completed", payload={"status": "completed"}, ts="t"),
        )
        return tuple(e for e in events if e.sequence > after_sequence)

    async def list_sessions(self, *, limit: int = 50) -> tuple[ResearchSessionRecord, ...]:
        return (_completed("sess-sync", "a"), _failed("sess-fail", "b"))


def _client(*, service: object | None) -> TestClient:
    app = FastAPI()
    app.include_router(research_router, prefix="")  # router carries its own /api/v1/research prefix
    app.dependency_overrides[get_atlas] = lambda: SimpleNamespace(research=service)
    return TestClient(app)


def test_disabled_subsystem_is_503() -> None:
    resp = _client(service=None).post(f"{BASE}/sessions", json={"question": "hi"})
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"]


def test_empty_question_is_400() -> None:
    resp = _client(service=FakeResearchService()).post(f"{BASE}/sessions", json={"question": "   "})
    assert resp.status_code == 400


def test_unknown_mode_is_400() -> None:
    resp = _client(service=FakeResearchService()).post(
        f"{BASE}/sessions", json={"question": "bad mode", "mode": "nonsense"}
    )
    assert resp.status_code == 400


def test_synchronous_session_returns_grounded_answer() -> None:
    svc = FakeResearchService()
    resp = _client(service=svc).post(f"{BASE}/sessions", json={"question": "what is RAG?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["answer"]["answered"] is True
    assert body["answer"]["text"].startswith("a grounded answer")
    assert len(body["answer"]["citations"]) == 1
    assert len(body["sources"]) == 1
    assert body["total_rounds"] == 2
    assert ("start_session", "what is RAG?") in svc.calls


def test_failed_session_serializes_with_null_answer_and_error() -> None:
    body = _client(service=FakeResearchService()).post(f"{BASE}/sessions", json={"question": "fail"}).json()
    assert body["status"] == "failed"
    assert body["answer"] is None
    assert "denied" in body["error"]


def test_list_returns_compact_rows() -> None:
    body = _client(service=FakeResearchService()).get(f"{BASE}/sessions").json()
    rows = {r["session_id"]: r for r in body["sessions"]}
    assert rows["sess-sync"]["answered"] is True and rows["sess-sync"]["source_count"] == 1
    # the failed row degrades to answered=false / no sources rather than 500-ing
    assert rows["sess-fail"]["answered"] is False and rows["sess-fail"]["source_count"] == 0


def test_get_unknown_session_is_404() -> None:
    resp = _client(service=FakeResearchService()).get(f"{BASE}/sessions/missing")
    assert resp.status_code == 404


def test_follow_up_links_to_parent() -> None:
    svc = FakeResearchService()
    resp = _client(service=svc).post(f"{BASE}/sessions/sess-sync/follow-up", json={"question": "more?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent_session_id"] == "sess-sync"
    assert ("follow_up", "sess-sync") in svc.calls


def test_follow_up_unknown_parent_is_404() -> None:
    resp = _client(service=FakeResearchService()).post(f"{BASE}/sessions/missing/follow-up", json={"question": "q"})
    assert resp.status_code == 404


def test_background_start_returns_running_stub() -> None:
    svc = FakeResearchService()
    resp = _client(service=svc).post(f"{BASE}/sessions", json={"question": "stream me", "background": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["answer"] is None
    assert ("start_session_background", "stream me") in svc.calls


def test_stream_unknown_session_is_404() -> None:
    resp = _client(service=FakeResearchService()).get(f"{BASE}/sessions/missing/stream")
    assert resp.status_code == 404


def test_stream_emits_connected_phase_frames_and_closes() -> None:
    resp = _client(service=FakeResearchService()).get(f"{BASE}/sessions/sess-x/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: connected" in body
    # Each real phase event is framed with its sequence as the SSE id.
    assert "event: research_event" in body
    assert "id: 1" in body and "id: 3" in body
    assert '"phase":"sources_found"' in body
    # Terminal session closes the stream.
    assert "event: stream_closed" in body
    assert '"status":"completed"' in body


def test_stream_resumes_from_last_event_id() -> None:
    resp = _client(service=FakeResearchService()).get(
        f"{BASE}/sessions/sess-x/stream", headers={"Last-Event-ID": "2"}
    )
    assert resp.status_code == 200
    body = resp.text
    # Resuming past sequence 2 replays only the strict tail (event 3), never 1 or 2.
    assert "id: 3" in body
    assert "id: 1\n" not in body and "id: 2\n" not in body
