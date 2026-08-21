"""Connector lifecycle — the gate between discovery and execution.

WHY a lifecycle: a DISCOVERED API is a rumor, not a capability. Spec rule:
unknown/discovered APIs MUST NOT execute; they stay DISCOVERED / UNVALIDATED
until connector validation promotes them. Promotion is an explicit, audited
step — never automatic.

    DISCOVERED → CANDIDATE → EXPERIMENTAL → VALIDATED → AVAILABLE
                                                 ↘ DISABLED
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel

from .catalog import CatalogEntry, PublicAPICatalog


class ConnectorStatus(StrEnum):
    DISCOVERED = "discovered"  # known from catalog; never executed
    CANDIDATE = "candidate"  # retrieved as relevant; awaiting validation
    EXPERIMENTAL = "experimental"  # validation in progress / sandbox probes
    VALIDATED = "validated"  # passed validation; may execute behind safety gate
    AVAILABLE = "available"  # validated + explicitly enabled for planning
    DISABLED = "disabled"  # turned off (user or policy)


_EXECUTABLE = frozenset({ConnectorStatus.VALIDATED, ConnectorStatus.AVAILABLE})


class ConnectorRecord(BaseModel):
    """State of one connector. Immutable history point — the registry swaps records."""

    model_config = {"frozen": True}
    api_id: str
    status: ConnectorStatus = ConnectorStatus.DISCOVERED
    promoted_ts: datetime | None = None
    note: str = ""

    @property
    def executable(self) -> bool:
        return self.status in _EXECUTABLE


class ConnectorRegistry:
    """In-memory connector state, seeded from the catalog as DISCOVERED."""

    def __init__(self, catalog: PublicAPICatalog) -> None:
        self._catalog = catalog
        self._records: dict[str, ConnectorRecord] = {
            entry.api_id: ConnectorRecord(api_id=entry.api_id) for entry in catalog.all()
        }

    def get(self, api_id: str) -> ConnectorRecord | None:
        return self._records.get(api_id)

    def all(self) -> tuple[ConnectorRecord, ...]:
        return tuple(self._records.values())

    def executable_ids(self) -> tuple[str, ...]:
        return tuple(api_id for api_id, rec in self._records.items() if rec.executable)

    def register_discovered(self, entry: CatalogEntry) -> ConnectorRecord:
        """Track an API not in the bundled seed (e.g. synced from public-apis).
        It enters as DISCOVERED — never with execution rights."""
        existing = self._records.get(entry.api_id)
        if existing is not None:
            return existing
        record = ConnectorRecord(api_id=entry.api_id)
        self._records[entry.api_id] = record
        return record

    def promote(self, api_id: str, status: ConnectorStatus, *, note: str = "") -> ConnectorRecord:
        """Explicit promotion. Validation promotion requires a validation note —
        promotion without evidence is refused (no fake autonomy)."""
        if api_id not in self._records:
            raise KeyError(f"unknown connector: {api_id}")
        if status in _EXECUTABLE and not note:
            raise ValueError("promotion to an executable status requires validation evidence (note)")
        record = ConnectorRecord(api_id=api_id, status=status, promoted_ts=datetime.now(UTC), note=note)
        self._records[api_id] = record
        return record
