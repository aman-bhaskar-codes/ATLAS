"""Crossref works API — keyless DOI registry (authoritative publication metadata).

source_kind='official'. Crossref's polite pool wants a contact address in the
User-Agent; supplied via config when available, omitted otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.providers.knowledge.scholarly import (
    authors_from_crossref,
    clean_text,
    normalize_doi,
    parse_date,
    strip_markup,
)
from atlas.capabilities.registry.capability import Capability

_BASE = "https://api.crossref.org/works"
_SELECT = "DOI,title,abstract,author,issued,container-title,publisher,URL,is-referenced-by-count,type"


class CrossrefProvider:
    name = "crossref"
    capability = Capability.KNOWLEDGE
    is_local = False
    requires_auth = False
    source_kind = "official"

    def __init__(self, timeout_s: float = 15.0, *, mailto: str = "") -> None:
        ua = "ATLAS/1.0 (research agent)"
        if mailto.strip():
            ua = f"ATLAS/1.0 (research agent; mailto:{mailto.strip()})"
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers={"User-Agent": ua})

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get(_BASE, params={"query": "test", "rows": "1"})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        try:
            r = await self._client.get(
                _BASE,
                params={
                    "query.bibliographic": query,
                    "rows": str(max(1, min(limit, 25))),
                    "select": _SELECT,
                },
            )
            r.raise_for_status()
            payload = r.json()
        except Exception:
            return []

        message = payload.get("message") if isinstance(payload, dict) else None
        works = message.get("items") if isinstance(message, dict) else None
        if not isinstance(works, list):
            return []

        items: list[KnowledgeItem] = []
        for work in works[:limit]:
            if not isinstance(work, dict):
                continue
            item = self._to_item(work)
            if item is not None:
                items.append(item)
        return items

    def _to_item(self, work: dict[str, Any]) -> KnowledgeItem | None:
        titles = work.get("title")
        title = clean_text(titles[0] if isinstance(titles, list) and titles else titles, limit=400)
        if not title:
            return None
        doi = normalize_doi(work.get("DOI"))
        url = str(work.get("URL") or "") or (f"https://doi.org/{doi}" if doi else "") or None

        containers = work.get("container-title")
        venue = clean_text(
            containers[0] if isinstance(containers, list) and containers else (containers or work.get("publisher")),
            limit=200,
        )
        abstract = strip_markup(work.get("abstract"))
        snippet = abstract or (f"{title} — {venue}" if venue else title)

        issued = work.get("issued")
        date_parts = issued.get("date-parts") if isinstance(issued, dict) else None
        first = date_parts[0] if isinstance(date_parts, list) and date_parts else None

        cited = work.get("is-referenced-by-count")
        work_type = str(work.get("type") or "")
        return KnowledgeItem(
            title=title,
            snippet=snippet,
            url=url,
            published=parse_date(first),
            provenance=Provenance(
                provider=self.name,
                source_kind=SourceKind.OFFICIAL,
                uri=url,
                retrieved_ts=datetime.now(UTC),
            ),
            authors=authors_from_crossref(work.get("author")),
            doi=doi,
            venue=venue,
            citation_count=int(cited) if isinstance(cited, int) else None,
            external_ids={"crossref_type": work_type} if work_type else {},
        )

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")), limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=0.5)

    async def shutdown(self) -> None:
        await self._client.aclose()
