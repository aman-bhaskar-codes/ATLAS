"""LiveBridge — existing KnowledgeProviders feed the canonical pipeline (§7).

The 9 providers (arxiv, brave, ddg, github_releases, rss, tavily, wikipedia,
memory_source, parametric) already implement `search(query) -> KnowledgeItem`.
Instead of summarizing their snippets straight into an LLM (old pipe A), the
fabric ingests each item as a document: indexed, retrievable, citable — and
deduped on content hash so repeat fan-outs are nearly free (§24).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from atlas.infra.logging import get_logger
from atlas.knowledge.domain import IngestionJob, SourceType
from atlas.knowledge.ingestion import IngestionPipeline

_log = get_logger("atlas.knowledge.live_bridge")

# provider name → source taxonomy (§5); unknown providers default to WEB_PAGE.
_PROVIDER_SOURCE: dict[str, SourceType] = {
    "arxiv": SourceType.ARXIV,
    "openalex": SourceType.SEMANTIC_SCHOLAR,  # scholarly index; shares the 0.8 authority floor
    "semantic_scholar": SourceType.SEMANTIC_SCHOLAR,
    "crossref": SourceType.CROSSREF,
    "wikipedia": SourceType.WEB_PAGE,
    "github_releases": SourceType.GITHUB,
    "rss": SourceType.RSS,
    "searxng": SourceType.WEB_PAGE,
    "parametric": SourceType.KNOWLEDGE_GRAPH,
    "memory_source": SourceType.MEMORY,
}


class ItemLike(Protocol):
    title: str
    snippet: str
    url: str | None
    published: object | None


class ProviderLike(Protocol):
    name: str

    async def search(self, query: str, *, limit: int) -> list[ItemLike]: ...


class LiveBridge:
    """Fan-out providers in parallel, ingest every item into the fabric."""

    def __init__(
        self,
        providers: list[ProviderLike],
        pipeline: IngestionPipeline,
        *,
        per_provider_limit: int = 4,
        timeout_s: float = 8.0,
    ) -> None:
        self._providers = providers
        self._pipeline = pipeline
        self._limit = per_provider_limit
        self._timeout = timeout_s

    async def gather(self, query: str) -> list[IngestionJob]:
        results = await asyncio.gather(*(self._search_one(p, query) for p in self._providers), return_exceptions=False)
        jobs: list[IngestionJob] = []
        for provider, items in results:
            source_type = _source_type_for(provider)
            for item in items:
                if not item.snippet.strip():  # a bare title carries no information
                    continue
                content = _item_content(item)
                if not content.strip():
                    continue
                try:
                    citation = _citation_metadata(item)
                    job = await self._pipeline.ingest(
                        source_id=f"{provider}:{item.url or item.title}",
                        source_type=source_type,
                        content=content,
                        title=item.title,
                        uri=item.url or "",
                        content_type="text/markdown",
                        author=citation.get("authors", ""),
                        published_at=_as_datetime(item.published),
                        metadata={"provider": provider, **citation},
                        provenance={"pipe": "live", "provider": provider},
                    )
                    jobs.append(job)
                except Exception as exc:
                    _log.warning(
                        "live_bridge.ingest_failed", event_type="knowledge", provider=provider, error=repr(exc)
                    )
        _log.info("live_bridge.gathered", event_type="knowledge", query=query[:80], items=len(jobs))
        return jobs

    async def _search_one(self, provider: ProviderLike, query: str) -> tuple[str, list[ItemLike]]:
        try:
            items = await asyncio.wait_for(provider.search(query, limit=self._limit), timeout=self._timeout)
            return provider.name, list(items)
        except Exception as exc:  # one dead provider never kills the fan-out (§135)
            _log.warning("live_bridge.provider_failed", event_type="knowledge", provider=provider.name, error=repr(exc))
            return provider.name, []


def _source_type_for(provider: str) -> SourceType:
    """Exact name first, then the `rss:<vendor>` family prefix."""
    if provider in _PROVIDER_SOURCE:
        return _PROVIDER_SOURCE[provider]
    family = provider.split(":", 1)[0]
    return _PROVIDER_SOURCE.get(family, SourceType.WEB_PAGE)


def _citation_metadata(item: ItemLike) -> dict[str, str]:
    """Pull scholarly metadata off the item, tolerating providers that lack it.

    Read via getattr because ItemLike is structural: the nine legacy providers
    (and test fakes) emit items without these fields, and a missing DOI must
    never cost us the document.
    """
    fn = getattr(item, "citation_metadata", None)
    if not callable(fn):
        return {}
    try:
        raw = fn()
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}


def _item_content(item: ItemLike) -> str:
    parts = [f"# {item.title}", ""]
    # Citation lines live IN the document body so an extracted quote can be
    # attributed even when only the chunk survives into the prompt.
    meta = _citation_metadata(item)
    for label, key in (("Authors", "authors"), ("Venue", "venue"), ("DOI", "doi"), ("arXiv", "arxiv_id")):
        if meta.get(key):
            parts.append(f"{label}: {meta[key]}")
    if len(parts) > 2:
        parts.append("")
    parts.append(item.snippet)
    if item.url:
        parts += ["", f"Source: {item.url}"]
    return "\n".join(parts)


def _as_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None
