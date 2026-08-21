"""Verification tests (Phase 14): evidence-based success, honest failure."""

from __future__ import annotations

from atlas.capabilities.computer_use.verify import ExpectationKind, ExpectationSpec, verify_snapshots

from .fakes import el, make_snapshot


def test_url_contains_passes() -> None:
    after = make_snapshot(url="https://github.com/atlas")
    result = verify_snapshots(
        (ExpectationSpec(kind=ExpectationKind.URL_CONTAINS, value="github.com"),),
        make_snapshot(),
        after,
    )
    assert result.verified is True
    assert "github.com" in result.evidence


def test_text_present_checks_elements_and_text() -> None:
    after = make_snapshot(elements=(el("heading", "Welcome to ATLAS"),))
    result = verify_snapshots(
        (ExpectationSpec(kind=ExpectationKind.TEXT_PRESENT, value="welcome"),),
        make_snapshot(),
        after,
    )
    assert result.verified is True


def test_element_present_with_role_filter() -> None:
    after = make_snapshot(elements=(el("button", "Settings"),))
    result = verify_snapshots(
        (ExpectationSpec(kind=ExpectationKind.ELEMENT_PRESENT, value="Settings", role="button"),),
        make_snapshot(),
        after,
    )
    assert result.verified is True


def test_app_active_for_macos() -> None:
    after = make_snapshot(app_name="TextEdit", url=None)
    result = verify_snapshots(
        (ExpectationSpec(kind=ExpectationKind.APP_ACTIVE, value="textedit"),),
        make_snapshot(),
        after,
    )
    assert result.verified is True


def test_state_changed_detects_dom_hash_change() -> None:
    before = make_snapshot(state={"dom_hash": "a"})
    after = make_snapshot(state={"dom_hash": "b"})
    result = verify_snapshots((ExpectationSpec(kind=ExpectationKind.STATE_CHANGED),), before, after)
    assert result.verified is True


def test_all_expectations_must_pass() -> None:
    after = make_snapshot(url="https://a.test")
    result = verify_snapshots(
        (
            ExpectationSpec(kind=ExpectationKind.URL_CONTAINS, value="a.test"),
            ExpectationSpec(kind=ExpectationKind.URL_CONTAINS, value="b.test"),
        ),
        make_snapshot(),
        after,
    )
    assert result.verified is False
    assert result.detail  # failure detail recorded


def test_no_expectations_refuses_to_claim_verified() -> None:
    """Phase 47: no expectations → never claim success."""
    result = verify_snapshots((), make_snapshot(), make_snapshot())
    assert result.verified is False
    assert "no expectations" in (result.detail or "")
