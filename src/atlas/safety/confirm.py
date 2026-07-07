"""Confirmation contract. WHY a protocol here (not the ntfy impl): safety must
not import interfaces. The engine depends on this protocol; the composition root
injects a concrete confirmer (CLI in dev, ntfy push in prod)."""

from __future__ import annotations

from typing import Protocol

from atlas.infra.types import SafetyDecision, ToolRequest


class Confirmer(Protocol):
    async def confirm(self, prompt: str, decision: SafetyDecision, req: ToolRequest) -> bool: ...
