"""PlaywrightProvider adapts the playwright async API to the BrowserProvider protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
)

from atlas.capabilities.browser.domain.locator import Locator
from atlas.capabilities.browser.errors import ProviderError
from atlas.capabilities.browser.providers.capabilities import ProviderCapabilities
from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.capabilities.browser.security.network_proxy import PinnedEgressProxy
from atlas.capabilities.browser.security.request_interceptor import ChromiumEgressInterceptor

_log = logging.getLogger("atlas.browser.providers.playwright")


class PlaywrightProvider:
    """Playwright-backed BrowserProvider.

    Manages one Playwright Browser (chromium, headless) and any number of
    contexts (sessions) / pages (tabs) on top of it.
    """

    name: str = "playwright"
    is_local: bool = True

    def __init__(self, egress_policy: EgressPolicy | None = None, *, executable_path: str | None = None) -> None:
        self._egress = egress_policy or EgressPolicy()
        self._executable_path = executable_path
        self._managed_browser = False
        self._profile: tempfile.TemporaryDirectory[str] | None = None
        self._default_proxy: PinnedEgressProxy | None = None
        self._proxies: dict[str, PinnedEgressProxy] = {}
        self._network: ChromiumEgressInterceptor | None = None
        self._page_policies: dict[tuple[str, str], EgressPolicy] = {}
        self._pw: Any = None
        self._browser: Any = None
        # Maps atlas session_id → playwright BrowserContext
        self._contexts: dict[str, BrowserContext] = {}
        # Maps atlas session_id → {tab_id → playwright Page}
        self._pages: dict[str, dict[str, Page]] = {}

        self.capabilities = ProviderCapabilities(
            persistent_profiles=False,
            network_transport="pinned_proxy",
            external_attach=False,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            if not self._managed_browser:
                raise ProviderError("Injected browser is unprotected: managed network isolation is required")
            return
        # Background/default-context traffic has no permission to egress. Each
        # owned context below gets its own authenticated enforcing proxy.
        self._default_proxy = PinnedEgressProxy(EgressPolicy(frozenset()))
        await self._default_proxy.start()
        try:
            self._pw = await async_playwright().start()
            options: dict[str, Any] = {
                "headless": True,
                "args": [
                    f"--proxy-server={self._default_proxy.settings['server']}",
                    "--proxy-bypass-list=<-loopback>",
                    "--disable-quic",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                    "--dns-prefetch-disable",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                ],
            }
            if self._executable_path:
                options["executable_path"] = self._executable_path
            elif not await asyncio.to_thread(os.path.isfile, self._pw.chromium.executable_path):
                options["channel"] = "chrome"  # installed browser, fresh managed profile
            self._profile = await asyncio.to_thread(_fresh_network_profile)
            owner = await self._pw.chromium.launch_persistent_context(self._profile.name, **options)
            self._browser = owner.browser
            if self._browser is None:
                raise ProviderError("Managed Chromium did not expose its browser owner")
            self._managed_browser = True
        except BaseException:
            await self.stop()
            raise
        _log.info("Managed Playwright browser started with enforcing egress proxy")

    async def launch(
        self,
        *,
        profile: str | None,
        incognito: bool,
        sandbox_spec: Any,
    ) -> str:
        """Create a new browser context and return its session_id."""
        await self._ensure_browser()
        await self._ensure_network_guard()
        proxy = PinnedEgressProxy(self._egress)
        await proxy.start()
        try:
            ctx = await self._browser.new_context(service_workers="block", proxy=proxy.settings)
        except BaseException:
            await proxy.close()
            raise
        session_id = str(id(ctx))
        self._contexts[session_id] = ctx
        self._proxies[session_id] = proxy
        self._pages[session_id] = {}
        _log.info("Launched Playwright context session_id=%s", session_id)
        return session_id

    async def attach(self, endpoint: str) -> str:
        raise ProviderError(
            "External CDP attach is unprotected: network isolation cannot be verified. Use a managed browser session."
        )

    async def _ensure_network_guard(self) -> None:
        if self._network is None:
            guard = ChromiumEgressInterceptor(self._egress)
            await guard.start(self._browser)
            self._network = guard
        self._network.check_ready()

    async def set_egress_policy(self, session_id: str, tab_id: str, policy: EgressPolicy) -> None:
        page = self._get_page(session_id, tab_id)
        await self._ensure_network_guard()
        assert self._network is not None
        await self._network.set_page_policy(self._contexts[session_id], page, policy)
        self._page_policies[(session_id, tab_id)] = policy
        await self._proxies[session_id].set_constraints(
            [p for (sid, _), p in self._page_policies.items() if sid == session_id]
        )

    async def health(self, session_id: str) -> bool:
        return session_id in self._contexts

    async def close(self, session_id: str) -> None:
        ctx = self._contexts.pop(session_id, None)
        self._pages.pop(session_id, None)
        for key in list(self._page_policies):
            if key[0] == session_id:
                self._page_policies.pop(key)
        if ctx:
            await ctx.close()
        proxy = self._proxies.pop(session_id, None)
        if proxy is not None:
            await proxy.close()

    # ------------------------------------------------------------------ #
    # Tabs
    # ------------------------------------------------------------------ #

    async def new_tab(self, session_id: str) -> str:
        ctx = self._contexts.get(session_id)
        if not ctx:
            raise ProviderError(f"Session {session_id} not found")
        page = await ctx.new_page()
        tab_id = str(id(page))
        self._pages[session_id][tab_id] = page
        await self.set_egress_policy(session_id, tab_id, self._egress)
        return tab_id

    async def list_tabs(self, session_id: str) -> list[str]:
        return list(self._pages.get(session_id, {}).keys())

    async def close_tab(self, session_id: str, tab_id: str) -> None:
        page = self._get_page(session_id, tab_id)
        await page.close()
        self._pages[session_id].pop(tab_id, None)
        self._page_policies.pop((session_id, tab_id), None)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _get_page(self, session_id: str, tab_id: str) -> Page:
        pages = self._pages.get(session_id)
        if pages is None:
            raise ProviderError(f"Session {session_id} not found")
        page = pages.get(tab_id)
        if page is None:
            raise ProviderError(f"Tab {tab_id} not found in session {session_id}")
        return page

    # ------------------------------------------------------------------ #
    # Navigation (read)
    # ------------------------------------------------------------------ #

    async def goto(self, session_id: str, tab_id: str, url: str) -> None:
        page = self._get_page(session_id, tab_id)
        await self._ensure_network_guard()
        await self._page_policies.get((session_id, tab_id), self._egress).check_resolved(url)
        await page.goto(url, wait_until="domcontentloaded")

    async def back(self, session_id: str, tab_id: str) -> None:
        page = self._get_page(session_id, tab_id)
        await page.go_back()

    async def forward(self, session_id: str, tab_id: str) -> None:
        page = self._get_page(session_id, tab_id)
        await page.go_forward()

    async def reload(self, session_id: str, tab_id: str) -> None:
        page = self._get_page(session_id, tab_id)
        await page.reload()

    # ------------------------------------------------------------------ #
    # DOM / extraction (read)
    # ------------------------------------------------------------------ #

    async def dom_snapshot(self, session_id: str, tab_id: str) -> Any:
        page = self._get_page(session_id, tab_id)
        return await page.content()

    async def accessibility_tree(self, session_id: str, tab_id: str) -> Any:
        page = self._get_page(session_id, tab_id)
        return await page.accessibility.snapshot() or {}  # type: ignore

    def _resolve_locator(self, page: Any, locator: Locator) -> Any:
        """Convert domain Locator to a playwright Locator object."""
        from atlas.capabilities.browser.domain.locator import LocatorKind

        kind, value = locator.kind, locator.value
        if kind == LocatorKind.CSS:
            return page.locator(value)
        elif kind == LocatorKind.XPATH:
            return page.locator(f"xpath={value}")
        elif kind == LocatorKind.TEXT:
            return page.get_by_text(value, exact=locator.exact)
        elif kind == LocatorKind.ROLE:
            return page.get_by_role(value, name=locator.name)
        elif kind == LocatorKind.LABEL:
            return page.get_by_label(value, exact=locator.exact)
        elif kind == LocatorKind.ARIA:
            return page.get_by_role(value)
        # Fallback to CSS
        return page.locator(value)

    async def query(self, session_id: str, tab_id: str, locator: Locator) -> list[Any]:
        page = self._get_page(session_id, tab_id)
        return await self._resolve_locator(page, locator).element_handles()  # type: ignore

    async def content_html(self, session_id: str, tab_id: str) -> str:
        page = self._get_page(session_id, tab_id)
        return await page.content()

    async def eval_readonly(self, session_id: str, tab_id: str, expr: str) -> Any:
        page = self._get_page(session_id, tab_id)
        return await page.evaluate(expr)

    # ------------------------------------------------------------------ #
    # Input (MUTATING)
    # ------------------------------------------------------------------ #

    async def click(self, session_id: str, tab_id: str, locator: Locator) -> None:
        page = self._get_page(session_id, tab_id)
        await self._resolve_locator(page, locator).first.click()

    async def type_text(self, session_id: str, tab_id: str, locator: Locator, text: str) -> None:
        page = self._get_page(session_id, tab_id)
        await self._resolve_locator(page, locator).first.fill(text)

    async def press(self, session_id: str, tab_id: str, key: str) -> None:
        page = self._get_page(session_id, tab_id)
        await page.keyboard.press(key)

    async def scroll(self, session_id: str, tab_id: str, x: int, y: int) -> None:
        page = self._get_page(session_id, tab_id)
        await page.mouse.wheel(x, y)

    async def submit(self, session_id: str, tab_id: str, locator: Locator) -> None:
        page = self._get_page(session_id, tab_id)
        await self._resolve_locator(page, locator).first.evaluate("el => el.submit()")

    async def set_files(
        self,
        session_id: str,
        tab_id: str,
        locator: Locator,
        paths: list[str],
    ) -> None:
        page = self._get_page(session_id, tab_id)
        await self._resolve_locator(page, locator).first.set_input_files(paths)

    # ------------------------------------------------------------------ #
    # Media (read)
    # ------------------------------------------------------------------ #

    async def screenshot(
        self,
        session_id: str,
        tab_id: str,
        *,
        full_page: bool,
        clip: Any | None,
    ) -> bytes:
        page = self._get_page(session_id, tab_id)
        return await page.screenshot(full_page=full_page, clip=clip)

    async def pdf(self, session_id: str, tab_id: str) -> bytes:
        page = self._get_page(session_id, tab_id)
        return await page.pdf()

    # ------------------------------------------------------------------ #
    # Network + downloads
    # ------------------------------------------------------------------ #

    async def start_network_capture(self, session_id: str, tab_id: str) -> None:
        pass  # would hook page.on("request") / page.on("response")

    async def drain_network_events(self, session_id: str, tab_id: str) -> list[Any]:
        return []

    async def await_download(self, session_id: str, tab_id: str) -> tuple[str, str]:
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Storage / cookies
    # ------------------------------------------------------------------ #

    async def get_cookies(self, session_id: str) -> list[Any]:
        ctx = self._contexts.get(session_id)
        if not ctx:
            return []
        return await ctx.cookies()

    async def set_cookies(self, session_id: str, cookies: list[Any]) -> None:
        ctx = self._contexts.get(session_id)
        if ctx:
            await ctx.add_cookies(cookies)

    async def get_storage_state(self, session_id: str) -> Any:
        ctx = self._contexts.get(session_id)
        if not ctx:
            return None
        return await ctx.storage_state()

    async def set_storage_state(self, session_id: str, state: Any) -> None:
        # Playwright doesn't support setting storage state on an existing context
        # This is typically done at context creation time.
        pass

    # ------------------------------------------------------------------ #
    # Teardown
    # ------------------------------------------------------------------ #

    async def stop(self) -> None:
        """Tear down ALL contexts and the underlying browser process."""
        for ctx in list(self._contexts.values()):
            try:
                await ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        for proxy in list(self._proxies.values()):
            await proxy.close()
        self._proxies.clear()
        self._pages.clear()
        self._page_policies.clear()
        if self._network is not None:
            await self._network.close()
            self._network = None

        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        if self._default_proxy is not None:
            await self._default_proxy.close()
            self._default_proxy = None
        self._managed_browser = False
        if self._profile is not None:
            await asyncio.to_thread(self._profile.cleanup)
            self._profile = None
        _log.info("Playwright provider stopped")


def _fresh_network_profile() -> tempfile.TemporaryDirectory[str]:
    """Native Chrome prefs apply where the content-shell-only CLI switch does not."""
    profile = tempfile.TemporaryDirectory(prefix="atlas-browser-")
    default = Path(profile.name) / "Default"
    default.mkdir()
    (default / "Preferences").write_text(
        json.dumps(
            {
                "webrtc": {
                    "ip_handling_policy": "disable_non_proxied_udp",
                    "multiple_routes_enabled": False,
                    "nonproxied_udp_enabled": False,
                },
                "network_prediction_options": 2,
            }
        ),
        encoding="utf-8",
    )
    return profile
