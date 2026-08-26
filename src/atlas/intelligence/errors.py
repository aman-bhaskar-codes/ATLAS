"""Intelligence platform error taxonomy.

WHY separate from orchestration errors: these classify INFERENCE failures so the
retry/fallback engines can decide switch-provider vs switch-model vs abort. Each
carries whether it is retryable and whether switching providers may help.
"""

from __future__ import annotations

from atlas.infra.errors import AtlasError


class IntelligenceError(AtlasError):
    retryable: bool = False
    provider_switch_helps: bool = False


class ProviderError(IntelligenceError):
    retryable = True
    provider_switch_helps = True


class RoutingError(IntelligenceError):
    retryable = False


class RateLimitError(IntelligenceError):
    retryable = True
    provider_switch_helps = True


class BudgetExceededError(IntelligenceError):
    retryable = False


class InferenceTimeoutError(IntelligenceError):
    retryable = True
    provider_switch_helps = True


class ParsingError(IntelligenceError):
    retryable = True  # a re-ask sometimes fixes malformed output


class StreamingError(IntelligenceError):
    retryable = True


class FallbackError(IntelligenceError):
    """Every candidate in the fallback chain failed."""

    retryable = False


class ConfigurationError(IntelligenceError):
    retryable = False


class QuotaExhaustedError(IntelligenceError):
    """Free-tier quota exhausted for a provider. Switching providers helps."""

    retryable = False
    provider_switch_helps = True

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"{provider}: {reason}")


class PolicyViolationError(IntelligenceError):
    """A hard policy constraint was violated (e.g. ZERO_COST + paid provider).
    No retry or provider switch — the constraint itself must change."""

    retryable = False
    provider_switch_helps = False
