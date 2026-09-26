"""Progress ledger + stall/loop detection (Part 3 §48-§50).

Adapted from deep-agent task tracking and Magentic-style progress management
(§48): explicit subgoal state, bounded repetition, cycle detection. The
routing layer consumes verdicts from these detectors to stop autonomous loops
— they never run unbounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from atlas.tooling.routing.models import ProgressState


@dataclass
class ProgressLedger:
    objective: str = ""
    subgoals: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    attempt_count: int = 0
    last_progress_ts: datetime | None = None

    def add_subgoal(self, subgoal: str) -> None:
        if subgoal not in self.subgoals:
            self.subgoals.append(subgoal)
            self.in_progress.append(subgoal)

    def mark_completed(self, subgoal: str) -> None:
        self._move(subgoal, self.completed)
        self._touch_progress()

    def mark_blocked(self, subgoal: str) -> None:
        self._move(subgoal, self.blocked)

    def mark_failed(self, subgoal: str) -> None:
        self._move(subgoal, self.failed)
        self.attempt_count += 1

    def reopen(self, subgoal: str) -> None:
        """A completed subgoal reopened — a stall signal the detector watches (§49)."""
        if subgoal in self.completed:
            self.completed.remove(subgoal)
            self.in_progress.append(subgoal)
            self.attempt_count += 1

    def snapshot(self) -> ProgressState:
        return ProgressState(
            objective=self.objective,
            subgoals=tuple(self.subgoals),
            completed=tuple(self.completed),
            in_progress=tuple(self.in_progress),
            blocked=tuple(self.blocked),
            failed=tuple(self.failed),
            attempt_count=self.attempt_count,
            last_progress_ts=self.last_progress_ts,
        )

    def _move(self, subgoal: str, target: list[str]) -> None:
        for bucket in (self.in_progress, self.blocked, self.failed, self.completed):
            if subgoal in bucket:
                bucket.remove(subgoal)
        target.append(subgoal)

    def _touch_progress(self) -> None:
        self.last_progress_ts = datetime.now(UTC)


@dataclass(frozen=True)
class StallVerdict:
    stalled: bool = False
    reason: str = ""


class StallDetector:
    """§49: same action repeated / same failure repeated / no state change /
    subgoal reopened repeatedly."""

    def __init__(self, *, window: int = 5, max_identical_actions: int = 3) -> None:
        self._window = window
        self._max_identical = max_identical_actions
        self._recent_actions: list[str] = []
        self._recent_failures: list[str] = []
        self._reopen_counts: dict[str, int] = {}

    def record_action(self, action_key: str) -> StallVerdict:
        self._recent_actions.append(action_key)
        self._recent_actions = self._recent_actions[-self._window :]
        if len(self._recent_actions) >= self._max_identical:
            tail = self._recent_actions[-self._max_identical :]
            if len(set(tail)) == 1:
                return StallVerdict(
                    stalled=True,
                    reason=f"identical action {action_key!r} repeated {self._max_identical} times",
                )
        return StallVerdict()

    def record_failure(self, failure_key: str) -> StallVerdict:
        self._recent_failures.append(failure_key)
        self._recent_failures = self._recent_failures[-self._window :]
        if (
            len(self._recent_failures) >= self._max_identical
            and len(set(self._recent_failures[-self._max_identical :])) == 1
        ):
            return StallVerdict(stalled=True, reason=f"same failure {failure_key!r} repeated")
        return StallVerdict()

    def record_reopen(self, subgoal: str, *, max_reopens: int = 2) -> StallVerdict:
        count = self._reopen_counts.get(subgoal, 0) + 1
        self._reopen_counts[subgoal] = count
        if count > max_reopens:
            return StallVerdict(stalled=True, reason=f"subgoal {subgoal!r} reopened {count} times (> {max_reopens})")
        return StallVerdict()


@dataclass(frozen=True)
class LoopVerdict:
    looping: bool = False
    cycle: tuple[str, ...] = ()
    reason: str = ""


class LoopDetector:
    """§50: detects cycles in the action sequence (A→B→C→A) and
    retry→same-failure→retry patterns, with bounded repetition limits."""

    def __init__(self, *, max_cycle_repeats: int = 2, window: int = 12) -> None:
        self._max_cycle_repeats = max_cycle_repeats
        self._window = window
        self._history: list[str] = []

    def record(self, action_key: str) -> LoopVerdict:
        self._history.append(action_key)
        self._history = self._history[-self._window :]
        # Look for the shortest repeating cycle ending at the current action.
        for cycle_len in range(1, len(self._history) // 2 + 1):
            cycle = tuple(self._history[-cycle_len:])
            preceding = tuple(self._history[-2 * cycle_len : -cycle_len])
            if cycle == preceding and len(cycle) > 0:
                repeats = self._count_trailing_repeats(cycle)
                if repeats >= self._max_cycle_repeats:
                    return LoopVerdict(
                        looping=True,
                        cycle=cycle,
                        reason=f"cycle {' → '.join(cycle)} repeated {repeats} times",
                    )
        return LoopVerdict()

    def _count_trailing_repeats(self, cycle: tuple[str, ...]) -> int:
        n = len(cycle)
        repeats = 0
        i = len(self._history)
        while i - n >= 0 and tuple(self._history[i - n : i]) == cycle:
            repeats += 1
            i -= n
        return repeats


__all__ = ["LoopDetector", "ProgressLedger", "StallDetector", "StallVerdict"]
