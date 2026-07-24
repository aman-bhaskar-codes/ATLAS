"""BrowserPlatform: The top-level facade for the Browser & Web Automation capability."""
from __future__ import annotations

import logging
from typing import Any

from atlas.capabilities.browser.domain.content import Article, WebPage
from atlas.capabilities.browser.domain.dom import AccessibilityNode
from atlas.capabilities.browser.domain.locator import Locator
from atlas.capabilities.browser.domain.page import PageHandle, PageState
from atlas.capabilities.browser.domain.session import BrowserSession
from atlas.capabilities.browser.domain.vision import Screenshot
from atlas.capabilities.browser.engines.click import ClickEngine
from atlas.capabilities.browser.engines.dom import DOMEngine
from atlas.capabilities.browser.engines.extraction import ExtractionEngine
from atlas.capabilities.browser.engines.locator import LocatorEngine
from atlas.capabilities.browser.engines.navigation import NavigationEngine
from atlas.capabilities.browser.engines.network import NetworkEngine
from atlas.capabilities.browser.engines.screenshot import ScreenshotEngine
from atlas.capabilities.browser.engines.submit import SubmitEngine
from atlas.capabilities.browser.engines.type import TypeEngine
from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.capabilities.browser.page.state_builder import StateBuilder
from atlas.capabilities.browser.research.crawler import CrawlerEngine, ResearchResult
from atlas.capabilities.browser.session.manager import SessionManager
from atlas.infra.ids import CorrelationId

_log = logging.getLogger("atlas.browser.platform")


class BrowserPlatform:
    """Facade for the complete browser automation capability.
    
    Callers work in terms of high-level domain objects (PageHandle, Locator, etc.)
    and never interact with providers directly. Engine + safety tiers are encapsulated.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        page_manager: PageManager,
        state_builder: StateBuilder,
        navigation_engine: NavigationEngine,
        dom_engine: DOMEngine,
        screenshot_engine: ScreenshotEngine,
        extraction_engine: ExtractionEngine,
        locator_engine: LocatorEngine,
        network_engine: NetworkEngine,
        click_engine: ClickEngine,
        type_engine: TypeEngine,
        submit_engine: SubmitEngine,
        crawler_engine: CrawlerEngine | None = None,
    ) -> None:
        self._sessions = session_manager
        self._pages = page_manager
        self._state = state_builder
        self._nav = navigation_engine
        self._dom = dom_engine
        self._screen = screenshot_engine
        self._extract = extraction_engine
        self._locate = locator_engine
        self._net = network_engine
        self._click = click_engine
        self._type = type_engine
        self._submit = submit_engine
        self._crawler = crawler_engine

    # --- Sessions ---

    async def create_session(
        self, *, profile: str | None = None, incognito: bool = False
    ) -> BrowserSession:
        return await self._sessions.acquire(profile=profile, incognito=incognito)

    async def close_session(self, session_id: str) -> None:
        await self._sessions.release(session_id)

    # --- Pages ---

    async def new_page(self, session_id: str) -> PageHandle:
        return await self._pages.new_page(session_id)

    async def close_page(self, handle: PageHandle) -> None:
        await self._pages.close_page(handle)

    async def build_state(self, handle: PageHandle) -> PageState:
        return await self._state.build_state(handle)

    # --- Navigation (Tier-0: read-only) ---

    async def goto(self, handle: PageHandle, url: str, cid: CorrelationId) -> PageState:
        return await self._nav.goto(handle, url, cid)

    async def back(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        return await self._nav.back(handle, cid)

    async def forward(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        return await self._nav.forward(handle, cid)

    async def reload(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        return await self._nav.reload(handle, cid)

    # --- Read engines (Tier-0/1) ---

    async def get_accessibility_tree(
        self, handle: PageHandle, cid: CorrelationId
    ) -> AccessibilityNode:
        return await self._dom.get_accessibility_tree(handle, cid)

    async def capture_screenshot(
        self, handle: PageHandle, full_page: bool, cid: CorrelationId
    ) -> Screenshot:
        return await self._screen.capture(handle, full_page, cid)

    async def extract_web_page(self, handle: PageHandle, cid: CorrelationId) -> WebPage:
        return await self._extract.extract_web_page(handle, cid)

    async def extract_article(self, handle: PageHandle, cid: CorrelationId) -> Article:
        return await self._extract.extract_article(handle, cid)

    # --- Mutating engines (Tier-2+) ---

    async def click(
        self, handle: PageHandle, locator: Locator, cid: CorrelationId
    ) -> Any:
        return await self._click.click(handle, locator, cid)

    async def type_text(
        self, handle: PageHandle, locator: Locator, text: str, cid: CorrelationId
    ) -> Any:
        return await self._type.type_text(handle, locator, text, cid)

    async def submit_form(
        self,
        handle: PageHandle,
        form_id: str,
        data: dict[str, str],
        cid: CorrelationId,
    ) -> Any:
        # Submit engine expects a FormModel; form_id is passed as a simple string here.
        # Callers with a full FormModel should use submit_engine.submit() directly.
        from atlas.capabilities.browser.domain.content import FormModel
        form = FormModel(id=form_id, action_url="", fields=[], submits_externally=False)  # type: ignore
        return await self._submit.submit(handle, form, data, cid)

    # --- Locator / Network helpers ---

    async def resolve_locator(
        self, handle: PageHandle, locator: Locator
    ) -> list[Any]:
        return await self._locate.resolve(handle, locator)

    async def start_network_capture(self, handle: PageHandle, cid: CorrelationId) -> None:
        await self._net.start_capture(handle, cid)

    async def drain_network_events(self, handle: PageHandle, cid: CorrelationId) -> list[Any]:
        return await self._net.drain_events(handle, cid)

    # --- Research ---

    async def research(
        self,
        session_id: str,
        seed_url: str,
        depth: int,
        budget: int,
        cid: CorrelationId,
    ) -> ResearchResult:
        if not self._crawler:
            raise RuntimeError("Crawler engine not initialized")
        return await self._crawler.crawl(session_id, seed_url, depth, budget, cid)
