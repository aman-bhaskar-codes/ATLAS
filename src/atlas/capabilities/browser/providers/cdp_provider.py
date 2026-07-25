"""PlaywrightProvider adapts the playwright async API to the BrowserProvider protocol."""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
)

from atlas.capabilities.browser.domain.locator import Locator
from atlas.capabilities.browser.errors import ProviderError
from atlas.capabilities.browser.providers.capabilities import ProviderCapabilities

_log = logging.getLogger("atlas.browser.providers.cdp")


class CDPProvider:
    """CDP-backed BrowserProvider to prove protocol swappability.

    Connects to a remote or local CDP port instead of using standard launch.
    """

    name: str = "cdp"
    is_local: bool = False

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        # Maps atlas session_id → playwright BrowserContext
        self._contexts: dict[str, BrowserContext] = {}
        # Maps atlas session_id → {tab_id → playwright Page}
        self._pages: dict[str, dict[str, Page]] = {}

        self.capabilities = ProviderCapabilities(
            supports_cdp=True,  # type: ignore
            supports_extensions=False,
            supports_stealth=False,
            supports_headful=True,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        
        # Start a local Chrome process with remote debugging enabled
        # In a real CDP setup, this would be a remote endpoint like browserbase or a running docker container.
        # For the test, we launch it via playwright's executable path in a subprocess to expose the CDP port.
        self._pw = await async_playwright().start()
        exe_path = self._pw.chromium.executable_path
        
        self._subproc = subprocess.Popen([
            exe_path,
            "--headless=new",
            "--remote-debugging-port=9222",
            "--disable-gpu",
            "--no-sandbox"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Wait for the port to bind
        for _ in range(10):
            await asyncio.sleep(0.5)
            try:
                self._browser = await self._pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
                _log.info("CDP browser connected on port 9222")
                break
            except Exception:
                continue
        else:
            raise RuntimeError("Failed to connect to CDP browser")

    async def _cleanup(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if hasattr(self, "_subproc") and self._subproc:
            self._subproc.terminate()
            try:
                self._subproc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._subproc.kill()
                self._subproc.wait(timeout=1)
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def launch(
        self,
        *,
        profile: str | None,
        incognito: bool,
        sandbox_spec: Any,
    ) -> str:
        """Create a new browser context and return its session_id."""
        await self._ensure_browser()
        # Use a synthetic id based on object address
        ctx = await self._browser.new_context()
        session_id = str(id(ctx))
        self._contexts[session_id] = ctx
        self._pages[session_id] = {}
        _log.info("Launched Playwright context session_id=%s", session_id)
        return session_id

    async def attach(self, endpoint: str) -> str:
        await self._ensure_browser()
        # Minimal CDP attach — connect to remote browser via CDP endpoint
        ctx = await self._browser.new_context()
        session_id = str(id(ctx))
        self._contexts[session_id] = ctx
        self._pages[session_id] = {}
        return session_id

    async def health(self, session_id: str) -> bool:
        return session_id in self._contexts

    async def close(self, session_id: str) -> None:
        ctx = self._contexts.pop(session_id, None)
        self._pages.pop(session_id, None)
        if ctx:
            await ctx.close()

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
        return tab_id

    async def list_tabs(self, session_id: str) -> list[str]:
        return list(self._pages.get(session_id, {}).keys())

    async def close_tab(self, session_id: str, tab_id: str) -> None:
        page = self._get_page(session_id, tab_id)
        await page.close()
        self._pages[session_id].pop(tab_id, None)

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

    async def type_text(
        self, session_id: str, tab_id: str, locator: Locator, text: str
    ) -> None:
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

    async def await_download(
        self, session_id: str, tab_id: str
    ) -> tuple[str, str]:
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
        self._pages.clear()

        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        _log.info("Playwright provider stopped")
