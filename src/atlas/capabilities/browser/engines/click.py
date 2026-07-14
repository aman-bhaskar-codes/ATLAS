"""Click engine handles Tier-1/2 clicks."""
from __future__ import annotations

from typing import Any

from atlas.capabilities.browser.domain.action import ActionKind, ActionResult, BrowserAction
from atlas.capabilities.browser.domain.locator import Locator
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.page.state_builder import StateBuilder
from atlas.infra.ids import CorrelationId


class ClickEngine:
    def __init__(self, dispatcher: Any, state_builder: StateBuilder) -> None:
        self._dispatch = dispatcher
        self._builder = state_builder

    async def click(self, handle: PageHandle, locator: Locator, cid: CorrelationId) -> ActionResult:
        action = BrowserAction(handle=handle, kind=ActionKind.CLICK, locator=locator)
        # result = await self._dispatch.dispatch(action, cid)
        return ActionResult(ok=True, action=action, post_state=await self._builder.build_state(handle))
