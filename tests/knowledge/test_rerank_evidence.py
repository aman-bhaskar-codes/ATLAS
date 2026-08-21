"""Reranker + evidence pipeline tests (§26-33): features, selection, conflicts, claims."""

from __future__ import annotations

from dataclasses import replace

from atlas.knowledge.domain import (
    Claim,
    ClaimStatus,
    Evidence,
    FabricChunk,
    KnowledgeDocument,
    SourceType,
)
from atlas.knowledge.evidence import (
    ClaimExtractor,
    ClaimVerifier,
    ContradictionDetector,
    EvidenceSelector,
)
from atlas.knowledge.reranking import FeatureReranker, RerankWeights
from atlas.knowledge.retrieval import Candidate
from tests.fakes import FakeClock, FakeIdGen
from tests.knowledge.harness import NOW


def _doc(doc_id: str, *, authority: float = 0.5, freshness: float = 0.5) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=doc_id,
        source_id=doc_id,
        source_type=SourceType.WEB_PAGE,
        title=doc_id,
        uri=f"https://example.com/{doc_id}",
        retrieved_at=NOW,
        authority=authority,
        freshness=freshness,
    )


def _cand(doc_id: str, text: str, *, rrf: float, authority: float = 0.5, freshness: float = 0.5) -> Candidate:
    doc = _doc(doc_id, authority=authority, freshness=freshness)
    chunk = FabricChunk(chunk_id=f"chk_{doc_id}", document_id=doc_id, content=text)
    return Candidate(chunk=chunk, document=doc, rrf_score=rrf)


# ── FeatureReranker ─────────────────────────────────────────────────────
def test_reranker_lifts_high_authority_fresh_sources() -> None:
    low = _cand("low", "steam engine history overview text", rrf=1.0, authority=0.3, freshness=0.3)
    high = _cand("high", "steam engine history overview text", rrf=0.95, authority=1.0, freshness=1.0)
    out = FeatureReranker().rerank("steam engine history", [low, high], k=5)
    assert out[0].document.document_id == "high"


def test_reranker_respects_relevance_weight() -> None:
    strong = _cand("a", "totally different unrelated content here", rrf=1.0, authority=0.5)
    weak = _cand("b", "steam engine steam engine steam engine", rrf=0.05, authority=0.5)
    weights = RerankWeights(relevance=0.9, authority=0.0, freshness=0.0, overlap=0.1)
    out = FeatureReranker(weights).rerank("steam engine", [strong, weak], k=5)
    assert out[0].document.document_id == "a"


def test_reranker_penalizes_same_document_repeats() -> None:
    c1 = _cand("same", "steam engines first chunk text", rrf=1.0)
    c2 = replace(_cand("same", "steam engines second chunk text", rrf=0.9), chunk=FabricChunk(
        chunk_id="chk_same_2", document_id="same", content="steam engines second chunk text"
    ))
    other = _cand("other", "steam engines from another source", rrf=0.85)
    out = FeatureReranker().rerank("steam engines", [c1, c2, other], k=3)
    ids = [c.document.document_id for c in out]
    assert ids[0] == "same"
    # diversity: the second slot should prefer the other document
    assert ids[1] == "other"


def test_reranker_empty_input() -> None:
    assert FeatureReranker().rerank("x", [], k=5) == []


# ── EvidenceSelector ────────────────────────────────────────────────────
def test_selector_picks_best_sentence_as_quote() -> None:
    text = (
        "Intro filler sentence that says nothing about the topic at hand. "
        "The bamboo steamer cooks vegetables gently over simmering water. "
        "Closing remarks about unrelated matters in the kitchen today."
    )
    cand = _cand("doc1", text, rrf=0.5)
    selector = EvidenceSelector(FakeIdGen(), FakeClock(NOW))
    evidence = selector.select("bamboo steamer vegetables", [cand])
    assert len(evidence) == 1
    assert "bamboo steamer cooks vegetables" in evidence[0].quote
    assert evidence[0].document_id == "doc1"
    assert evidence[0].hash  # pinned
    assert evidence[0].provenance["source_type"] == "web_page"


def test_selector_dedupes_identical_quotes_and_caps_count() -> None:
    text = "The steam engine transformed industry and manufacturing forever."
    cands = [
        replace(_cand(f"d{i}", text, rrf=0.5), chunk=FabricChunk(chunk_id=f"c{i}", document_id=f"d{i}", content=text))
        for i in range(4)
    ]
    selector = EvidenceSelector(FakeIdGen(), FakeClock(NOW), max_evidence=10)
    evidence = selector.select("steam engine industry", cands)
    assert len(evidence) == 1  # same quote from different docs still dedupes


def test_selector_respects_max_evidence() -> None:
    cands = [
        _cand(f"d{i}", f"Unique sentence number {i} about a distinct topic altogether.", rrf=0.5)
        for i in range(6)
    ]
    selector = EvidenceSelector(FakeIdGen(), FakeClock(NOW), max_evidence=3)
    evidence = selector.select("distinct topic", cands)
    assert len(evidence) == 3


# ── ContradictionDetector ───────────────────────────────────────────────
def _ev(doc_id: str, quote: str, ev_id: str) -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        chunk_id=f"chk_{doc_id}",
        source=SourceType.WEB_PAGE,
        quote=quote,
        retrieved_at=NOW,
    )


def test_contradiction_detected_across_documents() -> None:
    ev_a = _ev("docA", "The reactor produces 200 megawatts of power.", "ev_a")
    ev_b = _ev("docB", "The reactor produces 900 megawatts of power.", "ev_b")
    conflicts = ContradictionDetector().detect("reactor power output megawatts", [ev_a, ev_b])
    assert len(conflicts) == 1
    assert {conflicts[0].evidence_id_a, conflicts[0].evidence_id_b} == {"ev_a", "ev_b"}


def test_same_document_values_do_not_conflict_with_themselves() -> None:
    ev_a = _ev("docA", "The reactor produces 200 megawatts of power.", "ev_a")
    ev_a2 = _ev("docA", "It is rated at 900 megawatts in another sentence.", "ev_a2")
    assert ContradictionDetector().detect("reactor power megawatts", [ev_a, ev_a2]) == []


def test_small_numeric_difference_is_not_a_contradiction() -> None:
    ev_a = _ev("docA", "The trip takes 100 minutes total.", "ev_a")
    ev_b = _ev("docB", "The trip takes 110 minutes total.", "ev_b")
    assert ContradictionDetector().detect("trip minutes duration", [ev_a, ev_b]) == []


def test_single_evidence_never_contradicts() -> None:
    ev_a = _ev("docA", "The reactor produces 200 megawatts.", "ev_a")
    assert ContradictionDetector().detect("reactor", [ev_a]) == []


# ── ClaimExtractor + ClaimVerifier ──────────────────────────────────────
def test_claim_extractor_finds_factual_sentences() -> None:
    text = (
        "The engine was invented in 1712. "
        "Short. "
        "What a lovely day it is outside today, right? "
        "Watt's design is far more efficient than earlier designs."
    )
    claims = ClaimExtractor().extract(text)
    texts = [c.text for c in claims]
    assert any("1712" in t for t in texts)
    assert any("Watt" in t for t in texts)
    assert all(c.status is ClaimStatus.UNSUPPORTED for c in claims)  # unverified at extraction


def test_claim_verifier_supports_grounded_claims() -> None:
    ev = _ev("docA", "The Newcomen engine was invented in 1712 for pumping water.", "ev_a")
    claims = [
        Claim(claim_id="c1", text="The Newcomen engine was invented in 1712 for pumping water."),
        Claim(claim_id="c2", text="Steam engines were used on the moon base in 1999."),
    ]
    verified = ClaimVerifier().verify(claims, [ev], [])
    by_id = {c.claim_id: c for c in verified}
    assert by_id["c1"].status is ClaimStatus.SUPPORTED
    assert by_id["c1"].evidence_ids == ("ev_a",)
    assert by_id["c2"].status is ClaimStatus.UNSUPPORTED


def test_claim_verifier_marks_disputed_when_evidence_conflicts() -> None:
    ev_a = _ev("docA", "The reactor produces 200 megawatts of power output.", "ev_a")
    ev_b = _ev("docB", "The reactor produces 900 megawatts of power output.", "ev_b")
    contradictions = ContradictionDetector().detect("reactor megawatts power", [ev_a, ev_b])
    assert contradictions
    claim = Claim(claim_id="c1", text="The reactor produces 200 megawatts of power output.")
    verified = ClaimVerifier().verify([claim], [ev_a, ev_b], contradictions)
    assert verified[0].status is ClaimStatus.DISPUTED
