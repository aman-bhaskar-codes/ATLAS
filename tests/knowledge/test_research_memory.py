"""ResearchMemory deletion tests (§11, §12, §22) — deterministic, no network.

The invariant under test: a `forget()` fans out across ALL four stores that a
document write touched — SQL truth (documents/chunks/evidence), the dense vector
collection (`kc_<chunk_id>`), the BM25 lexical index, and `research_sessions`
links — and reports honest per-store counts. `dry_run` previews without mutating.
Personal trusted memory is never in scope (the coordinator only ever holds the
fabric stores), so deleting research can never delete the user's own facts (§11).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from atlas.infra.db import Database
from atlas.knowledge.bm25 import BM25Index
from atlas.knowledge.deletion import DeletionReport, DeletionScope, ResearchMemory
from atlas.knowledge.domain import (
    Evidence,
    FabricChunk,
    KnowledgeDocument,
    ResearchQuestionStatus,
    SourceType,
)
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.store import FabricStore

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


class FakeVectorStore:
    """Tracks knowledge-chunk vector deletes; can be told to fail (dead store)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.deleted: list[str] = []
        self.fail = fail

    async def delete_knowledge_chunk(self, embedding_id: str) -> None:
        if self.fail:
            raise RuntimeError("vector store unavailable")
        self.deleted.append(embedding_id)


def _doc(n: int, *, source: SourceType = SourceType.WEB_PAGE, uri: str | None = None) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=f"doc_{n}",
        source_id=f"src_{n}",
        source_type=source,
        title=f"Doc {n}",
        uri=uri or f"https://ex.test/{n}",
        retrieved_at=NOW,
        content_hash=f"hash_{n}",
    )


def _chunk(doc_id: str, n: int) -> FabricChunk:
    return FabricChunk(
        chunk_id=f"{doc_id}_c{n}",
        document_id=doc_id,
        content=f"chunk {n} of {doc_id} about turbines and engines",
        chunk_index=n,
        total_chunks=2,
        embedding_id=f"kc_{doc_id}_c{n}",
    )


def _ev(eid: str, doc_id: str, chunk_id: str) -> Evidence:
    return Evidence(
        evidence_id=eid,
        document_id=doc_id,
        chunk_id=chunk_id,
        source=SourceType.WEB_PAGE,
        quote="output is 200 megawatts",
        retrieved_at=NOW,
    )


@pytest_asyncio.fixture
async def wired(memory_db: Database) -> tuple[FabricStore, HybridRetriever, FakeVectorStore, ResearchMemory]:
    store = FabricStore(memory_db)
    vector = FakeVectorStore()
    retriever = HybridRetriever(store, BM25Index(), None, vector)
    rm = ResearchMemory(store, retriever, vector)
    return store, retriever, vector, rm


async def _seed_doc(store: FabricStore, retriever: HybridRetriever, n: int, *, chunks: int = 2, **kw: Any) -> str:
    doc = _doc(n, **kw)
    ch = [_chunk(doc.document_id, i) for i in range(chunks)]
    await store.save_document(doc, ch)
    for c in ch:
        retriever.index_chunk(c, doc)
    return doc.document_id


# ── EVIDENCE scope: annotation only, chunk/doc survive ──────────────────────
async def test_forget_evidence_leaves_chunk_and_document(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    doc_id = await _seed_doc(store, retriever, 1)
    await store.save_evidence(_ev("ev_1", doc_id, f"{doc_id}_c0"))

    report = await rm.forget(DeletionScope.EVIDENCE, "ev_1")
    assert report.evidence == 1
    assert report.chunks == 0 and report.documents == 0 and report.vectors == 0
    assert await store.evidence_id_exists("ev_1") is False
    assert await store.get_document(doc_id) is not None  # doc untouched
    assert _vector.deleted == []  # evidence delete never purges vectors


# ── CHUNK scope: SQL row + vector + lexical + evidence-on-chunk ─────────────
async def test_forget_chunk_fans_out(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    doc_id = await _seed_doc(store, retriever, 2)
    await store.save_evidence(_ev("ev_c", doc_id, f"{doc_id}_c0"))

    report = await rm.forget(DeletionScope.CHUNK, f"{doc_id}_c0")
    assert report.chunks == 1
    assert report.evidence == 1
    assert report.vectors == 1 and report.lexical == 1
    assert _vector.deleted == [f"kc_{doc_id}_c0"]
    assert await store.get_chunk(f"{doc_id}_c0") is None
    assert await store.get_chunk(f"{doc_id}_c1") is not None  # sibling survives
    assert await store.get_document(doc_id) is not None  # parent doc survives


# ── DOCUMENT scope: doc + all chunks + all evidence + vectors + unlink ─────
async def test_forget_document_fans_out_and_unlinks_sessions(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    doc_id = await _seed_doc(store, retriever, 3)
    await store.save_evidence(_ev("ev_a", doc_id, f"{doc_id}_c0"))
    await store.save_evidence(_ev("ev_b", doc_id, f"{doc_id}_c1"))
    await store.save_session(
        "sess_1",
        goal="turbine output",
        status=ResearchQuestionStatus.OPEN,
        questions=[],
        visited_urls=[],
        document_ids=[doc_id, "doc_other"],
        budget_used={},
        started_ts=NOW,
        updated_ts=NOW,
    )

    report = await rm.forget(DeletionScope.DOCUMENT, doc_id)
    assert report.documents == 1
    assert report.chunks == 2
    assert report.evidence == 2
    assert report.vectors == 2
    assert set(_vector.deleted) == {f"kc_{doc_id}_c0", f"kc_{doc_id}_c1"}
    assert await store.get_document(doc_id) is None
    assert await store.evidence_for_document(doc_id) == []
    # session no longer dangles the deleted document, but keeps the other one (§22)
    session = await store.get_session("sess_1")
    assert session is not None and session["document_ids"] == ["doc_other"]
    assert any("unlinked" in note for note in report.notes)


# ── SESSION scope: unlink-only by default, cascade when asked ──────────────
async def test_forget_session_default_keeps_documents(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    doc_id = await _seed_doc(store, retriever, 4)
    await store.save_session(
        "sess_2",
        goal="g",
        status=ResearchQuestionStatus.OPEN,
        questions=[],
        visited_urls=[],
        document_ids=[doc_id],
        budget_used={},
        started_ts=NOW,
        updated_ts=NOW,
    )
    report = await rm.forget(DeletionScope.SESSION, "sess_2")
    assert report.sessions == 1
    assert report.documents == 0  # documents NOT nuked by default
    assert await store.get_document(doc_id) is not None
    assert await store.get_session("sess_2") is None


async def test_forget_session_cascade_removes_documents(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    doc_id = await _seed_doc(store, retriever, 5)
    await store.save_session(
        "sess_3",
        goal="g",
        status=ResearchQuestionStatus.OPEN,
        questions=[],
        visited_urls=[],
        document_ids=[doc_id],
        budget_used={},
        started_ts=NOW,
        updated_ts=NOW,
    )
    report = await rm.forget(DeletionScope.SESSION, "sess_3", cascade_documents=True)
    assert report.sessions == 1
    assert report.documents == 1
    assert report.chunks == 2
    assert await store.get_document(doc_id) is None


# ── SOURCE_TYPE scope: every document of one type, not others ──────────────
async def test_forget_source_type_is_selective(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    web = await _seed_doc(store, retriever, 6, source=SourceType.WEB_PAGE)
    arxiv = await _seed_doc(store, retriever, 7, source=SourceType.ARXIV)

    report = await rm.forget(DeletionScope.SOURCE_TYPE, SourceType.WEB_PAGE.value)
    assert report.documents == 1
    assert await store.get_document(web) is None
    assert await store.get_document(arxiv) is not None  # different type survives


async def test_forget_source_type_rejects_unknown(wired: Any) -> None:
    _, _, _, rm = wired
    with pytest.raises(ValueError, match="unknown source_type"):
        await rm.forget(DeletionScope.SOURCE_TYPE, "not_a_source")


# ── URI scope: every document from one source URI ──────────────────────────
async def test_forget_uri_matches_uri_and_canonical(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    target = await _seed_doc(store, retriever, 8, uri="https://ex.test/paper")
    other = await _seed_doc(store, retriever, 9, uri="https://ex.test/other")

    report = await rm.forget(DeletionScope.URI, "https://ex.test/paper")
    assert report.documents == 1
    assert await store.get_document(target) is None
    assert await store.get_document(other) is not None


# ── ALL scope: whole research corpus wiped, lexical reset ──────────────────
async def test_forget_all_wipes_corpus(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    d1 = await _seed_doc(store, retriever, 10)
    await _seed_doc(store, retriever, 11)
    await store.save_evidence(_ev("ev_all", d1, f"{d1}_c0"))
    await store.save_session(
        "sess_all",
        goal="g",
        status=ResearchQuestionStatus.OPEN,
        questions=[],
        visited_urls=[],
        document_ids=[d1],
        budget_used={},
        started_ts=NOW,
        updated_ts=NOW,
    )

    report = await rm.forget(DeletionScope.ALL)
    assert report.documents == 2
    assert report.chunks == 4
    assert report.evidence == 1
    assert report.sessions == 1
    assert report.vectors == 4
    assert await store.all_document_ids() == []
    assert await store.count_all_sessions() == 0
    assert retriever._built is False  # lexical index reset


# ── dry_run: honest preview, zero mutation (§22) ───────────────────────────
async def test_dry_run_previews_without_mutating(wired: Any) -> None:
    store, retriever, _vector, rm = wired
    doc_id = await _seed_doc(store, retriever, 12)
    await store.save_evidence(_ev("ev_d", doc_id, f"{doc_id}_c0"))

    report = await rm.forget(DeletionScope.DOCUMENT, doc_id, dry_run=True)
    assert report.dry_run is True
    assert report.documents == 1 and report.chunks == 2 and report.evidence == 1 and report.vectors == 2
    assert "Would remove" in report.summary
    # nothing actually changed
    assert await store.get_document(doc_id) is not None
    assert _vector.deleted == []
    assert await store.evidence_id_exists("ev_d") is True


# ── vector-store down: SQL still becomes truth, failure counted honestly ───
async def test_vector_failure_is_reported_not_fatal(memory_db: Database) -> None:
    store = FabricStore(memory_db)
    vector = FakeVectorStore(fail=True)
    retriever = HybridRetriever(store, BM25Index(), None, vector)
    rm = ResearchMemory(store, retriever, vector)
    doc_id = await _seed_doc(store, retriever, 13)

    report = await rm.forget(DeletionScope.DOCUMENT, doc_id)
    assert report.documents == 1
    assert report.vectors == 0  # none purged
    assert report.vectors_failed == 2  # honestly counted
    assert "orphaned" in report.summary
    assert await store.get_document(doc_id) is None  # SQL truth still deleted


# ── missing targets: honest zero, no crash ─────────────────────────────────
async def test_forget_missing_targets_report_zero(wired: Any) -> None:
    _, _, _, rm = wired
    assert (await rm.forget(DeletionScope.EVIDENCE, "nope")).evidence == 0
    assert (await rm.forget(DeletionScope.DOCUMENT, "nope")).documents == 0
    assert (await rm.forget(DeletionScope.SESSION, "nope")).sessions == 0


async def test_missing_target_raises_for_scoped_deletes(wired: Any) -> None:
    _, _, _, rm = wired
    with pytest.raises(ValueError, match="requires a non-empty target"):
        await rm.forget(DeletionScope.DOCUMENT, "")


def test_report_summary_is_human_readable() -> None:
    r = DeletionReport(scope=DeletionScope.DOCUMENT, target="doc_x", documents=1, chunks=3, vectors=3)
    assert "Removed" in r.summary and "doc_x" in r.summary and "3 vectors" in r.summary
