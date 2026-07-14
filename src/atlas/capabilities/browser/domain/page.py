"""Page as STATE, not a handle. WHY: checkpointing, crash recovery, and future
autonomous replay all need a serializable snapshot of 'where we are'. The live
provider page object never leaves the session layer; callers reason over PageState.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from atlas.capabilities.browser.domain.content import FormModel, Link
from atlas.capabilities.browser.domain.dom import AccessibilityNode, ElementRef


class AuthState(StrEnum):
    ANONYMOUS = "anonymous"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"

class Viewport(BaseModel):
    model_config = {"frozen": True}
    width: int = 1280
    height: int = 800
    device_scale: float = 1.0
    mobile: bool = False

class ScrollState(BaseModel):
    model_config = {"frozen": True}
    x: int = 0
    y: int = 0
    max_y: int = 0

class PageHandle(BaseModel):
    """Opaque id the caller holds; resolves to a live page inside PageManager only."""
    model_config = {"frozen": True}
    session_id: str
    tab_id: str

class PageState(BaseModel):
    """A structured, serializable snapshot of a page at a moment in time.
    Checkpointable: persist this + the session's auth state and you can recover."""
    model_config = {"frozen": True}
    handle: PageHandle
    url: str
    title: str = ""
    auth: AuthState = AuthState.ANONYMOUS
    viewport: Viewport = Viewport()
    scroll: ScrollState = ScrollState()
    visible_elements: tuple[ElementRef, ...] = ()      # interactable, in-viewport
    accessibility: tuple[AccessibilityNode, ...] = ()  # AX tree slice (bounded)
    forms: tuple[FormModel, ...] = ()
    links: tuple[Link, ...] = ()
    loading: bool = False
    captured_ts: datetime
    dom_hash: str = ""                                  # cheap change-detection between snapshots
