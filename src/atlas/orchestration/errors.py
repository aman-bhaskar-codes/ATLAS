"""Orchestration error taxonomy.

WHY distinct from infra.errors: the runtime needs categories that map to
recovery strategy (retry? escalate? abort?). Each subclass carries whether it is
conventionally recoverable so the ExecutionMonitor/RetryManager can decide
without string-matching messages.
"""

from __future__ import annotations

from atlas.infra.errors import AtlasError


class OrchestrationError(AtlasError):
    recoverable: bool = False
    code = "orchestration.error"


class PlanningError(OrchestrationError):
    recoverable = True
    code = "planning.error"


class ReasoningError(OrchestrationError):
    recoverable = True
    code = "reasoning.error"


class ToolExecutionError(OrchestrationError):
    recoverable = True
    code = "tool.execution"


class ValidationError(OrchestrationError):
    recoverable = True
    code = "validation.error"


class VerificationError(OrchestrationError):
    """The verifier itself failed (not a failed verification — that is a
    VerificationResult with passed=False)."""

    recoverable = True
    code = "verification.error"


class ContextError(OrchestrationError):
    recoverable = False
    code = "context.error"


class OrchestrationMemoryError(OrchestrationError):
    recoverable = False
    code = "memory.orchestration"


class CancellationError(OrchestrationError):
    recoverable = False
    code = "task.cancelled"
    user_message = "The task was cancelled."


class OrchestrationTimeoutError(OrchestrationError):
    recoverable = True
    code = "task.timeout"
    user_message = "The task exceeded its time limit."


class RecoveryError(OrchestrationError):
    recoverable = False
    code = "recovery.error"


class IllegalTransitionError(OrchestrationError):
    recoverable = False
    code = "state.illegal_transition"
