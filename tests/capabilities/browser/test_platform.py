import pytest
from unittest.mock import AsyncMock, MagicMock

from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.domain.locator import Locator, LocatorKind
from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.infra.ids import CorrelationId


def _make_platform() -> BrowserPlatform:
    return BrowserPlatform(
        session_manager=AsyncMock(),
        page_manager=AsyncMock(),
        state_builder=AsyncMock(),
        navigation_engine=AsyncMock(),
        dom_engine=AsyncMock(),
        screenshot_engine=AsyncMock(),
        extraction_engine=AsyncMock(),
        locator_engine=AsyncMock(),
        network_engine=AsyncMock(),
        click_engine=AsyncMock(),
        type_engine=AsyncMock(),
        submit_engine=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_platform_create_session_delegates():
    platform = _make_platform()
    platform._sessions.acquire.return_value = "session_obj"  # type: ignore[attr-defined]

    result = await platform.create_session(profile=None, incognito=False)
    assert result == "session_obj"
    platform._sessions.acquire.assert_awaited_once_with(profile=None, incognito=False)


@pytest.mark.asyncio
async def test_platform_goto_delegates():
    platform = _make_platform()
    handle = PageHandle(session_id="sess1", tab_id="tab1")
    cid = CorrelationId("cid-1")

    platform._nav.goto.return_value = MagicMock()  # type: ignore[attr-defined]
    await platform.goto(handle, "https://example.com", cid)

    platform._nav.goto.assert_awaited_once_with(handle, "https://example.com", cid)


@pytest.mark.asyncio
async def test_platform_click_delegates():
    platform = _make_platform()
    handle = PageHandle(session_id="sess1", tab_id="tab1")
    locator = Locator(kind=LocatorKind.CSS, value="button#submit")
    cid = CorrelationId("cid-2")

    platform._click.click.return_value = MagicMock()  # type: ignore[attr-defined]
    await platform.click(handle, locator, cid)
    platform._click.click.assert_awaited_once_with(handle, locator, cid)
