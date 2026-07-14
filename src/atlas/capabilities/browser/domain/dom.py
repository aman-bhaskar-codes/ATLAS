from __future__ import annotations

from pydantic import BaseModel


class BoundingBox(BaseModel):
    model_config = {"frozen": True}
    x: float
    y: float
    width: float
    height: float

class ElementRef(BaseModel):
    """A reference to an element on a page."""
    model_config = {"frozen": True}
    id: str
    tag_name: str = ""
    text: str = ""
    attributes: dict[str, str] = {}
    bounding_box: BoundingBox | None = None

class DOMNode(BaseModel):
    """Raw DOM node representation (rarely used directly by intelligence)."""
    model_config = {"frozen": True}
    node_name: str
    node_type: int
    node_value: str = ""
    attributes: dict[str, str] = {}
    children: tuple[DOMNode, ...] = ()

class AccessibilityNode(BaseModel):
    """Accessibility tree node (used for grounding and reasoning)."""
    model_config = {"frozen": True}
    role: str
    name: str = ""
    value: str | float | int = ""
    description: str = ""
    keyshortcuts: str = ""
    disabled: bool = False
    expanded: bool = False
    focused: bool = False
    required: bool = False
    checked: str | bool = False
    pressed: str | bool = False
    level: int = 0
    children: tuple[AccessibilityNode, ...] = ()
