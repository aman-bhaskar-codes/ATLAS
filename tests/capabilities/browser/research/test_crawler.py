from datetime import UTC, datetime

import pytest

from atlas.capabilities.browser.domain.content import Article
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.research.crawler import CrawlerEngine
from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.infra.ids import CorrelationId


class FakeNavEngine:
    async def goto(self, handle, url, cid) -> None:  # type: ignore
        pass


class FakeExtractionEngine:
    def __init__(self, articles=None) -> None:  # type: ignore
        self.articles = articles or {}

    async def extract_article(self, handle, cid) -> None:  # type: ignore
        url = getattr(handle, "_fake_url", "https://example.com")
        if url in self.articles:
            return self.articles[url]  # type: ignore
        return Article(  # type: ignore
            title="Test",
            text="Text",
            markdown="Markdown",
            provenance=Provenance(provider="fake", source_kind=SourceKind.WEB, retrieved_ts=datetime.now(UTC), uri=url),
        )


class FakePageManager:
    async def new_page(self, session_id) -> None:  # type: ignore
        handle = PageHandle(session_id=session_id, tab_id="tab-1")
        handle._fake_url = "https://example.com"  # type: ignore
        return handle  # type: ignore

    async def close_page(self, handle) -> None:  # type: ignore
        pass

    def get_provider(self, handle) -> None:  # type: ignore
        return FakeProvider(), "sess", "tab"  # type: ignore


class FakeProvider:
    async def content_html(self, session_id, tab_id) -> None:  # type: ignore
        return '<a href="https://example.com/page2">Link</a>'  # type: ignore


@pytest.mark.asyncio
async def test_crawler_engine_basic() -> None:
    nav = FakeNavEngine()
    extract = FakeExtractionEngine()
    pages = FakePageManager()

    crawler = CrawlerEngine(
        nav_engine=nav,  # type: ignore
        extract_engine=extract,  # type: ignore
        page_manager=pages,  # type: ignore
    )

    cid = CorrelationId("test")
    result = await crawler.crawl("sess-1", "https://example.com", depth=1, budget=2, cid=cid)

    assert result.seed_url == "https://example.com"
    assert len(result.articles) == 2
    assert len(result.visited_urls) == 2
    assert "https://example.com" in result.visited_urls
    assert "https://example.com/page2" in result.visited_urls
    assert result.confidence > 0.0


class FakeProviderHostileLinks:
    """Serves links a substring host-check would wrongly follow: a look-alike
    off-domain host, a subdomain (allowed), and an internal SSRF target."""

    async def content_html(self, session_id, tab_id) -> None:  # type: ignore
        return (  # type: ignore
            '<a href="https://example.com.attacker.net/steal">lookalike</a>'
            '<a href="https://evil-example.com/steal">substring</a>'
            '<a href="http://169.254.169.254/latest/meta-data/">metadata</a>'
            '<a href="https://docs.example.com/ok">subdomain</a>'
        )


class HostileLinkPageManager(FakePageManager):
    def get_provider(self, handle) -> None:  # type: ignore
        return FakeProviderHostileLinks(), "sess", "tab"  # type: ignore


@pytest.mark.asyncio
async def test_crawler_confines_to_seed_domain_and_blocks_ssrf() -> None:
    crawler = CrawlerEngine(
        nav_engine=FakeNavEngine(),  # type: ignore
        extract_engine=FakeExtractionEngine(),  # type: ignore
        page_manager=HostileLinkPageManager(),  # type: ignore
    )

    result = await crawler.crawl("sess-1", "https://example.com", depth=1, budget=10, cid=CorrelationId("t"))

    # The look-alike (example.com.attacker.net), substring (evil-example.com) and
    # metadata IP must NEVER be visited; the real subdomain is in-scope.
    assert "https://example.com.attacker.net/steal" not in result.visited_urls
    assert "https://evil-example.com/steal" not in result.visited_urls
    assert "http://169.254.169.254/latest/meta-data/" not in result.visited_urls
    assert "https://docs.example.com/ok" in result.visited_urls
