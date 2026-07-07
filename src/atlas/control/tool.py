"""AppControlTool — named AppleScript intents as an audited, tiered Tool.

WHY operations map to manifest tiers: 'music_current' / 'calendar_today' are
reads (Tier-1, benign side-effect-free). 'open_app' / 'music_play' /
'notification' are side-effecting (Tier-2 per manifest). The tool refuses any
intent not in the allowlist (deny-by-default) and validates required params
before rendering.
"""

from __future__ import annotations

from typing import Any

from atlas.control.osascript import ScriptRunner
from atlas.control.scripts import get_template, known_intents
from atlas.infra.logging import get_logger
from atlas.infra.types import SideEffect, ToolResult

_log = get_logger("atlas.control")


class AppControlTool:
    name = "app_control"

    def __init__(self, runner: ScriptRunner, *, timeout_s: float = 15.0) -> None:
        self._runner = runner
        self._timeout_s = timeout_s

    def dry_run(self, args: dict[str, Any]) -> str:
        intent = str(args.get("intent", ""))
        tmpl = get_template(intent)
        if tmpl is None:
            return f"UNKNOWN intent {intent!r} (allowed: {', '.join(known_intents())})"
        params = {k: v for k, v in args.items() if k != "intent"}
        return f"run AppleScript intent {intent!r} ({tmpl.description}) with {params}"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        intent = str(args.get("intent", ""))
        tmpl = get_template(intent)
        if tmpl is None:
            return ToolResult(ok=False,
                              error=f"intent {intent!r} not allowlisted; "
                                    f"known: {', '.join(known_intents())}")
        params = {k: str(v) for k, v in args.items() if k != "intent"}
        missing = [p for p in tmpl.required_params if p not in params]
        if missing:
            return ToolResult(ok=False, error=f"missing params for {intent!r}: {missing}")

        script = tmpl.render(params)
        result = await self._runner.run(script, timeout_s=self._timeout_s)
        _log.info("control.ran", event_type="control", intent=intent, ok=result.ok)
        if not result.ok:
            return ToolResult(ok=False, error=result.stderr or "script failed")

        effects: tuple[SideEffect, ...] = ()
        if tmpl.side_effecting:
            effects = (SideEffect(kind="app_control", target=intent,
                                  detail=str(params), reversible=False),)
        return ToolResult(ok=True, output={"intent": intent, "stdout": result.stdout},
                          side_effects=effects)
