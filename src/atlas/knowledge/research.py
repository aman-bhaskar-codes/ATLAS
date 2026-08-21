"""Research fabric — bounded, resumable, graph-tracked research (§38-44, §75-82).

A research session is NOT an unbounded crawl: it owns explicit budgets
(pages / wall time / tokens / queries), a tracked question list that is
never silently dropped, and a ResearchGraph of question→fact edges so
"continue yesterday's research" can extend instead of repeat (§137).

Stopping is explicit: budget exhausted, no remaining open questions, or
information gain below threshold for N consecutive rounds. Everything is
deterministic — the fabric never needs an LLM to plan or stop research.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.knowledge.domain import ResearchQuestionStatus, content_hash
from atlas.knowledge.retrieval import Candidate, HybridRetriever, RetrievalResult
from atlas.knowledge.store import FabricStore

_log = get_logger("atlas.knowledge.research")

MAX_GRAPH_NODES = 200
MAX_QUESTIONS = 12
STOP_GAIN = 0.08  # below this, a round contributes nothing
STOP_STREAK = 2  # consecutive low-gain rounds before halting


# ── tracked questions (§76) ──────────────────────────────────────────────
@dataclass(frozen=True)
class ResearchQuestion:
    question_id: str
    text: str
    status: ResearchQuestionStatus = ResearchQuestionStatus.OPEN
    answer_summary: str = ""
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "status": self.status.value,
            "answer_summary": self.answer_summary,
            "evidence_ids": list(self.evidence_ids),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ResearchQuestion:
        return ResearchQuestion(
            question_id=str(d.get("question_id", "")),
            text=str(d.get("text", "")),
            status=ResearchQuestionStatus(d.get("status", "OPEN")),
            answer_summary=str(d.get("answer_summary", "")),
            evidence_ids=tuple(d.get("evidence_ids", ())),
        )


# ── research graph (§75) ─────────────────────────────────────────────────
@dataclass(frozen=True)
class ResearchNode:
    node_id: str
    kind: str  # "goal" | "question" | "fact"
    text: str
    depth: int = 0


@dataclass(frozen=True)
class ResearchEdge:
    src: str
    dst: str
    relation: str  # DERIVES | SUPPORTS | REFUTES


class ResearchGraph:
    """Bounded question→fact DAG. Never grows past MAX_GRAPH_NODES."""

    def __init__(self) -> None:
        self.nodes: dict[str, ResearchNode] = {}
        self.edges: list[ResearchEdge] = []

    def add_node(
        self, node_id: str, kind: str, text: str, *, parent: str | None = None, relation: str = "DERIVES"
    ) -> ResearchNode | None:
        if node_id in self.nodes or len(self.nodes) >= MAX_GRAPH_NODES:
            return self.nodes.get(node_id)
        depth = (self.nodes[parent].depth + 1) if parent and parent in self.nodes else 0
        node = ResearchNode(node_id=node_id, kind=kind, text=text[:300], depth=depth)
        self.nodes[node_id] = node
        if parent and parent in self.nodes:
            self.edges.append(ResearchEdge(src=parent, dst=node_id, relation=relation))
        return node

    def facts(self) -> list[ResearchNode]:
        return [n for n in self.nodes.values() if n.kind == "fact"]

    def max_depth(self) -> int:
        return max((n.depth for n in self.nodes.values()), default=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {"node_id": n.node_id, "kind": n.kind, "text": n.text, "depth": n.depth} for n in self.nodes.values()
            ],
            "edges": [{"src": e.src, "dst": e.dst, "relation": e.relation} for e in self.edges],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ResearchGraph:
        g = ResearchGraph()
        for n in d.get("nodes", []):
            g.nodes[n["node_id"]] = ResearchNode(
                node_id=n["node_id"], kind=n["kind"], text=n["text"], depth=n.get("depth", 0)
            )
        g.edges = [ResearchEdge(src=e["src"], dst=e["dst"], relation=e["relation"]) for e in d.get("edges", [])]
        return g


# ── budgets (§40) ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ResearchBudget:
    max_pages: int = 10
    max_seconds: float = 120.0
    max_tokens: int = 20_000
    max_queries: int = 6

    @staticmethod
    def default() -> ResearchBudget:
        return ResearchBudget()


@dataclass(frozen=True)
class StopDecision:
    stop: bool
    reason: str = ""


# ── session (§39) ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ResearchSession:
    session_id: str
    goal: str
    questions: tuple[ResearchQuestion, ...] = ()
    graph: ResearchGraph = field(default_factory=ResearchGraph)
    visited_urls: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    budget: ResearchBudget = field(default_factory=ResearchBudget.default)
    pages_used: int = 0
    queries_used: int = 0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def budget_exhausted(self) -> StopDecision:
        if self.pages_used >= self.budget.max_pages:
            return StopDecision(True, "page budget exhausted")
        if self.queries_used >= self.budget.max_queries:
            return StopDecision(True, "query budget exhausted")
        if self.elapsed() >= self.budget.max_seconds:
            return StopDecision(True, "time budget exhausted")
        return StopDecision(False)


class ResearchPlanner:
    """Deterministic planner: seed, continue/stop, information gain (§41-43)."""

    def seed_questions(self, goal: str, *, rewrites: tuple[str, ...] = ()) -> list[ResearchQuestion]:
        texts = [goal]
        texts += [r for r in rewrites if r.lower() != goal.lower()]
        # aspect probes keep multi-session research from tunneling on one phrasing
        for suffix in ("requirements and constraints", "alternatives and tradeoffs"):
            texts.append(f"{goal}: {suffix}")
        out: list[ResearchQuestion] = []
        seen: set[str] = set()
        for t in texts[:MAX_QUESTIONS]:
            norm = " ".join(t.lower().split())
            if norm in seen:
                continue
            seen.add(norm)
            out.append(ResearchQuestion(question_id=f"q_{content_hash(norm)[:12]}", text=t))
        return out

    def information_gain(self, question: str, candidate: Candidate, *, seen_texts: tuple[str, ...] = ()) -> float:
        """Novelty-weighted relevance: token overlap with the question, penalized
        by overlap with already-seen content. Deterministic, no LLM (§43)."""
        q_tokens = set(_tokens(question))
        c_tokens = _tokens(candidate.chunk.content[:800])
        if not q_tokens or not c_tokens:
            return 0.0
        overlap = len(q_tokens & c_tokens) / max(len(q_tokens), 1)
        novelty = 1.0
        for seen in seen_texts[-6:]:
            s_tokens = set(_tokens(seen[:800]))
            if s_tokens:
                dup = len(c_tokens & s_tokens) / max(len(c_tokens), 1)
                novelty = min(novelty, 1.0 - 0.8 * dup)
        return round(min(1.0, overlap * (0.4 + 0.6 * novelty)), 3)

    def should_stop(self, session: ResearchSession, *, last_gain: float, low_gain_streak: int) -> StopDecision:
        exhausted = session.budget_exhausted()
        if exhausted.stop:
            return exhausted
        if not any(q.status is ResearchQuestionStatus.OPEN for q in session.questions):
            return StopDecision(True, "no open questions remain")
        if low_gain_streak >= STOP_STREAK and last_gain < STOP_GAIN:
            return StopDecision(True, f"information gain below {STOP_GAIN} for {low_gain_streak} rounds")
        return StopDecision(False)


_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have how in is it its of on or that the this to was"
    " what when where which who will with".split()
)


def _tokens(text: str) -> set[str]:
    import re

    return {t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in _STOPWORDS and len(t) > 2}


# ── runner (§44, §81) ────────────────────────────────────────────────────
@dataclass(frozen=True)
class ResearchOutcome:
    session: ResearchSession
    candidates: tuple[Candidate, ...]
    stop_reason: str
    gains: tuple[float, ...]


class ResearchRunner:
    """Executes one bounded research round against the fabric index.

    Sources are the hybrid retriever itself: anything already ingested
    (files, prior browser crawls, provider results) is a research source.
    Continuation (§137): if a prior session matches the goal, resume it and
    skip every source it already visited.
    """

    def __init__(
        self,
        retriever: HybridRetriever,
        store: FabricStore,
        ids: IdGenerator,
        clock: Clock,
        *,
        planner: ResearchPlanner | None = None,
    ) -> None:
        self._retriever = retriever
        self._store = store
        self._ids = ids
        self._clock = clock
        self._planner = planner or ResearchPlanner()

    async def start(
        self,
        goal: str,
        *,
        resume: bool = True,
        budget: ResearchBudget | None = None,
        rewrites: tuple[str, ...] = (),
    ) -> ResearchOutcome:
        session: ResearchSession | None = None
        if resume:
            prior = await self._find_prior(goal)
            if prior is not None:
                session = _resume_session(prior, budget)
                _log.info("research.resumed", event_type="knowledge", session=session.session_id, goal=goal)
        if session is None:
            questions = self._planner.seed_questions(goal, rewrites=rewrites)
            graph = ResearchGraph()
            graph.add_node("goal", "goal", goal)
            for q in questions:
                graph.add_node(q.question_id, "question", q.text, parent="goal")
            session = ResearchSession(
                session_id=f"rs_{self._ids.execution_id()}",
                goal=goal,
                questions=tuple(questions),
                graph=graph,
                budget=budget or ResearchBudget.default(),
            )
        return await self._run(session)

    async def _find_prior(self, goal: str) -> dict[str, Any] | None:
        """Match prior sessions by content-token overlap (§137).

        Continuation phrasing ("continue the research about X") must map back
        to the original goal, so we compare token sets of recent sessions and
        accept when at least half of the smaller goal's tokens recur.
        """
        tokens = _tokens(goal)
        best: dict[str, Any] | None = None
        best_overlap = 0.0
        for row in await self._store.recent_sessions(limit=50):
            prior_tokens = _tokens(str(row.get("goal", "")))
            if not tokens or not prior_tokens:
                continue
            overlap = len(tokens & prior_tokens) / min(len(tokens), len(prior_tokens))
            if overlap >= 0.5 and overlap > best_overlap:
                best, best_overlap = row, overlap
        return best

    async def _run(self, session: ResearchSession) -> ResearchOutcome:
        collected: list[Candidate] = []
        seen_texts: list[str] = []
        gains: list[float] = []
        streak = 0
        stop_reason = ""

        open_qs = [q for q in session.questions if q.status is ResearchQuestionStatus.OPEN]
        for q in open_qs[: session.budget.max_queries - session.queries_used or 1]:
            session = replace(session, queries_used=session.queries_used + 1)
            result: RetrievalResult = await self._retriever.retrieve(q.text, k=20)
            fresh = [c for c in result.candidates if _source_of(c) not in session.visited_urls]
            if not fresh:
                gains.append(0.0)
                streak += 1
            else:
                best = max(
                    fresh,
                    key=lambda c: self._planner.information_gain(q.text, c, seen_texts=tuple(seen_texts)),
                )
                gain = self._planner.information_gain(q.text, best, seen_texts=tuple(seen_texts))
                gains.append(gain)
                streak = streak + 1 if gain < STOP_GAIN else 0
                session = replace(
                    session,
                    pages_used=session.pages_used + 1,
                    tokens_used=session.tokens_used + len(best.chunk.content) // 4,
                    visited_urls=(*session.visited_urls, _source_of(best)),
                    document_ids=(*session.document_ids, best.document.document_id),
                )
                session.graph.add_node(
                    f"fact_{content_hash(best.chunk.content)[:12]}",
                    "fact",
                    best.chunk.content,
                    parent=q.question_id,
                    relation="SUPPORTS",
                )
                collected.append(best)
                seen_texts.append(best.chunk.content)
                answered = replace(
                    q,
                    status=ResearchQuestionStatus.ANSWERED,
                    answer_summary=best.chunk.content[:200],
                    evidence_ids=(best.chunk.chunk_id,),
                )
                session = replace(
                    session,
                    questions=tuple(answered if x.question_id == q.question_id else x for x in session.questions),
                )

            decision = self._planner.should_stop(session, last_gain=gains[-1] if gains else 0.0, low_gain_streak=streak)
            if decision.stop:
                stop_reason = decision.reason
                break

        if not stop_reason:
            stop_reason = "question list processed"
        await self._persist(session, stop_reason)
        _log.info(
            "research.done",
            event_type="knowledge",
            session=session.session_id,
            pages=session.pages_used,
            facts=len(session.graph.facts()),
            stop=stop_reason,
        )
        return ResearchOutcome(
            session=session, candidates=tuple(collected), stop_reason=stop_reason, gains=tuple(gains)
        )

    async def _persist(self, session: ResearchSession, stop_reason: str) -> None:
        all_answered = all(q.status is ResearchQuestionStatus.ANSWERED for q in session.questions)
        status = ResearchQuestionStatus.ANSWERED if all_answered else ResearchQuestionStatus.OPEN
        now = self._clock.now()
        await self._store.save_session(
            session.session_id,
            goal=session.goal,
            status=status,
            questions=[q.to_dict() for q in session.questions],
            visited_urls=list(session.visited_urls),
            document_ids=list(session.document_ids),
            budget_used={
                "pages": float(session.pages_used),
                "queries": float(session.queries_used),
                "tokens": float(session.tokens_used),
                "elapsed_s": round(session.elapsed(), 2),
                "stop_reason": stop_reason,
            },
            started_ts=now,
            updated_ts=now,
        )


def _source_of(candidate: Candidate) -> str:
    return candidate.document.uri or candidate.document.document_id


def _resume_session(prior: dict[str, Any], budget: ResearchBudget | None) -> ResearchSession:
    questions = tuple(ResearchQuestion.from_dict(q) for q in prior.get("questions", []))
    # Re-open previously open/in-progress questions so continuation does real work.
    reopened = tuple(
        replace(q, status=ResearchQuestionStatus.OPEN)
        if q.status in (ResearchQuestionStatus.OPEN, ResearchQuestionStatus.IN_PROGRESS)
        else q
        for q in questions
    ) or (ResearchQuestion(question_id="q_resume", text=prior.get("goal", "")),)
    return ResearchSession(
        session_id=str(prior.get("session_id", "rs_resumed")),
        goal=str(prior.get("goal", "")),
        questions=reopened,
        visited_urls=tuple(prior.get("visited_urls", ())),
        document_ids=tuple(prior.get("document_ids", ())),
        budget=budget or ResearchBudget.default(),
    )
