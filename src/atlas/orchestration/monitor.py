"""Execution monitor — the single 'may I continue?' gate.

WHY centralize: the loop should not scatter kill-switch / cancellation / limit
checks. The monitor answers before every step and every dispatch, and decides if
a caught error is recoverable (retry) or terminal (fail).
"""

from __future__ import annotations

from atlas.orchestration.errors import CancellationError, OrchestrationError
from atlas.orchestration.managers.cancellation import CancellationToken
from atlas.safety.killswitch import KillSwitch


class ExecutionMonitor:
    def __init__(self, killswitch: KillSwitch) -> None:
        self._ks = killswitch

    def check_may_continue(self, token: CancellationToken) -> None:
        if self._ks.is_active():
            raise CancellationError("kill switch active")
        if token.cancelled:
            raise CancellationError("task cancelled")

    @staticmethod
    def is_recoverable(exc: Exception) -> bool:
        return isinstance(exc, OrchestrationError) and exc.recoverable
