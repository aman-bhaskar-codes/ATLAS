"""Evaluators — score an answer against a golden task's criteria.

DeterministicEvaluator is pure and CI-safe. LLMJudge asks a capability-routed
model for structured scoring and degrades to a conservative fail-open=False
result (passed with 0.5 score is NOT allowed here: a broken judge must surface
as 'unknown', never silently pass). Judge failures are explicit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from atlas.evaluation.golden import GoldenTask
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.eval")

_CITATION = re.compile(r"\[(\d{1,3})\]")


def check_citation_grounding(answer: str) -> tuple[bool, dict[str, bool], str | None]:
    """Pure structural citation check for research answers (R8).

    A research answer is grounded when every inline citation marker ``[n]`` used
    in prose resolves to a source *defined* on its own line (``[n] Title — url``,
    the footnote convention the synthesizer and the knowledge tool emit). This
    catches the failure that matters — an answer that cites ``[3]`` when only two
    sources exist — without judging whether the citation is *apt*, which only
    grounding verification against the evidence can decide.

    Returns ``(grounded, criteria, reason)``. An answer with no citations at all
    is vacuously grounded here; require their presence with ``regex_all`` or
    ``contains_*`` on the same task when the domain demands citations.
    """
    defined: set[int] = set()
    used: set[int] = set()
    for line in answer.splitlines():
        markers = _CITATION.findall(line)
        if not markers:
            continue
        head = line.lstrip()
        if head.startswith(f"[{markers[0]}]"):
            # Leading marker defines the source; any others on the line are uses.
            defined.add(int(markers[0]))
            used.update(int(m) for m in markers[1:])
        else:
            used.update(int(m) for m in markers)
    dangling = sorted(used - defined)
    grounded = not dangling
    criteria = {
        "citations_present": bool(used or defined),
        "no_dangling_citations": grounded,
    }
    reason = None if grounded else f"citations point at undefined sources: {dangling}"
    return grounded, criteria, reason


@dataclass(frozen=True)
class EvalResult:
    passed: bool
    score: float  # 0.0 - 1.0
    evaluator: str  # "deterministic" | "llm_judge"
    criteria: dict[str, bool] = field(default_factory=dict)
    failure_reason: str | None = None
    judge_rationale: str | None = None

    @property
    def ok(self) -> bool:
        return self.passed


class Evaluator(Protocol):
    name: str

    async def evaluate(self, task: GoldenTask, answer: str) -> EvalResult: ...


class DeterministicEvaluator:
    """Pure string-criteria matching. No I/O, no model — CI-safe."""

    name = "deterministic"

    async def evaluate(self, task: GoldenTask, answer: str) -> EvalResult:
        spec = task.expected
        lowered = answer.lower()
        criteria: dict[str, bool] = {}
        reasons: list[str] = []

        if spec.contains_all:
            missing = [s for s in spec.contains_all if s.lower() not in lowered]
            criteria["contains_all"] = not missing
            if missing:
                reasons.append(f"missing required content: {missing}")

        if spec.contains_any:
            hit = any(s.lower() in lowered for s in spec.contains_any)
            criteria["contains_any"] = hit
            if not hit:
                reasons.append(f"none of {list(spec.contains_any)} found")

        if spec.contains_none:
            leaked = [s for s in spec.contains_none if s.lower() in lowered]
            criteria["contains_none"] = not leaked
            if leaked:
                reasons.append(f"forbidden content present: {leaked}")

        for pattern in spec.regex_all:
            ok = re.search(pattern, answer, re.MULTILINE) is not None
            criteria[f"regex:{pattern[:40]}"] = ok
            if not ok:
                reasons.append(f"regex did not match: {pattern[:60]}")

        if spec.min_length and len(answer) < spec.min_length:
            criteria["min_length"] = False
            reasons.append(f"answer too short ({len(answer)} < {spec.min_length})")
        elif spec.min_length:
            criteria["min_length"] = True

        if spec.max_length and len(answer) > spec.max_length:
            criteria["max_length"] = False
            reasons.append(f"answer too long ({len(answer)} > {spec.max_length})")
        elif spec.max_length:
            criteria["max_length"] = True

        if spec.citations_grounded:
            grounded, cite_criteria, cite_reason = check_citation_grounding(answer)
            criteria.update(cite_criteria)
            if not grounded and cite_reason:
                reasons.append(cite_reason)

        passed = all(criteria.values()) if criteria else True
        score = (sum(1 for v in criteria.values() if v) / len(criteria)) if criteria else (1.0 if passed else 0.0)
        return EvalResult(
            passed=passed,
            score=1.0 if passed else score,
            evaluator=self.name,
            criteria=criteria,
            failure_reason="; ".join(reasons) if reasons else None,
        )


class LLMJudge:
    """Structured model scoring for criteria that cannot be string-matched.

    Failure policy: an unreachable or unparseable judge returns
    passed=False with failure_reason='judge_error'. The caller decides whether
    to retry or escalate — a judge failure must never silently pass a task.
    """

    name = "llm_judge"

    _SYSTEM = (
        "You are a strict evaluation judge. Given a TASK, CRITERIA, and ANSWER, "
        "score the answer. Output ONLY JSON: "
        '{"passed": bool, "score": 0.0-1.0, "criteria": {"<criterion>": bool}, '
        '"reason": str}. Score 1.0 only when every criterion is fully satisfied.'
    )

    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def evaluate(self, task: GoldenTask, answer: str) -> EvalResult:
        criteria_text = "\n".join(
            [
                f"- category: {task.category}",
                f"- must include: {list(task.expected.contains_all) or '(see prompt)'}",
                f"- must not include: {list(task.expected.contains_none) or 'nothing specific'}",
                "- answer the TASK prompt correctly, completely, and safely",
            ]
        )
        prompt = f"TASK:\n{task.prompt}\n\nCRITERIA:\n{criteria_text}\n\nANSWER:\n{answer[:4000]}"
        try:
            resp = await self._gw.complete(
                ModelRequest(
                    correlation_id=CorrelationId(f"eval:{task.id}"),
                    system=self._SYSTEM,
                    prompt=prompt,
                    required_capabilities=frozenset(
                        {
                            ModelCapability.REASONING,
                            ModelCapability.JSON_GENERATION,
                        }
                    ),
                    max_tokens=512,
                )
            )
            raw = resp.text
            s, e = raw.find("{"), raw.rfind("}")
            if s == -1 or e == -1:
                raise ValueError("no JSON in judge response")
            data = json.loads(raw[s : e + 1])
            raw_criteria = data.get("criteria", {})
            criteria = {str(k): bool(v) for k, v in raw_criteria.items()} if isinstance(raw_criteria, dict) else {}
            return EvalResult(
                passed=bool(data.get("passed", False)),
                score=max(0.0, min(1.0, float(data.get("score", 0.0)))),
                evaluator=self.name,
                criteria=criteria,
                failure_reason=None if data.get("passed") else str(data.get("reason", "unspecified")),
                judge_rationale=str(data.get("reason", "")) or None,
            )
        except Exception as exc:
            _log.warning("eval.judge_error", event_type="evaluation", golden_id=task.id, error=repr(exc))
            return EvalResult(
                passed=False,
                score=0.0,
                evaluator=self.name,
                failure_reason=f"judge_error: {exc!r}",
            )
