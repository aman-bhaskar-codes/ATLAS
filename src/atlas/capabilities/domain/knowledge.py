"""Knowledge domain models.

WHY a distinct KnowledgeItem vs Answer: an Item is one raw-but-normalized finding
from one source (with provenance). An Answer is the synthesized, ranked, confidence-
scored result the orchestrator consumes. Evidence groups Items that speak to the
same claim so disagreement can be measured.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from atlas.capabilities.domain.common import Confidence, Provenance


class KnowledgeIntent(StrEnum):
    STATIC = "static"  # answerable from the model's parametric knowledge
    MEMORY = "memory"  # answerable from our own memory
    LIVE = "live"  # needs current external information
    MIXED = "mixed"  # combine memory + live


class KnowledgeQuery(BaseModel):
    model_config = {"frozen": True}
    text: str
    prefer_official: bool = True
    max_sources: int = 6
    freshness_days: int | None = None  # e.g. 7 for "this week"


class KnowledgeItem(BaseModel):
    """One normalized finding from one source.

    The scholarly fields are OPTIONAL and default-empty on purpose: the nine
    pre-existing providers keep working untouched, while scholarly providers
    (arXiv/OpenAlex/Crossref/Semantic Scholar) can carry the citation metadata
    the fabric needs to build a real citation instead of a bare URL. Anything
    a provider knows but this schema does not model goes in `external_ids`.
    """

    model_config = {"frozen": True}
    title: str
    snippet: str
    url: str | None = None
    published: datetime | None = None
    provenance: Provenance
    # ── scholarly metadata (optional; empty for plain web results) ──
    authors: tuple[str, ...] = ()
    doi: str = ""
    arxiv_id: str = ""
    venue: str = ""  # journal / conference / publisher
    citation_count: int | None = None
    external_ids: Mapping[str, str] = Field(default_factory=dict)

    def citation_metadata(self) -> dict[str, str]:
        """Flat, JSON-safe view for KnowledgeDocument.metadata (never lossy-typed)."""
        out: dict[str, str] = {}
        if self.authors:
            out["authors"] = "; ".join(self.authors)
        for key, value in (
            ("doi", self.doi),
            ("arxiv_id", self.arxiv_id),
            ("venue", self.venue),
        ):
            if value:
                out[key] = value
        if self.citation_count is not None:
            out["citation_count"] = str(self.citation_count)
        for key, value in self.external_ids.items():
            if value and key not in out:
                out[key] = str(value)
        return out


class Evidence(BaseModel):
    model_config = {"frozen": True}
    claim: str
    items: tuple[KnowledgeItem, ...]
    agreement: float = 1.0  # fraction of items supporting the claim


class Answer(BaseModel):
    model_config = {"frozen": True}
    text: str
    confidence: Confidence
    sources: tuple[KnowledgeItem, ...] = ()
    intent: KnowledgeIntent = KnowledgeIntent.LIVE
