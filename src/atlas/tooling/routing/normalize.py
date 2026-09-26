"""L0 request normalization + deterministic fast-path rules (Part 3 §12/§14/§46).

Every ingress becomes a TaskIR with its source metadata preserved. The
fast-path rule table routes the obvious cases WITHOUT any model call (§46):
the expensive judgment cascade engages only when rules are inconclusive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from atlas.infra.types import CostPolicy, NetworkPolicy, PrivacyClass
from atlas.tooling.routing.config import RoutingCfg
from atlas.tooling.routing.models import Complexity, TaskIR, Urgency

# Deterministic fast paths (§46): ordered, first match wins, each rule names
# the domain + the rule id (which becomes part of the route explanation, §68).
# KEPT SMALL AND EXACT — a rule that is wrong 10% of the time is worse than
# falling through to judgment.
_FAST_PATH_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("research_papers", "research", re.compile(r"\b(research|papers?|literature|arxiv|survey)\b", re.I)),
    ("compare_sources", "research", re.compile(r"\bcompare\b.*\b(approaches|sources|methods|papers)\b", re.I)),
    ("ide_tests", "agentic_ide", re.compile(r"\b(run|fix).{0,20}\btests?\b", re.I)),
    ("ide_repo", "agentic_ide", re.compile(r"\b(repository|repo|refactor|bug|implement|patch)\b", re.I)),
    ("read_files", "general", re.compile(r"\b(read|open|list|show)\s+(the\s+)?(file|readme|directory|dir)\b", re.I)),
    ("trivial_math", "general", re.compile(r"^what\s+is\s+[\d\s+\-*/().]+$", re.I)),
)

_COMPLEXITY_SIGNALS = re.compile(
    r"\b(investigate|identify|implement|fix|compare|analyze|design|migrate|"
    r"then|prepare)\b",
    re.I,
)


@dataclass(frozen=True)
class DomainHint:
    domain: str
    rule_id: str
    score: float  # deterministic certainty of THIS rule (1.0 = exact match)


class TaskNormalizer:
    """L0: any ingress (API/CLI/scheduler/voice/system, §12) → TaskIR."""

    def __init__(self, routing_cfg: RoutingCfg | None = None) -> None:
        self._routing = routing_cfg or RoutingCfg()

    def normalize(
        self,
        *,
        objective: str,
        task_id: str,
        correlation_id: str,
        source: str = "api",
        preferences: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
    ) -> TaskIR:
        return TaskIR(
            task_id=task_id,
            correlation_id=correlation_id,
            objective=objective,
            source=source,
            preferences=preferences,
            constraints=constraints,
            complexity=self._assess_complexity(objective),
            urgency=Urgency.NORMAL,
            risk=0,
            # Policy comes from CONFIG, never from request text (§78/§79:
            # untrusted input cannot widen its own boundaries).
            privacy_class=PrivacyClass(self._routing.privacy_class),
            network_policy=NetworkPolicy(self._routing.network_policy),
            cost_policy=CostPolicy(self._routing.cost_policy),
        )

    @staticmethod
    def fast_path(objective: str) -> DomainHint | None:
        """L1 tier-0: deterministic domain rules (§14/§46)."""
        for rule_id, domain, pattern in _FAST_PATH_RULES:
            if pattern.search(objective):
                return DomainHint(domain=domain, rule_id=rule_id, score=1.0)
        return None

    @staticmethod
    def _assess_complexity(objective: str) -> Complexity:
        """Cheap lexical complexity prior — a routing signal, not a verdict."""
        signals = len(_COMPLEXITY_SIGNALS.findall(objective))
        words = len(objective.split())
        if signals >= 3 or words > 40:
            return Complexity.COMPLEX
        if signals >= 1 or words > 12:
            return Complexity.MODERATE
        return Complexity.SIMPLE


__all__ = ["DomainHint", "TaskNormalizer"]
