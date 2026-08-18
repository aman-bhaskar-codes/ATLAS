"""Canonical Event Model for the Autonomy Fabric.

This module defines the foundational AtlasEvent schema, durability tiers,
and delivery statuses used by the unified MessageBus.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DurabilityTier(str, enum.Enum):
    """Event durability tiers."""

    EPHEMERAL = "ephemeral"      # In-memory only (or drops if not processed)
    DURABLE = "durable"          # Persisted until delivered to all subscribers
    REPLAYABLE = "replayable"    # Persisted indefinitely for historical replay


class DeliveryStatus(str, enum.Enum):
    """Event delivery statuses."""

    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


class AtlasEvent(BaseModel):
    """Canonical event model for the entire ATLAS platform.
    
    All system events (tasks, safety, routing, memory) are encapsulated 
    within this unified schema before entering the MessageBus.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    type: str
    source: str
    correlation_id: str
    causation_id: str | None = None
    deduplication_key: str | None = None
    occurred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1
