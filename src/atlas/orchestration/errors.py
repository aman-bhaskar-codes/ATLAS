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


class PlanningError(OrchestrationError):
    recoverable = True


class ReasoningError(OrchestrationError):
    recoverable = True


class ToolExecutionError(OrchestrationError):
    recoverable = True


class ValidationError(OrchestrationError):
    recoverable = True


class ContextError(OrchestrationError):
    recoverable = False


class OrchestrationMemoryError(OrchestrationError):
    recoverable = False


class CancellationError(OrchestrationError):
    recoverable = False


class OrchestrationTimeoutError(OrchestrationError):
    recoverable = True


class RecoveryError(OrchestrationError):
    recoverable = False


class IllegalTransitionError(OrchestrationError):
    recoverable = False
