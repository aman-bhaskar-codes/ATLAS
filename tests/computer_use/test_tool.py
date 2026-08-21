"""ComputerUseTool tests — the safety-funnel surface the orchestrator calls."""

from __future__ import annotations

from atlas.capabilities.computer_use.engine import ComputerUseEngine
from atlas.capabilities.computer_use.tool import ComputerUseTool
from atlas.perception.contracts import Substrate

from .fakes import FakeControlAdapter, FakePerceptionAdapter, el, make_snapshot


def _tool(snaps: list, ctrl: FakeControlAdapter | None = None) -> tuple[ComputerUseTool, FakeControlAdapter]:
    control = ctrl or FakeControlAdapter()
    engine = ComputerUseEngine(
        {Substrate.BROWSER: FakePerceptionAdapter(snaps)},
        {Substrate.BROWSER: control},
    )
    return ComputerUseTool(engine), control


async def test_perceive_returns_redacted_summary() -> None:
    snap = make_snapshot(
        url="https://bank.test",
        elements=(el("text_field", "Password", value="hunter2"),),
    )
    tool, _ = _tool([snap])
    result = await tool.execute({"op": "perceive", "substrate": "browser"})
    assert result.ok is True
    summary = result.output["summary"]
    assert "hunter2" not in summary  # credential value never reaches the model surface
    assert result.output["element_count"] == 1


async def test_act_reports_verified_flag_and_side_effect() -> None:
    before = make_snapshot(elements=(el("button", "Send"),))
    after = make_snapshot(elements=(el("button", "Send"),), state={"dom_hash": "changed"})
    tool, ctrl = _tool([before, after])
    result = await tool.execute(
        {
            "op": "act",
            "substrate": "browser",
            "operation": "click",
            "target": {"strategy": "text", "value": "Send"},
            "expectations": [{"kind": "state_changed"}],
        }
    )
    assert result.ok is True
    assert result.output["verified"] is True
    assert len(result.side_effects) == 1
    assert len(ctrl.dispatched) == 1


async def test_act_unverified_is_not_ok() -> None:
    before = make_snapshot(elements=(el("button", "Send"),))
    after = make_snapshot(elements=(el("button", "Send"),))
    tool, _ = _tool([before, after])
    result = await tool.execute(
        {
            "op": "act",
            "substrate": "browser",
            "operation": "click",
            "target": {"strategy": "text", "value": "Send"},
            "expectations": [{"kind": "url_contains", "value": "never-happens"}],
        }
    )
    assert result.ok is False
    assert result.output["verified"] is False


async def test_unknown_substrate_is_clean_error() -> None:
    tool, _ = _tool([])
    result = await tool.execute({"op": "perceive", "substrate": "holodeck"})
    assert result.ok is False
    assert "unknown substrate" in (result.error or "")


async def test_unavailable_substrate_is_honest_error() -> None:
    engine = ComputerUseEngine({}, {})
    tool = ComputerUseTool(engine)
    result = await tool.execute({"op": "perceive", "substrate": "android"})
    assert result.ok is False
    assert "no perception adapter" in (result.error or "")


async def test_dry_run_never_executes() -> None:
    tool, ctrl = _tool([])
    preview = tool.dry_run(
        {"op": "act", "substrate": "macos", "operation": "click", "target": {"strategy": "text", "value": "OK"}}
    )
    assert "ACT on macos" in preview
    assert ctrl.dispatched == []
