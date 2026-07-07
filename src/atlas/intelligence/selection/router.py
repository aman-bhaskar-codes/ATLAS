"""Capability router — request -> required capabilities.

WHY here and distinct from the Phase-4 orchestration Router: that one decides
task-level needs (tools/confirmation). THIS one maps an inference request to the
capability set models are matched against. Kept separate so inference routing can
evolve independently of task routing.
"""

from __future__ import annotations

from atlas.intelligence.capabilities import Capability, CapabilitySet
from atlas.intelligence.contracts import InferenceRequest


class CapabilityRouter:
    def required(self, req: InferenceRequest) -> CapabilitySet:
        caps = set(req.required_capabilities)
        if req.stream:
            caps.add(Capability.STREAMING)
        if req.constraints.min_context and req.constraints.min_context > 32000:
            caps.add(Capability.LONG_CONTEXT)
        if not caps:
            caps.add(Capability.REASONING)  # sane default
        return frozenset(caps)
