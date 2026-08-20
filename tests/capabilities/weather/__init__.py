from unittest.mock import MagicMock, patch

import pytest

from atlas.capabilities.providers.weather.open_meteo import OpenMeteoProvider


@pytest.mark.asyncio
async def test_open_meteo_forecast_success() -> None:
    provider = OpenMeteoProvider()

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://api.open-meteo.com/v1/forecast?..."
        mock_resp.json.return_value = {
            "latitude": 52.52,
            "longitude": 13.41,
            "timezone": "Europe/Berlin",
            "current": {"temperature_2m": 18.3, "weather_code": 3},
            "daily": {
                "time": ["2026-08-20"],
                "temperature_2m_min": [14.1],
                "temperature_2m_max": [22.4],
                "precipitation_sum": [0.0],
                "weather_code": [3],
            },
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        report = await provider.forecast(latitude=52.52, longitude=13.41, days=1)

        assert report.current_temp_c == 18.3
        assert len(report.daily) == 1
        assert report.daily[0].temp_max_c == 22.4
        assert report.provenance.provider == "open_meteo"


@pytest.mark.asyncio
async def test_open_meteo_forecast_failure_returns_empty_report() -> None:
    provider = OpenMeteoProvider()
    with patch("httpx.AsyncClient.get", side_effect=Exception("network down")):
        report = await provider.forecast(latitude=0.0, longitude=0.0)
        assert report.current_temp_c is None
        assert report.daily == ()