"""Capability-aware verification — checking work with evidence (Phase 12).

WHY this module exists: a model asked "did you succeed?" will usually say yes.
Phase 12 requires that domains which *can* be checked mechanically are checked
mechanically, and that a model opinion is the fallback, not the default.

  CODING      -> run the project's test/lint command and read the exit status
  FILESYSTEM  -> re-read the affected paths and compare actual state
  RESEARCH    -> require the answer to be grounded in recorded evidence
  SELF_*      -> same grounding rule; a claim about ATLAS needs a cited file
  everything else -> GoalVerifier (model judgement against criteria)

WHY verification goes through the dispatcher rather than calling subprocess:
Phase 31 says every action passes through the SafetyEngine, and verification is
not exempt. A verifier that shelled out directly would be the same
LLM-adjacent-code-executes-freely hole the safety layer exists to close. It
also means verification commands are tier-classified, audited and killable.

WHY every verifier fails CLOSED: an unrunnable check is not a passed check.
"""

from __future__ import annotations

import re
import time

from atlas.infra.cognition import (
    CriterionResult,
    Evidence,
    GoalState,
    TaskDomain,
    VerificationResult,
    Verifier,
)
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.types import Action

_log = get_logger("atlas.orch.verification")

# Extracts filesystem-ish paths from criteria/answers so the filesystem
# verifier knows what to re-read. Deliberately conservative: a false negative
# degrades to the model verifier, a false positive would waste a tool call.
_PATH_RE = re.compile(r"(?:^|[\s'\"`(])((?:/|\./|src/|tests/|docs/)[\w./\-]{2,200})")
_FAIL_MARKERS = ("failed", "error", "traceback", "exception", "no such file")


def _evidence_lines(evidence: tuple[Evidence, ...], limit: int = 8) -> tuple[str, ...]:
    return tuple(f"{e.source}: {e.summary[:120]}" for e in evidence[:limit])


class DomainVerifierRouter:
    """Selects the verifier for a task's domain, with a model-based default.

    WHY a router rather than domain branching inside one verifier: the
    reasoning loop must not know about domains, and each strategy needs its own
    dependencies (a dispatcher for mechanical checks, a gateway for judgement).
    """

    name = "domain_router"

    def __init__(self, *, default: Verifier, by_domain: dict[TaskDomain, Verifier]) -> None:
        self._default = default
        self._by_domain = dict(by_domain)

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        correlation_id: CorrelationId,
        context: str = "",
        domain: TaskDomain = TaskDomain.UNKNOWN,
        evidence: tuple[Evidence, ...] = (),
    ) -> VerificationResult:
        chosen = self._by_domain.get(domain, self._default)
        result = await chosen.verify(goal, answer, correlation_id, context, domain, evidence)
        # A mechanical verifier that could not run defers to judgement rather
        # than reporting a phantom pass.
        if result.verifier == "none" and chosen is not self._default:
            _log.info(
                "verification.deferred_to_default",
                event_type="orchestration",
                correlation_id=correlation_id,
                domain=domain.value,
                reason=result.failure_reason,
            )
            return await self._default.verify(goal, answer, correlation_id, context, domain, evidence)
        return result


class CommandVerifier:
    """Verifies coding work by running a read-only check command.

    The command is configured, never model-supplied. WHY: letting the verifier
    execute a string the model produced would make verification an arbitrary
    code execution path, which is precisely what Phase 31 forbids. The command
    still passes through the SafetyEngine, so an operator who configures
    something dangerous is still stopped by tier classification.
    """

    name = "command"

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        *,
        command: str,
        tool: str = "shell",
        operation: str = "read_only",
        timeout_s: int = 120,
    ) -> None:
        self._dispatcher = dispatcher
        self._command = command.strip()
        self._tool = tool
        self._operation = operation
        self._timeout_s = timeout_s

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        correlation_id: CorrelationId,
        context: str = "",
        domain: TaskDomain = TaskDomain.UNKNOWN,
        evidence: tuple[Evidence, ...] = (),
    ) -> VerificationResult:
        del goal, answer, context, domain
        if not self._command:
            return VerificationResult.not_applicable("no verification command configured")

        started = time.perf_counter()
        try:
            obs = await self._dispatcher.dispatch(
                Action(
                    step=0,
                    kind="tool_call",
                    tool=self._tool,
                    operation=self._operation,
                    args={"command": self._command, "timeout_s": self._timeout_s},
                ),
                correlation_id,
            )
        except Exception as exc:
            return VerificationResult.error(repr(exc), verifier=self.name)

        latency = int((time.perf_counter() - started) * 1000)
        output = str(obs.content or "")
        if not obs.ok:
            # A denial or halt is not a verification failure of the *work* — we
            # could not check it. Defer instead of condemning the answer.
            err = str(obs.error or "")
            if err.startswith(("denied", "halted")):
                _log.warning(
                    "verification.command_blocked",
                    event_type="orchestration",
                    correlation_id=correlation_id,
                    error=err,
                )
                return VerificationResult.not_applicable(f"check could not run: {err}")
            return VerificationResult(
                passed=False,
                score=0.0,
                verifier=self.name,
                criteria_results=(
                    CriterionResult(criterion=self._command, passed=False, detail=err[:500] or "command failed"),
                ),
                failure_reason=f"{self._command} failed: {err[:300]}",
                suggested_next_action="fix the failure reported by the check command",
                evidence=_evidence_lines(evidence),
                latency_ms=latency,
            )

        low = output.lower()
        failed = any(m in low for m in _FAIL_MARKERS)
        return VerificationResult(
            passed=not failed,
            score=0.0 if failed else 1.0,
            verifier=self.name,
            criteria_results=(
                CriterionResult(
                    criterion=self._command,
                    passed=not failed,
                    detail=output[-500:],
                ),
            ),
            failure_reason=f"{self._command} reported failures" if failed else None,
            suggested_next_action="address the reported failures" if failed else None,
            evidence=(*_evidence_lines(evidence), f"{self._tool}: {self._command}"),
            latency_ms=latency,
        )


class FilesystemStateVerifier:
    """Verifies filesystem work by re-reading the affected paths.

    WHY re-read instead of trusting the write observation: the observation says
    the call returned, not that the resulting state matches the goal. Phase 12
    requires comparing actual state.
    """

    name = "filesystem_state"

    def __init__(self, dispatcher: ToolDispatcher, *, tool: str = "filesystem") -> None:
        self._dispatcher = dispatcher
        self._tool = tool

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        correlation_id: CorrelationId,
        context: str = "",
        domain: TaskDomain = TaskDomain.UNKNOWN,
        evidence: tuple[Evidence, ...] = (),
    ) -> VerificationResult:
        del context, domain
        paths = _extract_paths(goal.success_criteria, answer, evidence)
        if not paths:
            return VerificationResult.not_applicable("no concrete paths to check")

        started = time.perf_counter()
        results: list[CriterionResult] = []
        for path in paths[:8]:
            try:
                obs = await self._dispatcher.dispatch(
                    Action(
                        step=0,
                        kind="tool_call",
                        tool=self._tool,
                        operation="read_only",
                        args={"path": path},
                    ),
                    correlation_id,
                )
            except Exception as exc:
                results.append(CriterionResult(criterion=path, passed=False, detail=f"check failed: {exc!r}"))
                continue
            exists = bool(obs.ok)
            results.append(
                CriterionResult(
                    criterion=f"state of {path}",
                    passed=exists,
                    detail="readable" if exists else str(obs.error or "unreadable")[:200],
                    evidence=(path,),
                )
            )

        if not results:
            return VerificationResult.not_applicable("no path check produced a result")

        passed = all(r.passed for r in results)
        return VerificationResult(
            passed=passed,
            score=sum(1.0 for r in results if r.passed) / len(results),
            verifier=self.name,
            criteria_results=tuple(results),
            failure_reason=None if passed else "expected filesystem state not observed",
            suggested_next_action=None if passed else "re-check the paths the goal refers to",
            evidence=_evidence_lines(evidence),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class GroundingVerifier:
    """Requires an answer to be grounded in evidence the run actually produced.

    Used for RESEARCH and SELF_KNOWLEDGE. WHY no model call: this is a
    mechanical property — either the run recorded successful observations and
    the answer cites them, or it is unsupported assertion. Checking it costs
    microseconds, and it catches the single most common failure mode, which is
    a fluent answer produced without ever looking anything up.

    WHY it does not judge correctness: grounding is necessary, not sufficient.
    A grounded answer is handed to the default verifier for criteria judgement.
    """

    name = "grounding"

    def __init__(self, *, min_sources: int = 1) -> None:
        self._min_sources = max(1, min_sources)

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        correlation_id: CorrelationId,
        context: str = "",
        domain: TaskDomain = TaskDomain.UNKNOWN,
        evidence: tuple[Evidence, ...] = (),
    ) -> VerificationResult:
        del goal, context, domain
        started = time.perf_counter()
        successful = [e for e in evidence if e.ok]
        if len(successful) < self._min_sources:
            _log.info(
                "verification.ungrounded",
                event_type="orchestration",
                correlation_id=correlation_id,
                evidence_count=len(evidence),
                successful=len(successful),
            )
            return VerificationResult(
                passed=False,
                score=0.0,
                verifier=self.name,
                criteria_results=(
                    CriterionResult(
                        criterion="answer is grounded in observed sources",
                        passed=False,
                        detail=(f"{len(successful)} successful observation(s); at least {self._min_sources} required"),
                    ),
                ),
                failure_reason="answer is not grounded in any successful observation",
                suggested_next_action="gather evidence with a retrieval or inspection tool",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        # Grounded enough to be judged on its merits: defer to the default
        # verifier via the router's "none" contract.
        return VerificationResult.not_applicable(
            f"grounded in {len(successful)} source(s); criteria judgement required"
        )


def _extract_paths(criteria: tuple[str, ...], answer: str, evidence: tuple[Evidence, ...]) -> list[str]:
    """Collect candidate paths, preserving order and removing duplicates."""
    seen: dict[str, None] = {}
    for e in evidence:
        if e.source.startswith(("/", "./", "src/", "tests/", "docs/")):
            seen.setdefault(e.source, None)
    for text in (*criteria, answer[:4000]):
        for match in _PATH_RE.finditer(text):
            seen.setdefault(match.group(1).rstrip(".,;:)"), None)
    return list(seen)
