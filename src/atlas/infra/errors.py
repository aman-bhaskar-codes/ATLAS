"""Typed error taxonomy.

WHY: a single base (`AtlasError`) lets logging/observability treat all domain
errors uniformly, while narrow subclasses let callers branch precisely. Bare
`Exception` is banned repo-wide; everything raised on an ATLAS code path is one
of these (or a library error we immediately wrap).
"""

from __future__ import annotations


class AtlasError(Exception):
    """Root of all ATLAS errors."""


class FatalError(AtlasError):
    """Unrecoverable; abort the process (e.g. cannot open the audit DB)."""


class RetryableError(AtlasError):
    """Transient; the caller may retry with backoff."""


class UserError(AtlasError):
    """Caused by bad user input; report cleanly, no stack spam."""


class SystemError_(AtlasError):  # noqa: N801, N818
    """Internal invariant violation / bug. Named with trailing underscore to
    avoid shadowing the builtin while staying importable."""


class ConfigError(FatalError):
    """Invalid or missing configuration; fatal at startup."""


class ManifestError(FatalError):
    """Invalid or missing permission manifest; fatal at startup."""


class RegistryError(FatalError):
    """Service registry misuse (cycle, unknown dependency)."""


class BusError(AtlasError):
    """Message bus misuse (publish on a closed bus)."""


class ModelError(RetryableError):
    """A model provider call failed."""


class BudgetExceeded(AtlasError):  # noqa: N818
    """A paid model call would breach the configured spend ceiling."""
