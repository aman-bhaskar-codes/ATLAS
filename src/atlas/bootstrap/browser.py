"""Bootstrap for the browser platform."""

from __future__ import annotations

from typing import Any

from atlas.capabilities.browser.builder import build_browser_platform
from atlas.capabilities.browser.dispatcher import NullBrowserDispatcher, PlaywrightBrowserDispatcher
from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.infra.config import BrowserCfg
from atlas.infra.ids import IdGenerator
from atlas.safety.engine import SafetyEngine


def build_browser(
    config: BrowserCfg,
    ids: IdGenerator,
    notifications: NotificationPlatform,
    safety: SafetyEngine,
    approval_channels: tuple[str, ...] = ("push",),
    safe_browsing_api_key: str = "",
    virustotal_api_key: str = "",
) -> BrowserPlatform | None:
    if not config.enabled:
        return None

    dispatcher: Any = NullBrowserDispatcher()
    playwright_provider: PlaywrightProvider | None = None

    if config.execution_backend == "playwright":
        playwright_provider = PlaywrightProvider()
        dispatcher = PlaywrightBrowserDispatcher(
            provider=playwright_provider,
            safety=safety,
        )

    return build_browser_platform(
        ids=ids,
        notifications=notifications,
        approval_channels=approval_channels,
        safe_browsing_api_key=safe_browsing_api_key,
        virustotal_api_key=virustotal_api_key,
        dispatcher=dispatcher,
        playwright_provider=playwright_provider,
    )
