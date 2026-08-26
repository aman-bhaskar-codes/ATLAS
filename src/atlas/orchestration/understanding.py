"""Task understanding — request text becomes a typed TaskIntent (Phase 2).

WHY this stage exists: before this, three separate components each re-guessed
what the user wanted. ``Router`` made its own model call to produce a 7-flag
``Capabilities`` bag whose output was ~70% unread; the planner re-derived risk
from the raw request; nothing ever produced success criteria, which is why
verification silently degraded to an unconditional pass.

The intent is built ONCE per task, here, and every downstream stage reads it.

WHY ``Capabilities`` is now derived rather than classified: it is a strictly
weaker projection of ``TaskIntent``. Deriving it deterministically removes an
entire model round trip from the critical path and guarantees the two cannot
disagree.
"""

from __future__ import annotations

import json

from atlas.infra.cognition import (
    Complexity,
    ReasoningLevel,
    RiskLevel,
    TaskDomain,
    TaskIntent,
    Urgency,
)
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest, PrivacyClass
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.plan_parsing import extract_json_object
from atlas.orchestration.types import Capabilities

_log = get_logger("atlas.orch.understanding")

_UNDERSTAND_SYSTEM = (
    "You extract structured intent from a user request for an autonomous agent runtime. "
    "Output ONLY a JSON object, no prose, with exactly these keys:\n"
    '{"objective":str,'
    '"domain":"coding|filesystem|research|communication|scheduling|system|self_knowledge|conversation|unknown",'
    '"constraints":[str],'
    '"success_criteria":[str],'
    '"risk":"low|medium|high",'
    '"privacy_level":"public|internal|private|sensitive|secret",'
    '"urgency":"low|normal|high",'
    '"complexity":"trivial|simple|moderate|complex",'
    '"required_capabilities":[str],'
    '"likely_side_effects":[str],'
    '"verification_requirements":[str],'
    '"confidence":float}\n'
    "Rules: objective restates the goal in one imperative sentence. "
    "success_criteria are observable, checkable conditions — never restatements of the request. "
    "risk is high if the action is irreversible, spends money, or touches credentials. "
    "likely_side_effects lists anything that changes state outside ATLAS. "
    "Leave a list empty rather than inventing entries."
)

# Deterministic pre-signals. WHY: these run in microseconds and give the
# extraction a floor it cannot fall below, so a degraded model response cannot
# mislabel an obviously stateful request as pure conversation.
_SELF_MARKERS = (
    "your code",
    "your source",
    "your architecture",
    "atlas itself",
    "inspect atlas",
    "how do you",
    "how does atlas",
    "what can you do",
    "why can't you",
    "why cannot you",
    "your capabilities",
    "your limitations",
)
_TOOL_MARKERS = (
    "file",
    "open",
    "run",
    "delete",
    "send",
    "install",
    "search",
    "write",
    "read",
    "create",
    "commit",
    "test",
)
_MUTATION_MARKERS = ("delete", "remove", "send", "install", "commit", "push", "overwrite", "rm ")


class IntentExtractor:
    """Produces the one authoritative ``TaskIntent`` for a task.

    Uses the FAST inference tier: this is structured extraction, which Phase 4
    explicitly assigns to the fast model.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def understand(self, request: str, correlation_id: CorrelationId) -> TaskIntent:
        low = request.lower()
        is_self = any(m in low for m in _SELF_MARKERS)
        tool_hint = any(m in low for m in _TOOL_MARKERS)
        mutation_hint = any(m in low for m in _MUTATION_MARKERS)

        raw_text: str | None = None
        try:
            resp = await self._gw.complete(
                ModelRequest(
                    correlation_id=correlation_id,
                    system=_UNDERSTAND_SYSTEM,
                    prompt=request,
                    required_capabilities=frozenset({ModelCapability.CLASSIFICATION, ModelCapability.JSON_GENERATION}),
                    # WHY 4096: qwen3:4b is a thinking model that writes verbose
                    # chain-of-thought THEN the JSON. With 1024 tokens it would
                    # run out mid-thought and return no JSON at all.
                    max_tokens=4096,
                    temperature=0.0,
                    needs_deep_reasoning=False,
                )
            )
            raw_text = str(resp.text)
            data = json.loads(extract_json_object(raw_text))
            if not isinstance(data, dict):
                raise ValueError("intent JSON is not an object")
        except Exception as exc:
            # WHY fail toward MORE work, not less: an unparsed request is an
            # ambiguous one, and Phase 10 assigns ambiguity to a deeper level.
            # Cautious risk also means the SafetyEngine sees a higher stakes
            # tier rather than a lower one.
            _log.warning(
                "understanding.failed",
                event_type="orch",
                correlation_id=correlation_id,
                error=repr(exc),
                raw_text=raw_text,
            )
            return TaskIntent(
                objective=request.strip()[:500],
                domain=TaskDomain.SELF_KNOWLEDGE if is_self else TaskDomain.UNKNOWN,
                risk=RiskLevel.MEDIUM,
                privacy_level=PrivacyClass.INTERNAL.value,
                complexity=Complexity.MODERATE,
                required_capabilities=("filesystem",) if tool_hint else (),
                reasoning_level=ReasoningLevel.L2_MULTI_STEP,
                confidence=0.2,
            )

        domain = _enum_or(TaskDomain, data.get("domain"), TaskDomain.UNKNOWN)
        if is_self and domain in (TaskDomain.UNKNOWN, TaskDomain.CONVERSATION):
            # Deterministic floor: a question about ATLAS is a self-knowledge
            # task even when the model labels it small talk.
            domain = TaskDomain.SELF_KNOWLEDGE

        risk = _enum_or(RiskLevel, data.get("risk"), RiskLevel.MEDIUM)
        if mutation_hint and risk is RiskLevel.LOW:
            risk = RiskLevel.MEDIUM

        complexity = _enum_or(Complexity, data.get("complexity"), Complexity.MODERATE)
        side_effects = _str_tuple(data.get("likely_side_effects"))
        criteria = _str_tuple(data.get("success_criteria"))

        intent = TaskIntent(
            objective=str(data.get("objective") or request.strip())[:500],
            domain=domain,
            constraints=_str_tuple(data.get("constraints")),
            success_criteria=criteria,
            risk=risk,
            privacy_level=_privacy(data.get("privacy_level")),
            urgency=_enum_or(Urgency, data.get("urgency"), Urgency.NORMAL),
            complexity=complexity,
            required_capabilities=_str_tuple(data.get("required_capabilities")),
            likely_side_effects=side_effects,
            verification_requirements=_str_tuple(data.get("verification_requirements")),
            reasoning_level=select_reasoning_level(domain, complexity, risk),
            confidence=_confidence(data.get("confidence")),
        )
        _log.info(
            "understanding.created",
            event_type="orch",
            correlation_id=correlation_id,
            domain=intent.domain.value,
            risk=intent.risk.value,
            complexity=intent.complexity.value,
            reasoning_level=int(intent.reasoning_level),
            criteria_count=len(intent.success_criteria),
            capability_count=len(intent.required_capabilities),
        )
        return intent


def select_reasoning_level(domain: TaskDomain, complexity: Complexity, risk: RiskLevel) -> ReasoningLevel:
    """Choose bounded reasoning effort (Phase 10).

    Deterministic on purpose: the level decides step and token budgets, so it
    must be reproducible and explainable rather than a second model opinion.
    """
    if domain is TaskDomain.RESEARCH:
        return ReasoningLevel.L4_RESEARCH
    if risk is RiskLevel.HIGH or complexity is Complexity.COMPLEX:
        return ReasoningLevel.L3_DEEP
    if complexity is Complexity.MODERATE:
        return ReasoningLevel.L2_MULTI_STEP
    if domain is TaskDomain.CONVERSATION and complexity is Complexity.TRIVIAL:
        return ReasoningLevel.L0_DIRECT
    return ReasoningLevel.L1_SIMPLE


def capabilities_from_intent(intent: TaskIntent) -> Capabilities:
    """Project a ``TaskIntent`` onto the legacy ``Capabilities`` bag.

    WHY a projection instead of a second classification: ``Capabilities`` is
    strictly less informative than ``TaskIntent``. Deriving it keeps the
    existing planner call sites working unchanged while removing the router's
    duplicate model call, and makes it impossible for the two to disagree.
    """
    return Capabilities(
        needs_memory=True,
        needs_retrieval=intent.domain is not TaskDomain.CONVERSATION,
        needs_tools=intent.needs_tools(),
        needs_reasoning=intent.reasoning_level >= ReasoningLevel.L2_MULTI_STEP,
        needs_confirmation=intent.risk is not RiskLevel.LOW or bool(intent.likely_side_effects),
        needs_cloud=False,  # decided by the model router under the active policy
        max_risk=intent.risk,
    )


def _str_tuple(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(x).strip() for x in raw if str(x).strip())


def _confidence(raw: object) -> float:
    try:
        return max(0.0, min(1.0, float(str(raw))))
    except (TypeError, ValueError):
        return 0.5


def _privacy(raw: object) -> str:
    try:
        return PrivacyClass(str(raw)).value
    except ValueError:
        return PrivacyClass.INTERNAL.value


def _enum_or[T: (TaskDomain, RiskLevel, Urgency, Complexity)](enum_cls: type[T], raw: object, default: T) -> T:
    try:
        return enum_cls(str(raw))
    except ValueError:
        return default
