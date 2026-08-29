"""Research fabric tests (§38-44, §75-82): budgets, graphs, resumable sessions."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from atlas.knowledge.domain import FabricChunk, KnowledgeDocument, ResearchQuestionStatus, SourceType
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.research import (
    MAX_GRAPH_NODES,
    MAX_QUESTIONS,
    STOP_GAIN,
    STOP_STREAK,
    ResearchBudget,
    ResearchGraph,
    ResearchPlanner,
    ResearchRunner,
    ResearchSession,
)
from atlas.knowledge.retrieval import Candidate, HybridRetriever
from atlas.knowledge.store import FabricStore
from tests.fakes import FakeClock, FakeIdGen
from tests.knowledge.harness import NOW, SAMPLE_DOC, SAMPLE_DOC_B


def _cand(text: str) -> Candidate:
    doc = KnowledgeDocument(
        document_id="doc_x",
        source_id="x",
        source_type=SourceType.WEB_PAGE,
        title="x",
        uri="https://example.com/x",
        retrieved_at=NOW,
    )
    return Candidate(
        chunk=FabricChunk(chunk_id="chk_x", document_id="doc_x", content=text), document=doc, rrf_score=0.5
    )


# ── planner ─────────────────────────────────────────────────────────────
def test_seed_questions_dedupe_and_stay_bounded() -> None:
    planner = ResearchPlanner()
    questions = planner.seed_questions(
        "steam engine efficiency",
        rewrites=("steam engine efficiency", "totally different phrasing of the goal"),
    )
    texts = [q.text for q in questions]
    assert texts.count("steam engine efficiency") == 1  # dedupe
    assert len(questions) <= MAX_QUESTIONS
    assert all(q.status is ResearchQuestionStatus.OPEN for q in questions)
    assert all(q.question_id.startswith("q_") for q in questions)
    # aspect probes keep research from tunneling on one phrasing
    assert any("requirements" in t for t in texts)
    assert any("alternatives" in t for t in texts)


def test_information_gain_rewards_relevance_penalizes_repetition() -> None:
    planner = ResearchPlanner()
    relevant = _cand("Steam engine efficiency improved dramatically with condensers.")
    irrelevant = _cand("Cake recipes require flour and butter for the batter.")
    g_rel = planner.information_gain("steam engine efficiency", relevant)
    g_irr = planner.information_gain("steam engine efficiency", irrelevant)
    assert g_rel > g_irr

    seen = ("Steam engine efficiency improved dramatically with condensers.",)
    g_repeat = planner.information_gain("steam engine efficiency", relevant, seen_texts=seen)
    assert g_repeat < g_rel  # novelty penalty for already-seen content


def test_should_stop_honors_budget_questions_and_gain_streak() -> None:
    planner = ResearchPlanner()
    base = ResearchSession(
        session_id="rs_1",
        goal="g",
        questions=(planner.seed_questions("steam engines")[0],),
        budget=ResearchBudget(max_queries=2),
    )
    # budget exhausted
    assert planner.should_stop(replace(base, queries_used=2), last_gain=1.0, low_gain_streak=0).stop
    # low gain streak
    decision = planner.should_stop(base, last_gain=STOP_GAIN - 0.01, low_gain_streak=STOP_STREAK)
    assert decision.stop and "information gain" in decision.reason
    # healthy round continues
    assert not planner.should_stop(base, last_gain=0.5, low_gain_streak=0).stop


def test_should_stop_when_no_open_questions_remain() -> None:
    planner = ResearchPlanner()
    answered = planner.seed_questions("steam engines")[0]
    done = replace(answered, status=ResearchQuestionStatus.ANSWERED)
    session = ResearchSession(session_id="rs_1", goal="g", questions=(done,))
    decision = planner.should_stop(session, last_gain=1.0, low_gain_streak=0)
    assert decision.stop and "no open questions" in decision.reason


# ── graph ───────────────────────────────────────────────────────────────
def test_graph_tracks_depth_and_edges() -> None:
    g = ResearchGraph()
    g.add_node("goal", "goal", "steam engines")
    g.add_node("q1", "question", "how efficient?", parent="goal")
    g.add_node("f1", "fact", "200 megawatts", parent="q1", relation="SUPPORTS")
    assert g.max_depth() == 2
    assert len(g.facts()) == 1
    assert len(g.edges) == 2
    # duplicate node is a no-op
    g.add_node("f1", "fact", "other")
    assert len(g.nodes) == 3


def test_graph_is_bounded() -> None:
    g = ResearchGraph()
    for i in range(MAX_GRAPH_NODES + 50):
        g.add_node(f"n{i}", "fact", f"fact {i}")
    assert len(g.nodes) == MAX_GRAPH_NODES


def test_graph_roundtrips_through_dict() -> None:
    g = ResearchGraph()
    g.add_node("goal", "goal", "g")
    g.add_node("q1", "question", "q", parent="goal")
    restored = ResearchGraph.from_dict(g.to_dict())
    assert set(restored.nodes) == {"goal", "q1"}
    assert len(restored.edges) == 1


# ── runner (integration over the fabric index) ──────────────────────────
async def test_runner_executes_bounded_research_round(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    await pipeline.ingest(source_id="a", source_type=SourceType.LOCAL_FILE, content=SAMPLE_DOC)
    await pipeline.ingest(source_id="b", source_type=SourceType.LOCAL_FILE, content=SAMPLE_DOC_B)
    runner = ResearchRunner(retriever, store, FakeIdGen(), FakeClock(NOW))
    outcome = await runner.start("hybrid retrieval rank fusion evidence", budget=ResearchBudget(max_queries=6))

    assert outcome.session.queries_used >= 1
    assert outcome.stop_reason
    assert len(outcome.session.questions) <= MAX_QUESTIONS
    assert outcome.session.graph.facts()  # facts recorded in the graph
    # budget is never exceeded
    assert outcome.session.pages_used <= outcome.session.budget.max_pages
    assert outcome.session.queries_used <= outcome.session.budget.max_queries + 1
    # session persisted for continuation
    sessions = await store.recent_sessions()
    assert sessions and sessions[0]["goal"] == "hybrid retrieval rank fusion evidence"


async def test_runner_resumes_prior_session_and_skips_visited_sources(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    await pipeline.ingest(source_id="a", source_type=SourceType.LOCAL_FILE, content=SAMPLE_DOC)
    runner = ResearchRunner(retriever, store, FakeIdGen(), FakeClock(NOW))

    first = await runner.start("hybrid retrieval rank fusion evidence")
    assert first.session.document_ids

    second = await runner.start("hybrid retrieval rank fusion evidence")
    # resumed the SAME session (§137), not a fresh one
    assert second.session.session_id == first.session.session_id
    # previously visited sources are not re-collected
    assert set(first.session.document_ids).issubset(set(second.session.document_ids))
    assert second.stop_reason


async def test_runner_empty_corpus_stops_quickly(retriever: HybridRetriever, store: FabricStore) -> None:
    runner = ResearchRunner(retriever, store, FakeIdGen(), FakeClock(NOW))
    outcome = await runner.start("anything at all about obscure topics")
    assert outcome.candidates == ()
    assert outcome.stop_reason


# ── discovery hook (§44: a cold index must not be a dead end) ───────────
class FakeDiscovery:
    """Discovery double shaped like `LiveBridge.gather`.

    On success it ingests one fresh, relevant document per question — exactly
    what a provider fan-out does — so the retriever has something to find on a
    question nobody has asked before.
    """

    def __init__(
        self,
        pipeline: IngestionPipeline,
        *,
        fail: bool = False,
        hang: bool = False,
        cancel: bool = False,
    ) -> None:
        self._pipeline = pipeline
        self.queries: list[str] = []
        self.fail = fail
        self.hang = hang
        self.cancel = cancel

    async def gather(self, query: str) -> list[object]:
        self.queries.append(query)
        if self.cancel:
            raise asyncio.CancelledError
        if self.fail:
            raise RuntimeError("every provider in the fan-out failed")
        if self.hang:
            await asyncio.sleep(30)
        n = len(self.queries)
        job = await self._pipeline.ingest(
            source_id=f"discovered:{n}",
            source_type=SourceType.ARXIV,
            content=(
                f"# Autonomous adaptation study {n}\n\n"
                f"Autonomous adaptation evaluation harness number {n} measures how agents "
                f"adapt their own policies. Distinct finding {n} about adaptation benchmarks."
            ),
            title=f"Adaptation study {n}",
            uri=f"https://discovered.test/{n}",
        )
        return [job]


_GOAL = "autonomous adaptation evaluation"
_REWRITES = ("adaptation benchmarks harness", "self improving agent evaluation", "policy adaptation measurement")


async def test_discovery_turns_a_cold_index_into_findings(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    discovery = FakeDiscovery(pipeline)
    runner = ResearchRunner(retriever, store, FakeIdGen(), FakeClock(NOW), discovery=discovery)

    # index is EMPTY: without the discovery leg this returns nothing at all
    outcome = await runner.start(_GOAL, rewrites=_REWRITES)

    assert discovery.queries, "discovery was never called"
    assert outcome.discovered > 0
    assert outcome.candidates, "discovered documents never became findings"
    assert outcome.session.graph.facts()


async def test_discovery_is_bounded_independently_of_the_query_budget(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    discovery = FakeDiscovery(pipeline)
    runner = ResearchRunner(retriever, store, FakeIdGen(), FakeClock(NOW), discovery=discovery, max_discovery_queries=2)
    outcome = await runner.start(_GOAL, rewrites=_REWRITES, budget=ResearchBudget(max_queries=6))

    # Discovery is the expensive leg (N providers per call): a wide question list
    # must not turn into a crawl.
    assert len(discovery.queries) == 2
    assert outcome.discovered == 2
    assert outcome.session.queries_used >= len(discovery.queries)


async def test_discovery_can_be_disabled_entirely(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    discovery = FakeDiscovery(pipeline)
    runner = ResearchRunner(retriever, store, FakeIdGen(), FakeClock(NOW), discovery=discovery, max_discovery_queries=0)
    outcome = await runner.start(_GOAL)
    assert discovery.queries == []
    assert outcome.discovered == 0


async def test_discovery_failure_degrades_instead_of_dying(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    await pipeline.ingest(source_id="local", source_type=SourceType.LOCAL_FILE, content=SAMPLE_DOC)
    runner = ResearchRunner(retriever, store, FakeIdGen(), FakeClock(NOW), discovery=FakeDiscovery(pipeline, fail=True))
    outcome = await runner.start("hybrid retrieval rank fusion evidence")

    # A discovery failure is not a research failure (§22): the retriever still
    # runs against whatever is already indexed.
    assert outcome.discovered == 0
    assert outcome.stop_reason
    assert outcome.candidates


async def test_discovery_timeout_is_bounded_and_not_fatal(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    discovery = FakeDiscovery(pipeline, hang=True)
    runner = ResearchRunner(
        retriever,
        store,
        FakeIdGen(),
        FakeClock(NOW),
        discovery=discovery,
        discovery_timeout_s=0.01,
    )
    outcome = await runner.start(_GOAL)
    assert discovery.queries  # attempted
    assert outcome.discovered == 0  # timed out, no documents claimed
    assert outcome.stop_reason


async def test_discovery_cancellation_propagates(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    runner = ResearchRunner(
        retriever, store, FakeIdGen(), FakeClock(NOW), discovery=FakeDiscovery(pipeline, cancel=True)
    )
    with pytest.raises(asyncio.CancelledError):
        await runner.start(_GOAL)


async def test_discovered_count_is_persisted_for_continuation(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    runner = ResearchRunner(
        retriever, store, FakeIdGen(), FakeClock(NOW), discovery=FakeDiscovery(pipeline), max_discovery_queries=1
    )
    outcome = await runner.start(_GOAL)
    sessions = await store.recent_sessions()
    assert sessions[0]["budget_used"]["discovered"] == float(outcome.discovered)
    assert sessions[0]["budget_used"]["stop_reason"] == outcome.stop_reason
