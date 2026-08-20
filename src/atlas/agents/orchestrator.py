"""Unified Agent Orchestration Facade - The main entry point for advanced agentic capabilities.

This brings together all advanced agentic components into a cohesive system:
1. Hierarchical Planning
2. Meta-Learning & Adaptive Strategy Selection
3. Collaborative Multi-Agent Reasoning
4. Self-Reflection & Improvement
5. Dynamic Tool Orchestration
6. Uncertainty Quantification
7. Causal Reasoning
8. Knowledge Graph Memory Consolidation

Provides a unified interface for the ATLAS agentic AI system.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from atlas.agents.causal import (
    CausalExplanation,
    CausalReasoningEngine,
)
from atlas.agents.collaborative import (
    CollaborativeReasoner,
    CollaborativeResult,
)
from atlas.agents.hierarchical_planner import (
    HierarchicalPlanner,
    PlanningContext,
)
from atlas.agents.memory_graph import (
    MemoryGraphConsolidator,
)
from atlas.agents.meta_learning import (
    ExecutionTrace,
    MetaLearningEngine,
    StrategyType,
    TaskCategory,
)
from atlas.agents.reflection import (
    ReflectionEngine,
)
from atlas.agents.tool_orchestration import (
    DynamicToolOrchestrator,
)
from atlas.agents.uncertainty import (
    CalibratedPrediction,
    UncertaintyQuantifier,
)
from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.orchestrator")


class TaskComplexity(Enum):
    """Complexity levels for tasks."""
    SIMPLE = "simple"              # Single agent, direct execution
    MODERATE = "moderate"          # Some decomposition, few tools
    COMPLEX = "complex"            # Multi-level planning, multiple tools
    EXPERT = "expert"              # Full collaborative reasoning needed


@dataclass
class AgenticConfig:
    """Configuration for the agentic system."""
    # Planning
    max_planning_depth: int = 4
    enable_hierarchical_planning: bool = True
    
    # Meta-learning
    enable_meta_learning: bool = True
    min_samples_for_recommendation: int = 3
    
    # Collaborative reasoning
    enable_collaborative: bool = True
    max_debate_rounds: int = 3
    consensus_threshold: float = 0.75
    
    # Reflection
    enable_reflection: bool = True
    reflection_depth: int = 3
    
    # Tool orchestration
    enable_dynamic_tools: bool = True
    max_parallel_tools: int = 5
    
    # Uncertainty
    enable_uncertainty: bool = True
    ensemble_size: int = 3
    
    # Causal reasoning
    enable_causal: bool = True
    causal_discovery_threshold: float = 0.6
    
    # Memory graph
    enable_memory_graph: bool = True
    consolidation_interval_hours: int = 6
    
    # General
    auto_apply_learnings: bool = False
    verbose_logging: bool = True


@dataclass
class TaskContext:
    """Context for a task execution."""
    task_id: str
    correlation_id: str
    description: str
    objective: str
    constraints: dict[str, Any] = field(default_factory=dict)
    available_tools: list[str] = field(default_factory=list)
    context_data: dict[str, Any] = field(default_factory=dict)
    priority: int = 5  # 1-10
    time_limit: timedelta | None = None
    cost_limit: float | None = None


@dataclass
class AgenticResult:
    """Result of agentic execution."""
    task_id: str
    success: bool
    result: Any
    confidence: float
    uncertainty: float
    plan_used: str | None = None
    strategy_used: str | None = None
    reasoning_trace: list[dict[str, Any]] = field(default_factory=list)
    reflections: list[dict[str, Any]] = field(default_factory=list)
    improvements_identified: list[dict[str, Any]] = field(default_factory=list)
    causal_insights: list[dict[str, Any]] = field(default_factory=list)
    knowledge_updated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class AgenticOrchestrator:
    """Main orchestrator for advanced agentic AI capabilities.
    
    This is the unified entry point that coordinates all advanced components
    to provide world-class agentic AI functionality.
    """
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        ids: IdGenerator,
        clock: Clock,
        config: AgenticConfig | None = None,
    ) -> None:
        self._gw = gateway
        self._ids = ids
        self._clock = clock
        self._config = config or AgenticConfig()
        
        # Initialize all components
        self._hierarchical_planner = HierarchicalPlanner(
            gateway=gateway,
            ids=ids,
            clock=clock,
            max_depth=self._config.max_planning_depth,
        )
        
        self._meta_learning = MetaLearningEngine(
            gateway=gateway,
            ids=ids,
            clock=clock,
            min_samples_for_recommendation=self._config.min_samples_for_recommendation,
        )
        
        self._collaborative_reasoner = CollaborativeReasoner(
            gateway=gateway,
            ids=ids,
            clock=clock,
            max_debate_rounds=self._config.max_debate_rounds,
            consensus_threshold=self._config.consensus_threshold,
        )
        
        self._reflection_engine = ReflectionEngine(
            gateway=gateway,
            ids=ids,
            clock=clock,
            reflection_depth=self._config.reflection_depth,
            auto_apply_learnings=self._config.auto_apply_learnings,
        )
        
        # Tool orchestrator needs registry - will be set later
        self._tool_orchestrator: DynamicToolOrchestrator | None = None
        
        self._uncertainty_quantifier = UncertaintyQuantifier(
            gateway=gateway,
            ids=ids,
            clock=clock,
            ensemble_size=self._config.ensemble_size,
        )
        
        self._causal_reasoner = CausalReasoningEngine(
            gateway=gateway,
            ids=ids,
            clock=clock,
            discovery_threshold=self._config.causal_discovery_threshold,
        )
        
        self._memory_graph = MemoryGraphConsolidator(
            gateway=gateway,
            ids=ids,
            clock=clock,
            consolidation_interval_hours=self._config.consolidation_interval_hours,
        )
        
        # Execution statistics
        self._stats = {
            "tasks_executed": 0,
            "successful_tasks": 0,
            "total_latency_ms": 0,
            "total_cost_usd": 0.0,
            "avg_confidence": 0.0,
        }
        
        _log.info(
            "agentic_orchestrator.initialized",
            event_type="lifecycle",
            config=self._config.__dict__,
        )

    def set_tool_registry(self, registry: Any) -> None:
        """Set the tool registry for dynamic tool orchestration."""
        
        self._tool_orchestrator = DynamicToolOrchestrator(
            gateway=self._gw,
            registry=registry,
            ids=self._ids,
            clock=self._clock,
            max_parallel_tools=self._config.max_parallel_tools,
        )

    async def execute_task(
        self,
        ctx: TaskContext,
    ) -> AgenticResult:
        """Execute a task using the full agentic pipeline.
        
        This is the main entry point that orchestrates all advanced capabilities.
        """
        
        start_time = self._clock.now()
        
        _log.info(
            "agentic.task_started",
            event_type="execution",
            task_id=ctx.task_id,
            objective=ctx.objective[:100],
        )
        
        try:
            # Step 1: Classify task and recommend strategy
            category = await self._meta_learning.classify_task(ctx.description)
            strategy, strategy_confidence = await self._meta_learning.recommend_strategy(
                ctx.description,
                ctx.constraints,
            )
            
            _log.info(
                "agentic.strategy_selected",
                event_type="execution",
                task_id=ctx.task_id,
                category=category.value,
                strategy=strategy.value,
                confidence=strategy_confidence,
            )
            
            # Step 2: Hierarchical planning (if enabled and complex enough)
            plan = None
            if self._config.enable_hierarchical_planning and strategy != StrategyType.SINGLE_AGENT:
                planning_ctx = PlanningContext(
                    task_id=ctx.task_id,
                    correlation_id=ctx.correlation_id,
                    objective=ctx.objective,
                    constraints=ctx.constraints,
                    available_tools=ctx.available_tools,
                    complexity_threshold=0.7,
                )
                plan_result = await self._hierarchical_planner.plan(planning_ctx)
                plan = plan_result.plan
            
            # Step 3: Collaborative reasoning (if enabled and complex)
            collaborative_result: CollaborativeResult | None = None
            if self._config.enable_collaborative and self._is_complex_task(ctx):
                collaborative_result = await self._collaborative_reasoner.reason_collaboratively(
                    task=ctx.objective,
                    context=json.dumps(ctx.context_data),
                )
            
            # Step 4: Causal reasoning for complex decisions
            causal_insights = []
            if self._config.enable_causal:
                causal_insights = await self._perform_causal_analysis(ctx, plan)
            
            # Step 5: Tool orchestration for execution
            execution_result = await self._execute_with_tools(
                ctx,
                plan,
                collaborative_result,
            )
            
            # Step 6: Uncertainty quantification
            uncertainty_result = await self._uncertainty_quantifier.calibrated_prediction(
                prompt=f"Assess the outcome: {execution_result}",
                context=ctx.objective,
            )
            
            # Step 7: Self-reflection
            reflection_result = None
            if self._config.enable_reflection:
                reflection_result = await self._reflection_engine.reflect_on_execution(
                    task_id=ctx.task_id,
                    task_description=ctx.description,
                    execution_trace={
                        "plan": str(plan),
                        "strategy": strategy.value,
                        "execution": str(execution_result),
                    },
                    outcome="success" if execution_result else "failure",
                )
            
            # Step 8: Record execution for meta-learning
            await self._record_execution_trace(
                ctx=ctx,
                category=category,
                strategy=strategy,
                execution_result=execution_result,
                uncertainty_result=uncertainty_result,
            )
            
            # Step 9: Memory graph consolidation
            knowledge_updated = False
            if self._config.enable_memory_graph:
                knowledge_updated = await self._update_memory_graph(ctx, execution_result)
            
            # Compile final result
            result = AgenticResult(
                task_id=ctx.task_id,
                success=execution_result is not None,
                result=execution_result,
                confidence=uncertainty_result.calibrated_confidence,
                uncertainty=uncertainty_result.uncertainty.total_uncertainty,
                plan_used=str(plan) if plan else None,
                strategy_used=strategy.value,
                reasoning_trace=self._compile_reasoning_trace(
                    plan,
                    collaborative_result,
                    causal_insights,
                ),
                reflections=[reflection_result.__dict__] if reflection_result else [],
                improvements_identified=[
                    vars(imp)
                    for imp in await self._reflection_engine.get_improvement_priorities(5)
                ],
                causal_insights=causal_insights,
                knowledge_updated=knowledge_updated,
                metadata={
                    "category": category.value,
                    "strategy_confidence": strategy_confidence,
                    "collaborative_rounds": collaborative_result.debate_rounds if collaborative_result else 0,
                },
            )
            
            # Update statistics
            latency_ms = int((self._clock.now() - start_time).total_seconds() * 1000)
            self._stats["tasks_executed"] += 1
            if result.success:
                self._stats["successful_tasks"] += 1
            self._stats["total_latency_ms"] += latency_ms
            n = self._stats["tasks_executed"]
            self._stats["avg_confidence"] = (
                self._stats["avg_confidence"] * (n - 1) + result.confidence
            ) / n
            
            _log.info(
                "agentic.task_completed",
                event_type="execution",
                task_id=ctx.task_id,
                success=result.success,
                confidence=result.confidence,
                latency_ms=latency_ms,
            )
            
            return result
        
        except Exception as e:
            _log.error(
                "agentic.task_failed",
                event_type="execution",
                task_id=ctx.task_id,
                error=str(e),
            )
            
            # Record failure for learning
            await self._meta_learning.record_execution(
                ExecutionTrace(
                    trace_id=self._ids.execution_id(),
                    task_id=ctx.task_id,
                    task_description=ctx.description,
                    task_category=await self._meta_learning.classify_task(ctx.description),
                    strategy_used=StrategyType.SINGLE_AGENT,
                    success=False,
                    confidence=0.0,
                    latency_ms=0,
                    cost_usd=0.0,
                    steps_taken=0,
                    tool_calls=0,
                    model_calls=0,
                    replan_count=0,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    user_feedback=None,
                    timestamp=datetime.now(UTC),
                )
            )
            
            return AgenticResult(
                task_id=ctx.task_id,
                success=False,
                result=None,
                confidence=0.0,
                uncertainty=1.0,
                metadata={"error": str(e)},
            )

    async def _is_complex_task(self, ctx: TaskContext) -> bool:
        """Determine if a task requires collaborative reasoning."""
        
        # Complex if multiple constraints, many tools, or high priority
        return bool(
            len(ctx.constraints) > 2
            or len(ctx.available_tools) > 3
            or ctx.priority >= 8
            or (ctx.time_limit is not None and ctx.time_limit > timedelta(minutes=30))
        )

    async def _perform_causal_analysis(
        self,
        ctx: TaskContext,
        plan: Any,
    ) -> list[dict[str, Any]]:
        """Perform causal analysis on the task."""
        
        # Build causal model from context
        observations = [ctx.context_data]
        
        try:
            graph = await self._causal_reasoner.build_causal_model(observations, ctx.objective)
            
            # Get causal explanation for the objective
            explanation = await self._causal_reasoner.explain_event(
                graph=graph,
                event=ctx.objective,
                context=ctx.context_data,
            )
            
            # Suggest interventions
            interventions = await self._causal_reasoner.suggest_interventions(
                graph=graph,
                desired_outcome=ctx.objective,
                current_state=ctx.context_data,
            )
            
            return [
                {
                    "type": "explanation",
                    "event": explanation.event,
                    "causes": explanation.causes,
                    "mechanism": explanation.mechanism,
                    "confidence": explanation.confidence,
                },
                {
                    "type": "interventions",
                    "interventions": [
                        {"target": i.target_variable, "value": i.intervention_value, "confidence": c}
                        for i, c in interventions
                    ],
                },
            ]
        except Exception as e:
            _log.warning(
                "agentic.causal_analysis_failed",
                event_type="execution",
                task_id=ctx.task_id,
                error=str(e),
            )
            return []

    async def _execute_with_tools(
        self,
        ctx: TaskContext,
        plan: Any,
        collaborative_result: CollaborativeResult | None,
    ) -> Any:
        """Execute the task using tool orchestration."""
        
        if not self._tool_orchestrator or not self._config.enable_dynamic_tools:
            # Simple execution without orchestration
            return await self._simple_execution(ctx, collaborative_result)
        
        # Create execution plan
        exec_plan = await self._tool_orchestrator.orchestrate(
            task_description=ctx.objective,
            available_tools=ctx.available_tools,
            constraints=ctx.constraints,
        )
        
        # Execute plan
        results = await self._tool_orchestrator.execute_plan(
            exec_plan,
            ctx.context_data,
        )
        
        # Aggregate results
        return self._aggregate_results(results)

    async def _simple_execution(
        self,
        ctx: TaskContext,
        collaborative_result: CollaborativeResult | None,
    ) -> Any:
        """Simple execution using LLM directly."""
        
        # Use collaborative result if available, otherwise direct LLM
        if collaborative_result:
            prompt = collaborative_result.final_solution
        else:
            prompt = f"Execute this task: {ctx.objective}\nContext: {json.dumps(ctx.context_data)}"
        
        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are an expert agent executing tasks."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=2000,
                temperature=0.5,
            )
        )
        
        return resp.text

    def _aggregate_results(
        self,
        results: list[Any],
    ) -> Any:
        """Aggregate tool execution results."""
        
        if not results:
            return None
        
        successful = [r for r in results if r.success]
        
        if not successful:
            return {"error": "All tool executions failed", "details": [r.error for r in results]}
        
        # Combine outputs
        outputs = {}
        for r in successful:
            if isinstance(r.output, dict):
                outputs.update(r.output)
            else:
                outputs[r.requirement_id] = r.output
        
        return {
            "results": outputs,
            "summary": f"{len(successful)}/{len(results)} tools succeeded",
        }

    async def _record_execution_trace(
        self,
        ctx: TaskContext,
        category: TaskCategory,
        strategy: StrategyType,
        execution_result: Any,
        uncertainty_result: CalibratedPrediction,
    ) -> None:
        """Record execution trace for meta-learning."""
        
        trace = ExecutionTrace(
            trace_id=self._ids.execution_id(),
            task_id=ctx.task_id,
            task_description=ctx.description,
            task_category=category,
            strategy_used=strategy,
            success=execution_result is not None and not (
                isinstance(execution_result, dict) and execution_result.get("error")
            ),
            confidence=uncertainty_result.calibrated_confidence,
            latency_ms=0,  # Would need actual timing
            cost_usd=0.0,
            steps_taken=1,
            tool_calls=0,
            model_calls=1,
            replan_count=0,
            error_type=None if execution_result else "execution_failed",
            error_message=str(execution_result.get("error")) if isinstance(execution_result, dict) else None,
            user_feedback=None,
            timestamp=datetime.now(UTC),
        )
        
        await self._meta_learning.record_execution(trace)

    async def _update_memory_graph(
        self,
        ctx: TaskContext,
        execution_result: Any,
    ) -> bool:
        """Update memory graph with execution results."""
        
        episode = {
            "id": ctx.task_id,
            "content": f"Task: {ctx.objective}\nResult: {execution_result}",
            "ts": datetime.now(UTC).isoformat(),
        }
        
        await self._memory_graph.consolidate_episodes([episode])
        return True

    def _compile_reasoning_trace(
        self,
        plan: Any,
        collaborative_result: CollaborativeResult | None,
        causal_insights: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Compile reasoning trace from all components."""
        
        trace = []
        
        if plan:
            trace.append({
                "step": "planning",
                "details": f"Hierarchical plan with {len(plan.steps)} steps",
                "confidence": getattr(plan, 'confidence', 0.0),
            })
        
        if collaborative_result:
            trace.append({
                "step": "collaborative_reasoning",
                "details": f"Debate rounds: {collaborative_result.debate_rounds}",
                "consensus": collaborative_result.consensus_achieved,
            })
        
        for insight in causal_insights:
            trace.append({
                "step": "causal_analysis",
                "type": insight["type"],
                "details": insight,
            })
        
        return trace

    # Convenience methods for specific capabilities

    async def plan_task(
        self,
        objective: str,
        constraints: dict[str, Any] | None = None,
        available_tools: list[str] | None = None,
    ) -> Any:
        """Create a plan for a task."""
        
        planning_ctx = PlanningContext(
            task_id=self._ids.execution_id(),
            correlation_id=self._ids.correlation_id(),
            objective=objective,
            constraints=constraints or {},
            available_tools=available_tools or [],
        )
        
        return await self._hierarchical_planner.plan(planning_ctx)

    async def reason_collaboratively(
        self,
        task: str,
        context: str = "",
    ) -> CollaborativeResult:
        """Perform collaborative reasoning."""
        
        return await self._collaborative_reasoner.reason_collaboratively(task, context)

    async def reflect(
        self,
        task_id: str,
        task_description: str,
        execution_trace: dict[str, Any],
        outcome: str,
    ) -> Any:
        """Perform self-reflection."""
        
        return await self._reflection_engine.reflect_on_execution(
            task_id, task_description, execution_trace, outcome
        )

    async def quantify_uncertainty(
        self,
        prediction: str,
        context: str,
        confidence: float,
    ) -> Any:
        """Quantify uncertainty."""
        
        return await self._uncertainty_quantifier.quantify_uncertainty(
            prediction, context, confidence
        )

    async def causal_analysis(
        self,
        event: str,
        context: dict[str, Any],
    ) -> CausalExplanation:
        """Perform causal analysis."""
        
        graph = await self._causal_reasoner.build_causal_model(
            [context],
            event,
        )
        
        return await self._causal_reasoner.explain_event(graph, event, context)

    async def query_knowledge(
        self,
        query: str,
    ) -> list[dict[str, Any]]:
        """Query the knowledge graph."""
        
        return await self._memory_graph.query_knowledge(query)

    def get_statistics(self) -> dict[str, Any]:
        """Get comprehensive statistics from all components."""
        
        return {
            "orchestrator": self._stats,
            "hierarchical_planner": self._hierarchical_planner._stats,
            "meta_learning": self._meta_learning.get_statistics(),
            "collaborative_reasoner": {
                "total_collaborations": len(self._collaborative_reasoner.__dict__),
            },
            "reflection": self._reflection_engine.get_statistics(),
            "tool_orchestration": self._tool_orchestrator.get_statistics() if self._tool_orchestrator else {},
            "uncertainty": self._uncertainty_quantifier.get_statistics(),
            "causal": self._causal_reasoner.get_statistics(),
            "memory_graph": self._memory_graph.get_statistics(),
        }