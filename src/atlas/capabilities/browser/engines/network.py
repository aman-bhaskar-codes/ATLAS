"""Network engine captures HAR and events."""
from __future__ import annotations

from atlas.capabilities.browser.domain.network import NetworkEvent
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.infra.ids import CorrelationId


class NetworkEngine:
    def __init__(self, page_manager: PageManager) -> None:
        self._pages = page_manager

    async def start_capture(self, handle: PageHandle, cid: CorrelationId) -> None:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        await provider.start_network_capture(provider_session_id, tab_id)

    async def drain_events(self, handle: PageHandle, cid: CorrelationId) -> list[NetworkEvent]:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        _raw_events = await provider.drain_network_events(provider_session_id, tab_id)
        # In a real implementation we map raw events to NetworkEvent
        return []
