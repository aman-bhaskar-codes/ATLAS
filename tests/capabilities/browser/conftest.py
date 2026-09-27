"""Shared guards for the browser *provider* tests.

`test_playwright` and `test_provider_swap` are true integration tests: they launch
a real headless Chromium (the swap test additionally spawns Chrome with a CDP
debugging port). On a developer/CI machine with a browser installed they run in
full; in a sandbox with no launchable browser (or no egress to bind the CDP port)
they cannot pass and must SKIP rather than FAIL — a missing browser is an absent
environment, not a defect in our code.

`browser_available` probes launchability exactly once per session (cached) by
actually starting and tearing down a headless Chromium. Tests depend on it and
`pytest.skip` when the probe reports the browser can't start.
"""

from __future__ import annotations

import asyncio

import pytest

_probe_cache: bool | None = None


async def _try_launch() -> bool:
    """True iff a headless Chromium can actually launch here. Any failure
    (missing binary, sandbox restriction, cert/trust errors) → False."""
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return False
    try:
        pw = await async_playwright().start()
    except Exception:
        return False
    try:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        await browser.close()
        return True
    except Exception:
        return False
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


@pytest.fixture(scope="session")
def browser_available() -> bool:
    global _probe_cache
    if _probe_cache is None:
        try:
            _probe_cache = asyncio.run(_try_launch())
        except Exception:
            _probe_cache = False
    return _probe_cache


@pytest.fixture
def require_browser(browser_available: bool) -> None:
    if not browser_available:
        pytest.skip("no launchable browser in this environment (integration test)")
