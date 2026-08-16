"""Explicit crash resume — only for provably idempotent plans.

WHY gated: re-running a plan whose completed steps had side effects duplicates
those effects. Resume is therefore allowed ONLY when every remaining tool
step maps to a tool whose registry metadata declares `idempotent=True`
(reads, searches, pure computations). Anything else stays failed — the user
can always re-issue the task, which is the safe equivalent of a resume with
full replay.

Restores GoalState, the Plan, and the checkpointed history summary into a
fresh reasoning run, skipping steps already recorded as successful in the
checkpoint's plan progress.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.orchestration.checkpoint import CheckpointStore
from atlas.orchestration.registry import ToolRegistry
from atlas.orchestration.types import Plan, PlanStep, RiskLevel


@dataclass(frozen=True)
class ResumeDecision:
    allowed: bool
    reason: str


def assess_resume_safety(plan: Plan, registry: ToolRegistry) -> ResumeDecision:
    """Every step must reference a registered idempotent tool (or no tool)."""
    for step in plan.steps:
        if step.tool is None:
            continue  # reasoning-only step: re-execution is harmless
        meta = registry.metadata(step.tool)
        if meta is None:
            return ResumeDecision(False, f"unknown tool {step.tool!r}: cannot verify idempotency")
        if not meta.idempotent:
            return ResumeDecision(
                False,
                f"tool {step.tool!r} has side effects; re-execution could duplicate them",
            )
    return ResumeDecision(True, "all steps idempotent")


def _step_from(s: dict[str, object], i: int) -> PlanStep:
    tool = s.get("tool")
    operation = s.get("operation")
    args = s.get("args")
    deps = s.get("depends_on")
    return PlanStep(
        index=int(str(s.get("index", i))),
        intent=str(s.get("intent", "")),
        tool=tool if isinstance(tool, str) else None,
        operation=operation if isinstance(operation, str) else None,
        args={str(k): v for k, v in args.items()} if isinstance(args, dict) else {},
        depends_on=tuple(int(d) for d in deps if isinstance(d, (int, str, float))) if isinstance(deps, list) else (),
    )


def plan_from_checkpoint(raw: dict[str, object]) -> Plan:
    """Rebuild a Plan from checkpoint state (defensive, same rules as LLM JSON)."""
    steps_raw = raw.get("steps")
    typed_steps: list[dict[str, object]] = (
        [s for s in steps_raw if isinstance(s, dict)] if isinstance(steps_raw, list) else []
    )
    steps = tuple(_step_from(s, i) for i, s in enumerate(typed_steps))
    risk_raw = raw.get("risk")
    try:
        risk = RiskLevel(str(risk_raw)) if risk_raw is not None else RiskLevel.MEDIUM
    except ValueError:
        risk = RiskLevel.MEDIUM
    conf_raw = raw.get("confidence")
    constraints_raw = raw.get("constraints")
    return Plan(
        goal=str(raw.get("goal", "")),
        steps=steps,
        risk=risk,
        confidence=float(str(conf_raw)) if conf_raw is not None else 0.5,
        constraints=(tuple(str(c) for c in constraints_raw) if isinstance(constraints_raw, list) else ()),
    )


async def try_resume(
    *,
    task_id: str,
    checkpoints: CheckpointStore,
    registry: ToolRegistry,
) -> tuple[ResumeDecision, Plan | None]:
    """Load the latest checkpoint and decide whether resume is permitted.

    Returns (decision, restored_plan). The caller (CLI/API) performs the
    actual re-run via the normal Orchestrator path with the restored plan.
    """
    checkpoint = await checkpoints.latest(task_id)
    if checkpoint is None:
        return ResumeDecision(False, "no checkpoint for this task"), None
    plan = plan_from_checkpoint(checkpoint.plan)
    decision = assess_resume_safety(plan, registry)
    if not decision.allowed:
        return decision, None
    return decision, plan
