"""Kill switch. WHY file-backed: trippable from any terminal (`touch STOP.flag`)
even if the process is wedged. WHY fail-safe: if the existence check itself
errors, we treat the switch as ACTIVE — the safe direction."""

from __future__ import annotations

from pathlib import Path

from atlas.infra.logging import get_logger

_log = get_logger("atlas.killswitch")


class KillSwitch:
    def __init__(self, flag_path: str) -> None:
        self._flag = Path(flag_path)
        self._tripped = False

    def is_active(self) -> bool:
        if self._tripped:
            return True
        try:
            return self._flag.exists()
        except OSError:
            return True  # fail-safe

    def trip(self) -> None:
        self._tripped = True
        try:
            self._flag.touch(exist_ok=True)
        except OSError as exc:
            _log.error("killswitch.flag_write_failed", event_type="safety", error=repr(exc))
        _log.warning("killswitch.tripped", event_type="safety")

    def reset(self) -> None:
        self._tripped = False
        try:
            if self._flag.exists():
                self._flag.unlink()
        except OSError as exc:
            _log.error("killswitch.flag_clear_failed", event_type="safety", error=repr(exc))
        _log.info("killswitch.reset", event_type="safety")
