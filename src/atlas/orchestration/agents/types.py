"""Multi-agent contracts — SubTask, TaskDAG, SubTaskResult.

WHY a DAG and not a list: a complex request usually contains *independent*
branches ("research X" and "read the local config") that only converge at the
end. A dependency graph lets independent branches run concurrently while
dependent ones stay ordered, without inventing a second scheduler.

WHY frozen: these objects are handed to concurrently-running specialists and
are serialized into events/checkpoints. Immutability makes that safe by
construction — a specialist cannot mutate the graph it is executing.

Safety note: nothing here executes anything. A SubTask is a *request* for work;
execution still travels ReasoningLoop -> ToolDispatcher -> SafetyEngine.guard().
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from atlas.infra.cognition import RiskLevel

__all__ = [
    "AgentRole",
    "DecompositionOutcome",
    "RunOutcome",
    "SubTask",
    "SubTaskResult",
    "SubTaskStatus",
    "TaskDAG",
]


class AgentRole(StrEnum):
    """Specialist personas.

    A role is a *prompt + budget* profile, not a separate runtime: every role
    executes on the same bounded OTAR loop through the same safety funnel.
    """

    RESEARCHER = "researcher"  # gather + cite external/knowledge evidence
    CODER = "coder"  # read/modify code, run checks
    WRITER = "writer"  # compose prose from supplied material
    ANALYST = "analyst"  # compare, compute, reason over given data
    GENERAL = "general"  # anything that does not fit a specialist

    @classmethod
    def parse(cls, raw: object) -> AgentRole:
        """Never raise on model output — unknown roles degrade to GENERAL."""
        if isinstance(raw, cls):
            return raw
        try:
            return cls(str(raw).strip().lower())
        except ValueError:
            return cls.GENERAL


class SubTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"  # a dependency failed; never attempted


class RunOutcome(StrEnum):
    """Verdict on a whole delegated run.

    WHY this is separate from "did a subtask succeed": a specialist reporting
    SUCCEEDED means it finished its own loop, not that the run answered the
    request. Only independent verification can say that, so the run-level
    verdict is derived from the verification result — never self-reported.

    UNCERTAIN is a first-class outcome, not a soft failure: when verification
    was inconclusive, was never performed, branches contradicted each other, or
    part of the graph was abandoned for budget, the honest state is "we have an
    answer and cannot vouch for it". Collapsing that into either success or
    failure is what makes a multi-agent system confidently wrong.
    """

    VERIFIED = "verified"  # answered and independently verified
    UNCERTAIN = "uncertain"  # answered; verification inconclusive/absent/contested
    REJECTED = "rejected"  # answered; verification actively failed
    FAILED = "failed"  # no usable answer was produced at all


class SubTask(BaseModel):
    """One unit of delegated work."""

    model_config = {"frozen": True}

    id: str  # stable within a DAG: "st1", "st2", ...
    role: AgentRole = AgentRole.GENERAL
    objective: str
    success_criteria: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    # Advisory hint for the specialist prompt; the registry still resolves tools
    # and the SafetyEngine still gates every call.
    suggested_tools: tuple[str, ...] = ()
    max_steps: int = Field(default=6, ge=1, le=40)
    risk: RiskLevel = RiskLevel.LOW


class SubTaskResult(BaseModel):
    """Outcome of one SubTask. `output` is what the synthesizer reads."""

    model_config = {"frozen": True}

    subtask_id: str
    role: AgentRole
    status: SubTaskStatus
    output: str = ""
    error: str | None = None
    steps_taken: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    tokens_used: int = 0
    latency_ms: int = 0
    # Trajectory passthrough. Typed Any for the same reason TaskResult does it:
    # memory.trajectory sits above orchestration in the layer graph, so importing
    # ActionRecord/ObservationRecord here would invert the dependency. Without
    # this the parent task's trajectory would be empty for every delegated run,
    # and the learning loop would silently stop seeing multi-agent work.
    actions: tuple[Any, ...] = ()
    observations: tuple[Any, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is SubTaskStatus.SUCCEEDED


class TaskDAG(BaseModel):
    """A validated dependency graph of SubTasks.

    Construct through :meth:`build` — it is the only path that guarantees the
    two invariants the executor relies on: every dependency exists, and there
    are no cycles. A malformed graph is *repaired*, never raised, because the
    source is model output and a hard failure here would sink the whole task.
    """

    model_config = {"frozen": True}

    goal: str
    subtasks: tuple[SubTask, ...] = ()
    repairs: tuple[str, ...] = ()  # audit trail of what build() had to fix

    @classmethod
    def build(cls, goal: str, subtasks: tuple[SubTask, ...]) -> TaskDAG:
        """Drop dangling dependencies, then break any cycles deterministically."""
        repairs: list[str] = []
        known = {s.id for s in subtasks}
        cleaned: list[SubTask] = []
        for s in subtasks:
            deps = tuple(d for d in s.depends_on if d in known and d != s.id)
            if len(deps) != len(s.depends_on):
                repairs.append(f"{s.id}: dropped unknown/self dependencies")
                s = s.model_copy(update={"depends_on": deps})
            cleaned.append(s)

        ordered, cyclic = _topological_order(cleaned)
        if cyclic:
            # Deterministic cycle break: members of a cycle lose their deps and
            # run in the first batch. Serializing them would be arbitrary too,
            # and losing the whole task to a bad graph is strictly worse.
            repairs.append(f"broke dependency cycle among {sorted(cyclic)}")
            cleaned = [s.model_copy(update={"depends_on": ()}) if s.id in cyclic else s for s in cleaned]
            ordered, _ = _topological_order(cleaned)

        by_id = {s.id: s for s in cleaned}
        return cls(goal=goal, subtasks=tuple(by_id[i] for i in ordered), repairs=tuple(repairs))

    def batches(self) -> tuple[tuple[SubTask, ...], ...]:
        """Topological levels. Every SubTask in a batch may run concurrently."""
        remaining = {s.id: set(s.depends_on) for s in self.subtasks}
        by_id = {s.id: s for s in self.subtasks}
        done: set[str] = set()
        out: list[tuple[SubTask, ...]] = []
        while remaining:
            level = tuple(by_id[i] for i in remaining if remaining[i] <= done)
            if not level:  # unreachable after build(), but never spin forever
                break
            out.append(level)
            for s in level:
                done.add(s.id)
                del remaining[s.id]
        return tuple(out)

    def dependents_of(self, subtask_id: str) -> frozenset[str]:
        """Transitive closure of everything that would be blocked by a failure."""
        blocked: set[str] = set()
        frontier = {subtask_id}
        while frontier:
            nxt = {s.id for s in self.subtasks if s.depends_on and frontier & set(s.depends_on)} - blocked
            blocked |= nxt
            frontier = nxt
        return frozenset(blocked)


def _topological_order(subtasks: list[SubTask]) -> tuple[list[str], frozenset[str]]:
    """Kahn's algorithm. Returns (order, ids stuck in a cycle)."""
    pending = {s.id: set(s.depends_on) for s in subtasks}
    order: list[str] = []
    while True:
        ready = sorted(i for i, deps in pending.items() if not deps)
        if not ready:
            break
        for i in ready:
            order.append(i)
            del pending[i]
        for deps in pending.values():
            deps.difference_update(ready)
    return order, frozenset(pending)


class DecompositionOutcome(BaseModel):
    """Supervisor verdict: either delegate this DAG, or decline and stay serial."""

    model_config = {"frozen": True}

    should_delegate: bool
    reason: str = ""
    dag: TaskDAG | None = None
