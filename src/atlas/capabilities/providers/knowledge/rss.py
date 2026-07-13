"""RSS/Atom knowledge provider — one adapter, many feeds (config-driven).

WHY one adapter for all feeds: every vendor blog / release feed is RSS/Atom. The
list of feeds is DATA (knowledge_sources.yaml), so onboarding OpenAI/Anthropic/
DeepMind/Meta/Mistral/DeepSeek/Qwen/HF blogs is config, not code. source_kind is
'official' so the router/ranker prefer these over general web search.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability


class RSSProvider:
    capability = Capability.KNOWLEDGE
    is_local = False
    requires_auth = False
    source_kind = "official"

    def __init__(self, *, name: str, feeds: list[str], timeout_s: float = 15.0) -> None:
        self.name = f"rss:{name}"
        self._feeds = feeds
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        if not self._feeds:
            return False
        try:
            r = await self._client.head(self._feeds[0])
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        items: list[KnowledgeItem] = []
        q = query.lower()
        for feed_url in self._feeds:
            try:
                r = await self._client.get(feed_url)
                parsed = feedparser.parse(r.text)
            except (httpx.HTTPError, Exception):
                continue
            for entry in parsed.entries:
                title = str(getattr(entry, "title", ""))
                summary = str(getattr(entry, "summary", ""))
                # cheap relevance filter; the ranker/summarizer does the heavy lift
                if q and q not in (title + summary).lower() and not _term_overlap(q, title + summary):
                    continue
                items.append(KnowledgeItem(
                    title=title, snippet=summary[:500],
                    url=getattr(entry, "link", None),
                    published=_parse_date(entry),
                    provenance=Provenance(provider=self.name, source_kind=SourceKind.OFFICIAL,
                                          uri=getattr(entry, "link", None),
                                          retrieved_ts=datetime.now(UTC))))
        items.sort(key=lambda i: i.published or datetime.min.replace(tzinfo=UTC),
                   reverse=True)
        return items[:limit]

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")),
                                 limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw   # search() already returns domain models

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=0.5)

    async def shutdown(self) -> None:
        await self._client.aclose()


def _term_overlap(q: str, text: str) -> bool:
    qs, ts = set(q.split()), set(text.lower().split())
    return len(qs & ts) >= max(1, len(qs) // 2)


def _parse_date(entry: Any) -> datetime | None:
    import time
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if st is None:
        return None
    return datetime.fromtimestamp(time.mktime(st), tz=UTC)
