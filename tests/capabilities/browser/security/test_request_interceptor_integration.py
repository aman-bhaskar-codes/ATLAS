"""Real Chromium protocol with responses supplied before any socket is opened."""

import base64
import json
from typing import Any

import pytest
from playwright.async_api import Error, async_playwright

from atlas.capabilities.browser.providers.cdp_provider import CDPProvider
from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider
from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.capabilities.browser.security.request_interceptor import ChromiumEgressInterceptor


class InMemoryResponses:
    """CDP peer adapter replaces approved continue commands with fixture responses.

    Chrome itself creates redirects, subresources, frames and popups. Production
    policy evaluates every request. The adapter prevents actual network IO even
    if a regression erroneously approves the synthetic private DNS destination.
    """

    def __init__(self, cdp: Any) -> None:
        self.cdp = cdp
        self.urls: dict[tuple[str, str], str] = {}
        self.continued: list[str] = []

    def _record(self, event: dict[str, Any]) -> None:
        session = event["sessionId"]
        message = json.loads(event["message"])
        if message.get("method") == "Target.receivedMessageFromTarget":
            self._record(message["params"])
        elif message.get("method") == "Fetch.requestPaused":
            params = message["params"]
            self.urls[(session, params["requestId"])] = params["request"]["url"]

    def on(self, event: str, handler: Any) -> None:
        if event == "Target.receivedMessageFromTarget":
            self.cdp.on(event, self._record)
        self.cdp.on(event, handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        self.cdp.remove_listener(event, handler)
        if event == "Target.receivedMessageFromTarget":
            self.cdp.remove_listener(event, self._record)

    def _replace(self, params: dict[str, Any]) -> dict[str, Any]:
        command = json.loads(params["message"])
        if command["method"] == "Target.sendMessageToTarget":
            command["params"] = self._replace(command["params"])
        elif command["method"] == "Fetch.continueRequest":
            request_id = command["params"]["requestId"]
            url = self.urls[(params["sessionId"], request_id)]
            self.continued.append(url)
            headers = [{"name": "content-type", "value": "text/html"}]
            code = 200
            body = "<p>Child fixture</p>"
            if url == "https://public.test/private-start":
                code = 302
                headers.append({"name": "location", "value": "https://blocked.test/private"})
            elif url.endswith("/start"):
                code = 302
                headers.append({"name": "location", "value": "/final"})
            elif url == "https://public.test/final":
                body = """<img src="/image.png"><img src="https://blocked.test/private">
                <iframe src="https://frame.test/frame"></iframe>
                <script>window.open('https://popup.test/start')</script><p>Final fixture</p>"""
            command["method"] = "Fetch.fulfillRequest"
            command["params"] = {
                "requestId": request_id,
                "responseCode": code,
                "responseHeaders": headers,
                "body": base64.b64encode(body.encode()).decode(),
            }
        return {**params, "message": json.dumps(command)}

    async def send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if method == "Target.sendMessageToTarget":
            assert params is not None
            params = self._replace(params)
        return await self.cdp.send(method, params)

    async def detach(self) -> None:
        await self.cdp.detach()


async def test_native_redirect_frame_popup_and_subresource_checks_preserve_url(
    require_browser: None,
) -> None:
    seen: list[str] = []

    async def dns(host: str) -> list[str]:
        seen.append(host)
        return ["10.0.0.1"] if host == "blocked.test" else ["8.8.8.8"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        cdp = InMemoryResponses(await browser.new_browser_cdp_session())

        class GuardedBrowser:
            async def new_browser_cdp_session(self) -> InMemoryResponses:
                return cdp

        guard = ChromiumEgressInterceptor(EgressPolicy(resolver=dns))
        await guard.start(GuardedBrowser())
        context = await browser.new_context(service_workers="block")
        try:
            page = await context.new_page()
            page.set_default_timeout(60_000)
            page.set_default_navigation_timeout(60_000)
            await guard.set_page_policy(context, page, EgressPolicy(resolver=dns))
            async with page.expect_popup(timeout=60_000) as popup_info:
                await page.goto("https://public.test/start", wait_until="load", timeout=60_000)
            popup = await popup_info.value
            await popup.wait_for_load_state("load", timeout=60_000)
            assert page.url == "https://public.test/final"
            assert popup.url == "https://popup.test/final"
            assert await page.evaluate("location.origin") == "https://public.test"
            assert "https://public.test/start" in cdp.continued
            assert "https://public.test/final" in cdp.continued
            assert "https://public.test/image.png" in cdp.continued
            assert "https://frame.test/frame" in cdp.continued
            assert "https://popup.test/start" in cdp.continued
            assert "https://popup.test/final" in cdp.continued
            assert "blocked.test" in seen
            assert "https://blocked.test/private" not in cdp.continued
            guard.check_ready()
            with pytest.raises(Error, match="ERR_BLOCKED_BY_CLIENT"):
                await page.goto("https://public.test/private-start", wait_until="load")
            assert "https://public.test/private-start" in cdp.continued
            assert "https://blocked.test/private" not in cdp.continued
        finally:
            await context.close()
            await guard.close()
            await browser.close()


async def test_external_browser_injection_is_rejected_before_unprotected_context(require_browser: None) -> None:
    from atlas.capabilities.browser.errors import ProviderError

    async with async_playwright() as pw:
        for provider_type in (PlaywrightProvider, CDPProvider):
            browser = await pw.chromium.launch(headless=True)
            provider = provider_type()
            provider._browser = browser
            try:
                with pytest.raises(ProviderError, match="unprotected"):
                    await provider.launch(profile=None, incognito=True, sandbox_spec=None)
                assert not provider._contexts
            finally:
                await provider.stop()
