"""Replay environment — side-effect-free re-execution for counterfactuals
(Prompt 4 §28-§29).

HARD SAFETY RULE (§28): counterfactual replay must NEVER cause real-world
side effects. Only deterministic/sandbox/golden/recorded/simulated/dry-run
work is replayable; email sends, payments, deletions, real external
mutations and credential operations are NEVER replayed unless a fully
isolated safe simulator exists.
"""

from __future__ import annotations

from typing import Any, Protocol

from atlas.adaptation.domain import CounterfactualMode
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.replay")

#: Action/task classes that may be replayed (§28 allow-list).
REPLAYABLE_CLASSES: frozenset[str] = frozenset(
    {"deterministic", "sandbox", "golden", "recorded", "simulation", "dry_run"}
)

#: Action classes that must never be replayed against the real world (§28).
NEVER_REPLAY_CLASSES: frozenset[str] = frozenset(
    {"email_send", "payment", "deletion", "external_mutation", "credential_operation"}
)

_MODE_BY_CLASS: dict[str, CounterfactualMode] = {
    "deterministic": CounterfactualMode.DETERMINISTIC,
    "sandbox": CounterfactualMode.SANDBOX,
    "golden": CounterfactualMode.GOLDEN,
    "recorded": CounterfactualMode.RECORDED,
    "simulation": CounterfactualMode.SIMULATION,
    "dry_run": CounterfactualMode.DRY_RUN,
}


def replay_allowed(action_class: str, *, has_isolated_simulator: bool = False) -> bool:
    """§28 gate: deny-by-default. A never-replay class is only allowed when a
    fully isolated safe simulator exists; anything not on the allow-list is
    refused outright."""
    if action_class in NEVER_REPLAY_CLASSES:
        return has_isolated_simulator
    return action_class in REPLAYABLE_CLASSES


def mode_for(action_class: str) -> CounterfactualMode | None:
    """The counterfactual mode for a replayable class, or None if refused."""
    if not replay_allowed(action_class):
        return None
    return _MODE_BY_CLASS.get(action_class, CounterfactualMode.SIMULATION)


class ReplayOutcome:
    """What a replayed alternative action produced."""

    def __init__(self, *, success: bool, score: float, detail: str = "") -> None:
        self.success = success
        self.score = score
        self.detail = detail


class ReplayEnvironment(Protocol):
    """Snapshot / restore / simulate / replay (§29). Implementations may wrap
    browser test environments, filesystem sandboxes, code workspaces, API
    mocks or pure research replay — all must be side-effect free."""

    def snapshot(self, state: dict[str, Any]) -> str:
        """Clone the original environment state; returns a snapshot id."""
        ...

    def restore(self, snapshot_id: str) -> dict[str, Any]:
        """Materialize a previously cloned state."""
        ...

    def replay(self, snapshot_id: str, alternative_action: str) -> ReplayOutcome:
        """Apply the alternative action to the cloned state and observe."""
        ...


class InMemoryReplayEnvironment:
    """Deterministic in-memory replay for recorded results and simulations.

    `step_fn` is supplied by the caller and must be a pure function of
    (state, action) — this environment never touches external systems.
    """

    def __init__(self, *, step_fn: Any) -> None:
        self._step_fn = step_fn
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def snapshot(self, state: dict[str, Any]) -> str:
        self._counter += 1
        snapshot_id = f"snap_{self._counter:04d}"
        self._snapshots[snapshot_id] = dict(state)
        return snapshot_id

    def restore(self, snapshot_id: str) -> dict[str, Any]:
        if snapshot_id not in self._snapshots:
            msg = f"unknown snapshot: {snapshot_id}"
            raise KeyError(msg)
        return dict(self._snapshots[snapshot_id])

    def replay(self, snapshot_id: str, alternative_action: str) -> ReplayOutcome:
        state = self.restore(snapshot_id)
        result = self._step_fn(state, alternative_action)
        if isinstance(result, ReplayOutcome):
            return result
        # Plain (success, score) tuple from simple simulators.
        success, score = result
        return ReplayOutcome(success=bool(success), score=float(score), detail="simulated")


__all__ = [
    "NEVER_REPLAY_CLASSES",
    "REPLAYABLE_CLASSES",
    "InMemoryReplayEnvironment",
    "ReplayEnvironment",
    "ReplayOutcome",
    "mode_for",
    "replay_allowed",
]
