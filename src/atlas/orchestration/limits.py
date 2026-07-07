"""Execution limits — the loop's seatbelts.

WHY a live counter object (not scattered checks): every bound is enforced in one
place, so no reasoning path can forget one. Hitting a limit raises a typed error
the monitor treats as a graceful, audited termination — never a crash, never an
infinite loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from atlas.orchestration.errors import OrchestrationTimeoutError, ReasoningError


@dataclass(frozen=True)
class ExecutionLimits:
    max_steps: int = 12
    max_tool_calls: int = 20
    max_tokens: int = 40_000
    max_runtime_s: float = 300.0
    max_recursion: int = 3
    max_retries: int = 3


@dataclass
class LimitCounter:
    limits: ExecutionLimits
    steps: int = 0
    tool_calls: int = 0
    tokens: int = 0
    retries: int = 0
    recursion: int = 0
    _start: float = field(default_factory=time.perf_counter)

    def tick_step(self) -> None:
        self.steps += 1
        if self.steps > self.limits.max_steps:
            raise ReasoningError(f"max_steps {self.limits.max_steps} exceeded")
        self._check_time()

    def tick_tool(self) -> None:
        self.tool_calls += 1
        if self.tool_calls > self.limits.max_tool_calls:
            raise ReasoningError(f"max_tool_calls {self.limits.max_tool_calls} exceeded")

    def add_tokens(self, n: int) -> None:
        self.tokens += n
        if self.tokens > self.limits.max_tokens:
            raise ReasoningError(f"max_tokens {self.limits.max_tokens} exceeded")

    def tick_retry(self) -> bool:
        self.retries += 1
        return self.retries <= self.limits.max_retries

    def _check_time(self) -> None:
        if (time.perf_counter() - self._start) > self.limits.max_runtime_s:
            raise OrchestrationTimeoutError(f"max_runtime {self.limits.max_runtime_s}s exceeded")
