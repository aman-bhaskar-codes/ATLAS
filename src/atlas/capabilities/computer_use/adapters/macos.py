"""macOS substrate adapter — one body of ATLAS, built on AX + AppleScript.

WHY this file exists: the cognitive core speaks PerceptionSnapshot /
ControlAction only. This adapter translates those universal contracts to the
existing macOS assets:

* Perception: reuses ``atlas.perception.backend.PerceptionBackend`` (the AX
  tree walker). Blocking pyobjc work runs via ``asyncio.to_thread`` so the
  event loop never stalls.
* Control: reuses ``atlas.control.osascript.ScriptRunner`` with a SMALL
  allowlisted set of rendered AppleScript templates. Parameters are escaped;
  the model can never inject raw script text through this path.

No substrate knowledge leaks upward: callers receive HealthStatus /
PerceptionSnapshot / ControlResult only.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from atlas.control.contracts import ActionCapability, ControlAction, ControlResult
from atlas.control.osascript import ScriptRunner
from atlas.infra.platform import is_macos
from atlas.perception.backend import PerceptionBackend
from atlas.perception.contracts import (
    HealthStatus,
    PerceptionModality,
    PerceptionSnapshot,
    Substrate,
    snapshot_from_screen_state,
)
from atlas.perception.targets import TargetRef

_log = logging.getLogger("atlas.computer_use.macos")

_DEFAULT_TIMEOUT_S = 15.0


def _escape(value: str) -> str:
    """Escape a string for embedding in an AppleScript string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Perception
# ---------------------------------------------------------------------------


class MacOSPerceptionAdapter:
    """PerceptionAdapter over the macOS accessibility tree."""

    substrate = Substrate.MACOS

    def __init__(self, backend: PerceptionBackend) -> None:
        self._backend = backend

    async def snapshot(self, target: object | None = None) -> PerceptionSnapshot:
        # Blocking pyobjc AX walk must never stall the loop.
        state = await asyncio.to_thread(self._backend.capture_frontmost)
        return snapshot_from_screen_state(
            state,
            snapshot_id=uuid.uuid4().hex,
            captured_ts=datetime.now(UTC),
        )

    async def health(self) -> HealthStatus:
        if not is_macos():
            return HealthStatus(available=False, detail="not running on macOS")
        if not self._backend.available():
            return HealthStatus(
                available=False,
                detail="AX backend unavailable (missing pyobjc or accessibility permission)",
                permission_missing="macos_accessibility",
            )
        return HealthStatus(available=True, detail="AX perception ready")

    async def capabilities(self) -> tuple[PerceptionModality, ...]:
        return (PerceptionModality.ACCESSIBILITY, PerceptionModality.STRUCTURE, PerceptionModality.TEXT)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------

# Allowlisted UI action templates. WHY a fixed set: AppleScript can drive the
# whole machine; free-form script text from a model would be a prompt-injection
# superpower. Each operation renders escaped parameters into a known-good shape.

_KEY_CODES: dict[str, int] = {
    "return": 36,
    "tab": 48,
    "escape": 53,
    "delete": 51,
    "space": 49,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
}


def _target_label(target: TargetRef | None) -> str | None:
    if target is None:
        return None
    return target.label or target.text or target.value or None


class MacOSControlAdapter:
    """ControlAdapter over AppleScript via an injectable ScriptRunner."""

    substrate = Substrate.MACOS

    def __init__(self, runner: ScriptRunner, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._runner = runner
        self._timeout_s = timeout_s

    async def health(self) -> HealthStatus:
        if not is_macos():
            return HealthStatus(available=False, detail="osascript requires macOS")
        return HealthStatus(available=True, detail="osascript control ready")

    async def capabilities(self) -> tuple[str, ...]:
        return ("launch", "click", "type_text", "press_key", "open_file", "close_window")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def dispatch(self, action: ControlAction) -> ControlResult:
        if action.capability is ActionCapability.OBSERVATION:
            return ControlResult(ok=False, error="observation is handled by the perception adapter")
        try:
            script = self._render(action)
        except ValueError as exc:
            return ControlResult(ok=False, error=f"invalid action: {exc}")
        result = await self._runner.run(script, timeout_s=self._timeout_s)
        return ControlResult(
            ok=result.ok,
            output=result.stdout or None,
            error=result.stderr or None if not result.ok else None,
            evidence=f"osascript exit={result.exit_code}",
        )

    # --- template rendering (pure, testable) ---

    def _render(self, action: ControlAction) -> str:
        op = action.operation
        app = str(action.arguments.get("app", "") or action.arguments.get("app_name", ""))
        if op == "launch":
            if not app:
                raise ValueError("launch requires arguments.app")
            return f'tell application "{_escape(app)}" to activate'
        if op in {"click", "type_text", "press_key", "close_window"} and not app:
            raise ValueError(f"{op} requires arguments.app (the process name)")
        if op == "click":
            label = _target_label(action.target)
            if not label:
                raise ValueError("click requires a labelled target")
            return self._system_events(app, f'click (first UI element of window 1 whose name is "{_escape(label)}")')
        if op == "type_text":
            text = str(action.arguments.get("text", ""))
            if not text:
                raise ValueError("type_text requires arguments.text")
            return self._system_events(app, f'keystroke "{_escape(text)}"')
        if op == "press_key":
            key = str(action.arguments.get("key", "")).lower()
            code = _KEY_CODES.get(key)
            if code is None:
                raise ValueError(f"press_key supports only {sorted(_KEY_CODES)}")
            return self._system_events(app, f"key code {code}")
        if op == "close_window":
            return f'tell application "{_escape(app)}" to close front window'
        if op == "open_file":
            path = str(action.arguments.get("path", ""))
            if not app or not path:
                raise ValueError("open_file requires arguments.app and arguments.path")
            return f'tell application "{_escape(app)}" to open (POSIX file "{_escape(path)}")'
        raise ValueError(f"unknown operation: {op}")

    @staticmethod
    def _system_events(app: str, body: str) -> str:
        return (
            'tell application "System Events"\n'
            f'  tell process "{_escape(app)}"\n'
            "    set frontmost to true\n"
            f"    {body}\n"
            "  end tell\n"
            "end tell"
        )
