"""Domain model tests (§3-5): canonical objects, ids, taxonomy."""

from __future__ import annotations

from datetime import UTC, datetime

from atlas.knowledge.domain import (
    AUTHORITY_FLOOR,
    PIPELINE_VERSION,
    Evidence,
    KnowledgeDocument,
    QueryRoute,
    RAGMode,
    SecurityStatus,
    SourceType,
    content_hash,
    make_chunk_id,
    make_document_id,
    make_evidence_id,
)

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def test_source_taxonomy_covers_all_fabric_sources() -> None:
    expected = {
        "local_file",
        "document",
        "web_page",
        "browser_page",
        "github",
        "rss",
        "arxiv",
        "semantic_scholar",
        "crossref",
        "wolfram_alpha",
        "public_api",
        "email",
        "calendar",
        "memory",
        "experience",
        "knowledge_graph",
        "user_provided",
    }
    assert {s.value for s in SourceType} == expected


def test_authority_floor_rewards_trusted_sources() -> None:
    assert AUTHORITY_FLOOR[SourceType.LOCAL_FILE] >= 0.8
    assert AUTHORITY_FLOOR[SourceType.ARXIV] >= 0.8
    assert AUTHORITY_FLOOR[SourceType.MEMORY] >= 0.6


def test_rag_modes_and_routes_are_distinct_enums() -> None:
    assert len({m.value for m in RAGMode}) == len(RAGMode)
    assert len({r.value for r in QueryRoute}) == len(QueryRoute)


def test_content_hash_is_deterministic_and_sensitive() -> None:
    assert content_hash("hello world") == content_hash("hello world")
    assert content_hash("hello world") != content_hash("hello worlds")


def test_document_id_stable_for_same_source_and_time() -> None:
    a = make_document_id(SourceType.WEB_PAGE, "https://example.com/x", NOW)
    b = make_document_id(SourceType.WEB_PAGE, "https://example.com/x", NOW)
    assert a == b
    assert a.startswith("doc_")
    later = NOW.replace(second=1)
    assert make_document_id(SourceType.WEB_PAGE, "https://example.com/x", later) != a


def test_chunk_and_evidence_ids_are_unique_per_content() -> None:
    c1 = make_chunk_id("doc_x", 0, "first text")
    c2 = make_chunk_id("doc_x", 1, "second text")
    assert c1 != c2 and c1.startswith("chk_")
    e1 = make_evidence_id("doc_x", "quote one")
    e2 = make_evidence_id("doc_x", "quote two")
    assert e1 != e2 and e1.startswith("ev_")


def test_pipeline_version_encodes_component_versions() -> None:
    assert PIPELINE_VERSION.startswith("p") and ".c" in PIPELINE_VERSION and ".e" in PIPELINE_VERSION


def test_document_with_hash_computes_once() -> None:
    doc = KnowledgeDocument(
        document_id="doc_1",
        source_id="s",
        source_type=SourceType.WEB_PAGE,
        title="t",
        content="some content",
        retrieved_at=NOW,
    )
    hashed = doc.with_hash()
    assert hashed.content_hash == content_hash("some content")
    assert hashed.with_hash() is hashed  # idempotent


def test_evidence_with_hash_pins_document_and_quote() -> None:
    ev = Evidence(
        evidence_id="ev_1",
        document_id="doc_1",
        chunk_id="chk_1",
        source=SourceType.WEB_PAGE,
        quote="the quote",
        retrieved_at=NOW,
    )
    hashed = ev.with_hash()
    assert hashed.hash == content_hash("doc_1:the quote")
    assert hashed.with_hash() is hashed


def test_document_defaults_are_safe_and_neutral() -> None:
    doc = KnowledgeDocument(
        document_id="doc_2",
        source_id="s",
        source_type=SourceType.WEB_PAGE,
        title="t",
        retrieved_at=NOW,
    )
    assert doc.security_status is SecurityStatus.SAFE
    assert doc.authority == 0.5
    assert doc.freshness == 0.5
    assert doc.pipeline_version == PIPELINE_VERSION
