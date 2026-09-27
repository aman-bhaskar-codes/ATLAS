"""Slice R1 — the research use-case: ResearchService over the governed knowledge tool.

Proves the service composes the governed ``knowledge``-tool dispatch + the session
store into a persisted research surface: a question dispatches ONE governed
operation, its observation is projected into a typed grounded answer + source rail
+ round trace, and the ``ResearchSessionRecord`` is stored and rehydrates exactly;
get/list/follow-up work; an honest evidence-less refusal lands as ``REFUSED`` (never
a fabricated answer); a denied/errored dispatch lands as ``FAILED`` carrying the
error. Real temp SQLite store; a scripted fake dispatcher — no network, no fabric,
no registry. The fake asserts the dispatch is the governed ``knowledge`` tool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId
from atlas.orchestration.research.persistence import SqliteResearchSessionStore
from atlas.orchestration.research.records import (
    ResearchPhase,
    ResearchSessionRecord,
    ResearchStatus,
    build_result,
)
from atlas.orchestration.research.service import ResearchError, ResearchService
from atlas.orchestration.types import Action, Observation


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "atlas.db")
    await database.start()
    try:
        yield database
    finally:
        await database.stop()


class FakeIds:
    def __init__(self) -> None:
        self._n = 0

    def _next(self) -> str:
        self._n += 1
        return str(self._n)

    def task_id(self) -> TaskId:
        return TaskId(f"task-{self._next()}")

    def correlation_id(self) -> CorrelationId:
        return CorrelationId(f"cid-{self._next()}")

    def execution_id(self) -> ExecutionId:
        return ExecutionId(f"rsess-{self._next()}")


class AdvancingClock:
    def __init__(self) -> None:
        self._ticks = 0

    def now(self) -> datetime:
        self._ticks += 1
        return datetime(2026, 9, 26, tzinfo=UTC) + timedelta(seconds=self._ticks)


class FakeDispatcher:
    """Scripted dispatcher — returns a queued Observation per dispatch and records
    the Action, asserting it targets the governed ``knowledge`` tool."""

    def __init__(self, outcomes: list[Observation]) -> None:
        self._outcomes = list(outcomes)
        self.actions: list[Action] = []

    async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
        self.actions.append(action)
        assert action.tool == "knowledge"
        assert action.kind == "tool_call"
        return self._outcomes.pop(0)


# A realistic deep_research observation payload (mirrors tools/research.py shapes).
def _deep_payload(*, answered: bool = True) -> dict[str, object]:
    return {
        "session_id": "fabric-sess-1",
        "goal": "what is retrieval-augmented generation?",
        "stop_reason": "converged",
        "total_rounds": 2,
        "total_discovered": 5,
        "open_questions": 0,
        "rounds": [
            {"index": 0, "discovered": 3, "new_documents": 3, "facts": 4, "mean_gain": 0.8, "stop_reason": ""},
            {"index": 1, "discovered": 2, "new_documents": 2, "facts": 2, "mean_gain": 0.3, "stop_reason": "gain"},
        ],
        "questions": [{"text": "what is RAG?", "status": "answered", "answer_summary": "grounding LLMs"}],
        "findings": [
            {"title": "RAG paper", "uri": "https://example.com/rag", "quote": "retrieval augments generation"},
            {"title": "Survey", "uri": "https://example.com/survey", "quote": "hybrid retrieval improves grounding"},
        ],
        "answer": {
            "text": "RAG grounds a model in retrieved evidence [1][2].",
            "answered": answered,
            "confidence": 0.82 if answered else 0.0,
            "mode": "deep",
            "citations": [
                {"index": 1, "title": "RAG paper", "uri": "https://example.com/rag", "quote": "retrieval augments"},
                {"index": 2, "title": "Survey", "uri": "https://example.com/survey", "quote": "hybrid retrieval"},
            ],
            **({} if answered else {"refusal_reason": "no evidence retrieved"}),
        },
    }


def _service(db: Database, dispatcher: FakeDispatcher) -> ResearchService:
    return ResearchService(
        dispatcher=dispatcher,  # type: ignore[arg-type]
        store=SqliteResearchSessionStore(db),
        ids=FakeIds(),  # type: ignore[arg-type]
        clock=AdvancingClock(),  # type: ignore[arg-type]
    )


async def test_start_session_persists_grounded_answer(db: Database) -> None:
    obs = Observation(step=0, ok=True, content=_deep_payload(answered=True))
    dispatcher = FakeDispatcher([obs])
    svc = _service(db, dispatcher)

    record = await svc.start_session("what is retrieval-augmented generation?")

    # The governed knowledge tool was driven with the default deep_research mode.
    assert len(dispatcher.actions) == 1
    action = dispatcher.actions[0]
    assert action.tool == "knowledge"
    assert action.operation == "deep_research"
    assert action.args["goal"] == "what is retrieval-augmented generation?"

    assert record.status == ResearchStatus.COMPLETED.value
    assert record.answer is not None
    assert record.answer.answered is True
    assert record.answer.text.startswith("RAG grounds")
    assert len(record.answer.citations) == 2
    assert record.answer.citations[0].uri == "https://example.com/rag"
    assert len(record.sources) == 2
    assert record.total_rounds == 2
    assert record.stop_reason == "converged"

    # Rehydrates byte-exact from the store.
    loaded = await svc.get_session(record.session_id)
    assert loaded == record


async def test_refusal_persists_as_refused(db: Database) -> None:
    obs = Observation(step=0, ok=True, content=_deep_payload(answered=False))
    svc = _service(db, FakeDispatcher([obs]))

    record = await svc.start_session("an ungrounded question", mode="search")

    assert record.status == ResearchStatus.REFUSED.value
    assert record.answer is not None
    assert record.answer.answered is False
    assert record.answer.refusal_reason == "no evidence retrieved"


async def test_denied_dispatch_persists_as_failed(db: Database) -> None:
    obs = Observation(step=0, ok=False, error="denied (tier CONFIRM): web egress blocked")
    svc = _service(db, FakeDispatcher([obs]))

    record = await svc.start_session("blocked question")

    assert record.status == ResearchStatus.FAILED.value
    assert record.answer is None
    assert record.error is not None and "denied" in record.error


async def test_list_and_follow_up_link(db: Database) -> None:
    dispatcher = FakeDispatcher(
        [Observation(step=0, ok=True, content=_deep_payload()), Observation(step=0, ok=True, content=_deep_payload())]
    )
    svc = _service(db, dispatcher)

    first = await svc.start_session("root question")
    follow = await svc.follow_up(first.session_id, "a follow-up question")

    assert follow.parent_session_id == first.session_id
    assert follow.mode == first.mode  # inherited

    sessions = await svc.list_sessions()
    assert {s.session_id for s in sessions} == {first.session_id, follow.session_id}
    # Newest first.
    assert sessions[0].session_id == follow.session_id


async def test_empty_question_and_unknown_mode_raise(db: Database) -> None:
    svc = _service(db, FakeDispatcher([]))
    with pytest.raises(ResearchError):
        await svc.start_session("   ")
    with pytest.raises(ResearchError):
        await svc.start_session("ok", mode="nonsense")


async def test_follow_up_unknown_session_raises(db: Database) -> None:
    svc = _service(db, FakeDispatcher([]))
    with pytest.raises(ResearchError):
        await svc.follow_up("missing", "q")


async def test_background_session_streams_real_phase_trace(db: Database) -> None:
    obs = Observation(step=0, ok=True, content=_deep_payload(answered=True))
    dispatcher = FakeDispatcher([obs])
    svc = _service(db, dispatcher)

    stub = await svc.start_session_background("what is RAG?")
    # The stub is addressable and RUNNING the instant it returns; no answer yet.
    assert stub.status == ResearchStatus.RUNNING.value
    assert stub.answer is None

    # Await convergence, then the stored record is terminal + grounded.
    final = await svc.wait_for(stub.session_id)
    assert final is not None
    assert final.status == ResearchStatus.COMPLETED.value
    assert final.answer is not None and final.answer.answered is True

    # The durable phase trace is REAL and derived from the observation (§69):
    events = await svc.session_events(stub.session_id)
    phases = [e.phase for e in events]
    assert phases[0] == ResearchPhase.STARTED.value
    assert phases[1] == ResearchPhase.RETRIEVING.value
    # One ROUND event per real supervisor round (the payload has 2).
    assert phases.count(ResearchPhase.ROUND.value) == 2
    assert ResearchPhase.SOURCES_FOUND.value in phases
    assert ResearchPhase.SYNTHESIZING.value in phases
    assert ResearchPhase.CITATIONS.value in phases
    assert phases[-1] == ResearchPhase.COMPLETED.value
    # sequence is strictly monotonic — the SSE cursor.
    seqs = [e.sequence for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # sources_found carries the REAL count.
    found = next(e for e in events if e.phase == ResearchPhase.SOURCES_FOUND.value)
    assert found.payload["count"] == 2


async def test_session_events_resumable_from_cursor(db: Database) -> None:
    obs = Observation(step=0, ok=True, content=_deep_payload(answered=True))
    svc = _service(db, FakeDispatcher([obs]))
    stub = await svc.start_session_background("resume?")
    await svc.wait_for(stub.session_id)

    everything = await svc.session_events(stub.session_id)
    assert len(everything) > 3
    # A reader that resumes past the 2nd event sees only the strict tail.
    tail = await svc.session_events(stub.session_id, after_sequence=everything[1].sequence)
    assert [e.sequence for e in tail] == [e.sequence for e in everything[2:]]


async def test_background_refusal_closes_trace_with_refused(db: Database) -> None:
    obs = Observation(step=0, ok=True, content=_deep_payload(answered=False))
    svc = _service(db, FakeDispatcher([obs]))
    stub = await svc.start_session_background("ungrounded?", mode="search")
    final = await svc.wait_for(stub.session_id)

    assert final is not None and final.status == ResearchStatus.REFUSED.value
    phases = [e.phase for e in await svc.session_events(stub.session_id)]
    assert phases[-1] == ResearchPhase.REFUSED.value


async def test_background_denied_dispatch_closes_trace_with_failed(db: Database) -> None:
    obs = Observation(step=0, ok=False, error="denied (tier CONFIRM): web egress blocked")
    svc = _service(db, FakeDispatcher([obs]))
    stub = await svc.start_session_background("blocked?")
    final = await svc.wait_for(stub.session_id)

    assert final is not None and final.status == ResearchStatus.FAILED.value
    assert final.error is not None and "denied" in final.error
    phases = [e.phase for e in await svc.session_events(stub.session_id)]
    assert phases[-1] == ResearchPhase.FAILED.value


def test_build_result_handles_flat_search_payload() -> None:
    # A flat search payload IS the answer payload (no nested "answer").
    flat = {
        "text": "a grounded answer [1].",
        "answered": True,
        "confidence": 0.7,
        "mode": "hybrid",
        "citations": [{"index": 1, "title": "T", "uri": "u", "quote": "q"}],
    }
    outcome = build_result(flat)
    assert outcome.answer.answered is True
    assert outcome.answer.text == "a grounded answer [1]."
    assert len(outcome.answer.citations) == 1
    assert outcome.sources == ()


def test_build_result_folds_coverage_warning() -> None:
    payload = _deep_payload(answered=False)
    del payload["findings"]
    payload["coverage_warning"] = "No sources were retrieved."
    outcome = build_result(payload)
    assert outcome.answer.coverage_warning == "No sources were retrieved."
    assert outcome.sources == ()


def test_build_result_non_dict_is_honest_refusal() -> None:
    outcome = build_result("not a dict")
    assert outcome.answer.answered is False
    assert "no research payload" in outcome.answer.refusal_reason


def test_record_status_derivation_roundtrip() -> None:
    running = ResearchSessionRecord(session_id="s1", question="q")
    assert running.status == ResearchStatus.RUNNING.value
    # A loaded record keeps its stored status (no re-derivation).
    reloaded = ResearchSessionRecord.model_validate_json(running.model_dump_json())
    assert reloaded.status == ResearchStatus.RUNNING.value
