"""Extraction engine normalizes pages into WebPage/Article."""
from __future__ import annotations

from datetime import UTC, datetime

from atlas.capabilities.browser.domain.content import Article, PageMetadata, WebPage
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.capabilities.browser.research.reader import ReaderEngine
from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.infra.ids import CorrelationId
class ExtractionEngine:
    def __init__(self, page_manager: PageManager, reader: ReaderEngine | None = None) -> None:
        self._pages = page_manager
        self._reader = reader or ReaderEngine()

    async def extract_web_page(self, handle: PageHandle, cid: CorrelationId) -> WebPage:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        
        url = await provider.eval_readonly(provider_session_id, tab_id, "window.location.href") or "about:blank"
        title = await provider.eval_readonly(provider_session_id, tab_id, "document.title") or ""
        html = await provider.content_html(provider_session_id, tab_id)
        
        prov = Provenance(
            provider=provider.name,
            source_kind=SourceKind.WEB,
            uri=url,
            retrieved_ts=datetime.now(UTC),
        )
        
        return WebPage(
            url=url,
            metadata=PageMetadata(title=title),
            text="Extracted text placeholder",
            markdown="Extracted markdown placeholder",
            provenance=prov
        )

    async def extract_article(self, handle: PageHandle, cid: CorrelationId) -> Article:
        # Calls reader-mode extraction logic over the DOM.
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        
        url = await provider.eval_readonly(provider_session_id, tab_id, "window.location.href") or "about:blank"
        title = await provider.eval_readonly(provider_session_id, tab_id, "document.title") or ""
        html = await provider.content_html(provider_session_id, tab_id)
        
        prov = Provenance(
            provider=provider.name,
            source_kind=SourceKind.WEB,
            uri=url,
            retrieved_ts=datetime.now(UTC),
        )
        
        text = self._reader.extract_article_text(html, title)
        markdown = self._reader.extract_markdown(html, title)
        
        return Article(
            title=title,
            text=text,
            markdown=markdown,
            provenance=prov
        )
