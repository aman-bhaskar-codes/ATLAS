"""Hierarchical Task Planner - Multi-level planning with decomposition and refinement.

This implements a state-of-the-art hierarchical planning system inspired by:
- HTN (Hierarchical Task Networks)
- GOAP (Goal-Oriented Action Planning)
- Modern LLM-based task decomposition

Key features:
1. Multi-level abstraction (strategic → tactical → operational)
2. Dynamic task decomposition with confidence scoring
3. Resource-aware planning (time, cost, complexity)
4. Adaptive refinement based on execution feedback
5. Parallel plan generation with consensus voting
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.types import Plan, PlanStep, RiskLevel

_log = get_logger("atlas.agents.hierarchical")


class PlanLevel(Enum):
    """Planning abstraction levels."""
    STRATEGIC = "strategic"      # High-level goals (what to achieve)
    TACTICAL = "tactical"        # Mid-level strategies (how to achieve)
    OPERATIONAL = "operational"  # Low-level actions (exact steps)


class DecompositionStrategy(Enum):
    """Strategies for task decomposition."""
    SEQUENTIAL = "sequential"          # Linear dependency chain
    PARALLEL = "parallel"              # Independent subtasks
    CONDITIONAL = "conditional"        # Branching based on conditions
    ITERATIVE = "iterative"            # Repeated refinement
    HIERARCHICAL = "hierarchical"      # Nested decomposition


@dataclass
class PlanningContext:
    """Context for planning decisions."""
    task_id: str
    correlation_id: str
    objective: str
    constraints: dict[str, Any] = field(default_factory=dict)
    available_tools: list[str] = field(default_factory=list)
    resource_budget: dict[str, float] = field(default_factory=dict)
    time_limit: timedelta | None = None
    cost_limit: float | None = None
    complexity_threshold: float = 0.7
    historical_success_rate: float = 0.5


@dataclass
class TaskDecomposition:
    """Result of decomposing a task into subtasks."""
    parent_task: str
    level: PlanLevel
    strategy: DecompositionStrategy
    subtasks: list[PlanStep]
    confidence: float
    estimated_cost: float
    estimated_duration: timedelta
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    reasoning: str = ""
    alternatives: list[TaskDecomposition] = field(default_factory=list)


@dataclass
class RefinedPlan:
    """A refined, executable plan with metadata."""
    plan: Plan
    level: PlanLevel
    decomposition_path: list[str]
    total_confidence: float
    estimated_cost: float
    estimated_duration: timedelta
    risk_assessment: dict[str, float]
    fallback_plans: list[Plan] = field(default_factory=list)


class HierarchicalPlanner:
    """Advanced multi-level task planner with adaptive decomposition."""
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        ids: IdGenerator,
        clock: Clock,
        max_depth: int = 4,
        parallel_decomposition: bool = True,
        consensus_voting: bool = True,
        min_confidence_threshold: float = 0.65,
    ) -> None:
        self._gw = gateway
        self._ids = ids
        self._clock = clock
        self._max_depth = max_depth
        self._parallel = parallel_decomposition
        self._consensus = consensus_voting
        self._min_confidence = min_confidence_threshold
        
        # Cache for similar tasks
        self._decomposition_cache: dict[str, TaskDecomposition] = {}
        
        # Statistics for adaptive planning
        self._stats: dict[str, Any] = {
            "decompositions": 0,
            "cache_hits": 0,
            "fallbacks": 0,
            "avg_confidence": 0.0,
            "success_rate_by_strategy": {s.value: 0.5 for s in DecompositionStrategy},
        }

    async def plan(
        self,
        ctx: PlanningContext,
    ) -> RefinedPlan:
        """Generate a refined, executable plan from a planning context.
        
        This is the main entry point for hierarchical planning.
        """
        start_time = time.perf_counter()
        
        _log.info(
            "hierarchical_planning.started",
            event_type="planning",
            task_id=ctx.task_id,
            objective=ctx.objective[:100],
            complexity=ctx.complexity_threshold,
        )
        
        # Step 1: Strategic decomposition (what to achieve)
        strategic = await self._decompose_strategic(ctx)
        
        # Step 2: Tactical decomposition (how to achieve)
        tactical_decompositions = await self._decompose_tactical(ctx, strategic)
        
        # Step 3: Operational decomposition (exact steps)
        operational_plans = await self._decompose_operational(ctx, tactical_decompositions)
        
        # Step 4: Select best plan (with consensus voting if enabled)
        final_plan = await self._select_best_plan(ctx, operational_plans)
        
        # Step 5: Generate fallback plans
        fallbacks = await self._generate_fallbacks(ctx, final_plan)
        
        duration = time.perf_counter() - start_time
        
        _log.info(
            "hierarchical_planning.completed",
            event_type="planning",
            task_id=ctx.task_id,
            confidence=final_plan.total_confidence,
            steps=len(final_plan.plan.steps),
            duration_ms=int(duration * 1000),
        )
        
        return RefinedPlan(
            plan=final_plan.plan,
            level=PlanLevel.OPERATIONAL,
            decomposition_path=[ctx.objective, strategic.reasoning],
            total_confidence=final_plan.total_confidence,
            estimated_cost=final_plan.estimated_cost,
            estimated_duration=final_plan.estimated_duration,
            risk_assessment=self._assess_risks(final_plan.plan),
            fallback_plans=fallbacks,
        )

    async def _decompose_strategic(
        self,
        ctx: PlanningContext,
    ) -> TaskDecomposition:
        """Strategic level: Identify high-level goals and success criteria."""
        
        prompt = f"""Analyze this task and identify high-level strategic goals:

TASK: {ctx.objective}

CONSTRAINTS:
{json.dumps(ctx.constraints, indent=2)}

AVAILABLE CAPABILITIES:
{', '.join(ctx.available_tools)}

Provide:
1. 2-4 strategic goals (what must be achieved)
2. Success criteria for each goal
3. Key risks and dependencies
4. Recommended decomposition strategy (sequential/parallel/conditional/iterative/hierarchical)

Output as JSON:
{{
  "goals": [
    {{"goal": "description", "success_criteria": [...], "priority": 1-5}}
  ],
  "risks": ["risk1", "risk2"],
  "dependencies": ["dep1", "dep2"],
  "strategy": "sequential|parallel|conditional|iterative|hierarchical",
  "confidence": 0.0-1.0,
  "reasoning": "explanation"
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=self._strategic_system_prompt()),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=2048,
                temperature=0.3,
            )
        )
        
        data = self._parse_json_response(resp.text)
        
        return TaskDecomposition(
            parent_task=ctx.objective,
            level=PlanLevel.STRATEGIC,
            strategy=DecompositionStrategy(data.get("strategy", "hierarchical")),
            subtasks=[
                PlanStep(
                    index=0,
                    intent=g["goal"],
                    tool=None,
                    operation=None,
                    args={},
                    depends_on=(),
                    )
                for g in data.get("goals", [])
            ],
            confidence=data.get("confidence", 0.7),
            estimated_cost=0.0,
            estimated_duration=timedelta(minutes=5),
            reasoning=data.get("reasoning", ""),
        )

    async def _decompose_tactical(
        self,
        ctx: PlanningContext,
        strategic: TaskDecomposition,
    ) -> list[TaskDecomposition]:
        """Tactical level: Convert goals into actionable strategies."""
        
        if self._parallel and len(strategic.subtasks) > 1:
            # Decompose goals in parallel
            tasks = [
                self._decompose_single_tactical(ctx, strategic, step)
                for step in strategic.subtasks
            ]
            return list(await asyncio.gather(*tasks))
        else:
            # Sequential decomposition
            results = []
            for step in strategic.subtasks:
                deco = await self._decompose_single_tactical(ctx, strategic, step)
                results.append(deco)
            return results

    async def _decompose_single_tactical(
        self,
        ctx: PlanningContext,
        strategic: TaskDecomposition,
        goal_step: PlanStep,
    ) -> TaskDecomposition:
        """Decompose a single strategic goal into tactical steps."""
        
        prompt = f"""Convert this strategic goal into tactical steps:

STRATEGIC GOAL: {goal_step.intent}

OVERALL OBJECTIVE: {ctx.objective}

AVAILABLE TOOLS:
{chr(10).join(f'- {tool}' for tool in ctx.available_tools)}

Generate 2-5 tactical steps that achieve this goal.
Each step should be specific and actionable.

Output as JSON:
{{
  "steps": [
    {{
      "description": "what to do",
      "tools_needed": ["tool1", "tool2"],
      "expected_outcome": "result",
      "dependencies": ["other_step_ids"]
    }}
  ],
  "strategy": "sequential|parallel|conditional",
  "confidence": 0.0-1.0,
  "estimated_duration_minutes": 5
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=self._tactical_system_prompt()),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=1500,
                temperature=0.4,
            )
        )
        
        data = self._parse_json_response(resp.text)
        
        return TaskDecomposition(
            parent_task=goal_step.intent,
            level=PlanLevel.TACTICAL,
            strategy=DecompositionStrategy(data.get("strategy", "sequential")),
            subtasks=[
                PlanStep(
                    index=i,
                    intent=s["description"],
                    tool=s.get("tools_needed", [None])[0],
                    operation=None,
                    args={},
                )
                for i, s in enumerate(data.get("steps", []))
            ],
            confidence=data.get("confidence", 0.7),
            estimated_cost=0.0,
            estimated_duration=timedelta(minutes=data.get("estimated_duration_minutes", 5)),
            reasoning=f"Tactical decomposition of: {goal_step.intent}",
        )

    async def _decompose_operational(
        self,
        ctx: PlanningContext,
        tactical_decompositions: list[TaskDecomposition],
    ) -> list[RefinedPlan]:
        """Operational level: Convert tactical steps into executable actions."""
        
        plans = []
        
        for tactical in tactical_decompositions:
            plan = await self._create_operational_plan(ctx, tactical)
            plans.append(plan)
        
        # Merge all operational plans into final plans
        return plans

    async def _create_operational_plan(
        self,
        ctx: PlanningContext,
        tactical: TaskDecomposition,
    ) -> RefinedPlan:
        """Create an operational plan from tactical decomposition."""
        
        operational_steps = []
        
        for _idx, tactical_step in enumerate(tactical.subtasks):
            # Convert tactical step to operational step(s)
            if tactical_step.tool:
                # Direct tool invocation
                operational_steps.append(tactical_step)
            else:
                # Need to determine tool and operation
                determined = await self._determine_tool_operation(ctx, tactical_step)
                operational_steps.extend(determined)
        
        plan = Plan(
            goal=ctx.objective,
            steps=tuple(operational_steps),
            constraints=tuple(ctx.constraints.keys()),
            risk=RiskLevel.LOW,
            confidence=tactical.confidence,
        )
        
        return RefinedPlan(
            plan=plan,
            level=PlanLevel.OPERATIONAL,
            decomposition_path=[tactical.parent_task],
            total_confidence=tactical.confidence,
            estimated_cost=tactical.estimated_cost,
            estimated_duration=tactical.estimated_duration,
            risk_assessment=self._assess_risks(plan),
            fallback_plans=[],
        )

    async def _determine_tool_operation(
        self,
        ctx: PlanningContext,
        step: PlanStep,
    ) -> list[PlanStep]:
        """Determine which tool and operation to use for a step."""
        
        # Use LLM to determine tool
        prompt = f"""Determine the best tool and operation for this step:

STEP: {step.intent}

AVAILABLE TOOLS:
{chr(10).join(f'- {tool}' for tool in ctx.available_tools)}

Output as JSON:
{{
  "tool": "tool_name",
  "operation": "operation_name",
  "args": {{"key": "value"}},
  "reasoning": "why this tool"
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a tool selection expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=500,
                temperature=0.2,
            )
        )
        
        data = self._parse_json_response(resp.text)
        
        return [
            PlanStep(
                index=0,
                intent=step.intent,
                tool=data.get("tool"),
                operation=data.get("operation"),
                args=data.get("args", {}),
            )
        ]

    async def _select_best_plan(
        self,
        ctx: PlanningContext,
        plans: list[RefinedPlan],
    ) -> RefinedPlan:
        """Select the best plan using consensus voting or confidence scoring."""
        
        if not plans:
            raise ValueError("No plans generated")
        
        if len(plans) == 1:
            return plans[0]
        
        if self._consensus:
            # Consensus voting: select plan with highest agreement
            scored = []
            for plan in plans:
                score = await self._score_plan(ctx, plan)
                scored.append((plan, score))
            
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[0][0]
        else:
            # Simple confidence-based selection
            plans.sort(key=lambda p: p.total_confidence, reverse=True)
            return plans[0]

    async def _score_plan(
        self,
        ctx: PlanningContext,
        plan: RefinedPlan,
    ) -> float:
        """Score a plan based on multiple criteria."""
        
        scores = {
            "confidence": plan.total_confidence * 0.3,
            "complexity": (1.0 - len(plan.plan.steps) / 20.0) * 0.2,  # Prefer fewer steps
            "cost": (1.0 - min(plan.estimated_cost / 1.0, 1.0)) * 0.2,  # Prefer lower cost
            "risk": (1.0 - sum(plan.risk_assessment.values()) / len(plan.risk_assessment)) * 0.3,
        }
        
        return sum(scores.values())

    async def _generate_fallbacks(
        self,
        ctx: PlanningContext,
        primary: RefinedPlan,
    ) -> list[Plan]:
        """Generate fallback plans for risk mitigation."""
        
        fallbacks = []
        
        # Generate a simpler fallback
        if len(primary.plan.steps) > 2:
            simpler = Plan(
                goal=primary.plan.goal,
                steps=primary.plan.steps[:2],
                constraints=primary.plan.constraints,
                risk=primary.plan.risk,
                confidence=primary.total_confidence * 0.8,
            )
            fallbacks.append(simpler)
        
        # Generate a parallel fallback (if sequential)
        if primary.level == PlanLevel.OPERATIONAL:
            # Try to make steps more parallel
            parallel_steps = [
                PlanStep(
                    index=i,
                    intent=step.intent,
                    tool=step.tool,
                    operation=step.operation,
                    args=dict(step.args),
                )
                for i, step in enumerate(primary.plan.steps)
            ]
            parallel = Plan(
                goal=primary.plan.goal,
                steps=tuple(parallel_steps),
                constraints=primary.plan.constraints,
                risk=primary.plan.risk,
                confidence=primary.total_confidence * 0.7,
            )
            fallbacks.append(parallel)
        
        return fallbacks

    def _assess_risks(
        self,
        plan: Plan,
    ) -> dict[str, float]:
        """Assess risks for a plan."""
        
        risks = {}
        
        # Complexity risk
        risks["complexity"] = min(len(plan.steps) / 10.0, 1.0)
        
        # Dependency risk
        dep_count = sum(len(s.depends_on) for s in plan.steps)
        risks["dependencies"] = min(dep_count / 10.0, 1.0)
        
        # Tool availability risk
        risks["tool_availability"] = 0.1  # Low risk if tools are registered
        
        return risks

    def _strategic_system_prompt(self) -> str:
        return """You are a strategic planning AI. Your role is to:
1. Analyze complex tasks and identify high-level goals
2. Determine the best decomposition strategy
3. Assess risks and dependencies
4. Provide clear, actionable strategic direction

Focus on WHAT needs to be achieved, not HOW.
Think abstractly and identify key objectives."""

    def _tactical_system_prompt(self) -> str:
        return """You are a tactical planning AI. Your role is to:
1. Convert strategic goals into actionable steps
2. Select appropriate tools for each step
3. Determine step dependencies and ordering
4. Estimate effort and resources needed

Focus on HOW to achieve the goals.
Be specific and practical."""

    def _parse_json_response(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Parse JSON from LLM response with error handling."""
        
        try:
            # Find JSON boundaries
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end == 0:
                return {}
            return dict(json.loads(text[start:end]))
        except json.JSONDecodeError:
            return {}

    def update_statistics(
        self,
        strategy: DecompositionStrategy,
        success: bool,
        confidence: float,
    ) -> None:
        """Update planning statistics for adaptive improvement."""
        n_prev = int(self._stats.get("decompositions", 0))
        n = n_prev + 1
        self._stats["decompositions"] = n
        # Update running average confidence
        prev_conf = float(self._stats.get("avg_confidence", 0.0))
        self._stats["avg_confidence"] = (prev_conf * n_prev + confidence) / n
        # Update success rate by strategy
        rate_dict = self._stats["success_rate_by_strategy"]
        if isinstance(rate_dict, dict):
            current_rate = float(rate_dict.get(strategy.value, 0.5))
            rate_dict[strategy.value] = current_rate * 0.9 + (1.0 if success else 0.0) * 0.1
