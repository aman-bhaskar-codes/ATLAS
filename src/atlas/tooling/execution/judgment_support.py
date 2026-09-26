"""Judgment-cascade construction for the execution layer (Part 4 §32-§34/§64).

Kept out of the engine: the engine consumes a cascade via the recovery
controller; the composition root decides which rungs are enabled (deterministic
always; Jev/LLM only when config enables them — no decorative Jev, §88).
"""

from __future__ import annotations

from typing import Any

from atlas.infra.config import AppConfig
from atlas.tooling.routing.judgment import (
    DeterministicJudgmentProvider,
    JevClient,
    JevJudgmentProvider,
    JudgmentCascade,
    JudgmentThresholdPolicy,
    LLMJudgmentProvider,
)
from atlas.tooling.routing.models import DecisionRisk


def build_execution_cascade(config: AppConfig, gateway: Any = None) -> Any:
    """Build the recovery-judgment cascade from routing config. Deterministic
    rules first; the Jev/LLM rungs activate only where rules are inconclusive."""
    routing_cfg = config.routing
    jev_provider = JevJudgmentProvider(JevClient()) if routing_cfg.enable_jev else None
    llm_provider = LLMJudgmentProvider(gateway) if routing_cfg.enable_llm_judgment else None
    return JudgmentCascade(
        deterministic=DeterministicJudgmentProvider(rules={}),
        jev=jev_provider,
        llm=llm_provider,
        thresholds=JudgmentThresholdPolicy(
            {
                DecisionRisk.LOW_RISK_ROUTING: routing_cfg.thresholds.low_risk_routing,
                DecisionRisk.MEDIUM_RISK: routing_cfg.thresholds.medium_risk,
                DecisionRisk.HIGH_RISK: routing_cfg.thresholds.high_risk,
                DecisionRisk.DESTRUCTIVE: routing_cfg.thresholds.destructive,
            }
        ),
    )


__all__ = ["build_execution_cascade"]
