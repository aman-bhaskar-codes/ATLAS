"""Tool I/O schema — per-operation JSON schemas with an honest fallback.

WHY not bare ``{"type": "object"}`` everywhere (Part 1 §30): the fallback schema
is still derived from REAL information — the tool's registered operation list —
so a model or router sees the actual operation vocabulary. Per-operation
argument schemas arrive in later parts (MCP discovery fills them from
``tools/list``; native tools gain them as schemas are authored). Until then the
``operations`` map stays empty rather than shipping invented, invalid schemas.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolInputSchema(BaseModel):
    """Input contract for one tool.

    ``operations`` maps an operation name to the JSON schema of its ARGUMENTS
    (the ``operation`` discriminator itself is not part of the payload).
    ``fallback`` describes a call when no per-operation schema is known.
    """

    model_config = ConfigDict(frozen=True)

    operations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    fallback: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "operation": {"type": "string"},
                "args": {"type": "object", "description": "operation arguments"},
            },
            "required": ["operation"],
        }
    )

    def schema_for(self, operation: str) -> dict[str, Any]:
        """The argument schema for one operation, or the fallback."""
        return self.operations.get(operation, self.fallback)

    @classmethod
    def from_operations(cls, operations: tuple[str, ...]) -> ToolInputSchema:
        """Derive the best available schema: the real operation vocabulary as
        the ``operation`` enum, generic argument object otherwise."""
        fallback: dict[str, Any] = {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": list(operations)},
                "args": {"type": "object", "description": "operation arguments"},
            },
            "required": ["operation"],
        }
        return cls(fallback=fallback)
