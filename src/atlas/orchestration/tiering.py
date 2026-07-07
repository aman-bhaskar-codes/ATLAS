"""Provisional tier estimation for the critique gate.

WHY reuse the real classifier: we must not invent a second definition of
'consequential'. We build the same ToolRequest the dispatcher would and ask the
classifier (pure, no side effects) for its tier. This is an ESTIMATE used only
to decide whether to spend a critique call; the authoritative decision still
happens in SafetyEngine.guard() at dispatch.
"""

from __future__ import annotations

from atlas.infra.ids import CorrelationId
from atlas.infra.types import Tier, ToolRequest
from atlas.orchestration.types import Action
from atlas.safety.classifier import TierClassifier


class TierEstimator:
    def __init__(self, classifier: TierClassifier) -> None:
        self._clf = classifier

    def estimate(self, action: Action, correlation_id: CorrelationId) -> Tier:
        if action.kind != "tool_call" or action.tool is None or action.operation is None:
            return Tier.AUTO
        req = ToolRequest(
            correlation_id=correlation_id, tool=action.tool,
            operation=action.operation, args=action.args,
        )
        return self._clf.classify(req).tier

    def is_consequential(self, action: Action, correlation_id: CorrelationId) -> bool:
        return self.estimate(action, correlation_id) >= Tier.CONFIRM
