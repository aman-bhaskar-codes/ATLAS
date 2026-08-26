"""Tests for null perception backend."""

from __future__ import annotations

from atlas.perception.null_backend import NullPerceptionBackend
from atlas.perception.types import PerceptionSource


class TestNullPerceptionBackend:
    def test_available_returns_false(self) -> None:
        backend = NullPerceptionBackend()
        assert backend.available() is False

    def test_capture_returns_unsupported_state(self) -> None:
        backend = NullPerceptionBackend()
        state = backend.capture_frontmost()
        assert state.source == PerceptionSource.UNSUPPORTED

    def test_capture_includes_note(self) -> None:
        backend = NullPerceptionBackend()
        state = backend.capture_frontmost()
        assert "unsupported" in state.note.lower()
