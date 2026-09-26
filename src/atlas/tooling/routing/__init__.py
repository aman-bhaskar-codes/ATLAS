"""Routing fabric — the ATLAS control plane (Part 3).

``tooling.routing`` decides WHO acts, WHAT acts, HOW, with what fallback, and
when to escalate or stop. It never executes: plans reference candidates whose
execution flows through the existing governed funnels (ToolingExecutor /
dispatchers → SafetyEngine). See docs/routing/architecture.md.
"""

from __future__ import annotations

from atlas.tooling.routing.config import JudgmentThresholdsCfg, RoutingCfg, ScoringWeightsCfg
from atlas.tooling.routing.domains import DomainDefinition, DomainRegistry, builtin_domains
from atlas.tooling.routing.judgment import (
    DeterministicJudgmentProvider,
    JevClient,
    JevJudgmentProvider,
    JudgmentCascade,
    JudgmentQuestion,
    JudgmentState,
    JudgmentThresholdPolicy,
    LLMJudgmentProvider,
)
from atlas.tooling.routing.models import (
    CandidateType,
    Complexity,
    ConfidenceLevel,
    DecisionRisk,
    FailureCategory,
    JudgmentBatch,
    JudgmentResult,
    ProgressState,
    RecoveryAction,
    RecoveryDecision,
    RejectedCandidate,
    RouteCandidate,
    RouteDecision,
    RouteEdge,
    RouteGraph,
    RouteNode,
    RoutePlan,
    RouteStep,
    TaskIR,
)
from atlas.tooling.routing.normalize import DomainHint, TaskNormalizer
from atlas.tooling.routing.planner import RoutePlanner, RoutePlanValidationError
from atlas.tooling.routing.progress import LoopDetector, ProgressLedger, StallDetector
from atlas.tooling.routing.recovery import RecoveryRouter, classify_failure
from atlas.tooling.routing.store import RouteStore
from atlas.tooling.routing.strategies import StrategyDefinition, StrategyRegistry, builtin_strategies

__all__ = [
    "CandidateType",
    "Complexity",
    "ConfidenceLevel",
    "DecisionRisk",
    "DeterministicJudgmentProvider",
    "DomainDefinition",
    "DomainHint",
    "DomainRegistry",
    "FailureCategory",
    "JevClient",
    "JevJudgmentProvider",
    "JudgmentBatch",
    "JudgmentCascade",
    "JudgmentQuestion",
    "JudgmentResult",
    "JudgmentState",
    "JudgmentThresholdPolicy",
    "JudgmentThresholdsCfg",
    "LLMJudgmentProvider",
    "LoopDetector",
    "ProgressLedger",
    "ProgressState",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryRouter",
    "RejectedCandidate",
    "RouteCandidate",
    "RouteDecision",
    "RouteEdge",
    "RouteGraph",
    "RouteNode",
    "RoutePlan",
    "RoutePlanValidationError",
    "RoutePlanner",
    "RouteStep",
    "RouteStore",
    "RoutingCfg",
    "RoutingEngine",
    "ScoringWeightsCfg",
    "StallDetector",
    "StrategyDefinition",
    "StrategyRegistry",
    "TaskIR",
    "TaskNormalizer",
    "builtin_domains",
    "builtin_strategies",
    "classify_failure",
]

from atlas.tooling.routing.router import RoutingEngine

__all__.append("RoutingEngine")
