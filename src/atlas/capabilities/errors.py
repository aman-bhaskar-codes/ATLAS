"""Capability platform error taxonomy.

WHY distinct from intelligence/orchestration errors: these classify EXTERNAL
capability failures so the dispatcher/health monitor can decide retry vs
provider-switch vs abort, and so a provider outage never surfaces as an opaque
crash to the orchestrator (it becomes a typed, observable failure).
"""

from __future__ import annotations

from atlas.infra.errors import AtlasError


class CapabilityError(AtlasError):
    retryable: bool = False
    provider_switch_helps: bool = False


class CapabilityNotFound(CapabilityError):  # noqa: N818
    """No capability matches the request."""


class NoProviderAvailable(CapabilityError):  # noqa: N818
    """Capability exists but no healthy provider can serve it."""
    provider_switch_helps = True


class ProviderExecutionError(CapabilityError):
    retryable = True
    provider_switch_helps = True


class ProviderAuthError(CapabilityError):
    """Credentials missing/expired — the Identity Platform (6.2) must resolve."""
    provider_switch_helps = True


class NormalizationError(CapabilityError):
    """Provider returned something we couldn't map to a domain model."""
    retryable = True


class CapabilityTimeout(CapabilityError):  # noqa: N818
    retryable = True
    provider_switch_helps = True


class CapabilityDenied(CapabilityError):  # noqa: N818
    """The Safety Engine denied the capability's side effect. Terminal — the
    orchestrator receives this as information, not a crash."""
