"""Common domain contracts shared by all capabilities.

WHY a uniform CapabilityResult envelope: every capability, whatever the provider,
returns the same shape (payload + provenance + which provider served it + cost).
This is what lets the orchestrator stay provider-agnostic and lets telemetry be
uniform. Payloads are capability-specific typed models (KnowledgeItem, EmailMessage,
...) defined in their own domain modules in later parts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel

PayloadT = TypeVar("PayloadT", bound=BaseModel)


class SourceKind(StrEnum):
    LOCAL = "local"            # memory, filesystem, on-device
    OFFICIAL = "official"      # vendor RSS/API, authoritative
    WEB = "web"                # general web/search
    MODEL = "model"            # parametric (LLM) knowledge
    MCP = "mcp"                # served via an MCP server


class Provenance(BaseModel):
    """Where a result came from. WHY mandatory: legibility + knowledge ranking
    both need to know the source and how trustworthy it is."""
    model_config = {"frozen": True}
    provider: str
    source_kind: SourceKind
    uri: str | None = None
    retrieved_ts: datetime | None = None


class Confidence(BaseModel):
    model_config = {"frozen": True}
    score: float = 0.5           # 0..1
    basis: str = ""              # e.g. 'single-source', 'agreement 4/5'


PayloadT = TypeVar("PayloadT", bound=BaseModel)


class CapabilityResult[PayloadT](BaseModel):
    """Uniform envelope returned by every capability execution."""
    model_config = {"frozen": True}
    ok: bool
    payload: PayloadT | None = None
    provenance: tuple[Provenance, ...] = ()
    confidence: Confidence = Confidence()
    provider: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str | None = None
