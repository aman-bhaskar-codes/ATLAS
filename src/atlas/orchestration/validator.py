"""Output validator — structural + shape validity of an Action.

WHY not merged with the parser: parsing = 'is it readable', validation = 'is it
well-formed enough to proceed'. This does NOT decide permission (that's the
Safety Engine at dispatch) — it rejects malformed actions early so we never send
a half-specified tool call into the safety pipeline.
"""

from __future__ import annotations

from atlas.orchestration.errors import ValidationError
from atlas.orchestration.types import Action


class OutputValidator:
    def validate(self, action: Action) -> None:
        if action.kind == "tool_call":
            if not action.tool or not action.operation:
                raise ValidationError("tool_call requires both tool and operation")
        elif action.kind in ("final_answer", "ask_user"):
            if not (action.final_text and action.final_text.strip()):
                raise ValidationError(f"{action.kind} requires non-empty final_text")
        # noop needs nothing
