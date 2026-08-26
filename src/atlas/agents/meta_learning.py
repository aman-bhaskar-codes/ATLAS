"""Meta-Learning Engine - Adaptive strategy selection and continuous improvement.

This implements state-of-the-art meta-learning for agentic systems:
1. Task type classification and strategy recommendation
2. Performance tracking and adaptive selection
3. Experience distillation into reusable patterns
4. Few-shot learning from successful/failed executions
5. Dynamic capability discovery and optimization
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.meta_learning")


class TaskCategory(Enum):
    """Categories of tasks for meta-learning."""

    RESEARCH = "research"  # Information gathering
    ANALYSIS = "analysis"  # Data analysis and insights
    WRITING = "writing"  # Content generation
    CODING = "coding"  # Programming tasks
    PLANNING = "planning"  # Strategic planning
    EXECUTION = "execution"  # Action execution
    COMMUNICATION = "communication"  # Interaction with users/systems
    REASONING = "reasoning"  # Logical deduction
    CREATIVE = "creative"  # Creative generation
    OPTIMIZATION = "optimization"  # Performance tuning
    MONITORING = "monitoring"  # Observation and tracking
    DEBUGGING = "debugging"  # Problem diagnosis


class StrategyType(Enum):
    """Types of execution strategies."""

    SINGLE_AGENT = "single_agent"  # One agent handles everything
    SEQUENTIAL_DECOMPOSITION = "sequential"  # Break into sequential steps
    PARALLEL_DECOMPOSITION = "parallel"  # Break into parallel tasks
    HIERARCHICAL = "hierarchical"  # Multi-level decomposition
    COLLABORATIVE = "collaborative"  # Multiple agents work together
    ITERATIVE_REFINEMENT = "iterative"  # Repeated improvement
    EXPLORATORY = "exploratory"  # Explore multiple approaches
    CONSERVATIVE = "conservative"  # Low-risk approach


@dataclass
class TaskFeatures:
    """Features extracted from a task for classification."""

    task_id: str
    description: str
    length: int
    has_code: bool
    has_numbers: bool
    has_dates: bool
    has_urls: bool
    has_file_paths: bool
    keyword_counts: dict[str, int]
    entity_types: list[str]
    complexity_score: float
    ambiguity_score: float
    time_sensitivity: float
    risk_level: float


@dataclass
class ExecutionTrace:
    """Record of a task execution for learning."""

    trace_id: str
    task_id: str
    task_description: str
    task_category: TaskCategory
    strategy_used: StrategyType
    success: bool
    confidence: float
    latency_ms: int
    cost_usd: float
    steps_taken: int
    tool_calls: int
    model_calls: int
    replan_count: int
    error_type: str | None
    error_message: str | None
    user_feedback: int | None  # -1, 0, 1
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyPerformance:
    """Performance metrics for a strategy on a task category."""

    strategy: StrategyType
    category: TaskCategory
    total_executions: int = 0
    successes: int = 0
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    total_confidence: float = 0.0
    avg_user_feedback: float = 0.0
    last_updated: datetime | None = None

    @property
    def success_rate(self) -> float:
        return self.successes / max(1, self.total_executions)

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(1, self.total_executions)

    @property
    def avg_cost_usd(self) -> float:
        return self.total_cost_usd / max(1, self.total_executions)

    @property
    def avg_confidence(self) -> float:
        return self.total_confidence / max(1, self.total_executions)


@dataclass
class Pattern:
    """A reusable pattern distilled from experiences."""

    pattern_id: str
    name: str
    description: str
    task_features: dict[str, Any]
    recommended_strategy: StrategyType
    expected_confidence: float
    expected_latency_ms: int
    expected_cost_usd: float
    prerequisites: list[str]
    pitfalls: list[str]
    created_ts: datetime
    updated_ts: datetime
    success_count: int = 0
    failure_count: int = 0


class MetaLearningEngine:
    """Advanced meta-learning for adaptive strategy selection."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        ids: IdGenerator,
        clock: Clock,
        min_samples_for_recommendation: int = 5,
        pattern_discovery_threshold: float = 0.75,
        adaptation_rate: float = 0.1,
    ) -> None:
        self._gw = gateway
        self._ids = ids
        self._clock = clock
        self._min_samples = min_samples_for_recommendation
        self._pattern_threshold = pattern_discovery_threshold
        self._adaptation_rate = adaptation_rate

        # Performance tracking
        self._strategy_performance: dict[tuple[StrategyType, TaskCategory], StrategyPerformance] = {}

        # Execution history
        self._traces: list[ExecutionTrace] = []
        self._max_traces = 10000

        # Discovered patterns
        self._patterns: dict[str, Pattern] = {}

        # Task classifier cache
        self._category_cache: dict[str, TaskCategory] = {}

        # Feature extractors
        self._feature_extractors: list[Callable[[str], dict[str, Any]]] = [
            self._extract_basic_features,
            self._extract_keyword_features,
            self._extract_entity_features,
        ]

        # Statistics
        self._stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "patterns_discovered": 0,
            "recommendations_made": 0,
            "recommendations_followed": 0,
            "recommendations_succeeded": 0,
        }

    async def classify_task(
        self,
        task_description: str,
    ) -> TaskCategory:
        """Classify a task into a category using LLM and heuristics."""

        # Check cache
        cache_key = task_description[:100]
        if cache_key in self._category_cache:
            return self._category_cache[cache_key]

        # Extract features
        features = await self._extract_features(task_description)

        # Use LLM for classification
        prompt = f"""Classify this task into ONE category:

TASK: {task_description[:500]}

FEATURES:
- Length: {features.length}
- Has code: {features.has_code}
- Complexity: {features.complexity_score:.2f}
- Ambiguity: {features.ambiguity_score:.2f}

CATEGORIES:
{chr(10).join(f"- {c.value}" for c in TaskCategory)}

Output JSON:
{{"category": "category_name", "confidence": 0.0-1.0}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a task classification expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=200,
                temperature=0.1,
            )
        )

        data = self._parse_json(resp.text)
        category_str = data.get("category", "reasoning")

        try:
            category = TaskCategory(category_str)
        except ValueError:
            category = TaskCategory.REASONING

        # Cache result
        self._category_cache[cache_key] = category

        return category

    async def recommend_strategy(
        self,
        task_description: str,
        constraints: dict[str, Any] | None = None,
    ) -> tuple[StrategyType, float]:
        """Recommend the best strategy for a task based on learned performance.

        Returns (strategy, confidence).
        """

        # Classify task
        category = await self.classify_task(task_description)

        # Get performance history for this category
        performances = [
            perf
            for (strategy, cat), perf in self._strategy_performance.items()
            if cat == category and perf.total_executions >= self._min_samples
        ]

        if not performances:
            # No sufficient history, use heuristics
            return self._heuristic_strategy(task_description, category)

        # Score each strategy
        scored = []
        for perf in performances:
            score = self._score_strategy(perf, constraints or {})
            scored.append((perf.strategy, score, perf))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)

        best_strategy = scored[0][0]
        confidence = scored[0][2].avg_confidence

        self._stats["recommendations_made"] += 1

        _log.info(
            "meta_learning.recommendation",
            event_type="meta_learning",
            category=category.value,
            strategy=best_strategy.value,
            confidence=confidence,
            samples=scored[0][2].total_executions,
        )

        return best_strategy, confidence

    async def record_execution(
        self,
        trace: ExecutionTrace,
    ) -> None:
        """Record an execution trace for learning."""

        # Add to history
        self._traces.append(trace)
        if len(self._traces) > self._max_traces:
            self._traces = self._traces[-self._max_traces :]

        # Update strategy performance
        key = (trace.strategy_used, trace.task_category)
        if key not in self._strategy_performance:
            self._strategy_performance[key] = StrategyPerformance(
                strategy=trace.strategy_used,
                category=trace.task_category,
            )

        perf = self._strategy_performance[key]
        perf.total_executions += 1
        perf.total_latency_ms += trace.latency_ms
        perf.total_cost_usd += trace.cost_usd
        perf.total_confidence += trace.confidence
        if trace.success:
            perf.successes += 1
        if trace.user_feedback is not None:
            # Exponential moving average
            perf.avg_user_feedback = (
                perf.avg_user_feedback * (1 - self._adaptation_rate) + trace.user_feedback * self._adaptation_rate
            )
        perf.last_updated = self._clock.now()

        # Update statistics
        self._stats["total_executions"] += 1
        if trace.success:
            self._stats["successful_executions"] += 1

        # Check for pattern discovery
        await self._check_pattern_discovery(trace)

        _log.debug(
            "meta_learning.recorded",
            event_type="meta_learning",
            task_id=trace.task_id,
            strategy=trace.strategy_used.value,
            success=trace.success,
        )

    async def discover_patterns(
        self,
        min_occurrences: int = 3,
        min_success_rate: float = 0.8,
    ) -> list[Pattern]:
        """Discover reusable patterns from execution history."""

        # Group traces by similarity
        groups = await self._cluster_similar_traces(self._traces)

        patterns = []
        for group in groups:
            if len(group) < min_occurrences:
                continue

            # Check success rate
            success_rate = sum(1 for t in group if t.success) / len(group)
            if success_rate < min_success_rate:
                continue

            # Extract pattern
            pattern = await self._extract_pattern_from_group(group)
            if pattern:
                patterns.append(pattern)
                self._patterns[pattern.pattern_id] = pattern
                self._stats["patterns_discovered"] += 1

        return patterns

    async def get_applicable_patterns(
        self,
        task_description: str,
    ) -> list[Pattern]:
        """Get patterns that are applicable to a task."""

        features = await self._extract_features(task_description)
        category = await self.classify_task(task_description)

        applicable = []
        for pattern in self._patterns.values():
            if self._pattern_matches(pattern, features, category):
                applicable.append(pattern)

        # Sort by success rate
        applicable.sort(
            key=lambda p: p.success_count / max(1, p.success_count + p.failure_count),
            reverse=True,
        )

        return applicable

    async def _extract_features(
        self,
        task_description: str,
    ) -> TaskFeatures:
        """Extract features from a task description."""

        features_dict: dict[str, Any] = {}
        for extractor in self._feature_extractors:
            features_dict.update(extractor(task_description))

        return TaskFeatures(
            task_id=features_dict.get("task_id", ""),
            description=task_description,
            length=len(task_description),
            has_code=features_dict.get("has_code", False),
            has_numbers=features_dict.get("has_numbers", False),
            has_dates=features_dict.get("has_dates", False),
            has_urls=features_dict.get("has_urls", False),
            has_file_paths=features_dict.get("has_file_paths", False),
            keyword_counts=features_dict.get("keyword_counts", {}),
            entity_types=features_dict.get("entity_types", []),
            complexity_score=features_dict.get("complexity_score", 0.5),
            ambiguity_score=features_dict.get("ambiguity_score", 0.5),
            time_sensitivity=features_dict.get("time_sensitivity", 0.5),
            risk_level=features_dict.get("risk_level", 0.5),
        )

    def _extract_basic_features(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Extract basic features from text."""

        import re

        return {
            "has_code": bool(re.search(r"```|def |class |import |function ", text)),
            "has_numbers": bool(re.search(r"\d+", text)),
            "has_dates": bool(re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}", text)),
            "has_urls": bool(re.search(r"https?://", text)),
            "has_file_paths": bool(re.search(r"/[\w/]+\.\w+|[A-Z]:\\[\w\\]+\.\w+", text)),
            "complexity_score": min(len(text) / 1000.0, 1.0),
            "ambiguity_score": 0.5,  # Placeholder, could use NLP
        }

    def _extract_keyword_features(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Extract keyword-based features."""

        keywords = {
            "research": ["find", "search", "look up", "research", "investigate", "gather"],
            "analysis": ["analyze", "examine", "evaluate", "assess", "compare", "metrics"],
            "writing": ["write", "compose", "draft", "create", "generate", "author"],
            "coding": ["code", "program", "implement", "develop", "debug", "fix"],
            "planning": ["plan", "schedule", "organize", "arrange", "coordinate"],
            "execution": ["execute", "run", "perform", "do", "complete", "finish"],
            "communication": ["email", "message", "notify", "inform", "send", "communicate"],
            "reasoning": ["why", "how", "reason", "explain", "understand", "deduce"],
            "creative": ["design", "invent", "imagine", "create", "brainstorm"],
            "optimization": ["optimize", "improve", "enhance", "tune", "refine"],
        }

        text_lower = text.lower()
        counts = {}
        for category, words in keywords.items():
            counts[category] = sum(1 for w in words if w in text_lower)

        return {"keyword_counts": counts}

    def _extract_entity_features(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Extract entity-based features (simplified)."""

        # Simplified entity detection
        entities = []
        if "@" in text:
            entities.append("email")
        if "$" in text or "USD" in text:
            entities.append("currency")
        if any(w in text for w in ["today", "tomorrow", "yesterday", "next week"]):
            entities.append("relative_date")

        return {"entity_types": entities}

    def _heuristic_strategy(
        self,
        task_description: str,
        category: TaskCategory,
    ) -> tuple[StrategyType, float]:
        """Use heuristics to select strategy when no history is available."""

        # Category-based heuristics
        heuristics = {
            TaskCategory.RESEARCH: StrategyType.EXPLORATORY,
            TaskCategory.ANALYSIS: StrategyType.SEQUENTIAL_DECOMPOSITION,
            TaskCategory.WRITING: StrategyType.ITERATIVE_REFINEMENT,
            TaskCategory.CODING: StrategyType.HIERARCHICAL,
            TaskCategory.PLANNING: StrategyType.HIERARCHICAL,
            TaskCategory.EXECUTION: StrategyType.SINGLE_AGENT,
            TaskCategory.COMMUNICATION: StrategyType.SINGLE_AGENT,
            TaskCategory.REASONING: StrategyType.SEQUENTIAL_DECOMPOSITION,
            TaskCategory.CREATIVE: StrategyType.ITERATIVE_REFINEMENT,
            TaskCategory.OPTIMIZATION: StrategyType.ITERATIVE_REFINEMENT,
            TaskCategory.MONITORING: StrategyType.SINGLE_AGENT,
            TaskCategory.DEBUGGING: StrategyType.HIERARCHICAL,
        }

        strategy = heuristics.get(category, StrategyType.SINGLE_AGENT)
        confidence = 0.5  # Low confidence for heuristic selection

        return strategy, confidence

    def _score_strategy(
        self,
        perf: StrategyPerformance,
        constraints: dict[str, Any],
    ) -> float:
        """Score a strategy based on performance and constraints."""

        score = 0.0

        # Success rate (40% weight)
        score += perf.success_rate * 0.4

        # Confidence (30% weight)
        score += perf.avg_confidence * 0.3

        # User feedback (20% weight)
        score += (perf.avg_user_feedback + 1) / 2 * 0.2  # Normalize -1..1 to 0..1

        # Cost efficiency (10% weight)
        max_cost = float(constraints.get("max_cost_usd", 1.0))
        cost_efficiency = 1.0 - min(perf.avg_cost_usd / max_cost, 1.0)
        score += cost_efficiency * 0.1

        return score

    async def _check_pattern_discovery(
        self,
        trace: ExecutionTrace,
    ) -> None:
        """Check if a new pattern should be discovered from this execution."""

        if not trace.success:
            return

        # Get similar successful traces
        similar = [
            t
            for t in self._traces
            if t.task_category == trace.task_category
            and t.strategy_used == trace.strategy_used
            and t.success
            and abs(t.latency_ms - trace.latency_ms) < 5000
        ]

        if len(similar) >= 3:
            # Check if pattern already exists
            existing = any(
                p.task_features.get("category") == trace.task_category.value
                and p.recommended_strategy == trace.strategy_used
                for p in self._patterns.values()
            )

            if not existing:
                # Discover new pattern
                patterns = await self.discover_patterns()
                if patterns:
                    _log.info(
                        "meta_learning.pattern_discovered",
                        event_type="meta_learning",
                        pattern_count=len(patterns),
                    )

    async def _cluster_similar_traces(
        self,
        traces: list[ExecutionTrace],
    ) -> list[list[ExecutionTrace]]:
        """Cluster similar traces together."""

        # Simple clustering by category and strategy
        groups: dict[tuple[TaskCategory, StrategyType], list[ExecutionTrace]] = defaultdict(list)

        for trace in traces:
            key = (trace.task_category, trace.strategy_used)
            groups[key].append(trace)

        return list(groups.values())

    async def _extract_pattern_from_group(
        self,
        group: list[ExecutionTrace],
    ) -> Pattern | None:
        """Extract a pattern from a group of similar traces."""

        if not group:
            return None

        # Get representative trace
        representative = group[0]

        # Calculate aggregate metrics
        avg_latency = sum(t.latency_ms for t in group) / len(group)
        avg_cost = sum(t.cost_usd for t in group) / len(group)
        avg_confidence = sum(t.confidence for t in group) / len(group)

        # Extract common features
        common_features = await self._extract_common_features(group)

        pattern = Pattern(
            pattern_id=self._ids.execution_id(),
            name=f"{representative.task_category.value}_{representative.strategy_used.value}",
            description=(
                f"Pattern for {representative.task_category.value} tasks"
                f" using {representative.strategy_used.value} strategy"
            ),
            task_features=common_features,
            recommended_strategy=representative.strategy_used,
            expected_confidence=avg_confidence,
            expected_latency_ms=int(avg_latency),
            expected_cost_usd=avg_cost,
            prerequisites=[],
            pitfalls=[],
            success_count=sum(1 for t in group if t.success),
            failure_count=sum(1 for t in group if not t.success),
            created_ts=self._clock.now(),
            updated_ts=self._clock.now(),
        )

        return pattern

    async def _extract_common_features(
        self,
        group: list[ExecutionTrace],
    ) -> dict[str, Any]:
        """Extract common features from a group of traces."""

        return {
            "category": group[0].task_category.value,
            "avg_steps": sum(t.steps_taken for t in group) / len(group),
            "avg_tool_calls": sum(t.tool_calls for t in group) / len(group),
            "avg_model_calls": sum(t.model_calls for t in group) / len(group),
        }

    def _pattern_matches(
        self,
        pattern: Pattern,
        features: TaskFeatures,
        category: TaskCategory,
    ) -> bool:
        """Check if a pattern matches the given features."""

        # Check category
        if pattern.task_features.get("category") != category.value:
            return False

        # Check complexity (if pattern specifies)
        if "complexity_range" in pattern.task_features:
            min_c, max_c = pattern.task_features["complexity_range"]
            if not min_c <= features.complexity_score <= max_c:
                return False

        return True

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
        """Get meta-learning statistics."""

        return {
            **self._stats,
            "total_patterns": len(self._patterns),
            "strategy_performance": {
                f"{s.value}_{c.value}": {
                    "success_rate": perf.success_rate,
                    "avg_confidence": perf.avg_confidence,
                    "total_executions": perf.total_executions,
                }
                for (s, c), perf in self._strategy_performance.items()
            },
        }
