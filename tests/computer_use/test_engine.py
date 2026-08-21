"""Engine action-loop tests (Phase 13 loop + Phase 47 honesty rules)."""

from __future__ import annotations

import pytest

from atlas.capabilities.computer_use.engine import (
    ComputerUseEngine,
    SubstrateUnavailableError,
    resolve_target_in_snapshot,
)
from atlas.capabilities.computer_use.verify import ExpectationKind, ExpectationSpec
from atlas.control.contracts import ActionCapability, ControlAction, ControlResult
from atlas.perception.contracts import Substrate
from atlas.perception.targets import TargetRef, TargetStrategy

from .fakes import FakeControlAdapter, FakePerceptionAdapter, el, make_snapshot


def _engine(
    perception_snaps: list, control: FakeControlAdapter | None = None
) -> tuple[ComputerUseEngine, FakeControlAdapter]:
    ctrl = control or FakeControlAdapter()
    engine = ComputerUseEngine(
        {Substrate.BROWSER: FakePerceptionAdapter(perception_snaps)},
        {Substrate.BROWSER: ctrl},
    )
    return engine, ctrl


async def test_perceive_returns_snapshot() -> None:
    engine, _ = _engine([make_snapshot(url="https://a.test")])
    snap = await engine.perceive(Substrate.BROWSER)
    assert snap.url == "https://a.test"


async def test_perceive_missing_substrate_raises_unavailable() -> None:
    engine = ComputerUseEngine({}, {})
    with pytest.raises(SubstrateUnavailableError):
        await engine.perceive(Substrate.MACOS)


async def test_act_without_control_adapter_reports_honest_limitation() -> None:
    engine = ComputerUseEngine({Substrate.BROWSER: FakePerceptionAdapter([])}, {})
    action = ControlAction(capability=ActionCapability.UI, operation="click")
    outcome = await engine.act(Substrate.BROWSER, action)
    assert outcome.ok is False
    assert "no control adapter" in (outcome.note or "")


async def test_act_resolves_target_then_dispatches() -> None:
    before = make_snapshot(elements=(el("button", "Send"),))
    after = make_snapshot(elements=(el("button", "Send"),), state={"dom_hash": "h2"})
    engine, ctrl = _engine([before, after])
    action = ControlAction(
        capability=ActionCapability.UI,
        operation="click",
        target=TargetRef(strategy=TargetStrategy.TEXT, value="Send"),
    )
    outcome = await engine.act(Substrate.BROWSER, action, (ExpectationSpec(kind=ExpectationKind.STATE_CHANGED),))
    assert outcome.ok is True
    assert outcome.resolved is not None
    assert outcome.resolved.element_index == 0
    assert len(ctrl.dispatched) == 1


async def test_act_refuses_when_target_not_in_perception() -> None:
    before = make_snapshot(elements=(el("button", "Cancel"),))
    engine, ctrl = _engine([before])
    action = ControlAction(
        capability=ActionCapability.UI,
        operation="click",
        target=TargetRef(strategy=TargetStrategy.TEXT, value="Send"),
    )
    outcome = await engine.act(Substrate.BROWSER, action)
    assert outcome.ok is False
    assert "target not found" in (outcome.note or "")
    assert ctrl.dispatched == []  # never executed on a guess


async def test_act_failed_verification_is_not_upgraded_to_ok() -> None:
    before = make_snapshot(elements=(el("button", "Send"),))
    after = make_snapshot(elements=(el("button", "Send"),))  # no change
    engine, _ = _engine([before, after])
    action = ControlAction(
        capability=ActionCapability.UI,
        operation="click",
        target=TargetRef(strategy=TargetStrategy.TEXT, value="Send"),
    )
    # Expect a URL that never appears → verification must fail.
    outcome = await engine.act(
        Substrate.BROWSER, action, (ExpectationSpec(kind=ExpectationKind.URL_CONTAINS, value="does-not-exist"),)
    )
    assert outcome.result is not None and outcome.result.ok is True  # adapter ran
    assert outcome.ok is False  # but goal not verified
    assert outcome.verification is not None and outcome.verification.verified is False


async def test_act_adapter_failure_short_circuits() -> None:
    before = make_snapshot(elements=(el("button", "Send"),))
    ctrl = FakeControlAdapter(results=[ControlResult(ok=False, error="boom")])
    engine, _ = _engine([before], control=ctrl)
    action = ControlAction(
        capability=ActionCapability.UI,
        operation="click",
        target=TargetRef(strategy=TargetStrategy.TEXT, value="Send"),
    )
    outcome = await engine.act(Substrate.BROWSER, action)
    assert outcome.ok is False
    assert outcome.verification is None  # nothing to verify when execution failed


def test_resolve_prefers_stable_id_over_text() -> None:
    snap = make_snapshot(
        elements=(
            el("button", "Send", stable_id="btn-send"),
            el("link", "Send again"),
        )
    )
    target = TargetRef(strategy=TargetStrategy.STABLE_ID, value="btn-send", text="Send")
    resolved = resolve_target_in_snapshot(snap, target)
    assert resolved is not None
    assert resolved.strategy_used is TargetStrategy.STABLE_ID
    assert resolved.element_index == 0
    assert resolved.confidence > 0.9


def test_resolve_falls_back_to_text_hint() -> None:
    snap = make_snapshot(elements=(el("button", "Submit"),))
    target = TargetRef(strategy=TargetStrategy.SEMANTIC, value="", text="Submit")
    resolved = resolve_target_in_snapshot(snap, target)
    assert resolved is not None
    assert resolved.strategy_used is TargetStrategy.TEXT


def test_resolve_returns_none_when_nothing_matches() -> None:
    snap = make_snapshot(elements=(el("button", "Cancel"),))
    target = TargetRef(strategy=TargetStrategy.TEXT, value="Submit")
    assert resolve_target_in_snapshot(snap, target) is None


async def test_shutdown_stops_all_bodies() -> None:
    ctrl = FakeControlAdapter()
    engine, _ = _engine([], control=ctrl)
    await engine.shutdown()
    assert ctrl.stopped is True
