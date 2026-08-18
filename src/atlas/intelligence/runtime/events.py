"""Provider lifecycle events for the intelligence layer.

These events are published during inference attempts to provide visibility
into provider selection, failures, quota exhaustion, and rate limiting for
dashboard telemetry and trajectory capture.
"""

from __future__ import annotations

from atlas.infra.bus import Event


class ProviderLifecycleEvent(Event):
    """Provider call lifecycle: selected / failed / quota_exhausted / rate_limited."""

    kind: str  # e.g. "provider.selected" | "provider.failed" | "provider.quota_exhausted" | "provider.rate_limited"
    provider: str
    model: str
    cost_class: str
    task_id: str | None = None
    error: str | None = None
