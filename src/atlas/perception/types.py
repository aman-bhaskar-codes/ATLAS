"""Perception contracts.

WHY a stable ScreenState regardless of source: the orchestrator must reason over
screen content without caring whether it came from the AX tree (C1), OCR (C5),
or cloud vision (C5). Source is recorded for legibility + cost accounting.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class PerceptionSource(StrEnum):
    AX_TREE = "ax_tree"       # free, local, structured   (C1)
    OCR = "ocr"               # free, local, text          (C5)
    CLOUD_VISION = "cloud_vision"  # paid-ish, leaves machine (C5)
    UNSUPPORTED = "unsupported"    # not macOS / no PyObjC


Role = Literal[
    "window", "button", "text_field", "static_text", "menu", "menu_item",
    "checkbox", "link", "image", "group", "list", "row", "other",
]


class UIElement(BaseModel):
    """One node of the accessibility tree, flattened to the essentials the
    planner needs. `ax_path` is a stable-ish identity used by C3 to ACT on this
    element later without a pixel coordinate."""

    model_config = {"frozen": True}
    role: Role
    label: str | None = None          # AXTitle / AXDescription / AXValue-as-label
    value: str | None = None          # current text/value if any
    enabled: bool = True
    focused: bool = False
    ax_path: str | None = None        # e.g. "window[0]/group[2]/button[1]:Send"
    bounds: tuple[int, int, int, int] | None = None  # x,y,w,h (only if cheap to get)


class ScreenState(BaseModel):
    model_config = {"frozen": True}
    source: PerceptionSource
    app_name: str | None = None
    window_title: str | None = None
    elements: tuple[UIElement, ...] = ()
    sensitive: bool = False           # frontmost app is on the sensitivity list
    note: str | None = None           # why a fallback/limitation happened

    def summarize(self, limit: int = 40) -> str:
        """Compact text rendering for the planner's context window. WHY: we feed
        the planner text, never a raw object graph, to keep tokens bounded."""
        head = f"app={self.app_name!r} window={self.window_title!r} source={self.source.value}"
        if self.sensitive:
            head += " [SENSITIVE]"
        lines = [head]
        for el in self.elements[:limit]:
            bit = f"  {el.role}"
            if el.label:
                bit += f" {el.label!r}"
            if el.value:
                bit += f" = {el.value!r}"
            if not el.enabled:
                bit += " (disabled)"
            if el.focused:
                bit += " (focused)"
            lines.append(bit)
        if len(self.elements) > limit:
            lines.append(f"  ... +{len(self.elements) - limit} more elements")
        return "\n".join(lines)
