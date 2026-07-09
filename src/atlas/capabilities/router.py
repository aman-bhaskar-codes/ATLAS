"""Capability router — request -> required EXTERNAL capabilities.

WHY a third router (distinct from P4 task-router and P5 model-router): those
decide task shape and model. THIS one decides which external capabilities a step
needs (knowledge? email? browser?) so the platform can select providers. It is
deliberately conservative: cheap keyword signals first, optional local LLM
classification for ambiguity, and it NEVER names a provider.
"""

from __future__ import annotations

import json

from atlas.capabilities.registry.capability import Capability
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.cap.router")

_SIGNALS: dict[Capability, tuple[str, ...]] = {
    Capability.KNOWLEDGE: ("search", "look up", "latest", "news", "what is", "find out"),
    Capability.EMAIL: ("email", "inbox", "reply", "compose", "send a mail"),
    Capability.CALENDAR: ("calendar", "schedule", "meeting", "event", "free time"),
    Capability.BROWSER: ("browse", "website", "click", "fill", "web page"),
    Capability.NOTIFICATION: ("notify", "remind", "ping me", "alert"),
    Capability.GITHUB: ("github", "repo", "pull request", "commit", "issue"),
    Capability.WEATHER: ("weather", "forecast", "temperature", "rain"),
    Capability.FILES: ("file", "download", "upload", "document"),
}

_CLASSIFY_SYSTEM = (
    "Which EXTERNAL capabilities does this request need? Output ONLY a JSON array "
    "from: knowledge, browser, email, calendar, contacts, notification, "
    "cloud_storage, github, weather, location, files. Empty array if none."
)


class CapabilityRouter:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def route(self, request: str, correlation_id: CorrelationId) -> frozenset[Capability]:
        low = request.lower()
        hits = {cap for cap, sigs in _SIGNALS.items() if any(s in low for s in sigs)}
        if hits:
            return frozenset(hits)
        # ambiguous -> one cheap local classification
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=correlation_id, system=_CLASSIFY_SYSTEM,
                prompt=request, max_tokens=80, temperature=0.0))
            names = json.loads(self._json_array(resp.text))
            return frozenset(Capability(n) for n in names if n in Capability._value2member_map_)
        except Exception as exc:
            _log.warning("cap_router.classify_failed", event_type="cap",
                         correlation_id=correlation_id, error=repr(exc))
            return frozenset()

    @staticmethod
    def _json_array(text: str) -> str:
        s, e = text.find("["), text.rfind("]")
        if s == -1 or e == -1:
            raise ValueError("no JSON array")
        return text[s : e + 1]
