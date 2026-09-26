"""Typed tooling-fabric error taxonomy.

WHY distinct from orchestration/capabilities errors: the tooling fabric is a new
layer with its own failure modes (registration, lifecycle, adapter health) that
callers must branch on precisely. Like every ATLAS error, everything roots at
``AtlasError`` so observability and API error rendering treat these uniformly —
this is NOT a second error system, just a new branch of the existing one.

Execution-domain failures (safety denial, backend error) are NOT raised through
this taxonomy: the executor reports them as structured ``UniversalToolResult``
failures so callers always get a normalized result. These exceptions cover
lookup/lifecycle faults — programming or API misuse errors.
"""

from __future__ import annotations

from atlas.infra.errors import AtlasError


class ToolingError(AtlasError):
    """Root of all tooling-fabric errors."""

    code = "tooling.error"


class ToolRegistrationError(ToolingError):
    """A tool could not be registered (invalid definition, bad adapter)."""

    code = "tooling.registration"


class ToolAlreadyRegistered(ToolRegistrationError):  # noqa: N818
    """A tool with the same stable identity is already registered."""

    code = "tooling.already_registered"


class ToolNotFound(ToolingError):  # noqa: N818
    """No tool with the requested identity exists in the registry."""

    code = "tooling.not_found"


class ToolDisabled(ToolingError):  # noqa: N818
    """The tool exists but was disabled at runtime; it will not execute."""

    code = "tooling.disabled"


class ToolAdapterError(ToolingError):
    """An adapter failed in a way that is not tied to one execution."""

    code = "tooling.adapter"


class ToolInitializationError(ToolAdapterError):
    """Adapter startup failed; the tool is marked FAILED, others continue."""

    code = "tooling.initialization"


class ToolValidationError(ToolAdapterError):
    """Adapter-side validation failed (implementation missing, metadata wrong)."""

    code = "tooling.validation"


class ToolExecutionError(ToolAdapterError):
    """An adapter raised while executing; the fabric converts this into a
    structured failure result when possible, and raises it only when the
    adapter contract itself was violated."""

    code = "tooling.execution"
    retryable = True
