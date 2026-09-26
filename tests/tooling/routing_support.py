"""Shared routing-test support: judgment rules for the ablation harness."""

from __future__ import annotations

import re
from typing import Any

from atlas.tooling.routing.judgment import JudgmentState

# Deterministic rules mirroring the production fast-path vocabulary, used by
# the ablation harness so "rule_only" is the honest baseline (§87).
_DOMAIN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("research", re.compile(r"\b(research|papers?|survey|literature)\b", re.I)),
    ("agentic_ide", re.compile(r"\b(repository|repo|tests?|patch|refactor|bug)\b", re.I)),
    ("general", re.compile(r"\b(file|readme|directory|list|look ?up|fact)\b", re.I)),
)


def make_route_rules() -> dict[str, Any]:
    def domain_rule(state: JudgmentState) -> tuple[str, float] | None:
        objective = str(state.data.get("objective", ""))
        for domain, pattern in _DOMAIN_RULES:
            if pattern.search(objective):
                return domain, 1.0
        return None

    return {"domain": domain_rule}
