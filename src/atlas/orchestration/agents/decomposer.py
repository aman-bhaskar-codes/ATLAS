"""Task decomposition — the supervisor's planning half.

WHY a separate model call from the Planner: the Planner answers "what steps
achieve this goal", which is the right question for one agent working serially.
Decomposition answers a different question — "which parts of this goal are
independent enough to delegate" — and its output shape is a graph, not a list.
Conflating them produced plans whose `depends_on` was never populated.

WHY it may decline: delegation costs a model call per subtask plus a synthesis
call. For a simple request that is pure overhead, so the decomposer is allowed
to return `should_delegate=False` and let the serial OTAR loop handle it.

Failure policy: this module NEVER raises. Every failure path returns
`should_delegate=False`, which degrades to the existing single-agent pipeline.
"""

from __future__ import annotations

import json

from atlas.infra.cognition import Complexity, RiskLevel, TaskDomain, TaskIntent
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest, Tier
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.agents.types import (
    AgentRole,
    DecompositionOutcome,
    SubTask,
    TaskDAG,
)
from atlas.orchestration.plan_parsing import extract_json_object

_log = get_logger("atlas.orch.agents.decompose")

_DECOMPOSE_SYSTEM = (
    "You are a supervisor that splits a complex request into independent subtasks "
    "for specialist agents. Roles: researcher (gather external evidence), "
    "coder (read/edit code, run checks), analyst (compare/compute over data), "
    "writer (compose prose from supplied material), general (anything else).\n"
    "Rules: 2-6 subtasks. Maximise independence — only set depends_on when a "
    "subtask genuinely needs another's OUTPUT. Never split a request that one "
    "agent could do in a few steps; return delegate=false instead.\n"
    'Reply with ONLY JSON: {"delegate":bool,"reason":str,"subtasks":'
    '[{"id":"st1","role":str,"objective":str,"success_criteria":[str],'
    '"depends_on":[str],"suggested_tools":[str],"max_steps":int}]}'
)

_DELEGATABLE_COMPLEXITY = frozenset({Complexity.MODERATE, Complexity.COMPLEX})

# Appended to the system prompt ONLY for research-domain requests. WHY: a research
# question decomposes along a different seam than a coding or ops one — into
# independent *sub-questions / facets* that each need their own evidence gathering,
# not into a pipeline of build-steps. Naming that seam here is what turns the
# generic splitter into a research splitter without a second decomposer.
_RESEARCH_GUIDANCE = (
    "\nThis is a RESEARCH request. Prefer splitting it into INDEPENDENT sub-questions "
    "or facets that can be investigated in parallel, each as a 'researcher' subtask. "
    "Give every researcher subtask the 'knowledge' tool and a success criterion that "
    "requires citing the source of each claim. Reserve 'writer'/'analyst' for a final "
    "subtask that depends on the researchers and synthesises their findings."
)

# The evidence tool (ResearchTool.name). A researcher branch that never reaches for
# it is just an LLM guessing, so we default the hint on when the model omits it.
_KNOWLEDGE_TOOL = "knowledge"
_CITE_CRITERION = "cite a source for each claim"


class TaskDecomposer:
    """Turns a request + intent into a TaskDAG, or declines."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        max_subtasks: int = 6,
        min_subtasks: int = 2,
        max_steps_per_subtask: int = 8,
    ) -> None:
        self._gw = gateway
        self._max_subtasks = max_subtasks
        self._min_subtasks = min_subtasks
        self._max_steps = max_steps_per_subtask

    def is_candidate(self, intent: TaskIntent) -> bool:
        """Cheap deterministic gate — avoids a model call on trivial requests."""
        return intent.complexity in _DELEGATABLE_COMPLEXITY

    async def decompose(
        self,
        request: str,
        context: str,
        intent: TaskIntent,
        correlation_id: CorrelationId,
    ) -> DecompositionOutcome:
        if not self.is_candidate(intent):
            return DecompositionOutcome(
                should_delegate=False,
                reason=f"complexity={intent.complexity.value} below delegation threshold",
            )
        try:
            raw = await self._ask(request, context, intent, correlation_id)
        except Exception as exc:  # model/network/timeout — degrade, never crash
            _log.warning(
                "decompose.model_failed",
                event_type="orchestration",
                correlation_id=str(correlation_id),
                error=repr(exc),
            )
            return DecompositionOutcome(should_delegate=False, reason=f"decomposition call failed: {exc!r}")

        try:
            return self._parse(raw, request, intent)
        except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            _log.warning(
                "decompose.parse_failed",
                event_type="orchestration",
                correlation_id=str(correlation_id),
                error=repr(exc),
                raw_len=len(raw),
            )
            return DecompositionOutcome(should_delegate=False, reason=f"unparseable decomposition: {exc!r}")

    async def _ask(
        self,
        request: str,
        context: str,
        intent: TaskIntent,
        correlation_id: CorrelationId,
    ) -> str:
        criteria = "\n".join(f"- {c}" for c in intent.success_criteria) or "- (none stated)"
        system = _DECOMPOSE_SYSTEM
        if intent.domain is TaskDomain.RESEARCH:
            system += _RESEARCH_GUIDANCE
        resp = await self._gw.complete(
            ModelRequest(
                correlation_id=correlation_id,
                system=system,
                prompt=(
                    f"CONTEXT (truncated):\n{context[:4000]}\n\n"
                    f"REQUEST:\n{request}\n\n"
                    f"DOMAIN: {intent.domain.value}\n"
                    f"COMPLEXITY: {intent.complexity.value}\n"
                    f"SUCCESS CRITERIA:\n{criteria}\n\n"
                    f"Split into at most {self._max_subtasks} subtasks, "
                    f"at most {self._max_steps} steps each."
                ),
                required_capabilities=frozenset({ModelCapability.PLANNING, ModelCapability.JSON_GENERATION}),
                needs_deep_reasoning=True,
                stakes_tier=Tier.AUTO,  # decomposition itself has no side effects
                # Same reason as the Planner: local models emit long
                # chain-of-thought before the JSON object.
                max_tokens=6144,
            )
        )
        return str(resp.text)

    def _parse(self, raw: str, request: str, intent: TaskIntent) -> DecompositionOutcome:
        data = json.loads(extract_json_object(raw))
        if not isinstance(data, dict):
            raise TypeError(f"expected a JSON object, got {type(data).__name__}")
        reason = str(data.get("reason") or "")
        if not bool(data.get("delegate")):
            return DecompositionOutcome(should_delegate=False, reason=reason or "supervisor declined")

        items = data.get("subtasks")
        if not isinstance(items, list):
            raise TypeError("subtasks must be a list")

        subtasks = self._coerce_subtasks(items, intent)
        if len(subtasks) < self._min_subtasks:
            return DecompositionOutcome(
                should_delegate=False,
                reason=f"only {len(subtasks)} usable subtask(s); serial execution is cheaper",
            )

        dag = TaskDAG.build(goal=intent.objective or request, subtasks=subtasks)
        return DecompositionOutcome(should_delegate=True, reason=reason, dag=dag)

    def _coerce_subtasks(self, items: list[object], intent: TaskIntent) -> tuple[SubTask, ...]:
        """Best-effort conversion. Malformed entries are skipped, not fatal.

        IDs are regenerated positionally (st1, st2, ...) and dependencies are
        remapped onto them, so a model that invents duplicate or non-string ids
        cannot produce a graph whose edges silently point nowhere.
        """
        raw_rows: list[tuple[str, dict[str, object]]] = []
        for item in items[: self._max_subtasks]:
            if isinstance(item, dict) and str(item.get("objective") or "").strip():
                raw_rows.append((str(item.get("id") or ""), item))

        # Original id -> canonical id. Later duplicates lose; first wins.
        id_map: dict[str, str] = {}
        for position, (original, _) in enumerate(raw_rows, start=1):
            if original and original not in id_map:
                id_map[original] = f"st{position}"

        out: list[SubTask] = []
        for position, (_original, row) in enumerate(raw_rows, start=1):
            canonical = f"st{position}"
            raw_deps = row.get("depends_on")
            dep_list: list[object] = raw_deps if isinstance(raw_deps, list) else []
            deps = tuple(
                dict.fromkeys(  # de-duplicate, preserve order
                    id_map[str(d)] for d in dep_list if str(d) in id_map
                )
            )
            role = AgentRole.parse(row.get("role"))
            tools = _str_tuple(row.get("suggested_tools"), limit=6)
            criteria = _str_tuple(row.get("success_criteria"), limit=6)
            # Research branches must reach for the evidence tool and cite. These are
            # ADVISORY defaults filled only when the model left them empty — the
            # registry still resolves tools and the SafetyEngine still gates every
            # call, so this can widen a branch's intent but never its authority.
            if role is AgentRole.RESEARCHER:
                if not tools:
                    tools = (_KNOWLEDGE_TOOL,)
                if not criteria:
                    criteria = (_CITE_CRITERION,)
            out.append(
                SubTask(
                    id=canonical,
                    role=role,
                    objective=str(row["objective"]).strip()[:2000],
                    success_criteria=criteria,
                    depends_on=tuple(d for d in deps if d != canonical),
                    suggested_tools=tools,
                    max_steps=_clamp_steps(row.get("max_steps"), self._max_steps),
                    # A subtask can never carry MORE risk than the parent intent
                    # allows; the SafetyEngine is the real gate either way.
                    risk=intent.risk if isinstance(intent.risk, RiskLevel) else RiskLevel.LOW,
                )
            )
        return tuple(out)


def _str_tuple(value: object, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v).strip()[:300] for v in value[:limit] if str(v).strip())


def _clamp_steps(value: object, ceiling: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return min(6, ceiling)
    try:
        return max(1, min(int(value), ceiling))
    except (TypeError, ValueError):
        return min(6, ceiling)
