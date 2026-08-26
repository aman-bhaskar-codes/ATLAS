"""Redaction (Phase 17) and perception-cache (Phase 20) tests."""

from __future__ import annotations

from atlas.capabilities.computer_use.cache import MUTATING_OPERATIONS, PerceptionCache
from atlas.capabilities.computer_use.redaction import (
    RedactionPolicy,
    contains_secret_shape,
    is_sensitive_field,
    redact_snapshot,
)
from atlas.perception.contracts import VisualEvidence

from .fakes import el, make_snapshot


def test_sensitive_field_detection() -> None:
    assert is_sensitive_field("Password")
    assert is_sensitive_field("Enter your OTP code")
    assert is_sensitive_field("credit card number")
    assert not is_sensitive_field("Username")
    assert not is_sensitive_field(None)


def test_secret_shape_detection() -> None:
    assert contains_secret_shape("Authorization: Bearer abc123XYZ-token")
    assert contains_secret_shape("ghp_abcdefghijklmnopqrstuv")
    assert not contains_secret_shape("just a normal sentence")


def test_redaction_masks_sensitive_values() -> None:
    snap = make_snapshot(
        elements=(
            el("text_field", "Password", value="hunter2"),
            el("text_field", "Username", value="alice"),
        )
    )
    safe = redact_snapshot(snap)
    values = {e.label: e.value for e in safe.elements}
    assert values["Password"] == "[REDACTED]"
    assert values["Username"] == "alice"
    # Original untouched.
    assert snap.elements[0].value == "hunter2"


def test_redaction_strips_visual_on_sensitive_surface() -> None:
    snap = make_snapshot().model_copy(update={"sensitive": True, "visual": VisualEvidence(data=b"pixels")})
    safe = redact_snapshot(snap)
    assert safe.visual is None


def test_redaction_policy_can_allow_visual() -> None:
    snap = make_snapshot().model_copy(update={"sensitive": True, "visual": VisualEvidence(data=b"pixels")})
    safe = redact_snapshot(snap, policy=RedactionPolicy(allow_visual_for_sensitive=True))
    assert safe.visual is not None


def test_cache_invalidates_on_mutating_operations() -> None:
    cache = PerceptionCache()
    from atlas.perception.contracts import Substrate

    snap = make_snapshot()
    cache.put(Substrate.BROWSER, "https://a.test", snap)
    assert cache.get(Substrate.BROWSER, "https://a.test") is snap
    assert cache.invalidate_if_mutating(Substrate.BROWSER, "click") is True
    assert cache.get(Substrate.BROWSER, "https://a.test") is None


def test_cache_keeps_entries_for_read_only_ops() -> None:
    cache = PerceptionCache()
    from atlas.perception.contracts import Substrate

    snap = make_snapshot()
    cache.put(Substrate.BROWSER, "s", snap)
    assert cache.invalidate_if_mutating(Substrate.BROWSER, "observe") is False
    assert cache.get(Substrate.BROWSER, "s") is snap


def test_mutating_operations_cover_all_bodies() -> None:
    for op in ("click", "type", "tap", "swipe", "launch", "navigate"):
        assert op in MUTATING_OPERATIONS
