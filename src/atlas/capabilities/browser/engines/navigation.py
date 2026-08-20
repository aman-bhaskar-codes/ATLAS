"""Navigation engine handles goto, back, forward, and emits page state."""

from __future__ import annotations

import logging
from typing import Any

from atlas.capabilities.browser.domain.page import PageHandle, PageState
from atlas.capabilities.browser.errors import NavigationError, UnsafeURLError
from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.capabilities.browser.security.reputation import ReputationVerdict
from atlas.infra.ids import CorrelationId

_log = logging.getLogger("atlas.browser.navigation")


class NavigationEngine:
    def __init__(self, page_manager: PageManager, state_builder: Any, reputation_checker: Any = None) -> None:
        self._pages = page_manager
        self._builder = state_builder
        self._reputation = reputation_checker

    async def goto(self, handle: PageHandle, url: str, cid: CorrelationId) -> PageState:
        if self._reputation is not None:
            result = await self._reputation.check(url)
            if result.verdict == ReputationVerdict.MALICIOUS:
                _log.warning(f"Blocking navigation to malicious URL: {url}", extra={"cid": cid})
                raise UnsafeURLError(f"URL {url} is malicious")
            elif result.verdict == ReputationVerdict.UNKNOWN:
                _log.info(f"Unknown reputation for URL {url}, proceeding anyway", extra={"cid": cid})
            # If SAFE, proceed normally (no additional logging needed)

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


    async def shutdown(self) -> None:
        """Shuts down the URL reputation checker."""
        if self._reputation is not None:
            await self._reputation.shutdown()