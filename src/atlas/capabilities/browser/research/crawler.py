"""Crawler engine for autonomous research."""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from atlas.capabilities.browser.domain.content import Article
from atlas.capabilities.browser.engines.extraction import ExtractionEngine
from atlas.capabilities.browser.engines.navigation import NavigationEngine
from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.capabilities.browser.research.source_ranker import SourceRanker
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger

_log = get_logger("atlas.browser.crawler")


@dataclass(frozen=True)
class ResearchResult:
    seed_url: str
    articles: list[Article]
    visited_urls: set[str]
    confidence: float


class CrawlerEngine:
    """Bounded multi-page recursive crawler."""

    def __init__(
        self,
        nav_engine: NavigationEngine,
        extract_engine: ExtractionEngine,
        page_manager: PageManager,
        ranker: SourceRanker | None = None
    ) -> None:
        self._nav = nav_engine
        self._extract = extract_engine
        self._pages = page_manager
        self._ranker = ranker or SourceRanker()

    async def crawl(
        self,
        session_id: str,
        seed_url: str,
        depth: int,
        budget: int,
        cid: CorrelationId
    ) -> ResearchResult:
        """Crawl starting from seed_url, returning extracted articles."""
        visited: set[str] = set()
        articles: list[Article] = []
        
        handle = await self._pages.new_page(session_id)
        
        frontier = [(seed_url, 0)]
        cost = 0
        
        try:
            while frontier and cost < budget:
                # Rank frontier before popping, to always explore highest value first
                # (Simplistic BFS with ranking)
                frontier.sort(key=lambda item: self._ranker.score_url(item[0]), reverse=True)
                current_url, current_depth = frontier.pop(0)
                
                if current_url in visited:
                    continue
                    
                _log.info("crawler.visit", url=current_url, depth=current_depth, cost=cost)
                visited.add(current_url)
                cost += 1
                
                # Navigate
                try:
                    await self._nav.goto(handle, current_url, cid)
                except Exception as exc:
                    _log.warning("crawler.nav_failed", url=current_url, error=str(exc))
                    continue
                
                # Extract
                try:
                    article = await self._extract.extract_article(handle, cid)
                    articles.append(article)
                except Exception as exc:
                    _log.warning("crawler.extract_failed", url=current_url, error=str(exc))
                    continue
                    
                if current_depth < depth:
                    # Extract links to add to frontier
                    try:
                        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
                        html = await provider.content_html(provider_session_id, tab_id)
                        import re
                        links = re.findall(r'<a\s+(?:[^>]*?\s+)?href="([^"]*)"', html, re.IGNORECASE)
                        for link in links:
                            link = urllib.parse.urljoin(current_url, link)
                            if link.startswith("http") and link not in visited:
                                seed_domain = urllib.parse.urlparse(seed_url).netloc
                                link_domain = urllib.parse.urlparse(link).netloc
                                if seed_domain in link_domain or link_domain in seed_domain:
                                    frontier.append((link, current_depth + 1))
                    except Exception as exc:
                        _log.debug("crawler.link_extraction_failed", url=current_url, error=str(exc))
                        
        finally:
            await self._pages.close_page(handle)
            
        # Calculate simplistic confidence based on average source rank
        avg_score = 0.5
        if articles:
            scores = [self._ranker.score_url(a.url) for a in articles if getattr(a, 'url', None)]  # type: ignore
            scores = [s for s in scores if s is not None] # Fallback if url is stored in provenance
            if not scores:
                scores = [self._ranker.score_url(a.provenance.uri) for a in articles if a.provenance.uri]
            if scores:
                avg_score = sum(scores) / len(scores)
            
        return ResearchResult(
            seed_url=seed_url,
            articles=articles,
            visited_urls=visited,
            confidence=avg_score
        )
