"""GitHub Releases provider. Public (optional PAT via vault raises rate limit). source_kind='official'."""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability


class GitHubReleasesProvider:
    name = "github_releases"
    capability = Capability.KNOWLEDGE
    is_local = False
    requires_auth = False
    source_kind = "official"

    def __init__(self, timeout_s: float = 15.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers={"Accept": "application/vnd.github.v3+json"})

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get("https://api.github.com/repos/torvalds/linux/releases")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        # query is expected to be in the format `owner/repo`
        if "/" not in query:
            return []
            
        parts = query.split("/", 1)
        owner = parts[0].strip()
        repo = parts[1].strip().split()[0]  # Just take the first word as repo
        
        try:
            r = await self._client.get(
                f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/releases?per_page={limit}")
            r.raise_for_status()
            
            items: list[KnowledgeItem] = []
            for res in r.json()[:limit]:
                pub_str = res.get("published_at")
                pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00")) if pub_str else None
                
                items.append(KnowledgeItem(
                    title=f"{owner}/{repo} Release: {res.get('name') or res.get('tag_name')}",
                    snippet=res.get("body", "")[:500],
                    url=res.get("html_url"),
                    published=pub,
                    provenance=Provenance(provider=self.name, source_kind=SourceKind.OFFICIAL,
                                          uri=res.get("html_url"),
                                          retrieved_ts=datetime.now(UTC))))
            return items
        except (httpx.HTTPError, Exception):
            return []

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")),
                                 limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=0.5)

    async def shutdown(self) -> None:
        await self._client.aclose()
