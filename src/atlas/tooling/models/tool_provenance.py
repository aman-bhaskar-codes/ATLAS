"""Tool provenance — reuses the existing capability domain model.

WHY a thin wrapper instead of a new universe: ``SourceKind``/``Provenance``
(atlas.capabilities.domain.common) is already the canonical provenance currency
of the system — the knowledge fabric, telemetry, and results all speak it. The
fabric's provenance adds only a JSON-friendly validation home for definitions
and converts losslessly to the domain type so downstream consumers never see a
second provenance format.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from atlas.capabilities.domain.common import Provenance, SourceKind


class ToolProvenance(BaseModel):
    """Static provenance descriptor for a tool definition (where its results
    typically originate). Per-execution provenance travels on
    ``UniversalToolResult`` using the same shape."""

    model_config = ConfigDict(frozen=True)

    source_kind: SourceKind
    provider: str
    uri: str | None = None
    retrieved_ts: datetime | None = None

    def to_domain(self) -> Provenance:
        """Convert to the canonical capability-domain Provenance."""
        return Provenance(
            provider=self.provider,
            source_kind=self.source_kind,
            uri=self.uri,
            retrieved_ts=self.retrieved_ts,
        )

    @classmethod
    def from_domain(cls, provenance: Provenance) -> ToolProvenance:
        """Build from the canonical capability-domain Provenance."""
        return cls(
            source_kind=provenance.source_kind,
            provider=provenance.provider,
            uri=provenance.uri,
            retrieved_ts=provenance.retrieved_ts,
        )
