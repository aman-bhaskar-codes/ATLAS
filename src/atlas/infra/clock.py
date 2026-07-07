"""Injectable clock. WHY: tests must not depend on wall-clock time; every
module that needs 'now' takes a Clock, never calls datetime.now() directly."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Real UTC clock used in production wiring."""

    def now(self) -> datetime:
        return datetime.now(UTC)
