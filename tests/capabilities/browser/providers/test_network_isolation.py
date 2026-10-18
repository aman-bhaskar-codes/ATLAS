"""Unprotected external CDP tabs cannot silently become managed sessions."""

import pytest

from atlas.capabilities.browser.errors import ProviderError
from atlas.capabilities.browser.providers.cdp_provider import CDPProvider
from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider


@pytest.mark.parametrize("provider_type", [PlaywrightProvider, CDPProvider])
async def test_external_attach_fails_without_starting_or_modifying_browser(provider_type: type) -> None:
    provider = provider_type()
    with pytest.raises(ProviderError, match=r"unprotected|isolation"):
        await provider.attach("http://127.0.0.1:9222")
    assert provider._browser is None and provider._pw is None
