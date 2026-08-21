"""BrowserBridge tests (§6-10): crawled pages enter the canonical pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas.knowledge.browser_bridge import BrowserBridge
from atlas.knowledge.domain import IngestionState, SourceType
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.store import FabricStore
from tests.fakes import FakeClock
from tests.knowledge.harness import NOW


@dataclass
class FakeArticle:
    title: str
    text: str
    markdown: str = ""
    byline: str = ""
    url: str = ""


@dataclass
class FakeResearchResult:
    seed_url: str
    articles: list[FakeArticle] = field(default_factory=list)
    visited_urls: set[str] = field(default_factory=set)
    confidence: float = 0.8


ARTICLE_TEXT = (
    "Reciprocal rank fusion is a technique for combining multiple ranked lists. "
    "It sums reciprocal ranks so score scales do not need to be comparable."
)


async def test_ingest_article_indexes_crawled_page(pipeline: IngestionPipeline, store: FabricStore) -> None:
    bridge = BrowserBridge(pipeline, FakeClock(NOW))
    article = FakeArticle(title="RRF Explained", text=ARTICLE_TEXT, byline="A. Author", url="https://x.test/rrf")
    job = await bridge.ingest_article(article, url="https://x.test/rrf", session_id="sess_1")
    assert job.state is IngestionState.READY
    doc = await store.get_document(job.document_id or "")
    assert doc is not None
    assert doc.source_type is SourceType.BROWSER_PAGE  # same fabric, traced origin
    assert doc.title == "RRF Explained"
    assert doc.provenance["pipe"] == "browser"
    assert doc.metadata["browser_session"] == "sess_1"


async def test_markdown_preferred_over_plain_text(pipeline: IngestionPipeline, store: FabricStore) -> None:
    bridge = BrowserBridge(pipeline, FakeClock(NOW))
    article = FakeArticle(title="MD", text="plain fallback", markdown="# Heading\n\n" + ARTICLE_TEXT)
    job = await bridge.ingest_article(article, url="https://x.test/md")
    doc = await store.get_document(job.document_id or "")
    assert doc is not None
    assert doc.content_type == "text/markdown"
    assert doc.title == "MD"  # explicit extractor title wins over the markdown H1


async def test_empty_article_fails_without_raising(pipeline: IngestionPipeline) -> None:
    bridge = BrowserBridge(pipeline, FakeClock(NOW))
    job = await bridge.ingest_article(FakeArticle(title="Empty", text="  "), url="https://x.test/empty")
    assert job.state is IngestionState.FAILED
    assert "no extractable text" in job.error


async def test_batch_ingest_survives_individual_failures(
    pipeline: IngestionPipeline, store: FabricStore, retriever: HybridRetriever
) -> None:
    bridge = BrowserBridge(pipeline, FakeClock(NOW))
    result = FakeResearchResult(
        seed_url="https://x.test/seed",
        articles=[
            FakeArticle(title="Good One", text=ARTICLE_TEXT, url="https://x.test/1"),
            FakeArticle(title="Empty", text="", url="https://x.test/2"),
            FakeArticle(title="Good Two", text="Steam engines convert heat into mechanical work reliably.", url=""),
        ],
    )
    jobs = await bridge.ingest_research_result(result, session_id="sess_9")
    assert len(jobs) == 3
    states = [j.state for j in jobs]
    assert states.count(IngestionState.READY) == 2
    assert states.count(IngestionState.FAILED) == 1
    # article without its own URL falls back to the seed URL
    docs = await store.list_documents()
    assert any(d.uri == "https://x.test/seed" for d in docs)
    # and everything READY is retrievable through the same index
    found = await retriever.retrieve("reciprocal rank fusion")
    assert found.candidates


async def test_repeated_crawl_is_deduplicated(pipeline: IngestionPipeline, store: FabricStore) -> None:
    bridge = BrowserBridge(pipeline, FakeClock(NOW))
    article = FakeArticle(title="Same Page", text=ARTICLE_TEXT)
    await bridge.ingest_article(article, url="https://x.test/dup")
    await bridge.ingest_article(article, url="https://x.test/dup")
    assert len(await store.list_documents()) == 1  # content-hash dedupe (§24)
