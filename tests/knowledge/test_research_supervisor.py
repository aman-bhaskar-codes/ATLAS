"""ResearchSupervisor tests (§44, §81) — deterministic, no index, no network.

The invariant under test: the supervisor drives repeated `ResearchRunner` rounds
via the runner's own resume, enforces a budget ACROSS rounds, and stops
deterministically — goal covered, stalled (no new sources), diminishing returns,
round budget, or time budget — while reporting an honest per-round trace (§22). A
fake round runner stands in for the real runner so every stop path is exercised
without a warm index.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from atlas.knowledge.domain import FabricChunk, KnowledgeDocument, ResearchQuestionStatus, SourceType
from atlas.knowledge.research import (
    STOP_GAIN,
    ResearchBudget,
    ResearchGraph,
    ResearchOutcome,
    ResearchQuestion,
    ResearchSession,
)
from atlas.knowledge.retrieval import Candidate
from atlas.knowledge.supervisor import ResearchSupervisor, SupervisorBudget
from tests.knowledge.harness import NOW


def _cand(chunk_id: str, doc_id: str, text: str = "a fact about turbines and engines") -> Candidate:
    doc = KnowledgeDocument(
        document_id=doc_id,
        source_id=doc_id,
        source_type=SourceType.WEB_PAGE,
        title=doc_id,
        uri=f"https://ex.test/{doc_id}",
        retrieved_at=NOW,
    )
    return Candidate(
        chunk=FabricChunk(chunk_id=chunk_id, document_id=doc_id, content=text), document=doc, rrf_score=0.5
    )


def _q(qid: str, *, answered: bool = False) -> ResearchQuestion:
    return ResearchQuestion(
        question_id=qid,
        text=f"question {qid}",
        status=ResearchQuestionStatus.ANSWERED if answered else ResearchQuestionStatus.OPEN,
    )


def _session(questions: tuple[ResearchQuestion, ...], doc_ids: tuple[str, ...] = ()) -> ResearchSession:
    return ResearchSession(session_id="rs_fake", goal="g", questions=questions, document_ids=doc_ids)


class FakeRunner:
    """Returns a scripted `ResearchOutcome` per round; records how it was called.

    Each scripted outcome is a callable `(round_index) -> ResearchOutcome` so a test
    can vary discovery/gain/questions across rounds without a real index.
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self.calls: list[dict[str, Any]] = []

    async def start(
        self,
        goal: str,
        *,
        resume: bool = True,
        budget: ResearchBudget | None = None,
        rewrites: tuple[str, ...] = (),
    ) -> ResearchOutcome:
        i = len(self.calls)
        self.calls.append({"goal": goal, "resume": resume, "budget": budget, "rewrites": rewrites})
        step = self._script[min(i, len(self._script) - 1)]
        return step(i) if callable(step) else step


def _outcome(
    *,
    session: ResearchSession,
    candidates: tuple[Candidate, ...] = (),
    gains: tuple[float, ...] = (),
    discovered: int = 0,
    stop_reason: str = "question list processed",
) -> ResearchOutcome:
    return ResearchOutcome(
        session=session, candidates=candidates, stop_reason=stop_reason, gains=gains, discovered=discovered
    )


# ── empty goal is rejected ──────────────────────────────────────────────
async def test_investigate_rejects_empty_goal() -> None:
    sup = ResearchSupervisor(FakeRunner([_outcome(session=_session((_q("a"),)))]))
    with pytest.raises(ValueError, match="non-empty goal"):
        await sup.investigate("   ")


# ── goal covered: stops as soon as no open questions remain ─────────────
async def test_stops_when_all_questions_answered() -> None:
    covered = _session((_q("a", answered=True), _q("b", answered=True)), doc_ids=("doc_1",))
    runner = FakeRunner([_outcome(session=covered, candidates=(_cand("c1", "doc_1"),), gains=(0.5,), discovered=1)])
    out = await ResearchSupervisor(runner).investigate("g")
    assert out.stop_reason == "all questions answered"
    assert out.total_rounds == 1
    assert out.open_questions == 0
    assert len(runner.calls) == 1  # did not run a needless second round


# ── multi-round: compounds until covered, later rounds always resume ────
async def test_runs_multiple_rounds_until_covered() -> None:
    r0 = _outcome(
        session=_session((_q("a"), _q("b")), doc_ids=("doc_1",)),
        candidates=(_cand("c1", "doc_1"),),
        gains=(0.6,),
        discovered=1,
    )
    r1 = _outcome(
        session=_session((_q("a", answered=True), _q("b", answered=True)), doc_ids=("doc_1", "doc_2")),
        candidates=(_cand("c2", "doc_2"),),
        gains=(0.4,),
        discovered=1,
    )
    runner = FakeRunner([r0, r1])
    out = await ResearchSupervisor(runner).investigate("g", resume=False)
    assert out.total_rounds == 2
    assert out.stop_reason == "all questions answered"
    # findings are the deduped union across rounds
    assert {c.chunk.chunk_id for c in out.candidates} == {"c1", "c2"}
    assert out.total_discovered == 2
    # first round honored resume=False; the second resumed the persisted session
    assert runner.calls[0]["resume"] is False
    assert runner.calls[1]["resume"] is True


# ── stalled: no new docs AND nothing discovered → stop, don't spin ──────
async def test_stops_when_no_new_sources() -> None:
    # open questions remain but the round found nothing new: resuming would repeat.
    stalled = _outcome(session=_session((_q("a"),), doc_ids=()), candidates=(), gains=(), discovered=0)
    runner = FakeRunner([stalled])
    out = await ResearchSupervisor(runner).investigate("g")
    assert out.stop_reason == "no new sources found"
    assert out.total_rounds == 1
    assert out.candidates == ()


# ── diminishing returns: low round-mean gain for STOP_STREAK rounds ─────
async def test_stops_on_low_gain_streak() -> None:
    # each round makes a LITTLE progress (a new doc) so "stalled" never fires,
    # but mean gain stays below STOP_GAIN → diminishing-returns stop.
    low = STOP_GAIN - 0.02

    def step(i: int) -> ResearchOutcome:
        return _outcome(
            session=_session((_q("a"),), doc_ids=tuple(f"doc_{j}" for j in range(i + 1))),
            candidates=(_cand(f"c{i}", f"doc_{i}"),),
            gains=(low,),
            discovered=1,
        )

    runner = FakeRunner([step])
    out = await ResearchSupervisor(runner).investigate("g", budget=SupervisorBudget(max_rounds=6))
    assert "information gain below" in out.stop_reason
    # STOP_STREAK == 2 → stops on the second consecutive low-gain round
    assert out.total_rounds == 2


# ── round budget: never exceeds max_rounds even with steady progress ────
async def test_round_budget_caps_the_loop() -> None:
    # every round makes healthy progress and keeps a question open forever:
    # only the round ceiling can stop it.
    def step(i: int) -> ResearchOutcome:
        return _outcome(
            session=_session((_q("a"),), doc_ids=tuple(f"doc_{j}" for j in range(i + 1))),
            candidates=(_cand(f"c{i}", f"doc_{i}"),),
            gains=(0.9,),
            discovered=1,
        )

    runner = FakeRunner([step])
    out = await ResearchSupervisor(runner).investigate("g", budget=SupervisorBudget(max_rounds=3))
    assert out.total_rounds == 3
    assert "round budget exhausted" in out.stop_reason
    assert len(runner.calls) == 3


# ── time budget: a round past the wall-clock ceiling ends the loop ──────
async def test_time_budget_stops_the_loop() -> None:
    step = _outcome(
        session=_session((_q("a"),), doc_ids=("doc_1",)),
        candidates=(_cand("c1", "doc_1"),),
        gains=(0.9,),
        discovered=1,
    )
    runner = FakeRunner([step])
    # max_total_seconds=0 → the elapsed check trips after the first round.
    out = await ResearchSupervisor(runner).investigate("g", budget=SupervisorBudget(max_total_seconds=0.0))
    assert out.stop_reason == "total time budget exhausted"
    assert out.total_rounds == 1


# ── per-round budget is threaded down to the runner ─────────────────────
async def test_round_budget_is_passed_to_the_runner() -> None:
    rb = ResearchBudget(max_queries=3, max_pages=4)
    covered = _session((_q("a", answered=True),), doc_ids=("doc_1",))
    runner = FakeRunner([_outcome(session=covered, gains=(0.5,))])
    await ResearchSupervisor(runner).investigate("g", budget=SupervisorBudget(round_budget=rb))
    assert runner.calls[0]["budget"] is rb


# ── honest trace + summary (§22) ────────────────────────────────────────
async def test_round_records_and_summary_are_honest() -> None:
    g = ResearchGraph()
    g.add_node("q", "question", "q")
    g.add_node("f", "fact", "a fact", parent="q", relation="SUPPORTS")
    covered = replace(_session((_q("a", answered=True),), doc_ids=("doc_1",)), graph=g)
    runner = FakeRunner([_outcome(session=covered, candidates=(_cand("c1", "doc_1"),), gains=(0.5,), discovered=2)])
    out = await ResearchSupervisor(runner).investigate("g")
    assert len(out.rounds) == 1
    rec = out.rounds[0]
    assert rec.index == 0 and rec.facts == 1 and rec.discovered == 2 and rec.new_documents == 1
    assert rec.mean_gain == 0.5 and rec.candidates == 1
    assert "1 round(s)" in out.summary and "stopped: all questions answered" in out.summary


# ── rewrites are forwarded to the runner ────────────────────────────────
async def test_rewrites_are_forwarded() -> None:
    covered = _session((_q("a", answered=True),))
    runner = FakeRunner([_outcome(session=covered)])
    await ResearchSupervisor(runner).investigate("g", rewrites=("alt phrasing",))
    assert runner.calls[0]["rewrites"] == ("alt phrasing",)
