"""Capability retrieval — intent → small candidate set, never the whole corpus.

WHY: ATLAS can know about thousands of APIs without carrying thousands of
tools into every model call. Retrieval returns only the few relevant
candidates, ranked with validation status as a hard preference: a weaker
match that is VALIDATED beats a stronger match that cannot execute.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import CatalogEntry, PublicAPICatalog
from .connector import ConnectorRegistry, ConnectorStatus


@dataclass(frozen=True)
class Candidate:
    entry: CatalogEntry
    status: ConnectorStatus
    relevance: float

    @property
    def executable(self) -> bool:
        return self.status in {ConnectorStatus.VALIDATED, ConnectorStatus.AVAILABLE}


class CapabilityRetriever:
    def __init__(self, catalog: PublicAPICatalog, connectors: ConnectorRegistry) -> None:
        self._catalog = catalog
        self._connectors = connectors

    def retrieve(self, intent: str, *, limit: int = 5) -> list[Candidate]:
        hits = self._catalog.search(intent, limit=limit * 2)
        candidates: list[Candidate] = []
        for entry, score in hits:
            record = self._connectors.get(entry.api_id)
            status = record.status if record else ConnectorStatus.DISCOVERED
            candidates.append(Candidate(entry=entry, status=status, relevance=score))
        # validated/available first, then relevance; discovery-only sink to the bottom
        def rank(c: Candidate) -> tuple[int, float]:
            return (0 if c.executable else 1, -c.relevance)

        candidates.sort(key=rank)
        return candidates[:limit]

    def explain_limitation(self, intent: str) -> str:
        """Scenario 4 support: when nothing can serve the intent, explain why
        honestly instead of faking capability."""
        candidates = self.retrieve(intent, limit=3)
        if not candidates:
            return f"no catalog capability matches intent {intent!r}; nothing discovered"
        lines = [f"closest capabilities for {intent!r}:"]
        for c in candidates:
            lines.append(f"- {c.entry.name} ({c.entry.category}) — status={c.status.value}, relevance={c.relevance}")
            if not c.executable:
                lines.append(f"  limitation: connector is {c.status.value}; requires validation before execution")
        return "\n".join(lines)
