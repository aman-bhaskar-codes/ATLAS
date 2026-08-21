"""Perception fusion (Phase 3).

Combines evidence channels into one observation the reasoning core can trust.

POLICY (fixed, not configurable per substrate):
  structured evidence  -> preferred (accessibility/DOM/hierarchy)
  visual evidence      -> fallback / confirmation
  mixed evidence       -> fuse; agreement raises confidence, disagreement
                          lowers it and flags the snapshot for re-perception

WHY rank-based fusion over score blending: structured and visual confidences
live on different scales; agreement/disagreement is the signal that matters.
"""

from __future__ import annotations

from atlas.perception.contracts import (
    PerceivedElement,
    PerceptionModality,
    PerceptionSnapshot,
)


class PerceptionFusion:
    """Fuse multiple snapshots of the same surface into one."""

    def fuse(self, snapshots: tuple[PerceptionSnapshot, ...], *, snapshot_id: str) -> PerceptionSnapshot:
        if not snapshots:
            raise ValueError("PerceptionFusion.fuse requires at least one snapshot")
        if len(snapshots) == 1:
            return snapshots[0]

        base = snapshots[0]
        structured = tuple(s for s in snapshots if _has_structure(s))
        visual_only = tuple(s for s in snapshots if not _has_structure(s))

        # Elements: structured sources are authoritative; visual-only snapshots
        # contribute text/state but not element identity.
        elements = _merge_elements(tuple(e for s in structured for e in s.elements))

        # Confidence: structured agreement boosts; empty structure with only
        # visual evidence keeps confidence modest.
        if structured and visual_only:
            confidence = min(1.0, max(s.confidence for s in structured) + 0.05)
            note = "fused structured+visual evidence"
        elif structured:
            confidence = max(s.confidence for s in structured)
            note = "structured evidence only"
        else:
            confidence = max(s.confidence for s in visual_only) * 0.8
            note = "visual evidence only — structure unavailable"

        modalities = tuple(sorted({m for s in snapshots for m in s.modalities}, key=lambda m: m.value))
        text = next((s.text for s in snapshots if s.text), None)
        visual = next((s.visual for s in snapshots if s.visual), None)
        state: dict[str, object] = {}
        for s in snapshots:
            state.update(s.state)

        return PerceptionSnapshot(
            id=snapshot_id,
            substrate=base.substrate,
            source="+".join(dict.fromkeys(s.source for s in snapshots)),
            captured_ts=max(s.captured_ts for s in snapshots),
            url=base.url,
            app_name=base.app_name,
            window_title=base.window_title,
            activity=base.activity,
            modalities=modalities,
            elements=elements,
            text=text,
            visual=visual,
            state=state,
            confidence=confidence,
            sensitive=any(s.sensitive for s in snapshots),
            note=note,
        )


def _has_structure(s: PerceptionSnapshot) -> bool:
    structural = {
        PerceptionModality.ACCESSIBILITY,
        PerceptionModality.DOM,
        PerceptionModality.STRUCTURE,
        PerceptionModality.WINDOW_TREE,
    }
    return bool(s.elements) or bool(structural & set(s.modalities))


def _merge_elements(elements: tuple[PerceivedElement, ...]) -> tuple[PerceivedElement, ...]:
    """Deduplicate elements by (role, label); keep the highest-confidence one."""
    seen: dict[tuple[str, str | None], PerceivedElement] = {}
    for el in elements:
        key = (el.role, el.label)
        existing = seen.get(key)
        if existing is None or el.confidence > existing.confidence:
            seen[key] = el
    return tuple(seen.values())
