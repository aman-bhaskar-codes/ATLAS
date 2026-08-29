"""Semantic Scholar Graph API — keyless paper search with citation counts.

source_kind='official'. Works without a key at a low rate limit; an optional
`api_key` raises it. requires_auth stays False so the provider is always
registered — a rate-limited scholarly source still beats no scholarly source.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.providers.knowledge.scholarly import (
    authors_from_names,
    clean_text,
    normalize_doi,
    parse_date,
)
from atlas.capabilities.registry.capability import Capability

_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,abstract,url,venue,year,publicationDate,citationCount,externalIds,authors,openAccessPdf"


class SemanticScholarProvider:
    name = "semantic_scholar"
    capability = Capability.KNOWLEDGE
    is_local = False
    requires_auth = False
    source_kind = "official"

    def __init__(self, timeout_s: float = 15.0, *, api_key: str = "") -> None:
        headers = {"x-api-key": api_key} if api_key.strip() else {}
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers)

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get(_BASE, params={"query": "test", "limit": "1", "fields": "title"})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        try:
            r = await self._client.get(
                _BASE,
                params={"query": query, "limit": str(max(1, min(limit, 25))), "fields": _FIELDS},
            )
            r.raise_for_status()
            payload = r.json()
        except Exception:
            return []

        papers = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(papers, list):
            return []

        items: list[KnowledgeItem] = []
        for paper in papers[:limit]:
            if not isinstance(paper, dict):
                continue
            item = self._to_item(paper)
            if item is not None:
                items.append(item)
        return items

    def _to_item(self, paper: dict[str, Any]) -> KnowledgeItem | None:
        title = clean_text(paper.get("title"), limit=400)
        if not title:
            return None

        external_raw = paper.get("externalIds")
        external: dict[str, str] = {}
        arxiv_id = ""
        doi = ""
        if isinstance(external_raw, dict):
            arxiv_id = str(external_raw.get("ArXiv") or "")
            doi = normalize_doi(external_raw.get("DOI"))
            for key in ("PubMed", "PubMedCentral", "CorpusId", "DBLP"):
                value = external_raw.get(key)
                if value:
                    external[key.lower()] = str(value)

        pdf = paper.get("openAccessPdf")
        pdf_url = pdf.get("url") if isinstance(pdf, dict) else None
        if pdf_url:
            external["open_access_pdf"] = str(pdf_url)

        url = str(paper.get("url") or "") or (f"https://doi.org/{doi}" if doi else "") or None
        venue = clean_text(paper.get("venue"), limit=200)
        abstract = clean_text(paper.get("abstract"))
        cited = paper.get("citationCount")
        return KnowledgeItem(
            title=title,
            snippet=abstract or (f"{title} — {venue}" if venue else title),
            url=url,
            published=parse_date(paper.get("publicationDate") or paper.get("year")),
            provenance=Provenance(
                provider=self.name,
                source_kind=SourceKind.OFFICIAL,
                uri=url,
                retrieved_ts=datetime.now(UTC),
            ),
            authors=authors_from_names(paper.get("authors")),
            doi=doi,
            arxiv_id=arxiv_id,
            venue=venue,
            citation_count=int(cited) if isinstance(cited, int) else None,
            external_ids=external,
        )

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")), limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        # keyless tier is aggressively rate limited — back off harder than peers
        return RetryPolicy(max_attempts=2, base_backoff_s=2.0)

    async def shutdown(self) -> None:
        await self._client.aclose()
