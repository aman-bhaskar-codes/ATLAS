"""Browser adapter tests — TargetRef→Locator translation rules."""

from __future__ import annotations

import pytest

from atlas.capabilities.browser.domain.locator import LocatorKind
from atlas.capabilities.computer_use.adapters.browser import target_to_locator
from atlas.perception.targets import TargetRef, TargetStrategy


def test_dom_selector_maps_to_css() -> None:
    loc = target_to_locator(TargetRef(strategy=TargetStrategy.DOM_SELECTOR, value="#login-form button"))
    assert loc.kind is LocatorKind.CSS
    assert loc.value == "#login-form button"


def test_stable_id_maps_to_attribute_selector() -> None:
    loc = target_to_locator(TargetRef(strategy=TargetStrategy.STABLE_ID, value="submit-btn"))
    assert loc.kind is LocatorKind.CSS
    assert loc.value == '[id="submit-btn"]'


def test_text_strategy_uses_text_locator() -> None:
    loc = target_to_locator(TargetRef(strategy=TargetStrategy.TEXT, value="Sign in", exact=True))
    assert loc.kind is LocatorKind.TEXT
    assert loc.exact is True


def test_role_strategy_carries_accessible_name() -> None:
    loc = target_to_locator(TargetRef(strategy=TargetStrategy.ROLE, value="button", text="Submit"))
    assert loc.kind is LocatorKind.ROLE
    assert loc.name == "Submit"


def test_coordinates_rejected_for_browser() -> None:
    with pytest.raises(ValueError):
        target_to_locator(TargetRef(strategy=TargetStrategy.COORDINATES, value="10,20", x=10, y=20))
