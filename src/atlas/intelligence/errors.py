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
