"""PageManager handles tab lifecycle and routes calls to the appropriate provider."""
from __future__ import annotations

from typing import Any

from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.session.pool import BrowserPool


class PageManager:
    def __init__(self, pool: BrowserPool) -> None:
        self._pool = pool

    async def new_page(self, session_id: str) -> PageHandle:
        provider = self._pool.get_provider(session_id)
        # Just reaching in for now
        provider_session_id = self._pool._sessions[session_id].state.session_id
        tab_id = await provider.new_tab(provider_session_id)
        return PageHandle(session_id=session_id, tab_id=tab_id)

    async def close_page(self, handle: PageHandle) -> None:
        provider = self._pool.get_provider(handle.session_id)
        provider_session_id = self._pool._sessions[handle.session_id].state.session_id
        await provider.close_tab(provider_session_id, handle.tab_id)

    def get_provider(self, handle: PageHandle) -> tuple[Any, str, str]:
        """Return (provider, provider_session_id, tab_id) for engine operations."""
        provider = self._pool.get_provider(handle.session_id)
        provider_session_id = self._pool._sessions[handle.session_id].state.session_id
        return provider, provider_session_id, handle.tab_id
