"""Capability catalog — the discovery corpus, NOT a live tool list.

WHY a bundled curated seed instead of cloning public-apis at runtime:
thousands of APIs in memory (or worse, in prompts) is a performance and a
safety mistake. The catalog is a static, reviewable JSON document; a sync
command can regenerate it offline from the public-apis corpus. Retrieval then
selects only the handful relevant to the current intent.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

from pydantic import BaseModel


class CatalogEntry(BaseModel):
    """One discoverable public API. Discovery metadata only — no credentials,
    no execution rights. Execution rights come from the connector lifecycle."""

    model_config = {"frozen": True}
    api_id: str  # stable slug, e.g. "open_meteo"
    name: str
    description: str
    category: str
    auth: str = "No"  # No | apiKey | OAuth | X-RapidAPI-Key-Required | Unknown
    https: bool = True
    cors: str = "unknown"
    url: str
    free: bool = True

    @property
    def needs_key(self) -> bool:
        return self.auth.lower() not in {"no", "unknown"} and self.auth != "No"


_STOPWORDS = frozenset("the a an of for to get fetch find show me with in on and or using via api apis current".split())


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOPWORDS}


class PublicAPICatalog:
    """Searchable corpus of known public APIs."""

    def __init__(self, entries: tuple[CatalogEntry, ...]) -> None:
        self._entries = {e.api_id: e for e in entries}
        self._tokens = {
            api_id: _tokens(f"{e.name} {e.description} {e.category}") for api_id, e in self._entries.items()
        }

    @classmethod
    def load_default(cls) -> PublicAPICatalog:
        data_dir = Path("src/atlas/capabilities/public_api/data")
        # Works both as source tree and as installed package.
        try:
            pkg_files = resources.files("atlas.capabilities.public_api")
            text = pkg_files.joinpath("data/catalog.json").read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError, TypeError):
            text = (data_dir / "catalog.json").read_text(encoding="utf-8")
        raw = json.loads(text)
        return cls(tuple(CatalogEntry(**item) for item in raw["apis"]))

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, api_id: str) -> CatalogEntry | None:
        return self._entries.get(api_id)

    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({e.category for e in self._entries.values()}))

    def all(self) -> tuple[CatalogEntry, ...]:
        return tuple(self._entries.values())

    def search(self, query: str, *, category: str | None = None, limit: int = 5) -> list[tuple[CatalogEntry, float]]:
        """Keyword-overlap relevance. Deliberately cheap: retrieval must cost
        ~nothing so it can run inside every planning step."""
        wanted = _tokens(query)
        if not wanted:
            return []
        scored: list[tuple[CatalogEntry, float]] = []
        for api_id, entry in self._entries.items():
            if category and entry.category.lower() != category.lower():
                continue
            overlap = len(wanted & self._tokens[api_id])
            if overlap == 0:
                continue
            score = overlap / len(wanted)
            if entry.free:
                score += 0.05  # free-first policy
            if not entry.needs_key:
                score += 0.05
            scored.append((entry, round(score, 4)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]
