"""StateBuilder constructs PageState snapshots."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from atlas.capabilities.browser.domain.page import AuthState, PageHandle, PageState
from atlas.capabilities.browser.page.page_manager import PageManager


class StateBuilder:
    def __init__(self, page_manager: PageManager) -> None:
        self._pages = page_manager

    async def build_state(self, handle: PageHandle) -> PageState:
        provider, provider_session_id, tab_id = self._pages.get_provider(handle)
        
        url = await provider.eval_readonly(provider_session_id, tab_id, "window.location.href") or "about:blank"
        title = await provider.eval_readonly(provider_session_id, tab_id, "document.title") or ""
        html = await provider.content_html(provider_session_id, tab_id)
        
        dom_hash = hashlib.md5(html.encode("utf-8")).hexdigest()
        
        # Simplified snapshot for testing
        return PageState(
            handle=handle,
            url=url,
            title=title,
            auth=AuthState.ANONYMOUS,
            captured_ts=datetime.now(UTC),
            dom_hash=dom_hash
        )
