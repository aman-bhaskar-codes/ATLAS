"""Query routing + synthesis tests (§11-14, §34, §50-54): plans, citations, answers."""

from __future__ import annotations

from atlas.knowledge.citations import CitationEngine
from atlas.knowledge.domain import Evidence, QueryRoute, RAGMode, SourceType
from atlas.knowledge.router import QueryPlan, QueryRouter
from atlas.knowledge.synthesis import AnswerSynthesizer, FabricAnswer, build_evidence_context
from tests.knowledge.harness import NOW, FakeSynthModel


def _ev(i: int, quote: str, *, source: SourceType = SourceType.LOCAL_FILE, authority: float = 0.8) -> Evidence:
    return Evidence(
        evidence_id=f"ev_{i}",
        document_id=f"doc_{i}",
        chunk_id=f"chk_{i}",
        source=source,
        quote=quote,
        uri=f"https://example.com/{i}",
        title=f"Doc {i}",
        retrieved_at=NOW,
        authority=authority,
        provenance={"security_status": "SAFE"},
    ).with_hash()


# ── QueryRouter ─────────────────────────────────────────────────────────
def test_router_sends_arithmetic_to_computational() -> None:
    plan = QueryRouter().route("12 * 7 + 3")
    assert plan.route is QueryRoute.COMPUTATIONAL
    assert "arithmetic" in plan.signals


def test_router_routes_personal_questions_to_private_knowledge() -> None:
    plan = QueryRouter().route("What did I tell you about my project yesterday?")
    assert plan.route is QueryRoute.PRIVATE_KNOWLEDGE


def test_router_routes_codebase_questions() -> None:
    plan = QueryRouter().route("Which module in the codebase handles chunking?")
    assert plan.route is QueryRoute.CODEBASE


def test_router_routes_research_intent() -> None:
    plan = QueryRouter().route("Research the state of local embedding models")
    assert plan.route is QueryRoute.RESEARCH


def test_router_detects_freshness_requirements() -> None:
    plan = QueryRouter().route("What is the latest release of Python?")
    assert plan.freshness_required is True
    assert plan.route is QueryRoute.LIVE


def test_router_decomposes_compound_questions() -> None:
    plan = QueryRouter().route("How does BM25 work? How does RRF fuse ranked lists?")
    assert plan.route is QueryRoute.MULTI_HOP
    assert len(plan.sub_questions) == 2
    assert len(plan.sub_questions) <= 4  # bounded (§13)


def test_router_rewrites_are_bounded_and_varied() -> None:
    plan = QueryRouter().route("What is reciprocal rank fusion?")
    assert 1 <= len(plan.rewrites) <= 4
    assert plan.rewrites[0] == "What is reciprocal rank fusion?"
    assert any(r != plan.rewrites[0] for r in plan.rewrites)


def test_router_plain_question_routes_mixed() -> None:
    plan = QueryRouter().route("Explain the water cycle in detail")
    assert plan.route is QueryRoute.MIXED


# ── CitationEngine ──────────────────────────────────────────────────────
def test_citations_are_numbered_from_evidence_order() -> None:
    evidence = [_ev(1, "first quote here"), _ev(2, "second quote here")]
    citations = CitationEngine().build(evidence)
    assert [c.index for c in citations] == [1, 2]
    assert citations[0].evidence_id == "ev_1"
    assert citations[1].uri == "https://example.com/2"


def test_invalid_markers_are_stripped_and_flagged() -> None:
    engine = CitationEngine()
    citations = engine.build([_ev(1, "only one quote")])
    cleaned, ok = engine.validate_markers("Fact A [1] and fabricated fact [9].", citations)
    assert "[9]" not in cleaned
    assert "[1]" in cleaned
    assert ok is False
    cleaned2, ok2 = engine.validate_markers("Fact A [1].", citations)
    assert ok2 is True and cleaned2 == "Fact A [1]."


def test_render_markdown_lists_sources() -> None:
    engine = CitationEngine()
    md = engine.render_markdown(engine.build([_ev(1, "the quoted words")]))
    assert "### Sources" in md
    assert "[1]" in md and "Doc 1" in md
    assert engine.render_markdown([]) == ""


# ── AnswerSynthesizer ───────────────────────────────────────────────────
_PLAN = QueryPlan(text="q", route=QueryRoute.MIXED)


async def test_no_evidence_means_honest_refusal() -> None:
    synth = AnswerSynthesizer(CitationEngine(), model=None)
    answer = await synth.synthesize("anything", _PLAN, [], [], [], mode=RAGMode.RAG)
    assert answer.answered is False
    assert answer.confidence <= 0.1
    assert answer.refusal_reason
    assert answer.citations == ()


async def test_extractive_answer_cites_every_quote() -> None:
    evidence = [_ev(1, "The engine was built in 1712."), _ev(2, "Watt improved it later.")]
    synth = AnswerSynthesizer(CitationEngine(), model=None)
    answer = await synth.synthesize("engine history", _PLAN, evidence, [], [], mode=RAGMode.RAG)
    assert answer.answered is True
    assert "[1]" in answer.text and "[2]" in answer.text
    assert len(answer.citations) == 2
    assert answer.evidence == tuple(evidence)
    assert 0.0 < answer.confidence <= 0.95


async def test_model_answer_with_bad_marker_gets_cleaned() -> None:
    evidence = [_ev(1, "The engine was built in 1712.")]
    model = FakeSynthModel("The engine was built in 1712 [1] and also on Mars [7].")
    synth = AnswerSynthesizer(CitationEngine(), model=model)
    answer = await synth.synthesize("engine", _PLAN, evidence, [], [], mode=RAGMode.RAG)
    assert "[7]" not in answer.text
    assert answer.detail["markers_valid"] is False
    assert model.calls  # model was actually consulted


async def test_model_exception_falls_back_to_extractive() -> None:
    class Broken:
        async def complete(self, system: str, prompt: str) -> str:
            raise RuntimeError("model down")

    evidence = [_ev(1, "The engine was built in 1712.")]
    synth = AnswerSynthesizer(CitationEngine(), model=Broken())
    answer = await synth.synthesize("engine", _PLAN, evidence, [], [], mode=RAGMode.RAG)
    assert answer.answered is True
    assert "[1]" in answer.text


def test_evidence_context_frames_untrusted_sources() -> None:
    web = _ev(1, "web claim about engines", source=SourceType.WEB_PAGE, authority=0.5)
    ctx = build_evidence_context([web])
    assert "UNTRUSTED CONTENT" in ctx
    assert "web_page" in ctx
    local = _ev(2, "local trusted fact about engines")
    ctx2 = build_evidence_context([local])
    assert "UNTRUSTED CONTENT" not in ctx2


def test_evidence_context_respects_char_budget() -> None:
    evidence = [_ev(i, f"quote number {i} " + "x" * 200) for i in range(50)]
    ctx = build_evidence_context(evidence, max_chars=1000)
    assert len(ctx) <= 1000


async def test_contradictions_lower_confidence_and_are_carried() -> None:
    from atlas.knowledge.domain import Contradiction

    evidence = [_ev(1, "Output is 200 megawatts."), _ev(2, "Output is 900 megawatts.")]
    conflict = Contradiction(key="output", description="sources disagree", evidence_id_a="ev_1", evidence_id_b="ev_2")
    synth = AnswerSynthesizer(CitationEngine(), model=None)
    plain = await synth.synthesize("q", _PLAN, evidence, [], [], mode=RAGMode.RAG)
    conflicted: FabricAnswer = await synth.synthesize("q", _PLAN, evidence, [conflict], [], mode=RAGMode.RAG)
    assert conflicted.confidence < plain.confidence
    assert conflicted.contradictions == (conflict,)


async def test_degradation_lowers_confidence() -> None:
    evidence = [_ev(1, "The engine was built in 1712.")]
    synth = AnswerSynthesizer(CitationEngine(), model=None)
    ok = await synth.synthesize("q", _PLAN, evidence, [], [], mode=RAGMode.RAG)
    degraded = await synth.synthesize(
        "q", _PLAN, evidence, [], [], mode=RAGMode.RAG, degraded=True, degradation_reason="lexical only"
    )
    assert degraded.confidence < ok.confidence
    assert degraded.degradation_reason == "lexical only"
