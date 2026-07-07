"""Cancellation — cooperative, checked before every step and dispatch.

WHY cooperative (not task.cancel()): we want a CLEAN stop that still records
what happened and transitions to a terminal state, not a torn-off coroutine.
The kill switch (L1) is the global version; this is the per-task version.
"""

from __future__ import annotations


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True
