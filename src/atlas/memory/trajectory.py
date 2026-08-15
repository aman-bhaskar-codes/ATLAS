"""Trajectory and Experience models — durable learning from task execution.

Phase 2: Trajectories capture every task run with full execution history.
Experiences are post-task lessons extracted via LLM analysis. DecisionTraces
record model/tool/strategy choices. FailureRecords enable taxonomy building.

WHY frozen models: trajectories are immutable execution artifacts that may be
serialized for analysis, checkpointing, or export to training pipelines.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from atlas.infra.ids import CorrelationId, TaskId


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Decision Tracing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class DecisionPoint(StrEnum):
    """Where in the pipeline a decision was made."""
    ROUTING = "routing"              # Router chose capabilities
    PLANNING = "planning"            # Planner selected approach
    MODEL_SELECTION = "model_selection"  # Gateway chose model provider
    TOOL_SELECTION = "tool_selection"    # Reasoning loop picked tool
    REPLANNING = "replanning"        # Replanner revised plan
    VERIFICATION = "verification"    # Verifier evaluated answer
    SAFETY_TIER = "safety_tier"      # Safety engine classified tier
    CRITIQUE = "critique"            # Reflection hook evaluated action


class DecisionOutcome(StrEnum):
    """How the decision turned out."""
    SUCCESS = "success"              # Decision led to expected result
    FAILURE = "failure"              # Decision led to failure
    SUBOPTIMAL = "suboptimal"        # Succeeded but not ideal
    UNKNOWN = "unknown"              # Outcome not yet determined


class DecisionTrace(BaseModel):
    """Records a single decision point with options and outcome.
    
    Used to build understanding of which choices work in which contexts.
    """
    model_config = {"frozen": True}
    
    id: str                                    # Unique trace ID
    task_id: TaskId
    correlation_id: CorrelationId
    ts: datetime
    decision_point: DecisionPoint
    options_considered: tuple[str, ...]        # ["gpt-4", "claude-3.5-sonnet"]
    chosen_option: str                         # "claude-3.5-sonnet"
    rationale: str                             # Why this choice?
    context: dict[str, Any] = Field(default_factory=dict)  # Relevant state
    outcome: DecisionOutcome = DecisionOutcome.UNKNOWN
    outcome_detail: str | None = None          # Why success/failure?
    confidence: float = 0.5                    # Decision confidence
    latency_ms: int | None = None              # Time to decide
    cost_usd: float = 0.0                      # Cost of this decision


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Failure Taxonomy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class FailureCategory(StrEnum):
    """High-level failure classification."""
    TOOL_ERROR = "tool_error"                  # Tool execution failed
    MODEL_ERROR = "model_error"                # LLM returned invalid response
    PLANNING_ERROR = "planning_error"          # Bad plan structure
    VERIFICATION_FAILED = "verification_failed"  # Answer didn't meet criteria
    TIMEOUT = "timeout"                        # Execution exceeded limits
    CANCELLATION = "cancellation"              # User cancelled
    SAFETY_BLOCK = "safety_block"              # Safety engine blocked action
    RESOURCE_EXHAUSTION = "resource_exhaustion"  # Limits hit (tokens/steps)
    UNKNOWN = "unknown"                        # Unclassified failure


class FailureRecord(BaseModel):
    """Captures failure details for taxonomy building and mitigation.
    
    Multiple FailureRecords can exist per trajectory (e.g., tool fails,
    replanning succeeds, then verification fails).
    """
    model_config = {"frozen": True}
    
    id: str                                    # Unique failure ID
    task_id: TaskId
    correlation_id: CorrelationId
    ts: datetime
    category: FailureCategory
    step: int                                  # Which reasoning step failed
    component: str                             # "tool_dispatcher", "replanner", etc.
    error_message: str                         # Raw error
    context: dict[str, Any] = Field(default_factory=dict)  # State at failure
    recovered: bool = False                    # Was recovery attempted?
    recovery_method: str | None = None         # "replan", "retry", etc.
    recovery_succeeded: bool = False           # Did recovery work?
    
    # Pattern detection fields (for building taxonomy)
    similar_failure_ids: tuple[str, ...] = ()  # Other failures with same pattern
    mitigation_suggested: str | None = None    # Auto-suggested fix
    mitigation_applied: bool = False           # Was mitigation used?


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Trajectory: Complete Task Execution History
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ActionRecord(BaseModel):
    """Serializable action record from OTAR loop."""
    model_config = {"frozen": True}
    
    step: int
    kind: str                                  # "tool_call", "final_answer", etc.
    tool: str | None = None
    operation: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    final_text: str | None = None


class ObservationRecord(BaseModel):
    """Serializable observation record from OTAR loop."""
    model_config = {"frozen": True}
    
    step: int
    ok: bool
    content: str | None = None                 # Truncated for storage
    error: str | None = None


class Trajectory(BaseModel):
    """Complete execution history of a task from creation to completion.
    
    Trajectories are the foundation for:
    - Post-task experience extraction
    - Replay/debugging of agent behavior
    - Training data for fine-tuning
    - Audit trail for user transparency
    """
    model_config = {"frozen": True}
    
    # Identity
    id: str                                    # Unique trajectory ID
    task_id: TaskId
    correlation_id: CorrelationId
    
    # Request & Plan
    request: str                               # Original user request
    goal: str                                  # Planned goal
    plan_steps: tuple[str, ...]                # High-level plan steps
    risk_level: str                            # "low", "medium", "high"
    plan_confidence: float
    
    # Execution History
    actions: tuple[ActionRecord, ...] = ()
    observations: tuple[ObservationRecord, ...] = ()
    decision_traces: tuple[str, ...] = ()      # Decision trace IDs
    failure_records: tuple[str, ...] = ()      # Failure record IDs
    
    # Adaptive Behavior (Phase 1)
    replan_count: int = 0
    verification_passed: bool | None = None
    verification_score: float | None = None
    
    # Outcome
    success: bool
    answer: str | None = None
    error: str | None = None
    steps_taken: int
    
    # Performance Metrics
    latency_ms: int                            # Total execution time
    tokens_used: int                           # Total tokens (input + output)
    cost_usd: float                            # Total cost
    model_calls: int                           # Number of LLM calls
    tool_calls: int                            # Number of tool invocations
    
    # Metadata
    created_ts: datetime
    completed_ts: datetime


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Experience: Extracted Lessons
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ExperienceCategory(StrEnum):
    """Type of lesson learned."""
    TOOL_USAGE = "tool_usage"                  # How to use a tool effectively
    PLANNING_PATTERN = "planning_pattern"      # Successful planning approach
    ERROR_RECOVERY = "error_recovery"          # How to recover from failures
    USER_PREFERENCE = "user_preference"        # User likes/dislikes
    DOMAIN_KNOWLEDGE = "domain_knowledge"      # Facts about the problem domain
    OPTIMIZATION = "optimization"              # Faster/cheaper approach found
    CONSTRAINT = "constraint"                  # Hard constraint discovered


class Experience(BaseModel):
    """A lesson extracted from trajectory analysis.
    
    Experiences feed back into:
    - Planning (via context injection)
    - Replanning (avoid known failure patterns)
    - User model (preference updates)
    - Semantic memory (domain facts)
    """
    model_config = {"frozen": True}
    
    id: str                                    # Unique experience ID
    trajectory_id: str                         # Source trajectory
    task_id: TaskId
    correlation_id: CorrelationId
    
    # Lesson Content
    category: ExperienceCategory
    lesson_text: str                           # Natural language lesson
    applicability_context: str                 # When does this apply?
    confidence: float = 0.5                    # How confident in this lesson?
    
    # Evidence
    supporting_actions: tuple[int, ...] = ()   # Step numbers that support lesson
    supporting_observations: tuple[int, ...] = ()
    counter_examples: tuple[str, ...] = ()     # Trajectory IDs that contradict
    
    # Impact
    reuse_count: int = 0                       # Times applied in later tasks
    success_rate: float = 0.0                  # Success when applied
    avg_improvement_ms: int = 0                # Speed improvement
    avg_cost_savings_usd: float = 0.0          # Cost savings
    
    # Metadata
    extracted_ts: datetime                     # When was this extracted?
    last_applied_ts: datetime | None = None    # When last used?
    superseded_by: str | None = None           # Replaced by better lesson?


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Supporting Types
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TrajectoryQuery(BaseModel):
    """Query parameters for trajectory retrieval."""
    
    task_id: TaskId | None = None
    correlation_id: CorrelationId | None = None
    success: bool | None = None                # Filter by outcome
    min_replan_count: int = 0                  # Tasks with replans
    min_steps: int = 0                         # Complex tasks
    min_latency_ms: int = 0                    # Slow tasks
    category: FailureCategory | None = None    # Tasks with specific failures
    from_ts: datetime | None = None            # Time range
    to_ts: datetime | None = None
    limit: int = 100


class ExperienceQuery(BaseModel):
    """Query parameters for experience retrieval."""
    
    category: ExperienceCategory | None = None
    min_confidence: float = 0.5
    min_reuse_count: int = 0                   # Proven lessons
    min_success_rate: float = 0.0              # Effective lessons
    applicability_context: str | None = None   # Semantic search on context
    limit: int = 50
