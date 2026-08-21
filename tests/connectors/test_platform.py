"""PublicAPIPlatform tests — discover → validate → execute with a fake fetcher.

Covers acceptance Scenarios 5 (weather via validated provider) and
6 (unknown API stays DISCOVERED/UNVALIDATED, execution refused).
"""

from __future__ import annotations

import pytest

from atlas.capabilities.domain.common import SourceKind
from atlas.capabilities.public_api.catalog import PublicAPICatalog
from atlas.capabilities.public_api.connector import ConnectorRegistry, ConnectorStatus
from atlas.capabilities.public_api.platform import ConnectorNotExecutableError, PublicAPIPlatform
from atlas.capabilities.public_api.retrieval import CapabilityRetriever
from atlas.capabilities.public_api.validation import ConnectorValidator, FetchResponse


class FakeFetcher:
    """Scripted transport — tests never touch the network."""

    def __init__(
        self, status: int = 200, body: str = '{"temperature": 21.5}', content_type: str = "application/json"
    ) -> None:
        self._resp = FetchResponse(status=status, body=body, content_type=content_type)
        self.requests: list[str] = []

    async def get(self, url: str, *, timeout_s: float = 10.0) -> FetchResponse:
        self.requests.append(url)
        return self._resp


def _platform(fetcher: FakeFetcher | None = None) -> tuple[PublicAPIPlatform, FakeFetcher]:
    fetcher = fetcher or FakeFetcher()
    catalog = PublicAPICatalog.load_default()
    connectors = ConnectorRegistry(catalog)
    validator = ConnectorValidator(fetcher)
    retriever = CapabilityRetriever(catalog, connectors)
    return PublicAPIPlatform(catalog, connectors, validator, retriever), fetcher


async def test_execute_on_discovered_connector_is_refused() -> None:
    """Scenario 6: a discovered, unvalidated API must never execute."""
    platform, fetcher = _platform()
    with pytest.raises(ConnectorNotExecutableError, match="DISCOVERED/UNVALIDATED — execution refused"):
        await platform.execute("open_meteo")
    assert fetcher.requests == []  # not a single request escaped
    assert platform.connectors.get("open_meteo").status is ConnectorStatus.DISCOVERED  # type: ignore[union-attr]


async def test_validate_then_execute_returns_untrusted_provenanced_data() -> None:
    """Scenario 5: weather works only AFTER validation promotes the connector."""
    platform, fetcher = _platform()
    result = await platform.validate("open_meteo")
    assert result.ok is True
    assert platform.connectors.get("open_meteo").status is ConnectorStatus.VALIDATED  # type: ignore[union-attr]

    out = await platform.execute("open_meteo", params={"latitude": "52.5", "longitude": "13.4"})
    assert out.ok is True
    assert out.trust == "untrusted"  # external data is never trusted
    assert out.payload == {"temperature": 21.5}
    assert out.provenance.provider == "Open-Meteo"
    assert out.provenance.source_kind is SourceKind.WEB
    assert "latitude=52.5" in fetcher.requests[-1]


async def test_failed_validation_demotes_back_to_discovered() -> None:
    platform, _ = _platform(FakeFetcher(status=503, body="unavailable"))
    result = await platform.validate("open_meteo")
    assert result.ok is False
    assert platform.connectors.get("open_meteo").status is ConnectorStatus.DISCOVERED  # type: ignore[union-attr]
    with pytest.raises(ConnectorNotExecutableError):
        await platform.execute("open_meteo")


async def test_keyed_api_validation_is_honest_refusal() -> None:
    platform, fetcher = _platform()
    result = await platform.validate("github")  # auth "apiKey (optional)"
    assert result.ok is False
    assert "credential" in result.note
    assert fetcher.requests == []  # never probed anonymously


async def test_validate_unknown_api_id_fails_cleanly() -> None:
    platform, _ = _platform()
    result = await platform.validate("nope")
    assert result.ok is False
    assert "unknown api_id" in result.note


async def test_execute_unknown_api_id_raises() -> None:
    platform, _ = _platform()
    with pytest.raises(ConnectorNotExecutableError, match="unknown api_id"):
        await platform.execute("nope")


def test_discover_returns_candidates_ranked_validated_first() -> None:
    platform, _ = _platform()
    candidates = platform.discover("weather forecast")
    assert candidates, "expected weather candidates"
    assert all(not c.executable for c in candidates)  # nothing executable pre-validation
    # promote one, then it must outrank higher-relevance unvalidated peers
    platform.connectors.promote("wttr_in", ConnectorStatus.VALIDATED, note="probe → HTTP 200")
    ranked = platform.discover("weather forecast")
    assert ranked[0].entry.api_id == "wttr_in"
    assert ranked[0].executable is True


def test_explain_limitation_is_honest() -> None:
    platform, _ = _platform()
    explanation = platform.explain_limitation("teleportation to mars")
    assert "no catalog capability matches" in explanation
    weather = platform.explain_limitation("weather forecast")
    assert "requires validation before execution" in weather


async def test_normalization_bounds_external_payload() -> None:
    deep = '{"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}'
    platform, _ = _platform(FakeFetcher(body=deep))
    await platform.validate("open_meteo")
    out = await platform.execute("open_meteo")
    assert out.ok is True
    # depth-limited: the innermost value is replaced by a truncation marker
    assert "truncated depth" in str(out.payload)
