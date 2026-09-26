"""Routing configuration (Part 3 §29/§33/§80).

WHY defined in ``infra/config.py``: it is a plain settings section on
``AppConfig`` (like SafetyCfg/IDECfg) and the layer contract forbids infra
importing tooling — so the frozen config models live in infra and this module
re-exports them for routing consumers. Weights, thresholds, candidate bounds,
and latency budgets are CONFIG, not code constants.
"""

from __future__ import annotations

from atlas.infra.config import JudgmentThresholdsCfg, RoutingCfg, ScoringWeightsCfg

__all__ = ["JudgmentThresholdsCfg", "RoutingCfg", "ScoringWeightsCfg"]
