"""Typed error taxonomy.

WHY: a single base (`AtlasError`) lets logging/observability treat all domain
errors uniformly, while narrow subclasses let callers branch precisely. Bare
`Exception` is banned repo-wide; everything raised on an ATLAS code path is one
of these (or a library error we immediately wrap).
"""

from __future__ import annotations


class AtlasError(Exception):
    """Root of all ATLAS errors.

    Carries structured fields so interfaces can render a clean user-facing
    message without leaking internals, and so retry/escalation logic can branch
    on `retryable` instead of exception text. Subclasses override as needed.
    """

    #: stable machine-readable code (e.g. "provider.unavailable")
    code: str = "atlas.error"
    #: safe message for end users; None means "generic error" rendering
    user_message: str | None = None
    #: whether a retry with backoff is conventionally reasonable
    retryable: bool = False


class FatalError(AtlasError):
    """Unrecoverable; abort the process (e.g. cannot open the audit DB)."""

    code = "atlas.fatal"


class RetryableError(AtlasError):
    """Transient; the caller may retry with backoff."""

    code = "atlas.retryable"
    retryable = True


class UserError(AtlasError):
    """Caused by bad user input; report cleanly, no stack spam."""

    code = "atlas.user_error"


class SystemError_(AtlasError):  # noqa: N801, N818
    """Internal invariant violation / bug. Named with trailing underscore to
    avoid shadowing the builtin while staying importable."""

    code = "atlas.system"


class ConfigError(FatalError):
    """Invalid or missing configuration; fatal at startup."""

    code = "atlas.config"


class ManifestError(FatalError):
    """Invalid or missing permission manifest; fatal at startup."""

    code = "atlas.manifest"


class RegistryError(FatalError):
    """Service registry misuse (cycle, unknown dependency)."""

    code = "atlas.registry"


class BusError(AtlasError):
    """Message bus misuse (publish on a closed bus)."""

    code = "atlas.bus"


class ModelError(RetryableError):
    """A model provider call failed."""

    code = "provider.model"
    user_message = "The AI provider failed to respond. Retrying may help."


class ProviderUnavailableError(ModelError):
    """No healthy provider could serve the request (all fallbacks exhausted)."""

    code = "provider.unavailable"
    user_message = "No AI provider is currently available."


class ToolUnavailableError(RetryableError):
    """A tool/capability is unreachable (provider down, dependency missing)."""

    code = "tool.unavailable"
    user_message = "A required tool is currently unavailable."


class MemoryError_(AtlasError):  # noqa: N801, N818
    """Memory subsystem failure (store, embedding, vector index).

    Named with trailing underscore to avoid shadowing the builtin.
    """

    code = "memory.failure"
    user_message = "The agent's memory system failed."


class StorageError(RetryableError):
    """Database/storage layer failure."""

    code = "storage.failure"
    user_message = "A storage operation failed. Retrying may help."


class NotFoundError(AtlasError):
    """Resource could not be found."""

    code = "atlas.not_found"
    user_message = "The requested resource was not found."


class AuthenticationError(AtlasError):
    """Credential is missing, invalid, or expired."""

    code = "auth.authentication"
    user_message = "Authentication failed. Check the stored credential."


class AuthorizationError(AtlasError):
    """Identity may not perform this action."""

    code = "auth.authorization"
    user_message = "This action is not permitted for the current identity."


class BudgetExceeded(AtlasError):  # noqa: N818
    """A paid model call would breach the configured spend ceiling."""

    code = "budget.exceeded"
    user_message = "The configured spending limit was reached."
