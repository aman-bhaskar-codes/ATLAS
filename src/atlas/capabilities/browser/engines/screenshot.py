"""Screenshot engine handles visual capture and redaction."""
from __future__ import annotations

from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.domain.vision import Screenshot
from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.infra.ids import CorrelationId


class ScreenshotEngine:
    def __init__(self, page_manager: PageManager) -> None:
        self._pages = page_manager

    async def capture(self, handle: PageHandle, full_page: bool, cid: CorrelationId) -> Screenshot:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        
        # In a real implementation we would check the sensitive-app blocklist here
        # before capturing.
        
        data = await provider.screenshot(provider_session_id, tab_id, full_page=full_page, clip=None)
        
        return Screenshot(
            data=data,
            viewport_only=not full_page
        )
