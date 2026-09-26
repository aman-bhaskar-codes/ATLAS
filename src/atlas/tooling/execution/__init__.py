"""Durable execution fabric (Part 4) — how ATLAS reliably executes RoutePlans.

Responsibilities kept separate (§122): the ROUTER decides, the EXECUTION ENGINE
orchestrates, the ADAPTER translates, the SAFETY ENGINE authorizes, the
BACKEND acts, the VALIDATOR checks, JEV judges bounded uncertainty, the
RECOVERY CONTROLLER responds to failure, the CHECKPOINT preserves progress,
the TRAJECTORY records what happened.
"""

from __future__ import annotations

from atlas.tooling.execution.adapters import AgentStepAdapter, ToolStepAdapter, WorkflowStepAdapter
from atlas.tooling.execution.engine import ExecutionEngine, ExecutionValidationError
from atlas.tooling.execution.events import TOPIC_EXECUTION, ExecutionEvent, ExecutionEventPublisher
from atlas.tooling.execution.models import (
    ExecutionRequest,
    ExecutionRun,
    ExecutionRunResult,
    IllegalExecutionTransition,
    JoinPolicy,
    RetryClass,
    RetryPolicy,
    RunStatus,
    StepRun,
    StepStatus,
    TerminalOutcome,
    transition_run,
    transition_step,
)
from atlas.tooling.execution.recovery import (
    ControllerDecision,
    RecoveryContext,
    RecoveryController,
    RouteStalenessChecker,
)
from atlas.tooling.execution.retry import classify_exception, classify_result, should_retry
from atlas.tooling.execution.scheduler import StepScheduler
from atlas.tooling.execution.slots import ExecutionSlots
from atlas.tooling.execution.store import CHECKPOINT_SCHEMA_VERSION, ExecutionRunStore

__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "TOPIC_EXECUTION",
    "AgentStepAdapter",
    "ControllerDecision",
    "ExecutionEngine",
    "ExecutionEvent",
    "ExecutionEventPublisher",
    "ExecutionRequest",
    "ExecutionRun",
    "ExecutionRunResult",
    "ExecutionRunStore",
    "ExecutionSlots",
    "ExecutionValidationError",
    "IllegalExecutionTransition",
    "JoinPolicy",
    "RecoveryContext",
    "RecoveryController",
    "RetryClass",
    "RetryPolicy",
    "RouteStalenessChecker",
    "RunStatus",
    "StepRun",
    "StepScheduler",
    "StepStatus",
    "TerminalOutcome",
    "ToolStepAdapter",
    "WorkflowStepAdapter",
    "classify_exception",
    "classify_result",
    "should_retry",
    "transition_run",
    "transition_step",
]
