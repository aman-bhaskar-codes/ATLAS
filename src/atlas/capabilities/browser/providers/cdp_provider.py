"""CDP operations on owned, proxy-enforced Chromium contexts.

Reuse the managed Chromium lifecycle instead of spawning an unprotected browser
on a shared fixed debugging port. External attach is explicitly unsupported:
ATLAS cannot retrofit process-wide proxy/WebRTC policy onto user-owned tabs.
"""

from __future__ import annotations

from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider


class CDPProvider(PlaywrightProvider):
    name = "cdp"
    is_local = True

    async def _cleanup(self) -> None:
        await self.stop()
