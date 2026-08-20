"""Dynamic Tool Orchestration - Intelligent tool discovery, composition, and optimization.

This implements advanced tool orchestration inspired by:
- Task decomposition with tool requirements
- Dynamic tool discovery and binding
- Tool composition and chaining
- Parallel vs sequential optimization
- Resource-aware scheduling

Key features:
1. Automatic tool discovery from task requirements
2. Tool compatibility checking and composition
3. Optimal execution ordering (parallel vs sequential)
4. Resource-aware scheduling (time, cost, dependencies)
5. Tool health monitoring and fallback selection
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.registry import ToolRegistry

_log = get_logger("atlas.agents.tool_orchestration")


class ExecutionMode(Enum):
    """Tool execution modes."""
    SEQUENTIAL = "sequential"          # One after another
    PARALLEL = "parallel"               # All at once
    PIPELINE = "pipeline"              # Output feeds next input
    CONDITIONAL = "conditional"        # Based on conditions
    RETRY_WITH_FALLBACK = "fallback"   # Try primary, fallback on failure


class ToolCategory(Enum):
    """Categories of tools."""
    FILESYSTEM = "filesystem"
    NETWORK = "network"
    COMPUTATION = "computation"
    COMMUNICATION = "communication"
    ANALYSIS = "analysis"
    TRANSFORMATION = "transformation"
    STORAGE = "storage"
    MONITORING = "monitoring"


@dataclass
class ToolRequirement:
    """A requirement for a tool to accomplish a subtask."""
    requirement_id: str
    description: str
    category: ToolCategory | None
    capabilities: list[str]
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolBinding:
    """A binding between a requirement and a specific tool."""
    requirement_id: str
    tool_name: str
    operation: str
    args_mapping: dict[str, str]  # requirement input -> tool arg
    confidence: float
    estimated_duration_ms: int
    estimated_cost_usd: float
    prerequisites: list[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """A plan for executing multiple tools."""
    plan_id: str
    bindings: list[ToolBinding]
    execution_mode: ExecutionMode
    parallel_groups: list[list[str]]  # Groups of requirement IDs that can run in parallel
    total_estimated_duration_ms: int
    total_estimated_cost_usd: float
    confidence: float
    fallback_plans: list[ExecutionPlan] = field(default_factory=list)


@dataclass
class ToolExecutionResult:
    """Result from executing a tool."""
    requirement_id: str
    tool_name: str
    success: bool
    output: Any
    error: str | None
    duration_ms: int
    cost_usd: float


class DynamicToolOrchestrator:
    """Advanced dynamic tool orchestration system."""
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        registry: ToolRegistry,
        ids: IdGenerator,
        clock: Clock,
        max_parallel_tools: int = 5,
        cost_optimization: bool = True,
        time_optimization: bool = True,
    ) -> None:
        self._gw = gateway
        self._registry = registry
        self._ids = ids
        self._clock = clock
        self._max_parallel = max_parallel_tools
        self._cost_opt = cost_optimization
        self._time_opt = time_optimization
        
        # Tool health tracking
        self._tool_health: dict[str, float] = {}
        
        # Execution history for learning
        self._execution_history: dict[str, list[ToolExecutionResult]] = {}
        
        # Statistics
        self._stats = {
            "total_orchestrations": 0,
            "successful_executions": 0,
            "parallel_executions": 0,
            "fallback_invocations": 0,
            "avg_duration_ms": 0.0,
            "avg_cost_usd": 0.0,
        }

    async def orchestrate(
        self,
        task_description: str,
        available_tools: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """Create an optimal execution plan for a task.
        
        Process:
        1. Analyze task to identify tool requirements
        2. Discover and bind tools to requirements
        3. Optimize execution order (parallel vs sequential)
        4. Generate fallback plans
        5. Return executable plan
        """
        
        _log.info(
            "tool_orchestration.started",
            event_type="orchestration",
            task=task_description[:100],
        )
        
        # Step 1: Identify requirements
        requirements = await self._identify_requirements(task_description)
        
        # Step 2: Bind tools to requirements
        bindings = await self._bind_tools(
            requirements,
            available_tools or list(self._registry.registered().keys()),
            constraints or {},
        )
        
        # Step 3: Determine execution mode and ordering
        execution_mode, parallel_groups = self._determine_execution_mode(
            requirements,
            bindings,
            constraints or {},
        )
        
        # Step 4: Calculate estimates
        total_duration, total_cost, confidence = self._calculate_estimates(bindings)
        
        # Step 5: Generate fallback plans
        fallbacks = await self._generate_fallback_plans(
            task_description,
            requirements,
            bindings,
            constraints or {},
        )
        
        plan = ExecutionPlan(
            plan_id=self._ids.execution_id(),
            bindings=bindings,
            execution_mode=execution_mode,
            parallel_groups=parallel_groups,
            total_estimated_duration_ms=total_duration,
            total_estimated_cost_usd=total_cost,
            confidence=confidence,
            fallback_plans=fallbacks,
        )
        
        self._stats["total_orchestrations"] += 1
        
        _log.info(
            "tool_orchestration.plan_created",
            event_type="orchestration",
            plan_id=plan.plan_id,
            mode=execution_mode.value,
            tools=len(bindings),
            duration_ms=total_duration,
        )
        
        return plan

    async def execute_plan(
        self,
        plan: ExecutionPlan,
        context: dict[str, Any],
    ) -> list[ToolExecutionResult]:
        """Execute a plan with the specified context."""
        
        results: list[ToolExecutionResult] = []

        if plan.execution_mode == ExecutionMode.PARALLEL:
            results = await self._execute_parallel(plan, context)
            self._stats["parallel_executions"] += 1
        elif plan.execution_mode == ExecutionMode.PIPELINE:
            results = await self._execute_pipeline(plan, context)
        else:
            results = await self._execute_sequential(plan, context)
        
        # Check if we need fallback
        failures = [r for r in results if not r.success]
        
        if failures and plan.fallback_plans:
            _log.warning(
                "tool_orchestration.fallback_triggered",
                event_type="orchestration",
                plan_id=plan.plan_id,
                failures=len(failures),
            )
            self._stats["fallback_invocations"] += 1
            
            # Try fallback plan
            fallback_results = await self.execute_plan(
                plan.fallback_plans[0],
                context,
            )
            results.extend(fallback_results)
        
        # Update statistics
        success_count = sum(1 for r in results if r.success)
        if success_count == len(results):
            self._stats["successful_executions"] += 1
        
        total_duration = sum(r.duration_ms for r in results)
        total_cost = sum(r.cost_usd for r in results)
        n = self._stats["total_orchestrations"]
        self._stats["avg_duration_ms"] = (
            self._stats["avg_duration_ms"] * (n - 1) + total_duration
        ) / n
        self._stats["avg_cost_usd"] = (
            self._stats["avg_cost_usd"] * (n - 1) + total_cost
        ) / n
        
        return results

    async def _identify_requirements(
        self,
        task_description: str,
    ) -> list[ToolRequirement]:
        """Identify tool requirements from task description."""
        
        prompt = f"""Analyze this task and identify the tool requirements:

TASK: {task_description}

AVAILABLE TOOL CATEGORIES:
{chr(10).join(f'- {c.value}' for c in ToolCategory)}

For each requirement, specify:
1. What needs to be done
2. Required capabilities
3. Expected inputs and outputs
4. Any constraints

Output JSON:
{{
  "requirements": [
    {{
      "description": "what to do",
      "category": "category_name",
      "capabilities": ["cap1", "cap2"],
      "input_type": "description of input",
      "output_type": "description of output",
      "constraints": {{"key": "value"}}
    }}
  ]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a tool requirement analyst."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=1500,
                temperature=0.3,
            )
        )
        
        data = self._parse_json(resp.text)
        
        requirements = []
        for idx, req_data in enumerate(data.get("requirements", [])):
            try:
                category = ToolCategory(req_data.get("category", "computation"))
            except ValueError:
                category = None
            
            requirement = ToolRequirement(
                requirement_id=f"req_{idx}",
                description=req_data.get("description", ""),
                category=category,
                capabilities=req_data.get("capabilities", []),
                input_schema={"type": req_data.get("input_type", "any")},
                output_schema={"type": req_data.get("output_type", "any")},
                constraints=req_data.get("constraints", {}),
            )
            requirements.append(requirement)
        
        return requirements

    async def _bind_tools(
        self,
        requirements: list[ToolRequirement],
        available_tools: list[str],
        constraints: dict[str, Any],
    ) -> list[ToolBinding]:
        """Bind each requirement to a specific tool."""
        
        bindings = []
        
        for req in requirements:
            binding = await self._bind_single_requirement(
                req,
                available_tools,
                constraints,
            )
            bindings.append(binding)
        
        return bindings

    async def _bind_single_requirement(
        self,
        requirement: ToolRequirement,
        available_tools: list[str],
        constraints: dict[str, Any],
    ) -> ToolBinding:
        """Bind a single requirement to the best matching tool."""
        
        # Get tool catalog
        # catalog() returns a formatted string summary; list tools directly
        
        prompt = f"""Select the best tool for this requirement:

REQUIREMENT: {requirement.description}
CAPABILITIES NEEDED: {', '.join(requirement.capabilities)}
CATEGORY: {requirement.category.value if requirement.category else 'any'}

AVAILABLE TOOLS:
{chr(10).join(f'- {name}' for name in available_tools[:20])}

Select the best tool and specify how to use it.

Output JSON:
{{
  "tool": "tool_name",
  "operation": "operation_name",
  "args_mapping": {{"arg_name": "source_field"}},
  "confidence": 0.0-1.0,
  "estimated_duration_ms": 1000,
  "estimated_cost_usd": 0.01
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
        
        data = self._parse_json(resp.text)
        
        return ToolBinding(
            requirement_id=requirement.requirement_id,
            tool_name=data.get("tool", available_tools[0] if available_tools else ""),
            operation=data.get("operation", ""),
            args_mapping=data.get("args_mapping", {}),
            confidence=data.get("confidence", 0.5),
            estimated_duration_ms=data.get("estimated_duration_ms", 1000),
            estimated_cost_usd=data.get("estimated_cost_usd", 0.01),
        )

    def _determine_execution_mode(
        self,
        requirements: list[ToolRequirement],
        bindings: list[ToolBinding],
        constraints: dict[str, Any],
    ) -> tuple[ExecutionMode, list[list[str]]]:
        """Determine optimal execution mode and grouping."""
        
        # Build dependency graph
        dependencies: dict[str, list[str]] = {}
        for binding in bindings:
            dependencies[binding.requirement_id] = binding.prerequisites
        
        # Find parallel groups using topological sort
        parallel_groups: list[list[str]] = []
        remaining = set(b.requirement_id for b in bindings)
        completed: set[str] = set()
        
        while remaining:
            # Find all requirements with no pending dependencies
            ready = []
            for req_id in remaining:
                prereqs = dependencies.get(req_id, [])
                if all(p in completed for p in prereqs):
                    ready.append(req_id)
            
            if not ready:
                # Circular dependency - just add remaining
                ready = list(remaining)
            
            # Limit parallelism
            if len(ready) > self._max_parallel:
                ready = ready[:self._max_parallel]
            
            parallel_groups.append(ready)
            completed.update(ready)
            remaining -= set(ready)
        
        # Determine mode
        if len(parallel_groups) == 1 and len(parallel_groups[0]) > 1:
            mode = ExecutionMode.PARALLEL
        elif len(parallel_groups) > 1 and any(len(g) > 1 for g in parallel_groups):
            mode = ExecutionMode.PARALLEL  # Mixed parallel/sequential
        elif len(parallel_groups) == len(bindings):
            mode = ExecutionMode.PIPELINE
        else:
            mode = ExecutionMode.SEQUENTIAL
        
        return mode, parallel_groups

    def _calculate_estimates(
        self,
        bindings: list[ToolBinding],
    ) -> tuple[int, float, float]:
        """Calculate total duration, cost, and confidence."""
        
        # Use health-adjusted estimates
        total_duration = 0
        total_cost = 0.0
        total_confidence = 1.0
        
        for binding in bindings:
            health = self._tool_health.get(binding.tool_name, 1.0)
            total_duration += binding.estimated_duration_ms
            total_cost += binding.estimated_cost_usd
            total_confidence *= binding.confidence * health
        
        return total_duration, total_cost, total_confidence

    async def _generate_fallback_plans(
        self,
        task_description: str,
        requirements: list[ToolRequirement],
        bindings: list[ToolBinding],
        constraints: dict[str, Any],
    ) -> list[ExecutionPlan]:
        """Generate alternative fallback plans."""
        
        fallbacks = []
        
        # Strategy 1: Use different tools if available
        alternative_bindings = await self._find_alternative_bindings(
            requirements,
            bindings,
            constraints,
        )
        
        if alternative_bindings:
            mode, groups = self._determine_execution_mode(
                requirements,
                alternative_bindings,
                constraints,
            )
            duration, cost, conf = self._calculate_estimates(alternative_bindings)
            
            fallback = ExecutionPlan(
                plan_id=self._ids.execution_id(),
                bindings=alternative_bindings,
                execution_mode=mode,
                parallel_groups=groups,
                total_estimated_duration_ms=duration,
                total_estimated_cost_usd=cost,
                confidence=conf,
            )
            fallbacks.append(fallback)
        
        # Strategy 2: Simpler sequential execution
        sequential = ExecutionPlan(
            plan_id=self._ids.execution_id(),
            bindings=bindings,
            execution_mode=ExecutionMode.SEQUENTIAL,
            parallel_groups=[[b.requirement_id] for b in bindings],
            total_estimated_duration_ms=sum(b.estimated_duration_ms for b in bindings),
            total_estimated_cost_usd=sum(b.estimated_cost_usd for b in bindings),
            confidence=min(b.confidence for b in bindings) * 0.9,
        )
        fallbacks.append(sequential)
        
        return fallbacks

    async def _find_alternative_bindings(
        self,
        requirements: list[ToolRequirement],
        original_bindings: list[ToolBinding],
        constraints: dict[str, Any],
    ) -> list[ToolBinding] | None:
        """Find alternative tool bindings."""
        
        alternatives = []
        
        for orig in original_bindings:
            # Try to find a different tool
            other_tools = [
                t for t in self._registry.registered().keys()
                if t != orig.tool_name
            ]
            
            if other_tools:
                req = next(
                    (r for r in requirements if r.requirement_id == orig.requirement_id),
                    None,
                )
                if req:
                    alt_binding = await self._bind_single_requirement(
                        req,
                        other_tools,
                        constraints,
                    )
                    if alt_binding.tool_name != orig.tool_name:
                        alternatives.append(alt_binding)
                        continue
            
            alternatives.append(orig)
        
        if alternatives == original_bindings:
            return None
        
        return alternatives

    async def _execute_parallel(
        self,
        plan: ExecutionPlan,
        context: dict[str, Any],
    ) -> list[ToolExecutionResult]:
        """Execute tools in parallel."""
        
        all_results = []
        
        for group in plan.parallel_groups:
            # Execute this group in parallel
            tasks = []
            for binding in plan.bindings:
                if binding.requirement_id in group:
                    tasks.append(self._execute_single_tool(binding, context))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, BaseException):
                    # Convert exception to result
                    all_results.append(ToolExecutionResult(
                        requirement_id="unknown",
                        tool_name="unknown",
                        success=False,
                        output=None,
                        error=str(result),
                        duration_ms=0,
                        cost_usd=0.0,
                    ))
                elif isinstance(result, ToolExecutionResult):
                    all_results.append(result)
        
        return all_results

    async def _execute_sequential(
        self,
        plan: ExecutionPlan,
        context: dict[str, Any],
    ) -> list[ToolExecutionResult]:
        """Execute tools sequentially."""
        
        results = []
        
        for binding in plan.bindings:
            result = await self._execute_single_tool(binding, context)
            results.append(result)
            
            # Update context with output
            if result.success:
                context[result.requirement_id] = result.output
            
            # Stop on failure if not using fallbacks
            if not result.success and not plan.fallback_plans:
                break
        
        return results

    async def _execute_pipeline(
        self,
        plan: ExecutionPlan,
        context: dict[str, Any],
    ) -> list[ToolExecutionResult]:
        """Execute tools as a pipeline (output feeds next input)."""
        
        results = []
        current_input = context
        
        for binding in plan.bindings:
            result = await self._execute_single_tool(binding, current_input)
            results.append(result)
            
            if result.success:
                # Feed output to next stage
                current_input = {**current_input, **result.output}
            else:
                break
        
        return results

    async def _execute_single_tool(
        self,
        binding: ToolBinding,
        context: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute a single tool."""
        
        import time
        start = time.perf_counter()
        
        try:
            tool = self._registry.get(binding.tool_name)
            if not tool:
                return ToolExecutionResult(
                    requirement_id=binding.requirement_id,
                    tool_name=binding.tool_name,
                    success=False,
                    output=None,
                    error=f"Tool {binding.tool_name} not found",
                    duration_ms=0,
                    cost_usd=0.0,
                )
            
            # Prepare args
            args = {}
            for arg_name, source_field in binding.args_mapping.items():
                args[arg_name] = context.get(source_field)
            
            # Execute (Tool.execute is always async per the base protocol)
            output = await tool.execute(args)
            
            duration_ms = int((time.perf_counter() - start) * 1000)
            
            return ToolExecutionResult(
                requirement_id=binding.requirement_id,
                tool_name=binding.tool_name,
                success=True,
                output=output,
                error=None,
                duration_ms=duration_ms,
                cost_usd=binding.estimated_cost_usd,
            )
        
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            
            return ToolExecutionResult(
                requirement_id=binding.requirement_id,
                tool_name=binding.tool_name,
                success=False,
                output=None,
                error=str(e),
                duration_ms=duration_ms,
                cost_usd=0.0,
            )

    def update_tool_health(
        self,
        tool_name: str,
        success: bool,
    ) -> None:
        """Update tool health based on execution result."""
        
        current = self._tool_health.get(tool_name, 1.0)
        
        # Exponential moving average
        alpha = 0.1
        if success:
            self._tool_health[tool_name] = current * (1 - alpha) + 1.0 * alpha
        else:
            self._tool_health[tool_name] = current * (1 - alpha) + 0.0 * alpha

    def _parse_json(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Parse JSON from text."""
        
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end == 0:
                return {}
            return dict(json.loads(text[start:end]))
        except json.JSONDecodeError:
            return {}

    def get_statistics(self) -> dict[str, Any]:
        """Get orchestration statistics."""
        
        return {
            **self._stats,
            "tool_health": dict(self._tool_health),
        }
