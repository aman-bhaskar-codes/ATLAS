from unittest.mock import MagicMock, patch

import pytest

from atlas.capabilities.providers.location.nominatim import NominatimProvider


@pytest.mark.asyncio
async def test_nominatim_geocode_success() -> None:
    provider = NominatimProvider()

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://nominatim.openstreetmap.org/search?q=Berlin&format=json&limit=1"
        mock_resp.json.return_value = [{
            "lat": "52.5200",
            "lon": "13.4050",
            "display_name": "Berlin, Germany",
            "address": {"country": "Germany", "city": "Berlin"}
        }]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = await provider.geocode("Berlin")

        assert result is not None
        assert result.query == "Berlin"
        assert result.display_name == "Berlin, Germany"
        assert result.latitude == 52.5200
        assert result.longitude == 13.4050
        assert result.country == "Germany"
        assert result.provenance.provider == "nominatim"


@pytest.mark.asyncio
async def test_nominatim_geocode_empty_results() -> None:
    provider = NominatimProvider()

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = await provider.geocode("nonexistentplace12345")
        assert result is None


@pytest.mark.asyncio
async def test_nominatim_country_info_success() -> None:
    provider = NominatimProvider()

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://restcountries.com/v3.1/name/Germany?fields=name,capital,region,currencies,languages,timezones"
        mock_resp.json.return_value = [{
            "name": {"common": "Germany", "official": "Federal Republic of Germany"},
            "capital": ["Berlin"],
            "region": "Europe",
            "currencies": {"EUR": {"name": "Euro", "symbol": "€"}},
            "languages": {"deu": "German"},
            "timezones": ["Europe/Berlin"]
        }]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = await provider.country_info("Germany")

        assert result is not None
        assert result.name == "Germany"
        assert result.capital == "Berlin"
        assert result.region == "Europe"
        assert result.currencies == ("EUR",)
        assert result.languages == ("German",)
        assert result.timezones == ("Europe/Berlin",)
        assert result.provenance.provider == "nominatim"


@pytest.mark.asyncio
async def test_nominatim_country_info_failure_returns_none() -> None:
    provider = NominatimProvider()
    with patch("httpx.AsyncClient.get", side_effect=Exception("network down")):
        result = await provider.country_info("Germany")
        assert result is None