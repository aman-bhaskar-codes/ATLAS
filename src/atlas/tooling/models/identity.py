"""Universal tool identity.

WHY a dedicated module: tool identity is the join key across the registry,
catalog, router, trajectories, and audit. It must be deterministic, stable,
and collision-resistant — display names are NOT identities (a tool can be
renamed; its identity must not change).

Scheme: ``namespace:provider:tool`` (e.g. ``native:atlas:filesystem``,
``capability:atlas:knowledge``, ``mcp:github:search_repositories``).
Components are lowercase and restricted to ``[a-z0-9_.-]`` so IDs are safe in
URLs, CLI output, and log lines without escaping.
"""

from __future__ import annotations

import re
from enum import StrEnum

_COMPONENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class ToolNamespace(StrEnum):
    """Where a tool comes from. The namespace is part of the identity, so the
    router can always answer "which implementation universe does this belong
    to" without duck-typing on the adapter."""

    NATIVE = "native"
    CAPABILITY = "capability"
    MCP = "mcp"
    HTTP = "http"
    GRAPHQL = "graphql"
    CLI = "cli"
    BROWSER = "browser"
    COMPUTER = "computer"
    LOCAL = "local"
    REMOTE = "remote"
    PLUGIN = "plugin"


def build_tool_id(namespace: ToolNamespace | str, provider: str, name: str) -> str:
    """Deterministically build a stable tool identity.

    Raises ValueError on components that would produce an ambiguous or
    non-canonical ID (empty, uppercase, or containing the ``:`` separator).
    """
    ns = namespace.value if isinstance(namespace, ToolNamespace) else str(namespace)
    for label, component in (("namespace", ns), ("provider", provider), ("name", name)):
        if not component or not _COMPONENT_RE.match(component):
            raise ValueError(f"invalid tool-id {label} component {component!r}: must match {_COMPONENT_RE.pattern}")
    return f"{ns}:{provider}:{name}"


def parse_tool_id(tool_id: str) -> tuple[str, str, str]:
    """Split a tool ID into ``(namespace, provider, name)``.

    Raises ValueError on malformed IDs so callers fail loudly instead of
    silently mis-routing on a partial identity.
    """
    parts = tool_id.split(":")
    if len(parts) != 3:
        raise ValueError(f"malformed tool id {tool_id!r}: expected namespace:provider:name")
    namespace, provider, name = parts
    for label, component in (("namespace", namespace), ("provider", provider), ("name", name)):
        if not component or not _COMPONENT_RE.match(component):
            raise ValueError(f"malformed tool id {tool_id!r}: bad {label} component")
    return namespace, provider, name
