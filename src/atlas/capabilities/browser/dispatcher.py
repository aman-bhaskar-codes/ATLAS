"""Browser execution backends (dispatchers)."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from atlas.capabilities.browser.domain.action import ActionKind, BrowserAction
from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider
from atlas.capabilities.errors import NoProviderAvailable, ProviderExecutionError
from atlas.infra.ids import CorrelationId
from atlas.infra.types import Tier, ToolRequest, ToolResult
from atlas.safety.engine import SafetyEngine
from atlas.tools.base import Tool

_log = logging.getLogger("atlas.browser.dispatcher")


class BrowserDispatcher(Protocol):
    """Execution backend for browser mutations.

    CONTRACT: Must raise an exception if the action fails to execute.
    A normal return (None) guarantees the action executed successfully.
    """

    async def dispatch(self, action: BrowserAction, cid: CorrelationId) -> None: ...


class NullBrowserDispatcher:
    """Fail-closed dispatcher that always raises NoProviderAvailable."""

    async def dispatch(self, action: BrowserAction, cid: CorrelationId) -> None:
        raise NoProviderAvailable("browser execution not wired")


class _MutationTool(Tool):
    """Ephemeral tool adapter to pass browser mutations through the SafetyEngine."""

    name = "browser"

    def __init__(self, provider: PlaywrightProvider, action: BrowserAction):
        self.provider = provider
        self.action = action

    def dry_run(self, args: dict[str, Any]) -> str:
        loc = self.action.locator.value if self.action.locator else "no-locator"
        return f"{self.action.kind.value} on {loc}"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        h = self.action.handle
        kind = self.action.kind
        try:
            if kind == ActionKind.CLICK:
                await self.provider.click(h.session_id, h.tab_id, self.action.locator)  # type: ignore
            elif kind == ActionKind.TYPE:
                await self.provider.type_text(h.session_id, h.tab_id, self.action.locator, self.action.value or "")  # type: ignore
            elif kind == ActionKind.SUBMIT:
                await self.provider.submit(h.session_id, h.tab_id, self.action.locator)  # type: ignore
            else:
                raise ValueError(f"unsupported mutation kind: {kind}")
            return ToolResult(ok=True)
        except Exception as e:
            raise ProviderExecutionError(f"Playwright error: {e}") from e


class PlaywrightBrowserDispatcher:
    """Executes browser mutations using Playwright, gated by SafetyEngine."""

    def __init__(self, provider: PlaywrightProvider, safety: SafetyEngine):
        self.provider = provider
        self.safety = safety

    async def dispatch(self, action: BrowserAction, cid: CorrelationId) -> None:
        _log.info(
            "browser.dispatch_attempt",
            extra={
                "action": action.kind.value,
                "target": f"{action.handle.session_id}:{action.handle.tab_id}",
                "cid": cid,
            },
        )

        req = ToolRequest(
            correlation_id=cid,
            tool="browser",
            operation=action.kind.value,
            args={
                "locator": action.locator.value if action.locator else None,
                "value": action.value,
            },
            declared_tier_hint=Tier.NOTIFY,  # Tier-2 mutation
        )

        tool = _MutationTool(self.provider, action)
        
        # This will raise DeniedError/HaltedError if blocked
        # The tool.execute will raise ProviderExecutionError on playwright failure
        await self.safety.guard(req, tool)
        
        _log.info(
            "browser.dispatch_success",
            extra={
                "action": action.kind.value,
                "target": f"{action.handle.session_id}:{action.handle.tab_id}",
                "cid": cid,
            },
        )
