"""Null perception backend for non-macOS / no-pyobjc environments.
WHY: keeps wiring uniform and CI green; returns a typed UNSUPPORTED state."""

from __future__ import annotations

from atlas.perception.types import PerceptionSource, ScreenState


class NullPerceptionBackend:
    def available(self) -> bool:
        return False

    def capture_frontmost(self) -> ScreenState:
        return ScreenState(source=PerceptionSource.UNSUPPORTED,
                           note="perception unsupported on this platform")
