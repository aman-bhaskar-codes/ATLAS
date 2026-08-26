"""Tests for perception types."""

from __future__ import annotations

from atlas.perception.types import PerceptionSource, ScreenState, UIElement


class TestPerceptionSource:
    def test_values(self) -> None:
        assert PerceptionSource.AX_TREE.value == "ax_tree"
        assert PerceptionSource.OCR.value == "ocr"
        assert PerceptionSource.CLOUD_VISION.value == "cloud_vision"
        assert PerceptionSource.UNSUPPORTED.value == "unsupported"


class TestUIElement:
    def test_create_minimal(self) -> None:
        el = UIElement(role="button")
        assert el.role == "button"
        assert el.label is None
        assert el.enabled is True
        assert el.focused is False

    def test_create_full(self) -> None:
        el = UIElement(
            role="text_field",
            label="Username",
            value="admin",
            enabled=True,
            focused=True,
            ax_path="window[0]/text_field[1]:Username",
            bounds=(100, 200, 300, 400),
        )
        assert el.role == "text_field"
        assert el.label == "Username"
        assert el.value == "admin"
        assert el.focused is True


class TestScreenState:
    def test_create_minimal(self) -> None:
        state = ScreenState(source=PerceptionSource.AX_TREE)
        assert state.source == PerceptionSource.AX_TREE
        assert state.elements == ()
        assert state.sensitive is False

    def test_summarize_empty(self) -> None:
        state = ScreenState(source=PerceptionSource.AX_TREE, app_name="Safari")
        summary = state.summarize()
        assert "Safari" in summary
        assert "ax_tree" in summary

    def test_summarize_with_elements(self) -> None:
        el1 = UIElement(role="button", label="Send")
        el2 = UIElement(role="text_field", label="Subject")
        state = ScreenState(
            source=PerceptionSource.AX_TREE,
            app_name="Mail",
            elements=(el1, el2),
        )
        summary = state.summarize()
        assert "button" in summary
        assert "Send" in summary
        assert "text_field" in summary

    def test_summarize_sensitive(self) -> None:
        state = ScreenState(source=PerceptionSource.AX_TREE, app_name="1Password", sensitive=True)
        summary = state.summarize()
        assert "[SENSITIVE]" in summary

    def test_summarize_truncates(self) -> None:
        elements = tuple(UIElement(role="button", label=f"Button {i}") for i in range(50))
        state = ScreenState(source=PerceptionSource.AX_TREE, elements=elements)
        summary = state.summarize(limit=10)
        assert "more elements" in summary

    def test_summarize_disabled_element(self) -> None:
        el = UIElement(role="button", label="Submit", enabled=False)
        state = ScreenState(source=PerceptionSource.AX_TREE, elements=(el,))
        summary = state.summarize()
        assert "(disabled)" in summary

    def test_summarize_focused_element(self) -> None:
        el = UIElement(role="text_field", label="Search", focused=True)
        state = ScreenState(source=PerceptionSource.AX_TREE, elements=(el,))
        summary = state.summarize()
        assert "(focused)" in summary
