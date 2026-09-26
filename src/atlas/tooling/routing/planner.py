"""L8 route-plan compilation (Part 3 §36-§39/§53-§54/§65).

Compiles a RouteDecision into a RoutePlan + RouteGraph: static templates per
strategy (§39) with dynamic step injection. Every plan is VALIDATED before it
can execute (§65) — an invalid plan is rejected, never run. Budgets come from
the EXISTING ExecutionLimits (§54) — no duplicate budgeting system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from atlas.orchestration.limits import ExecutionLimits
from atlas.tooling.routing.models import (
    CandidateType,
    GraphEdgeCondition,
    GraphNodeType,
    RouteCandidate,
    RouteDecision,
    RouteEdge,
    RouteGraph,
    RouteNode,
    RoutePlan,
    RouteStep,
)


@dataclass(frozen=True)
class PlanValidationResult:
    ok: bool
    problems: tuple[str, ...] = ()


class RoutePlanValidationError(Exception):
    """§65: raised before execution; an invalid plan never runs."""


class RoutePlanner:
    def __init__(self, limits: ExecutionLimits | None = None) -> None:
        self._limits = limits or ExecutionLimits()

    # ── Compilation (§36/§37) ─────────────────────────────────────── #

    def compile(
        self,
        decision: RouteDecision,
        *,
        capability: str | None = None,
        operation: str | None = None,
        extra_steps: tuple[RouteStep, ...] = (),
    ) -> RoutePlan:
        plan_id = f"plan-{uuid.uuid4().hex[:12]}"
        strategy = decision.strategy
        primary = decision.selected_candidate
        fallbacks = decision.fallback_candidates

        steps: list[RouteStep] = []
        if decision.decision_type == "route" and primary:
            steps.append(
                RouteStep(
                    step_id=f"{plan_id}-s1",
                    candidate_id=primary,
                    operation=operation,
                    success_condition="ok",
                    failure_condition="error",
                )
            )
            # Fallback chain becomes retry steps on the failure edge (§53).
            for i, fallback_id in enumerate(fallbacks[:3], start=2):
                steps.append(
                    RouteStep(
                        step_id=f"{plan_id}-s{i}",
                        candidate_id=fallback_id,
                        operation=operation,
                        success_condition="ok",
                        failure_condition="error",
                    )
                )
        steps.extend(extra_steps)

        plan = RoutePlan(
            plan_id=plan_id,
            task_id=decision.request_id,
            route_id=decision.route_id,
            strategy=strategy,
            domain=decision.domain,
            steps=tuple(steps),
            fallbacks=fallbacks,
            budgets={
                "max_steps": float(self._limits.max_steps),
                "max_tool_calls": float(self._limits.max_tool_calls),
                "max_tokens": float(self._limits.max_tokens),
                "max_runtime_s": self._limits.max_runtime_s,
                "max_retries": float(self._limits.max_retries),
            },
            termination_conditions=("step_failure_exhausted", "budget_exceeded", "success"),
            verification_conditions=tuple(),
            template=self._template_for(decision),
        )
        return plan

    def build_graph(self, plan: RoutePlan, decision: RouteDecision | None = None) -> RouteGraph:
        """Explicit execution graph (§38) with the fallback graph folded in as
        failure edges (§53): primary →(failure)→ fallback-1 →(failure)→ ... →
        (failure)→ REPLAN/END."""
        nodes: list[RouteNode] = []
        edges: list[RouteEdge] = []
        exec_steps = [s for s in plan.steps if s.candidate_id]
        node_ids: dict[str, str] = {}
        for i, step in enumerate(exec_steps):
            node_id = f"n{i}"
            node_ids[step.step_id] = node_id
            nodes.append(RouteNode(node_id=node_id, node_type=GraphNodeType.EXECUTE, step_id=step.step_id))
        start_index = 0
        if decision is not None and decision.decision_type == "human_review":
            nodes.insert(
                0,
                RouteNode(node_id="n_human", node_type=GraphNodeType.HUMAN_REVIEW, label="operator decision"),
            )
            start_index = 1
        for i in range(len(exec_steps) - 1):
            edges.append(
                RouteEdge(
                    from_node=node_ids[exec_steps[i].step_id],
                    to_node=node_ids[exec_steps[i + 1].step_id],
                    condition=GraphEdgeCondition.FAILURE,
                )
            )
            edges.append(
                RouteEdge(
                    from_node=node_ids[exec_steps[i].step_id],
                    to_node="n_end",
                    condition=GraphEdgeCondition.SUCCESS,
                )
            )
        if exec_steps:
            last = node_ids[exec_steps[-1].step_id]
            edges.append(RouteEdge(from_node=last, to_node="n_replan", condition=GraphEdgeCondition.FAILURE))
            nodes.append(RouteNode(node_id="n_replan", node_type=GraphNodeType.REPLAN, label="recovery routing"))
            edges.append(RouteEdge(from_node="n_replan", to_node="n_end", condition=GraphEdgeCondition.SUCCESS))
        nodes.append(RouteNode(node_id="n_end", node_type=GraphNodeType.END))
        if start_index:
            edges.append(
                RouteEdge(
                    from_node="n_human", to_node=node_ids[exec_steps[0].step_id], condition=GraphEdgeCondition.SUCCESS
                )
            )
        return RouteGraph(plan_id=plan.plan_id, nodes=tuple(nodes), edges=tuple(edges))

    def _template_for(self, decision: RouteDecision) -> str | None:
        """Static route templates (§39) — the two defaults that exist today."""
        if decision.strategy == "RESEARCH":
            return "deep_research_default"
        if decision.strategy == "SOFTWARE_ENGINEERING":
            return "software_engineering_default"
        return None

    # ── Validation (§65) ──────────────────────────────────────────── #

    def validate(
        self,
        plan: RoutePlan,
        graph: RouteGraph,
        known_candidates: dict[str, RouteCandidate],
    ) -> PlanValidationResult:
        problems: list[str] = []
        if not plan.steps:
            problems.append("plan has no steps")
        for step in plan.steps:
            if step.candidate_id and step.candidate_id not in known_candidates:
                problems.append(f"candidate {step.candidate_id!r} does not exist")
            elif step.candidate_id:
                candidate = known_candidates[step.candidate_id]
                if not candidate.enabled or candidate.availability == "UNAVAILABLE":
                    problems.append(f"candidate {step.candidate_id!r} is not available")
                if (
                    step.operation
                    and step.operation not in candidate.operations
                    and candidate.candidate_type == CandidateType.TOOL
                ):
                    problems.append(f"operation {step.operation!r} unsupported by {step.candidate_id!r}")
        step_ids = {s.step_id for s in plan.steps}
        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    problems.append(f"step {step.step_id!r} depends on unknown step {dep!r}")
        if not graph.has_terminal():
            problems.append("graph has no END node (§84)")
        if not plan.termination_conditions:
            problems.append("plan has no termination conditions")
        if plan.budgets.get("max_retries", 0) > plan.budgets.get("max_steps", 0) + 1:
            problems.append("retry budget exceeds step budget")
        return PlanValidationResult(ok=not problems, problems=tuple(problems))

    def require_valid(
        self,
        plan: RoutePlan,
        graph: RouteGraph,
        known_candidates: dict[str, RouteCandidate],
    ) -> None:
        result = self.validate(plan, graph, known_candidates)
        if not result.ok:
            raise RoutePlanValidationError("; ".join(result.problems))


__all__ = ["PlanValidationResult", "RoutePlanValidationError", "RoutePlanner"]
