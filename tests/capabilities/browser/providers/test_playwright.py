import pytest

from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider


@pytest.mark.asyncio
async def test_playwright_provider_lifecycle() -> None:
    provider = PlaywrightProvider()

    # Create a session (this starts the browser lazily via launch)
    session_id = await provider.launch(profile=None, incognito=False, sandbox_spec=None)
    assert session_id
    assert session_id in provider._contexts

    # Create tab
    tab_id = await provider.new_tab(session_id)
    assert tab_id in provider._pages[session_id]

    # Health check
    assert await provider.health(session_id) is True

    # Close tab
    await provider.close_tab(session_id, tab_id)
    assert tab_id not in provider._pages[session_id]

    # Close session
    await provider.close(session_id)
    assert session_id not in provider._contexts

    # Stop provider
    await provider.stop()
    assert provider._browser is None
    assert provider._pw is None
