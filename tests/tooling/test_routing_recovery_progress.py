"""Recovery routing + progress ledger tests (Part 3 §48-§53/§84)."""

from __future__ import annotations

from atlas.tooling.routing.models import FailureCategory, RecoveryAction
from atlas.tooling.routing.progress import LoopDetector, ProgressLedger, StallDetector
from atlas.tooling.routing.recovery import RecoveryRouter, classify_failure

# ── Failure classification (§51) — integrates the existing taxonomy ────── #


def test_failure_classification_from_tooling_failure_kinds() -> None:
    assert classify_failure(failure_kind="timeout") == FailureCategory.TIMEOUT
    assert (
        classify_failure(failure_kind="policy_denied", error="denied (tier BLOCK): nope")
        == FailureCategory.GOAL_MISMATCH
    )
    assert classify_failure(failure_kind="unavailable", error="no provider available") == FailureCategory.UNAVAILABLE
    assert classify_failure(failure_kind="validation") == FailureCategory.SCHEMA
    assert (
        classify_failure(failure_kind="execution_error", error="ProviderAuthError: credential missing")
        == FailureCategory.AUTH
    )
    assert classify_failure(failure_kind="weirdness") == FailureCategory.UNKNOWN


def test_failure_classification_from_http_status() -> None:
    assert classify_failure(status_code=429) == FailureCategory.RATE_LIMIT
    assert classify_failure(status_code=402) == FailureCategory.QUOTA
    assert classify_failure(status_code=401) == FailureCategory.AUTH


# ── Recovery routing (§52/§53) ─────────────────────────────────────────── #


def test_timeout_recovery_ladder_walks_the_graph() -> None:
    """§52: TIMEOUT → retry → alternate provider → alternate tool → replan."""
    router = RecoveryRouter(max_retries=2, max_replans=2)
    fallbacks = ("mcp:b:tool", "native:atlas:shell")
    decisions = []
    cursor = 0
    for attempt in range(1, 6):
        decision = router.next_recovery(
            route_id="r1",
            failure=FailureCategory.TIMEOUT,
            attempt=attempt,
            fallback_candidates=fallbacks,
            fallback_cursor=cursor,
        )
        decisions.append(decision.action)
        if decision.action == RecoveryAction.ALTERNATE_CANDIDATE:
            cursor += 1
    assert decisions == [
        RecoveryAction.RETRY,
        RecoveryAction.RETRY,
        RecoveryAction.ALTERNATE_CANDIDATE,
        RecoveryAction.ALTERNATE_CANDIDATE,
        RecoveryAction.REPLAN,
    ]


def test_auth_recovery_escalates_to_human() -> None:
    """§52: AUTH → alternate credential/provider → human action (never silent retry)."""
    router = RecoveryRouter()
    decision = router.next_recovery(route_id="r2", failure=FailureCategory.AUTH, attempt=1)
    assert decision.action == RecoveryAction.ESCALATE_HUMAN


def test_recovery_bounds_are_hard_and_terminate() -> None:
    """§84: recovery can never exceed retry/replan bounds — exhaustion
    terminates instead of looping."""
    router = RecoveryRouter(max_retries=1, max_replans=1)
    actions = []
    attempt = 1
    for _ in range(10):
        decision = router.next_recovery(route_id="r3", failure=FailureCategory.UNKNOWN, attempt=attempt)
        actions.append(decision.action)
        if decision.exhausted:
            break
        attempt += 1
        if decision.action == RecoveryAction.REPLAN:
            # replan budget exhausted on second request
            pass
    assert RecoveryAction.TERMINATE in actions
    assert len(actions) <= 5  # bounded, no infinite recovery


def test_replan_budget_is_enforced_per_route() -> None:
    router = RecoveryRouter(max_retries=0, max_replans=2)
    first = router.next_recovery(route_id="r4", failure=FailureCategory.GOAL_MISMATCH, attempt=1)
    second = router.next_recovery(route_id="r4", failure=FailureCategory.GOAL_MISMATCH, attempt=1)
    third = router.next_recovery(route_id="r4", failure=FailureCategory.GOAL_MISMATCH, attempt=1)
    assert first.action == RecoveryAction.REPLAN
    assert second.action == RecoveryAction.REPLAN
    assert third.action == RecoveryAction.TERMINATE and third.exhausted


def test_safety_denial_is_not_a_recoverable_routing_failure() -> None:
    """§21/§31: a SafetyEngine denial is terminal at the routing layer —
    recovery cannot override it with a retry of the same action."""
    decision = RecoveryRouter().next_recovery(
        route_id="r5",
        failure=classify_failure(failure_kind="policy_denied", error="denied (tier BLOCK)"),
        attempt=1,
    )
    assert decision.action in (RecoveryAction.REPLAN, RecoveryAction.ESCALATE_HUMAN)


# ── Progress ledger (§48) ──────────────────────────────────────────────── #


def test_progress_ledger_tracks_subgoal_lifecycle() -> None:
    ledger = ProgressLedger(objective="do research")
    ledger.add_subgoal("gather sources")
    ledger.add_subgoal("synthesize")
    ledger.mark_completed("gather sources")
    ledger.mark_failed("synthesize")
    state = ledger.snapshot()
    assert state.completed == ("gather sources",)
    assert state.failed == ("synthesize",)
    assert state.attempt_count == 1
    assert state.last_progress_ts is not None


# ── Stall detection (§49) ──────────────────────────────────────────────── #


def test_stall_detector_catches_identical_actions() -> None:
    detector = StallDetector(max_identical_actions=3)
    assert detector.record_action("read:a.txt").stalled is False
    assert detector.record_action("read:a.txt").stalled is False
    verdict = detector.record_action("read:a.txt")
    assert verdict.stalled is True
    assert "identical action" in verdict.reason


def test_stall_detector_catches_repeated_same_failure() -> None:
    detector = StallDetector(max_identical_actions=3)
    detector.record_failure("timeout:tool-x")
    detector.record_failure("timeout:tool-x")
    verdict = detector.record_failure("timeout:tool-x")
    assert verdict.stalled is True


def test_stall_detector_catches_repeatedly_reopened_subgoal() -> None:
    detector = StallDetector()
    assert detector.record_reopen("synthesize").stalled is False
    assert detector.record_reopen("synthesize").stalled is False
    assert detector.record_reopen("synthesize").stalled is True


# ── Loop detection (§50) ───────────────────────────────────────────────── #


def test_loop_detector_catches_action_cycle() -> None:
    """§50: the cycle fires once it has repeated `max_cycle_repeats` full times
    (A→B→C twice) — before an unbounded loop can form."""
    detector = LoopDetector(max_cycle_repeats=2)
    for action in ("a", "b", "c", "a", "b"):
        assert detector.record(action).looping is False
    verdict = detector.record("c")  # A→B→C completed a second time
    assert verdict.looping is True
    assert verdict.cycle == ("a", "b", "c")


def test_loop_detector_ignores_non_cyclic_sequences() -> None:
    detector = LoopDetector(max_cycle_repeats=2)
    for action in ("a", "b", "c", "d", "e", "f", "g"):
        assert detector.record(action).looping is False
