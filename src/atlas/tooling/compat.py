"""Compatibility bridges from the universal fabric to existing ATLAS contracts.

Part 1 §60/§67: the ReasoningLoop is NOT changed, but the conversion from a
``UniversalToolDefinition`` to a provider-native ``ToolCallSpec`` exists here so
later parts can surface universal tools to the model without redesign. The
generated schema uses universal metadata (operation vocabulary from the
definition), not vendor formats.
"""

from __future__ import annotations

from atlas.infra.types import ToolCallSpec
from atlas.tooling.models.tool_definition import UniversalToolDefinition


def to_tool_call_spec(definition: UniversalToolDefinition) -> ToolCallSpec:
    """Convert a universal definition into a provider-native tool schema.

    ``name`` is the stable universal ID (display names are not identities);
    the router that later consumes these specs translates the ID back to a
    concrete execution route.
    """
    properties: dict[str, object] = {
        "operation": {"type": "string", "enum": list(definition.operations)},
        "args": {"type": "object", "description": "operation arguments"},
    }
    if definition.input_schema.operations:
        properties["args"] = {
            "type": "object",
            "description": "operation arguments; per-operation schemas available via the tool catalog",
        }
    return ToolCallSpec(
        name=definition.id,
        description=definition.description,
        parameters={"type": "object", "properties": properties, "required": ["operation"]},
    )


def to_tool_call_specs(definitions: tuple[UniversalToolDefinition, ...]) -> tuple[ToolCallSpec, ...]:
    """Convert many definitions at once (the future shortlist -> schemas seam)."""
    return tuple(to_tool_call_spec(d) for d in definitions)


__all__ = ["to_tool_call_spec", "to_tool_call_specs"]
