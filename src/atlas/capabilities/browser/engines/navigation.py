"""Navigation engine handles goto, back, forward, and emits page state."""
from __future__ import annotations

import logging
from typing import Any

from atlas.capabilities.browser.domain.page import PageHandle, PageState
from atlas.capabilities.browser.errors import NavigationError
from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.infra.ids import CorrelationId

_log = logging.getLogger("atlas.browser.navigation")

class NavigationEngine:
    def __init__(self, page_manager: PageManager, state_builder: Any) -> None:
        self._pages = page_manager
        self._builder = state_builder

    async def goto(self, handle: PageHandle, url: str, cid: CorrelationId) -> PageState:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        
        try:
            await provider.goto(provider_session_id, tab_id, url)
            _log.info(f"Navigated to {url}", extra={"cid": cid})
        except Exception as exc:
            raise NavigationError(f"Failed to navigate to {url}: {exc}") from exc
            
        return await self._builder.build_state(handle)  # type: ignore

    async def back(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        await provider.back(provider_session_id, tab_id)
        return await self._builder.build_state(handle)  # type: ignore

    async def forward(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        await provider.forward(provider_session_id, tab_id)
        return await self._builder.build_state(handle)  # type: ignore

    async def reload(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        await provider.reload(provider_session_id, tab_id)
        return await self._builder.build_state(handle)  # type: ignore
