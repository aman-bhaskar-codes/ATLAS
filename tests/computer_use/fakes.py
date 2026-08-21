"""Shared fakes for computer-use tests — no real browsers/Macs/devices in CI."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from atlas.capabilities.computer_use.verify import ExpectationKind, ExpectationSpec
from atlas.control.contracts import ControlAction, ControlResult
from atlas.perception.contracts import (
    HealthStatus,
    PerceivedElement,
    PerceptionModality,
    PerceptionSnapshot,
    Substrate,
)


def make_snapshot(
    *,
    substrate: Substrate = Substrate.BROWSER,
    url: str | None = "https://example.com",
    app_name: str | None = None,
    elements: tuple[PerceivedElement, ...] = (),
    state: dict[str, object] | None = None,
    confidence: float = 0.95,
) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        id=uuid.uuid4().hex,
        substrate=substrate,
        source="fake",
        captured_ts=datetime.now(UTC),
        url=url,
        app_name=app_name,
        modalities=(PerceptionModality.STRUCTURE,),
        elements=elements,
        state=state or {"dom_hash": "h1"},
        confidence=confidence,
    )


def el(
    role: str, label: str | None = None, *, stable_id: str | None = None, value: str | None = None
) -> PerceivedElement:
    return PerceivedElement(role=role, label=label, stable_id=stable_id, value=value, confidence=0.95)


class FakePerceptionAdapter:
    """Returns a queued sequence of snapshots (one per snapshot() call)."""

    substrate = Substrate.BROWSER

    def __init__(self, snapshots: list[PerceptionSnapshot]) -> None:
        self._queue = list(snapshots)
        self.calls = 0

    async def snapshot(self, target: object | None = None) -> PerceptionSnapshot:
        self.calls += 1
        if self._queue:
            return self._queue.pop(0)
        return make_snapshot()

    async def health(self) -> HealthStatus:
        return HealthStatus(available=True, detail="fake")

    async def capabilities(self) -> tuple[PerceptionModality, ...]:
        return (PerceptionModality.STRUCTURE,)


class FakeControlAdapter:
    """Records dispatched actions and returns scripted results."""

    substrate = Substrate.BROWSER

    def __init__(self, results: list[ControlResult] | None = None) -> None:
        self._results = list(results or [])
        self.dispatched: list[ControlAction] = []
        self.stopped = False

    async def dispatch(self, action: ControlAction) -> ControlResult:
        self.dispatched.append(action)
        if self._results:
            return self._results.pop(0)
        return ControlResult(ok=True, evidence="fake executed")

    async def health(self) -> HealthStatus:
        return HealthStatus(available=True, detail="fake")

    async def capabilities(self) -> tuple[str, ...]:
        return ("click", "navigate")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True


URL_EXPECT = ExpectationSpec(kind=ExpectationKind.URL_CONTAINS, value="example.com")
