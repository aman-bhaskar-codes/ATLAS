"""Capability catalog tests — bundled seed, relevance search, free-first."""

from __future__ import annotations

from atlas.capabilities.public_api.catalog import CatalogEntry, PublicAPICatalog


def test_load_default_seeds_curated_catalog() -> None:
    catalog = PublicAPICatalog.load_default()
    assert len(catalog) >= 10
    meteo = catalog.get("open_meteo")
    assert meteo is not None
    assert meteo.https is True
    assert meteo.free is True
    assert "Weather" in catalog.categories()


def test_search_ranks_relevant_weather_apis_first() -> None:
    catalog = PublicAPICatalog.load_default()
    hits = catalog.search("current weather forecast")
    assert hits, "expected weather matches"
    top_ids = [entry.api_id for entry, _ in hits]
    assert "open_meteo" in top_ids[:3] or "wttr_in" in top_ids[:3]
    # every hit must have some token overlap with the intent
    assert all(score > 0 for _, score in hits)


def test_search_empty_intent_returns_nothing() -> None:
    catalog = PublicAPICatalog.load_default()
    assert catalog.search("the of for") == []


def test_search_category_filter() -> None:
    catalog = PublicAPICatalog.load_default()
    hits = catalog.search("weather forecast", category="Currency")
    assert hits == []


def test_free_first_bonus_applied() -> None:
    paid = CatalogEntry(
        api_id="p",
        name="Paid Weather",
        description="weather forecast",
        category="Weather",
        auth="apiKey",
        url="https://p.test",
        free=False,
    )
    free = CatalogEntry(
        api_id="f",
        name="Free Weather",
        description="weather forecast",
        category="Weather",
        auth="No",
        url="https://f.test",
        free=True,
    )
    catalog = PublicAPICatalog((paid, free))
    hits = dict(catalog.search("weather forecast"))
    # identical keyword overlap; free + keyless entry must score strictly higher
    assert hits[free] > hits[paid]


def test_needs_key_detection() -> None:
    anon = CatalogEntry(api_id="a", name="A", description="", category="X", auth="No", url="https://a.test")
    keyed = CatalogEntry(api_id="k", name="K", description="", category="X", auth="apiKey", url="https://k.test")
    assert anon.needs_key is False
    assert keyed.needs_key is True
