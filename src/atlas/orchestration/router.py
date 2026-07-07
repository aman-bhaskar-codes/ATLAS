"""Capability router.

WHY capabilities, not model choice: separation of concerns. The router answers
'does this need tools/memory/confirmation/cloud/reasoning?'. The Model Gateway
(Phase 1) independently decides WHICH model serves a given call. This keeps the
router model-agnostic and lets model routing evolve (Phase 5) without touching
orchestration. A cheap local classification fills gaps deterministic rules miss.
"""

from __future__ import annotations

import json

from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.types import Capabilities, RiskLevel

_log = get_logger("atlas.orch.router")

_CLASSIFY_SYSTEM = (
    "Classify a user request for an agent runtime. Output ONLY JSON: "
    '{"needs_tools":bool,"needs_reasoning":bool,"needs_cloud":bool,'
    '"needs_confirmation":bool,"max_risk":"low|medium|high"}'
)


class Router:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def route(self, request: str, correlation_id: CorrelationId) -> Capabilities:
        # 1) cheap deterministic signals
        low = request.lower()
        tool_hint = any(
            k in low for k in ("file", "open", "run", "delete", "send", "install", "search")
        )
        # 2) local classification (thinking off; cheap)
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=correlation_id, system=_CLASSIFY_SYSTEM,
                prompt=request, max_tokens=120, temperature=0.0,
            ))
            data = json.loads(self._json(resp.text))
        except Exception as exc:  # fail toward MORE caution, not less
            _log.warning("router.classify_failed", event_type="orch",
                         correlation_id=correlation_id, error=repr(exc))
            return Capabilities(needs_tools=tool_hint, needs_confirmation=True,
                                max_risk=RiskLevel.MEDIUM)
        return Capabilities(
            needs_tools=bool(data.get("needs_tools", tool_hint)),
            needs_reasoning=bool(data.get("needs_reasoning", True)),
            needs_cloud=bool(data.get("needs_cloud", False)),
            needs_confirmation=bool(data.get("needs_confirmation", False)),
            needs_memory=True, needs_retrieval=True,
            max_risk=self._risk(data.get("max_risk", "low")),
        )

    @staticmethod
    def _risk(raw: object) -> RiskLevel:
        try:
            return RiskLevel(str(raw))
        except ValueError:
            return RiskLevel.MEDIUM  # unknown -> cautious

    @staticmethod
    def _json(text: str) -> str:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("no JSON")
        return text[s : e + 1]
