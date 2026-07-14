import pytest
from datetime import UTC, datetime

from atlas.capabilities.browser.domain.content import Article
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.research.crawler import CrawlerEngine
from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.infra.ids import CorrelationId


class FakeNavEngine:
    async def goto(self, handle, url, cid):
        pass


class FakeExtractionEngine:
    def __init__(self, articles=None):
        self.articles = articles or {}

    async def extract_article(self, handle, cid):
        url = getattr(handle, "_fake_url", "https://example.com")
        if url in self.articles:
            return self.articles[url]
        return Article(
            title="Test",
            text="Text",
            markdown="Markdown",
            provenance=Provenance(provider="fake", source_kind=SourceKind.WEB, retrieved_ts=datetime.now(UTC), uri=url)
        )


class FakePageManager:
    async def new_page(self, session_id):
        handle = PageHandle(session_id=session_id, tab_id="tab-1")
        handle._fake_url = "https://example.com"
        return handle

    async def close_page(self, handle):
        pass
        
    def get_provider(self, handle):
        return FakeProvider(), "sess", "tab"


class FakeProvider:
    async def content_html(self, session_id, tab_id):
        return '<a href="https://example.com/page2">Link</a>'


@pytest.mark.asyncio
async def test_crawler_engine_basic():
    nav = FakeNavEngine()
    extract = FakeExtractionEngine()
    pages = FakePageManager()
    
    crawler = CrawlerEngine(
        nav_engine=nav,
        extract_engine=extract,
        page_manager=pages,
    )
    
    cid = CorrelationId("test")
    result = await crawler.crawl("sess-1", "https://example.com", depth=1, budget=2, cid=cid)
    
    assert result.seed_url == "https://example.com"
    assert len(result.articles) == 2
    assert len(result.visited_urls) == 2
    assert "https://example.com" in result.visited_urls
    assert "https://example.com/page2" in result.visited_urls
    assert result.confidence > 0.0
