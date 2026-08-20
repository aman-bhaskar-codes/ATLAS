from unittest.mock import MagicMock, patch

import pytest

from atlas.capabilities.providers.currency.frankfurter import FrankfurterProvider


@pytest.mark.asyncio
async def test_frankfurter_convert_success() -> None:
    provider = FrankfurterProvider()

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR"
        mock_resp.json.return_value = {
            "base": "USD",
            "date": "2026-08-19",
            "rates": {"EUR": 0.85}
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        rate = await provider.convert(base="USD", target="EUR")

        assert rate is not None
        assert rate.base == "USD"
        assert rate.target == "EUR"
        assert rate.rate == 0.85
        assert rate.date == "2026-08-19"
        assert rate.provenance.provider == "frankfurter"


@pytest.mark.asyncio
async def test_frankfurter_convert_failure_returns_none() -> None:
    provider = FrankfurterProvider()
    with patch("httpx.AsyncClient.get", side_effect=Exception("network down")):
        rate = await provider.convert(base="USD", target="EUR")
        assert rate is None
