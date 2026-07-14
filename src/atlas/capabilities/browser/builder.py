"""Builder for the BrowserPlatform, wiring all dependencies together."""
from __future__ import annotations

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
from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider
from atlas.capabilities.browser.providers.cdp_provider import CDPProvider
from atlas.capabilities.browser.registry.provider_registry import ProviderRegistry
from atlas.capabilities.browser.research.reader import ReaderEngine
from atlas.capabilities.browser.research.source_ranker import SourceRanker
from atlas.capabilities.browser.research.crawler import CrawlerEngine
from atlas.capabilities.browser.session.manager import SessionManager
from atlas.capabilities.browser.session.pool import BrowserPool
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.infra.ids import IdGenerator


def build_browser_platform(
    ids: IdGenerator,
    notifications: NotificationPlatform,
    approval_channels: tuple[str, ...] = ("push",),
) -> BrowserPlatform:
    """Wire the full browser automation platform.

    Only ONE function in the entire codebase knows about this wiring — the DI root.
    Engines and engines-under-test wire themselves independently via fakes.
    """
    # 1. Provider registry
    registry = ProviderRegistry()
    playwright = PlaywrightProvider()
    cdp = CDPProvider()
    registry.register(playwright, preference=10)
    registry.register(cdp, preference=5)

    # 2. Pool + session manager (thin coordination layer)
    pool = BrowserPool(registry)
    session_manager = SessionManager(pool=pool, ids=ids)

    # 3. Page manager + state snapshot utility
    page_manager = PageManager(pool=pool)
    state_builder = StateBuilder(page_manager=page_manager)

    # 4. Read engines (Tier-0/1 — no gating needed)
    reader_engine = ReaderEngine()
    nav_engine = NavigationEngine(page_manager=page_manager, state_builder=state_builder)
    dom_engine = DOMEngine(page_manager=page_manager)
    screen_engine = ScreenshotEngine(page_manager=page_manager)
    extract_engine = ExtractionEngine(page_manager=page_manager, reader=reader_engine)
    locate_engine = LocatorEngine(page_manager=page_manager)
    net_engine = NetworkEngine(page_manager=page_manager)

    # 5. Mutating engines (Tier-2 — all gated through approval)
    #    dispatcher=None is acceptable for the click/type engines now —
    #    they build ActionResult directly without a dispatcher.
    click_engine = ClickEngine(dispatcher=None, state_builder=state_builder)
    type_engine = TypeEngine(dispatcher=None, state_builder=state_builder)
    submit_engine = SubmitEngine(
        dispatcher=None,
        notifications=notifications,
        ids=ids,
        approval_channels=approval_channels,
        state_builder=state_builder,
    )

    # 6. Research engines
    ranker = SourceRanker()
    crawler_engine = CrawlerEngine(
        nav_engine=nav_engine,
        extract_engine=extract_engine,
        page_manager=page_manager,
        ranker=ranker,
    )

    return BrowserPlatform(
        session_manager=session_manager,
        page_manager=page_manager,
        state_builder=state_builder,
        navigation_engine=nav_engine,
        dom_engine=dom_engine,
        screenshot_engine=screen_engine,
        extraction_engine=extract_engine,
        locator_engine=locate_engine,
        network_engine=net_engine,
        click_engine=click_engine,
        type_engine=type_engine,
        submit_engine=submit_engine,
        crawler_engine=crawler_engine,
    )
