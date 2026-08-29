"""OpenAlex works API — keyless scholarly index (240M+ works).

source_kind='official'. No API key; OpenAlex asks only for a `mailto` in the
polite pool, which is config-driven and omitted when unset.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.providers.knowledge.scholarly import (
    abstract_from_inverted_index,
    authors_from_openalex,
    clean_text,
    normalize_doi,
    parse_date,
)
from atlas.capabilities.registry.capability import Capability

_BASE = "https://api.openalex.org/works"


class OpenAlexProvider:
    name = "openalex"
    capability = Capability.KNOWLEDGE
    is_local = False
    requires_auth = False
    source_kind = "official"

    def __init__(self, timeout_s: float = 15.0, *, mailto: str = "") -> None:
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)
        self._mailto = mailto.strip()

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get(_BASE, params=self._params("test", 1))
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def _params(self, query: str, limit: int) -> dict[str, str]:
        params = {
            "search": query,
            "per-page": str(max(1, min(limit, 25))),
            "select": "id,doi,title,display_name,publication_date,publication_year,"
            "authorships,primary_location,cited_by_count,abstract_inverted_index,ids",
        }
        if self._mailto:
            params["mailto"] = self._mailto
        return params

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        try:
            r = await self._client.get(_BASE, params=self._params(query, limit))
            r.raise_for_status()
            payload = r.json()
        except Exception:  # a dead index must never break the fan-out
            return []

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return []

        items: list[KnowledgeItem] = []
        for work in results[:limit]:
            if not isinstance(work, dict):
                continue
            item = self._to_item(work)
            if item is not None:
                items.append(item)
        return items

    def _to_item(self, work: dict[str, Any]) -> KnowledgeItem | None:
        title = clean_text(work.get("title") or work.get("display_name"), limit=400)
        if not title:
            return None
        doi = normalize_doi(work.get("doi"))
        landing = ""
        primary = work.get("primary_location")
        venue = ""
        if isinstance(primary, dict):
            landing = str(primary.get("landing_page_url") or "")
            source = primary.get("source")
            if isinstance(source, dict):
                venue = clean_text(source.get("display_name"), limit=200)
        url = landing or (f"https://doi.org/{doi}" if doi else str(work.get("id") or "")) or None

        abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
        snippet = abstract or f"{title} ({venue})" if venue else abstract or title

        external: dict[str, str] = {}
        ids = work.get("ids")
        if isinstance(ids, dict):
            for key in ("openalex", "pmid", "pmcid", "mag"):
                value = ids.get(key)
                if value:
                    external[key] = str(value)

        cited = work.get("cited_by_count")
        return KnowledgeItem(
            title=title,
            snippet=snippet,
            url=url,
            published=parse_date(work.get("publication_date") or work.get("publication_year")),
            provenance=Provenance(
                provider=self.name,
                source_kind=SourceKind.OFFICIAL,
                uri=url,
                retrieved_ts=datetime.now(UTC),
            ),
            authors=authors_from_openalex(work.get("authorships")),
            doi=doi,
            venue=venue,
            citation_count=int(cited) if isinstance(cited, int) else None,
            external_ids=external,
        )

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")), limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=0.5)

    async def shutdown(self) -> None:
        await self._client.aclose()
