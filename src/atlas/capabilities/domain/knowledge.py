"""Knowledge domain models.

WHY a distinct KnowledgeItem vs Answer: an Item is one raw-but-normalized finding
from one source (with provenance). An Answer is the synthesized, ranked, confidence-
scored result the orchestrator consumes. Evidence groups Items that speak to the
same claim so disagreement can be measured.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from atlas.capabilities.domain.common import Confidence, Provenance


class KnowledgeIntent(StrEnum):
    STATIC = "static"        # answerable from the model's parametric knowledge
    MEMORY = "memory"        # answerable from our own memory
    LIVE = "live"            # needs current external information
    MIXED = "mixed"          # combine memory + live


class KnowledgeQuery(BaseModel):
    model_config = {"frozen": True}
    text: str
    prefer_official: bool = True
    max_sources: int = 6
    freshness_days: int | None = None   # e.g. 7 for "this week"


class KnowledgeItem(BaseModel):
    model_config = {"frozen": True}
    title: str
    snippet: str
    url: str | None = None
    published: datetime | None = None
    provenance: Provenance


class Evidence(BaseModel):
    model_config = {"frozen": True}
    claim: str
    items: tuple[KnowledgeItem, ...]
    agreement: float = 1.0   # fraction of items supporting the claim


class Answer(BaseModel):
    model_config = {"frozen": True}
    text: str
    confidence: Confidence
    sources: tuple[KnowledgeItem, ...] = ()
    intent: KnowledgeIntent = KnowledgeIntent.LIVE
