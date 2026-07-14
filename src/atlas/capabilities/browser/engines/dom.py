"""DOM engine fetches accessibility tree and raw nodes."""
from __future__ import annotations

from atlas.capabilities.browser.domain.dom import AccessibilityNode
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.infra.ids import CorrelationId


class DOMEngine:
    def __init__(self, page_manager: PageManager) -> None:
        self._pages = page_manager

    async def get_accessibility_tree(self, handle: PageHandle, cid: CorrelationId) -> AccessibilityNode:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        raw_tree = await provider.accessibility_tree(provider_session_id, tab_id)
        # In a real implementation this would map the provider's raw tree format
        # to our normalized AccessibilityNode model.
        return AccessibilityNode(role=raw_tree.get("role", "WebArea"), name=raw_tree.get("name", ""))
