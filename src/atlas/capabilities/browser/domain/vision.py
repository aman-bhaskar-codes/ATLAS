from __future__ import annotations

from pydantic import BaseModel


class Region(BaseModel):
    model_config = {"frozen": True}
    x: int
    y: int
    width: int
    height: int

class Screenshot(BaseModel):
    model_config = {"frozen": True}
    data: bytes
    mime_type: str = "image/png"
    viewport_only: bool = True
    clip: Region | None = None

class VisualElement(BaseModel):
    """Detected element via vision/OCR (independent of DOM)."""
    model_config = {"frozen": True}
    label: str
    region: Region
    confidence: float

class GroundingResult(BaseModel):
    """Result of mapping a semantic intent to a visual element coordinate."""
    model_config = {"frozen": True}
    intent: str
    element: VisualElement | None = None
    confidence: float = 0.0
