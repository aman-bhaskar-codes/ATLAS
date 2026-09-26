"""Execution recovery controller (Part 4 §63-§67) + route staleness (§30-§31).

One controller decides the response to a step failure: RETRY / FALLBACK /
REPLAN / HUMAN / SKIP / TERMINATE. It NEVER executes the decision itself (§63)
— the engine does, after re-checking policy, budget, and candidate health
(§67: never assume a fallback is automatically allowed; the world may have
changed since routing, §30-§31).

The decision cascade mirrors Part 3: deterministic classification first; the
JudgmentProvider (Jev/LLM) is consulted ONLY for genuinely ambiguous classes
(e.g. BAD_RESULT-ish "did this actually succeed?") and only when enabled
(§32/§64) — its answer is still constrained by policy, budgets, and
side-effect rules (§33).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.execution.models import RetryClass, RetryPolicy, TerminalOutcome
from atlas.tooling.execution.retry import should_retry
from atlas.tooling.models.tool_result import UniversalToolResult
from atlas.tooling.routing.judgment import (
    JudgmentCascade,
    JudgmentQuestion,
    JudgmentState,
)
from atlas.tooling.routing.models import DecisionRisk, RecoveryAction

_AMBIGUOUS_CLASSES = frozenset({RetryClass.UNKNOWN, RetryClass.PERMANENT, RetryClass.NOT_FOUND})


@dataclass
class RecoveryContext:
    """Everything the controller may need (§63)."""

    route_id: str
    step_id: str
    candidate_id: str
    failure_class: RetryClass
    failure_message: str
    attempt: int
    retry_policy_max_attempts: int
    fallback_candidates: tuple[str, ...]
    fallback_cursor: int
    fallback_count: int
    max_fallback_hops: int
    replan_count: int
    max_replans: int
    idempotent: bool = True
    side_effects: bool = False
    last_result: UniversalToolResult | None = None


@dataclass
class ControllerDecision:
    action: RecoveryAction
    reason: str
    next_candidate_id: str | None = None
    delay_s: float = 0.0
    terminal_outcome: TerminalOutcome | None = None
    judgment_used: bool = False
    jev_results: dict[str, object] = field(default_factory=dict)


class RouteStalenessChecker:
    """§30-§31: a candidate is revalidated against the CURRENT catalog before
    every step; a materially changed catalog invalidates stale routes."""

    def __init__(self, catalog: ToolCatalog | None) -> None:
        self._catalog = catalog

    def revalidate(self, candidate_id: str, route_catalog_version: int) -> tuple[bool, str]:
        """(still_valid, reason)."""
        if self._catalog is None:
            return True, "no catalog wired; revalidation skipped"
        record = self._catalog.inspect(candidate_id)
        if record is None:
            return False, f"candidate {candidate_id!r} no longer exists in the catalog"
        if record.status.value not in ("READY",):
            return False, f"candidate {candidate_id!r} is now {record.status.value} (was routable at route time)"
        if route_catalog_version and self._catalog.version() != route_catalog_version:
            # Catalog changed since routing — the candidate itself is still
            # healthy, so the step may proceed; the ENGINE logs the drift and
            # the next failure re-routes. Only a dead candidate blocks.
            return True, (
                f"catalog changed v{route_catalog_version} -> v{self._catalog.version()}; "
                f"candidate {candidate_id!r} revalidated OK"
            )
        return True, "candidate revalidated OK"


class RecoveryController:
    def __init__(
        self,
        *,
        cascade: JudgmentCascade | None = None,
        staleness: RouteStalenessChecker | None = None,
        enable_judgment: bool = False,
    ) -> None:
        self._cascade = cascade
        self._staleness = staleness
        self._enable_judgment = enable_judgment

    async def decide(self, ctx: RecoveryContext) -> ControllerDecision:
        # 0. Deterministic retry gate FIRST (§28/§33): class + policy +
        #    side-effect awareness. This can already terminate the question.
        retry_call = should_retry(
            policy=RetryPolicy(max_attempts=ctx.retry_policy_max_attempts),
            retry_class=ctx.failure_class,
            attempt=ctx.attempt,
            idempotent=ctx.idempotent,
            side_effects=ctx.side_effects,
        )
        if retry_call.retry:
            return ControllerDecision(
                action=RecoveryAction.RETRY,
                reason=retry_call.reason,
                delay_s=retry_call.delay_s,
            )
        # Deterministic says no-retry. Why? Either the class isn't retryable
        # (→ fallback/replan/human path) or bounds/side-effects stopped us.

        # 1. Fallback chain (§29): next candidate from the Part-3 fallback
        #    graph, revalidated against the CURRENT catalog (§30/§67).
        if ctx.fallback_cursor < len(ctx.fallback_candidates):
            if ctx.fallback_count >= ctx.max_fallback_hops:
                return self._terminal(ctx, TerminalOutcome.BUDGET_EXCEEDED, "fallback budget exhausted")
            candidate = ctx.fallback_candidates[ctx.fallback_cursor]
            if self._staleness is not None:
                valid, reason = self._staleness.revalidate(candidate, route_catalog_version=0)
                if not valid:
                    return ControllerDecision(
                        action=RecoveryAction.FALLBACK,
                        reason=f"fallback {candidate!r} rejected: {reason}; advancing",
                        next_candidate_id=None,
                        # the engine advances the cursor and re-asks
                    )
            return ControllerDecision(
                action=RecoveryAction.FALLBACK,
                reason=f"fallback to {candidate} after {ctx.failure_class.value}: {ctx.failure_message}",
                next_candidate_id=candidate,
            )

        # §52: AUTH has no replan rung — alternate credential/provider then
        # HUMAN. A missing credential is not something replanning fixes.
        if ctx.failure_class == RetryClass.AUTH:
            return ControllerDecision(
                action=RecoveryAction.ESCALATE_HUMAN,
                reason="auth failure requires human action (alternate credential/provider)",
                terminal_outcome=TerminalOutcome.WAITING_HUMAN,
            )

        # 2. Bounded judgment for genuinely ambiguous failures (§32/§64):
        #    only when enabled, only for classes deterministic rules cannot
        #    classify confidently, and only to choose RETRY-vs-REPLAN — policy
        #    and budgets still constrain the final action (§33).
        if (
            self._enable_judgment
            and self._cascade is not None
            and ctx.failure_class in _AMBIGUOUS_CLASSES
            and ctx.replan_count < ctx.max_replans
        ):
            batch = await self._cascade.ask(
                (
                    JudgmentQuestion(
                        question_id="recovery",
                        text="Should the failed step be retried with the same candidate, or should the route replan?",
                        kind="choice",
                        options=("retry", "replan"),
                    ),
                ),
                JudgmentState(
                    state_id=ctx.route_id,
                    summary=(
                        f"step {ctx.step_id} failed with {ctx.failure_class.value}: {ctx.failure_message}. "
                        f"candidate={ctx.candidate_id} attempt={ctx.attempt}"
                    ),
                    data={"step_id": ctx.step_id, "failure": ctx.failure_class.value},
                ),
                risk=DecisionRisk.MEDIUM_RISK,
            )
            answer = batch.results[0] if batch.results else None
            if answer is not None and answer.accepted and answer.choice == "retry":
                return ControllerDecision(
                    action=RecoveryAction.RETRY,
                    reason=f"judgment (accepted, score {answer.score:.2f}) says retry; "
                    "bounded by policy and side-effect rules (§33)",
                    delay_s=0.5,
                    judgment_used=True,
                    jev_results={"recovery": answer.choice or ""},
                )
            if answer is not None and answer.accepted and answer.choice == "replan":
                return self._replan(ctx, "judgment (accepted) says replan", judgment_used=True)

        # 3. Replan within budget (§52).
        if ctx.replan_count < ctx.max_replans:
            return self._replan(ctx, f"replan after {ctx.failure_class.value} with fallbacks exhausted")

        # 4. Nothing left → structured terminal (§93).
        return self._terminal(
            ctx,
            TerminalOutcome.DEAD_END,
            f"no recovery left: {ctx.failure_class.value}, fallbacks exhausted, replans exhausted",
        )

    def _replan(self, ctx: RecoveryContext, reason: str, *, judgment_used: bool = False) -> ControllerDecision:
        return ControllerDecision(
            action=RecoveryAction.REPLAN,
            reason=reason,
            judgment_used=judgment_used,
        )

    def _terminal(self, ctx: RecoveryContext, outcome: TerminalOutcome, reason: str) -> ControllerDecision:
        return ControllerDecision(
            action=RecoveryAction.TERMINATE,
            reason=reason,
            terminal_outcome=outcome,
        )


def recovery_outcome_for(action: RecoveryAction) -> TerminalOutcome | None:
    """Map a recovery action to the terminal outcome when it ends the run."""
    return {
        RecoveryAction.ESCALATE_HUMAN: TerminalOutcome.WAITING_HUMAN,
        RecoveryAction.TERMINATE: TerminalOutcome.FAILED,
    }.get(action)


__all__ = [
    "ControllerDecision",
    "RecoveryContext",
    "RecoveryController",
    "RouteStalenessChecker",
    "recovery_outcome_for",
]
