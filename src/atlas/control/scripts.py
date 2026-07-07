"""Allowlisted AppleScript templates.

WHY templates, not raw scripts: AppleScript can control the entire machine. If
the model could emit arbitrary script text, a prompt injection could do anything.
Instead we expose a SMALL set of named, parameter-validated templates. Parameters
are escaped. Raw-script execution is a SEPARATE, Tier-2, allowlist-gated op
(control/raw.py) that is off by default.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


def _escape(value: str) -> str:
    """Escape a string for safe embedding in an AppleScript string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True)
class ScriptTemplate:
    name: str
    render: Callable[[dict[str, str]], str]
    required_params: tuple[str, ...]
    side_effecting: bool  # True => consequential => higher tier
    description: str


def _open_app(p: dict[str, str]) -> str:
    return f'tell application "{_escape(p["app"])}" to activate'


def _music_current(_: dict[str, str]) -> str:
    return 'tell application "Music" to get name of current track'


def _music_play(_: dict[str, str]) -> str:
    return 'tell application "Music" to play'


def _music_pause(_: dict[str, str]) -> str:
    return 'tell application "Music" to pause'


def _calendar_today(_: dict[str, str]) -> str:
    # Read-only: today's event summaries from the default calendar.
    return (
        'set output to ""\n'
        'tell application "Calendar"\n'
        '  set todayStart to current date\n'
        '  set hours of todayStart to 0\n'
        '  set minutes of todayStart to 0\n'
        '  set seconds of todayStart to 0\n'
        '  set todayEnd to todayStart + (1 * days)\n'
        '  repeat with c in calendars\n'
        '    set evs to (every event of c whose start date ≥ todayStart '
        'and start date < todayEnd)\n'
        '    repeat with e in evs\n'
        '      set output to output & (summary of e) & " @ " & (start date of e as string) & "\n"\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return output'
    )


def _notification(p: dict[str, str]) -> str:
    return f'display notification "{_escape(p["body"])}" with title "{_escape(p["title"])}"'


_TEMPLATES: dict[str, ScriptTemplate] = {
    t.name: t for t in (
        ScriptTemplate("open_app", _open_app, ("app",), True, "Activate/launch an application"),
        ScriptTemplate("music_current", _music_current, (), False, "Name of current Music track"),
        ScriptTemplate("music_play", _music_play, (), True, "Start Music playback"),
        ScriptTemplate("music_pause", _music_pause, (), True, "Pause Music playback"),
        ScriptTemplate("calendar_today", _calendar_today, (), False, "Today's calendar events"),
        ScriptTemplate(
            "notification", _notification, ("title", "body"), True, "Show a notification"
        ),
    )
}


def get_template(name: str) -> ScriptTemplate | None:
    return _TEMPLATES.get(name)


def known_intents() -> tuple[str, ...]:
    return tuple(_TEMPLATES)
