"""ComputerUseTool — presents computer use AS a Tool so every action flows
through SafetyEngine.guard() exactly like filesystem/shell/email.

WHY a single tool with ops (perceive/health/act) instead of many tools: the
permission manifest can tier the WHOLE substrate family in three rules, and
the model gets ONE discoverable capability ("computer_use") regardless of how
many bodies (browser/macOS/Android) are attached. Safety sees the real
operation via ToolRequest.operation.

Honesty contract (Phase 47): the output reports verified/evidence/note. The
tool never upgrades a failed verification into ok=True.
"""

from __future__ import annotations

from typing import Any

from atlas.capabilities.computer_use.engine import ComputerUseEngine, SubstrateUnavailableError
from atlas.capabilities.computer_use.redaction import RedactionPolicy, redact_snapshot
from atlas.capabilities.computer_use.verify import ExpectationSpec
from atlas.control.contracts import ActionCapability, ControlAction
from atlas.infra.types import SideEffect, ToolResult
from atlas.perception.contracts import Substrate
from atlas.perception.targets import TargetRef

_MUTATING = {
    "click",
    "type_text",
    "press_key",
    "navigate",
    "goto",
    "back",
    "forward",
    "reload",
    "launch",
    "tap",
    "swipe",
    "long_press",
    "open_file",
    "close_window",
}


def _parse_substrate(raw: str) -> Substrate:
    try:
        return Substrate(raw)
    except ValueError as exc:
        raise ValueError(f"unknown substrate {raw!r}; valid: {[s.value for s in Substrate]}") from exc


def _parse_target(raw: dict[str, Any] | None) -> TargetRef | None:
    if not raw:
        return None
    return TargetRef(**raw)


def _parse_expectations(raw: list[dict[str, Any]] | None) -> tuple[ExpectationSpec, ...]:
    if not raw:
        return ()
    return tuple(ExpectationSpec(**e) for e in raw)


class ComputerUseTool:
    name = "computer_use"

    def __init__(self, engine: ComputerUseEngine, *, redaction: RedactionPolicy | None = None) -> None:
        self._engine = engine
        self._policy = redaction or RedactionPolicy()

    def dry_run(self, args: dict[str, Any]) -> str:
        op = args.get("op", "perceive")
        substrate = args.get("substrate", "?")
        if op == "act":
            operation = args.get("operation", "?")
            target = args.get("target") or {}
            return (
                f"computer_use ACT on {substrate}: {operation} "
                f"target={target.get('strategy', '?')}:{target.get('value', target.get('text', ''))!r} "
                f"arguments={args.get('arguments', {})}"
            )
        return f"computer_use {op} on {substrate} (read-only)"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        op = str(args.get("op", "perceive"))
        try:
            substrate = _parse_substrate(str(args.get("substrate", "")))
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        try:
            if op == "health":
                output = {"health": await self._engine.health(), "registered": self._engine.registered()}
                return ToolResult(ok=True, output=output)
            if op == "perceive":
                return await self._perceive(substrate, args)
            if op == "act":
                return await self._act(substrate, args)
            return ToolResult(ok=False, error=f"unknown op {op!r}; valid: perceive, health, act")
        except SubstrateUnavailableError as exc:
            return ToolResult(ok=False, error=str(exc))
        except (ValueError, TypeError) as exc:
            return ToolResult(ok=False, error=f"invalid arguments: {exc}")

    async def _perceive(self, substrate: Substrate, args: dict[str, Any]) -> ToolResult:
        snapshot = await self._engine.perceive(substrate, force=bool(args.get("force", False)))
        safe = redact_snapshot(snapshot, policy=self._policy)
        return ToolResult(
            ok=True,
            output={
                "substrate": substrate.value,
                "source": safe.source,
                "url": safe.url,
                "app_name": safe.app_name,
                "window_title": safe.window_title,
                "sensitive": safe.sensitive,
                "confidence": safe.confidence,
                "note": safe.note,
                "summary": safe.summarize(limit=int(args.get("limit", 40))),
                "element_count": len(safe.elements),
            },
        )

    async def _act(self, substrate: Substrate, args: dict[str, Any]) -> ToolResult:
        operation = str(args.get("operation", ""))
        if not operation:
            return ToolResult(ok=False, error="act requires 'operation'")
        action = ControlAction(
            capability=ActionCapability(str(args.get("capability", ActionCapability.UI.value))),
            operation=operation,
            target=_parse_target(args.get("target")),
            arguments=dict(args.get("arguments", {}) or {}),
            reversible=bool(args.get("reversible", operation not in {"launch", "open_file"})),
        )
        expectations = _parse_expectations(args.get("expectations"))
        outcome = await self._engine.act(substrate, action, expectations)
        side_effects: tuple[SideEffect, ...] = ()
        if operation in _MUTATING and outcome.result is not None and outcome.result.ok:
            fallback_target = args.get("arguments", {}).get("url", substrate.value)
            side_effects = (
                SideEffect(
                    kind=f"computer_use.{substrate.value}.{operation}",
                    target=str(action.target.value if action.target else fallback_target),
                    detail=outcome.result.evidence,
                    reversible=action.reversible,
                ),
            )
        return ToolResult(
            ok=outcome.ok,
            output={
                "substrate": substrate.value,
                "operation": operation,
                "executed": outcome.result.ok if outcome.result else False,
                "verified": outcome.verification.verified if outcome.verification else None,
                "verification_detail": outcome.verification.detail if outcome.verification else None,
                "evidence": (
                    (outcome.verification.evidence if outcome.verification else None)
                    or (outcome.result.evidence if outcome.result else None)
                ),
                "resolved": (
                    {
                        "strategy": outcome.resolved.strategy_used.value,
                        "confidence": outcome.resolved.confidence,
                        "evidence": outcome.resolved.evidence,
                    }
                    if outcome.resolved
                    else None
                ),
                "note": outcome.note,
                "after_summary": outcome.after.summarize(limit=25) if outcome.after else None,
            },
            side_effects=side_effects,
            error=outcome.note if not outcome.ok else None,
        )
