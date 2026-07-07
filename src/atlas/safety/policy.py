"""Policy engine — an ordered chain of tightening-only policies.

WHY separate from the classifier: the classifier answers 'which tier for this
request in principle'. Policies answer 'given current CONTEXT (kill switch now,
quiet hours later, earned autonomy later), should we tighten further'. Each
policy may only make the decision stricter. A policy raising => fail-closed deny.
"""

from __future__ import annotations

from typing import Protocol

from atlas.infra.types import SafetyDecision, Tier
from atlas.safety.killswitch import KillSwitch


class Policy(Protocol):
    def apply(self, decision: SafetyDecision) -> SafetyDecision: ...


class KillSwitchPolicy:
    """If the kill switch is active, deny everything. WHY a policy and also an
    engine-level check: defense in depth — the engine checks before execution,
    and this ensures the decision itself reflects the halt."""

    def __init__(self, killswitch: KillSwitch) -> None:
        self._ks = killswitch

    def apply(self, decision: SafetyDecision) -> SafetyDecision:
        if self._ks.is_active():
            return SafetyDecision(
                decision="deny", tier=Tier.BLOCK,
                reason="kill switch active", matched_rule="policy:killswitch",
            )
        return decision


class PolicyEngine:
    def __init__(self, policies: tuple[Policy, ...]) -> None:
        self._policies = policies

    def evaluate(self, decision: SafetyDecision) -> SafetyDecision:
        current = decision
        for policy in self._policies:
            current = policy.apply(current)
        return current
