"""Self-critique hook — online self-correction for consequential actions.

CONTRACT (ADR-011): runs BEFORE the Safety Engine; can only make an action
SAFER. 'ok' does not lower the tier (Tier-2 still confirms). 'revise'
regenerates the action ONCE (bounded — no ping-pong). 'abort' turns the action
into ask_user. Benign actions skip critique entirely (cost discipline). Every
critique is audited so the learning loop (Phase 8) can see where the agent
caught itself.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.tiering import TierEstimator
from atlas.orchestration.types import Action, Critique, CritiqueVerdict
from atlas.orchestration.validator import OutputValidator

_log = get_logger("atlas.orch.critique")

CritiqueAudit = Callable[[str, Action, Critique], Awaitable[None]]

_CRITIQUE_SYSTEM = (
    "You are a careful reviewer of an agent's PROPOSED next action, before it "
    "runs. Given the TASK, USER CONTEXT, and the PROPOSED ACTION, decide if it "
    "overreaches, misreads intent, risks an irreversible mistake, or targets the "
    "wrong thing. Output ONLY JSON: "
    '{"verdict":"ok|revise|abort","reason":str,"suggestion":str|null}. '
    "Prefer 'ok' unless there is a concrete concern. Use 'abort' only for clear, "
    "serious risk (wrong recipient, destructive scope, misread goal)."
)

_REVISE_SYSTEM = (
    "Regenerate the agent's next action, addressing the reviewer's concern. "
    "Output ONLY JSON for ONE step: "
    '{"thought":str,"confidence":0.0-1.0,"action":{"kind":"tool_call|final_answer|'
    'ask_user|noop","tool":str|null,"operation":str|null,"args":object,'
    '"final_text":str|null}}'
)


class SelfCritique:
    """Implements the ReflectionHook protocol from Phase 4."""

    def __init__(
        self, *, gateway: ModelGateway, estimator: TierEstimator,
        parser: ResponseParser, validator: OutputValidator,
        correlation_id_provider: Callable[[], CorrelationId],
        audit: CritiqueAudit | None = None,
    ) -> None:
        self._gw = gateway
        self._estimator = estimator
        self._parser = parser
        self._validator = validator
        self._corr = correlation_id_provider
        self._audit = audit

    async def critique(self, action: Action, context: str) -> Action:
        corr = self._corr()
        # 1) cost discipline: only critique consequential actions
        if not self._estimator.is_consequential(action, corr):
            return action

        # 2) one cheap local critique (thinking off, tight budget)
        verdict = await self._run_critique(action, context, corr)
        if self._audit is not None:
            await self._audit(str(corr), action, verdict)

        if verdict.verdict is CritiqueVerdict.OK:
            return action  # tier UNCHANGED — Safety Engine still gates it
        if verdict.verdict is CritiqueVerdict.ABORT:
            _log.info("critique.abort", event_type="critique",
                      correlation_id=corr, reason=verdict.reason)
            return Action(
                step=action.step, kind="ask_user",
                final_text=f"I held off: {verdict.reason} Want me to proceed anyway?",
            )
        # REVISE: regenerate once, bounded
        revised = await self._revise(action, context, verdict, corr)
        return revised

    async def _run_critique(
        self, action: Action, context: str, corr: CorrelationId
    ) -> Critique:
        prompt = (
            f"USER CONTEXT:\n{context[:2000]}\n\n"
            f"PROPOSED ACTION:\n{action.model_dump_json()}"
        )
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=corr, system=_CRITIQUE_SYSTEM, prompt=prompt,
                max_tokens=250, temperature=0.0,  # thinking off: fast + cheap
            ))
            data = json.loads(self._json(resp.text))
            return Critique(
                verdict=self._verdict(data.get("verdict")),
                reason=str(data.get("reason", "")),
                suggestion=data.get("suggestion"),
            )
        except Exception as exc:
            # fail-safe: if the critic itself fails, do NOT abort or revise blindly.
            # Return OK so the action proceeds to the Safety Engine, which is the
            # authoritative gate. Critique is an ADDITIONAL check, never the only one.
            _log.warning("critique.failed_open_to_safety", event_type="critique",
                         correlation_id=corr, error=repr(exc))
            return Critique(verdict=CritiqueVerdict.OK, reason="critic unavailable")

    async def _revise(
        self, action: Action, context: str, critique: Critique, corr: CorrelationId
    ) -> Action:
        prompt = (
            f"CONTEXT:\n{context[:2000]}\n\n"
            f"ORIGINAL ACTION:\n{action.model_dump_json()}\n\n"
            f"REVIEWER CONCERN: {critique.reason}\n"
            f"SUGGESTION: {critique.suggestion or '(none)'}"
        )
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=corr, system=_REVISE_SYSTEM, prompt=prompt,
                max_tokens=512, temperature=0.1,
            ))
            _thought, revised = self._parser.parse(resp.text, action.step)
            self._validator.validate(revised)
            return revised
        except Exception as exc:
            # revision failed -> safest fallback is to ask the human, not to run
            # the un-revised risky action.
            _log.info("critique.revise_failed_to_ask", event_type="critique",
                      correlation_id=corr, error=repr(exc))
            return Action(
                step=action.step, kind="ask_user",
                final_text=f"I wanted to adjust this action ({critique.reason}) "
                           f"but couldn't. How should I proceed?",
            )

    @staticmethod
    def _verdict(raw: object) -> CritiqueVerdict:
        try:
            return CritiqueVerdict(str(raw))
        except ValueError:
            return CritiqueVerdict.OK  # unknown -> defer to Safety Engine

    @staticmethod
    def _json(text: str) -> str:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("no JSON")
        return text[s : e + 1]
