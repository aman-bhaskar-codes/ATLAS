"""Scholarly federation providers (§5): OpenAlex, Crossref, Semantic Scholar, SearXNG.

Fakes only — no network. Each provider must (a) normalize into the ONE internal
`KnowledgeItem` schema with its scholarly metadata intact, and (b) return `[]`
rather than raising when the upstream index misbehaves, because one dead source
must never break the fan-out (§22).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from atlas.capabilities.domain.common import SourceKind
from atlas.capabilities.providers.knowledge.crossref import CrossrefProvider
from atlas.capabilities.providers.knowledge.openalex import OpenAlexProvider
from atlas.capabilities.providers.knowledge.scholarly import (
    abstract_from_inverted_index,
    normalize_doi,
    parse_date,
    strip_markup,
)
from atlas.capabilities.providers.knowledge.searxng import SearxngProvider
from atlas.capabilities.providers.knowledge.semantic_scholar import SemanticScholarProvider


def _resp(payload: Any) -> MagicMock:
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


# ── shared parsing helpers ──────────────────────────────────────────────
def test_normalize_doi_strips_every_prefix_form() -> None:
    for raw in ("10.1000/abc", "https://doi.org/10.1000/abc", "http://doi.org/10.1000/abc", "doi:10.1000/abc"):
        assert normalize_doi(raw) == "10.1000/abc"
    assert normalize_doi(None) == ""


def test_parse_date_accepts_iso_year_and_crossref_parts() -> None:
    assert parse_date("2024-03-05") is not None
    assert parse_date(2024) is not None
    assert parse_date([2024, 3, 5]) is not None
    # A malformed date loses the date, never the document.
    assert parse_date("not a date") is None
    assert parse_date(None) is None


def test_inverted_index_abstract_is_reconstructed_in_order() -> None:
    text = abstract_from_inverted_index({"Autonomous": [0], "adaptation": [1], "matters": [2]})
    assert text.startswith("Autonomous adaptation matters")
    assert abstract_from_inverted_index(None) == ""


def test_strip_markup_removes_jats_tags() -> None:
    assert "<jats:p>" not in strip_markup("<jats:p>Evaluation harness</jats:p>")
    assert "Evaluation harness" in strip_markup("<jats:p>Evaluation harness</jats:p>")


# ── OpenAlex ────────────────────────────────────────────────────────────
_OPENALEX = {
    "results": [
        {
            "display_name": "Autonomous Adaptation in Agents",
            "doi": "https://doi.org/10.1000/adapt",
            "publication_date": "2026-01-15",
            "cited_by_count": 42,
            "abstract_inverted_index": {"Adaptive": [0], "evaluation": [1]},
            "primary_location": {
                "landing_page_url": "https://example.org/adapt",
                "source": {"display_name": "Journal of Adaptation"},
            },
            "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
            "ids": {"openalex": "W123", "pmid": "999"},
        }
    ]
}


async def test_openalex_normalizes_scholarly_metadata() -> None:
    provider = OpenAlexProvider(mailto="research@example.org")
    with patch("httpx.AsyncClient.get", return_value=_resp(_OPENALEX)):
        items = await provider.search("autonomous adaptation", limit=5)

    assert len(items) == 1
    item = items[0]
    assert item.title == "Autonomous Adaptation in Agents"
    assert item.doi == "10.1000/adapt"  # prefix stripped
    assert item.authors == ("Ada Lovelace",)
    assert item.venue == "Journal of Adaptation"
    assert item.citation_count == 42
    assert "Adaptive evaluation" in item.snippet  # inverted index reconstructed
    assert item.url == "https://example.org/adapt"
    assert item.published is not None
    assert item.provenance.source_kind is SourceKind.OFFICIAL
    # flat, JSON-safe citation view for the document metadata
    meta = item.citation_metadata()
    assert meta["doi"] == "10.1000/adapt"
    assert meta["authors"] == "Ada Lovelace"


async def test_openalex_sends_mailto_only_when_configured() -> None:
    with patch("httpx.AsyncClient.get", return_value=_resp({"results": []})) as get:
        await OpenAlexProvider(mailto="research@example.org").search("q", limit=2)
        assert get.call_args.kwargs["params"]["mailto"] == "research@example.org"

    with patch("httpx.AsyncClient.get", return_value=_resp({"results": []})) as get:
        await OpenAlexProvider().search("q", limit=2)
        assert "mailto" not in get.call_args.kwargs["params"]


async def test_openalex_returns_empty_on_http_error() -> None:
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("down")):
        assert await OpenAlexProvider().search("q", limit=3) == []


async def test_openalex_health_is_false_when_unreachable() -> None:
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("down")):
        assert await OpenAlexProvider().health() is False


# ── Crossref ────────────────────────────────────────────────────────────
_CROSSREF = {
    "message": {
        "items": [
            {
                "DOI": "10.5555/eval",
                "title": ["Evaluating Adaptive Systems"],
                "abstract": "<jats:p>A benchmark for adaptation.</jats:p>",
                "author": [{"given": "Grace", "family": "Hopper"}, {"name": "Consortium X"}],
                "issued": {"date-parts": [[2025, 11, 2]]},
                "container-title": ["Transactions on Evaluation"],
                "URL": "https://doi.org/10.5555/eval",
                "is-referenced-by-count": 7,
                "type": "journal-article",
            }
        ]
    }
}


async def test_crossref_normalizes_dates_authors_and_markup() -> None:
    with patch("httpx.AsyncClient.get", return_value=_resp(_CROSSREF)):
        items = await CrossrefProvider(mailto="research@example.org").search("evaluating adaptive", limit=5)

    assert len(items) == 1
    item = items[0]
    assert item.title == "Evaluating Adaptive Systems"
    assert item.doi == "10.5555/eval"
    assert item.authors == ("Grace Hopper", "Consortium X")
    assert item.venue == "Transactions on Evaluation"
    assert item.citation_count == 7
    assert "<jats:p>" not in item.snippet
    assert item.published is not None and item.published.year == 2025
    assert item.external_ids.get("crossref_type") == "journal-article"


async def test_crossref_polite_pool_user_agent_carries_mailto() -> None:
    with_mail = CrossrefProvider(mailto="research@example.org")
    without = CrossrefProvider()
    assert "mailto:research@example.org" in with_mail._client.headers["User-Agent"]
    assert "mailto" not in without._client.headers["User-Agent"]
    await with_mail.shutdown()
    await without.shutdown()


async def test_crossref_skips_untitled_works_and_survives_errors() -> None:
    with patch("httpx.AsyncClient.get", return_value=_resp({"message": {"items": [{"DOI": "10.1/x"}]}})):
        assert await CrossrefProvider().search("q", limit=3) == []
    with patch("httpx.AsyncClient.get", side_effect=httpx.ReadTimeout("slow")):
        assert await CrossrefProvider().search("q", limit=3) == []


# ── Semantic Scholar ────────────────────────────────────────────────────
_S2 = {
    "data": [
        {
            "title": "Autonomous Adaptation Benchmarks",
            "abstract": "We measure self-improvement.",
            "url": "https://www.semanticscholar.org/paper/abc",
            "venue": "NeurIPS",
            "year": 2026,
            "publicationDate": "2026-02-01",
            "citationCount": 3,
            "authors": [{"name": "Alan Turing"}],
            "externalIds": {"ArXiv": "2602.00001", "DOI": "10.7777/bench", "CorpusId": 55},
            "openAccessPdf": {"url": "https://example.org/bench.pdf"},
        }
    ]
}


async def test_semantic_scholar_extracts_arxiv_doi_and_pdf_ids() -> None:
    with patch("httpx.AsyncClient.get", return_value=_resp(_S2)):
        items = await SemanticScholarProvider().search("autonomous adaptation", limit=5)

    assert len(items) == 1
    item = items[0]
    assert item.arxiv_id == "2602.00001"
    assert item.doi == "10.7777/bench"
    assert item.venue == "NeurIPS"
    assert item.authors == ("Alan Turing",)
    assert item.citation_count == 3
    assert item.published is not None
    assert item.external_ids.get("corpusid") == "55"
    assert item.external_ids.get("open_access_pdf") == "https://example.org/bench.pdf"


async def test_semantic_scholar_is_usable_without_a_key() -> None:
    # Deliberate: a rate-limited scholarly source still beats no scholarly source.
    assert SemanticScholarProvider().requires_auth is False
    keyed = SemanticScholarProvider(api_key="secret-value")
    assert keyed._client.headers.get("x-api-key") == "secret-value"
    assert "x-api-key" not in SemanticScholarProvider()._client.headers
    await keyed.shutdown()


async def test_semantic_scholar_returns_empty_on_rate_limit() -> None:
    with patch("httpx.AsyncClient.get", side_effect=httpx.HTTPError("429")):
        assert await SemanticScholarProvider().search("q", limit=3) == []


# ── SearXNG (keyless web meta-search; no paid default, §26) ─────────────
_SEARX = {
    "results": [
        {
            "title": "Adaptation blog",
            "url": "https://example.com/post",
            "content": "Notes on evaluation loops.",
            "publishedDate": "2026-03-01T00:00:00Z",
            "engine": "duckduckgo",
        }
    ]
}


async def test_searxng_maps_results_to_web_trust() -> None:
    with patch("httpx.AsyncClient.get", return_value=_resp(_SEARX)):
        items = await SearxngProvider("https://searx.example.org", engines="google,bing").search("q", limit=5)

    assert len(items) == 1
    item = items[0]
    assert item.title == "Adaptation blog"
    assert item.url == "https://example.com/post"
    assert "evaluation loops" in item.snippet
    # An instance may be local, but its results are still web-trust.
    assert item.provenance.source_kind is SourceKind.WEB


async def test_searxng_without_an_instance_is_a_no_op() -> None:
    provider = SearxngProvider("")
    assert provider.is_local is False
    assert await provider.search("q", limit=3) == []
    assert await provider.health() is False


async def test_searxng_returns_empty_when_instance_errors() -> None:
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("no instance")):
        assert await SearxngProvider("https://searx.example.org").search("q", limit=3) == []
