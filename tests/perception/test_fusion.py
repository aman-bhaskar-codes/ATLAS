"""Tests for perception fusion."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.perception.contracts import (
    PerceivedElement,
    PerceptionModality,
    PerceptionSnapshot,
    Substrate,
)
from atlas.perception.fusion import PerceptionFusion, _has_structure, _merge_elements


def make_snapshot(
    snapshot_id: str = "snap-1",
    elements: tuple[PerceivedElement, ...] = (),
    modalities: tuple[PerceptionModality, ...] = (PerceptionModality.ACCESSIBILITY,),
    confidence: float = 0.9,
    text: str | None = None,
    source: str = "accessibility",
    url: str | None = None,
    app_name: str | None = None,
    window_title: str | None = None,
    sensitive: bool = False,
) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        id=snapshot_id,
        substrate=Substrate.MACOS,
        source=source,
        captured_ts=datetime.now(UTC),
        url=url,
        app_name=app_name,
        window_title=window_title,
        activity=None,
        modalities=modalities,
        elements=elements,
        text=text,
        visual=None,
        state={},
        confidence=confidence,
        sensitive=sensitive,
    )


class TestPerceptionFusion:
    def test_empty_snapshots_raises(self) -> None:
        fusion = PerceptionFusion()
        with pytest.raises(ValueError, match="at least one"):
            fusion.fuse((), snapshot_id="test")

    def test_single_snapshot_returns_same(self) -> None:
        fusion = PerceptionFusion()
        snap = make_snapshot()
        result = fusion.fuse((snap,), snapshot_id="test")
        assert result == snap

    def test_fuses_multiple_snapshots(self) -> None:
        fusion = PerceptionFusion()
        snap1 = make_snapshot("snap-1", confidence=0.8, text="hello")
        snap2 = make_snapshot("snap-2", confidence=0.9, text="world")
        result = fusion.fuse((snap1, snap2), snapshot_id="fused")
        assert result.id == "fused"
        assert result.text == "hello"

    def test_fuses_modalities(self) -> None:
        fusion = PerceptionFusion()
        snap1 = make_snapshot("snap-1", modalities=(PerceptionModality.ACCESSIBILITY,))
        snap2 = make_snapshot("snap-2", modalities=(PerceptionModality.VISION,))
        result = fusion.fuse((snap1, snap2), snapshot_id="fused")
        assert PerceptionModality.ACCESSIBILITY in result.modalities
        assert PerceptionModality.VISION in result.modalities

    def test_merges_state(self) -> None:
        fusion = PerceptionFusion()
        snap1 = make_snapshot("snap-1")
        snap1.state["key1"] = "value1"
        snap2 = make_snapshot("snap-2")
        snap2.state["key2"] = "value2"
        result = fusion.fuse((snap1, snap2), snapshot_id="fused")
        assert result.state["key1"] == "value1"
        assert result.state["key2"] == "value2"

    def test_sensitive_propagates(self) -> None:
        fusion = PerceptionFusion()
        snap1 = make_snapshot("snap-1", sensitive=True)
        snap2 = make_snapshot("snap-2")
        result = fusion.fuse((snap1, snap2), snapshot_id="fused")
        assert result.sensitive is True

    def test_structured_plus_visual_confidence(self) -> None:
        fusion = PerceptionFusion()
        element = PerceivedElement(role="button", label="Click", confidence=0.9)
        snap1 = make_snapshot("snap-1", elements=(element,), confidence=0.8)
        snap2 = make_snapshot(
            "snap-2",
            modalities=(PerceptionModality.VISION,),
            confidence=0.7,
        )
        result = fusion.fuse((snap1, snap2), snapshot_id="fused")
        assert result.confidence > 0.8

    def test_structured_only_confidence(self) -> None:
        fusion = PerceptionFusion()
        element = PerceivedElement(role="button", label="Click", confidence=0.9)
        snap1 = make_snapshot("snap-1", elements=(element,), confidence=0.8)
        snap2 = make_snapshot("snap-2", elements=(element,), confidence=0.9)
        result = fusion.fuse((snap1, snap2), snapshot_id="fused")
        assert result.confidence == 0.9

    def test_visual_only_confidence_reduced(self) -> None:
        fusion = PerceptionFusion()
        snap1 = make_snapshot(
            "snap-1",
            modalities=(PerceptionModality.VISION,),
            confidence=0.8,
        )
        snap2 = make_snapshot(
            "snap-2",
            modalities=(PerceptionModality.VISION,),
            confidence=0.9,
        )
        result = fusion.fuse((snap1, snap2), snapshot_id="fused")
        assert result.confidence == 0.9 * 0.8


class TestHasStructure:
    def test_elements_present(self) -> None:
        element = PerceivedElement(role="button", label="Click", confidence=0.9)
        snap = make_snapshot(elements=(element,))
        assert _has_structure(snap) is True

    def test_structural_modality(self) -> None:
        snap = make_snapshot(modalities=(PerceptionModality.DOM,))
        assert _has_structure(snap) is True

    def test_no_structure(self) -> None:
        snap = make_snapshot(modalities=(PerceptionModality.VISION,))
        assert _has_structure(snap) is False


class TestMergeElements:
    def test_deduplicates_by_role_label(self) -> None:
        el1 = PerceivedElement(role="button", label="OK", confidence=0.7)
        el2 = PerceivedElement(role="button", label="OK", confidence=0.9)
        result = _merge_elements((el1, el2))
        assert len(result) == 1
        assert result[0].confidence == 0.9

    def test_keeps_different_elements(self) -> None:
        el1 = PerceivedElement(role="button", label="OK", confidence=0.7)
        el2 = PerceivedElement(role="button", label="Cancel", confidence=0.8)
        result = _merge_elements((el1, el2))
        assert len(result) == 2

    def test_empty_input(self) -> None:
        result = _merge_elements(())
        assert result == ()
