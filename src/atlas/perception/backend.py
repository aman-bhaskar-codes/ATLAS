"""Perception backend protocol. WHY: the AXPerceptionTool depends on this, not
on PyObjC, so tests inject a FakeBackend and the tool stays platform-agnostic."""

from __future__ import annotations

from typing import Protocol

from atlas.perception.types import ScreenState


class PerceptionBackend(Protocol):
    def capture_frontmost(self) -> ScreenState: ...
    def available(self) -> bool: ...
