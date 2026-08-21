"""Browser→Fabric bridge (§6-10): browsed pages enter the SAME pipeline.

The old path extracted articles in the browser and threw them away (§1 dead
end). Here every crawled page becomes a KnowledgeDocument via the canonical
IngestionPipeline — parsed, injection-scanned, chunked, indexed — so browser
research is retrievable, verifiable, and citable exactly like local docs.
"""

from __future__ import annotations

from typing import Any, Protocol

from atlas.infra.clock import Clock
from atlas.infra.logging import get_logger
from atlas.knowledge.domain import IngestionJob, IngestionState, SourceType
from atlas.knowledge.ingestion import IngestionPipeline

_log = get_logger("atlas.knowledge.browser_bridge")


class ArticleLike(Protocol):
    title: str
    text: str
    markdown: str
    byline: str


class ResearchResultLike(Protocol):
    seed_url: str
    articles: list[ArticleLike]
    visited_urls: set[str]
    confidence: float


class BrowserBridge:
    """Feeds browser extraction output into the canonical ingestion path."""

    def __init__(self, pipeline: IngestionPipeline, clock: Clock) -> None:
        self._pipeline = pipeline
        self._clock = clock

    async def ingest_article(self, article: ArticleLike, *, url: str, session_id: str = "") -> IngestionJob:
        content = article.markdown or article.text
        if not content.strip():
            _log.warning("browser_bridge.empty_article", event_type="knowledge", url=url)
            now = self._clock.now()
            return IngestionJob(
                job_id=f"job_bridge_{id(article)}",
                source=url,
                source_type=SourceType.BROWSER_PAGE,
                state=IngestionState.FAILED,
                error="article had no extractable text",
                created_ts=now,
                updated_ts=now,
            )
        return await self._pipeline.ingest(
            source_id=url,
            source_type=SourceType.BROWSER_PAGE,
            content=content,
            title=article.title or url,
            uri=url,
            content_type="text/markdown" if article.markdown else "text/plain",
            author=article.byline,
            metadata={"browser_session": session_id},
            provenance={"pipe": "browser", "session_id": session_id},
        )

    async def ingest_research_result(self, result: ResearchResultLike, *, session_id: str = "") -> list[IngestionJob]:
        """All articles of a crawl round → fabric. Failures never abort the batch."""
        jobs: list[IngestionJob] = []
        for article in result.articles:
            url = _article_url(article, fallback=result.seed_url)
            try:
                jobs.append(await self.ingest_article(article, url=url, session_id=session_id))
            except Exception as exc:
                _log.warning("browser_bridge.ingest_failed", event_type="knowledge", url=url, error=repr(exc))
        ready = sum(1 for j in jobs if j.state is IngestionState.READY)
        _log.info(
            "browser_bridge.batch",
            event_type="knowledge",
            articles=len(result.articles),
            ready=ready,
            seed=result.seed_url,
        )
        return jobs


def _article_url(article: Any, *, fallback: str) -> str:
    prov = getattr(article, "provenance", None)
    url = getattr(prov, "uri", "") or getattr(prov, "url", "") or getattr(article, "url", "")
    return url or fallback
