"""Tests for URLReputationChecker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.capabilities.browser.security.reputation import ReputationVerdict
from atlas.capabilities.browser.security.url_checker import URLReputationChecker


@pytest.mark.asyncio
async def test_no_keys_configured_returns_unknown() -> None:
    """Test that with no API keys, the checker returns UNKNOWN."""
    checker = URLReputationChecker(safe_browsing_api_key="", virustotal_api_key="")
    result = await checker.check("http://example.com")
    assert result.verdict == ReputationVerdict.UNKNOWN


@pytest.mark.asyncio
async def test_safe_browsing_flags_malicious_url() -> None:
    """Mock Safe Browsing API to return a malicious result."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "matches": [
            {
                "threatType": "MALWARE",
                "platformType": "ANY_PLATFORM",
                "threatEntryType": "URL",
                "threat": {"url": "http://malicious.com"},
            }
        ]
    }
    mock_response.status_code = 200

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        checker = URLReputationChecker(safe_browsing_api_key="test-key", virustotal_api_key="")
        result = await checker.check("http://malicious.com")
        assert result.verdict == ReputationVerdict.MALICIOUS
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_api_failure_fails_open_to_unknown() -> None:
    """Test that API failure returns UNKNOWN (fail-open)."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("API error")
        checker = URLReputationChecker(safe_browsing_api_key="test-key", virustotal_api_key="")
        result = await checker.check("http://example.com")
        assert result.verdict == ReputationVerdict.UNKNOWN


@pytest.mark.asyncio
async def test_cache_avoids_second_api_call() -> None:
    """Test that caching prevents duplicate API calls."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "matches": [
            {
                "threatType": "MALWARE",
                "platformType": "ANY_PLATFORM",
                "threatEntryType": "URL",
                "threat": {"url": "http://malicious.com"},
            }
        ]
    }
    mock_response.status_code = 200

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        checker = URLReputationChecker(safe_browsing_api_key="test-key", virustotal_api_key="")
        await checker.check("http://malicious.com")
        await checker.check("http://malicious.com")
        mock_post.assert_called_once()


@pytest.mark.parametrize("status", [204, 403, 429, 500])
async def test_virustotal_only_http_failures_are_unknown(status: int) -> None:
    import httpx

    checker = URLReputationChecker(virustotal_api_key="test-key")
    await checker._client.aclose()
    checker._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    try:
        result = await checker.check("https://example.test/")
        assert result.verdict == ReputationVerdict.UNKNOWN
    finally:
        await checker.shutdown()


async def test_virustotal_only_exception_is_unknown() -> None:
    import httpx

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unavailable", request=request)

    checker = URLReputationChecker(virustotal_api_key="test-key")
    await checker._client.aclose()
    checker._client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    try:
        assert (await checker.check("https://example.test/")).verdict == ReputationVerdict.UNKNOWN
    finally:
        await checker.shutdown()
