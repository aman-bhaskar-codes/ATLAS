"""Locator engine converts Domain locators to provider-specific selectors."""
from __future__ import annotations

from typing import Any

from atlas.capabilities.browser.domain.locator import Locator
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.page.page_manager import PageManager


class LocatorEngine:
    def __init__(self, page_manager: PageManager) -> None:
        self._pages = page_manager

    async def resolve(self, handle: PageHandle, locator: Locator) -> list[Any]:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        
        # Passes the Locator to the provider to resolve. 
        # In Playwright this calls `page.locator(...)`.
        return await provider.query(provider_session_id, tab_id, locator)  # type: ignore
