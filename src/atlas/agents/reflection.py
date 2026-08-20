"""Self-Reflection and Improvement Engine - Continuous self-awareness and optimization.

This implements state-of-the-art self-reflection inspired by:
- Metacognition in cognitive science
- Self-improving AI systems
- Reflective practice in expert systems
- Learning from mistakes and successes

Key features:
1. Post-execution reflection on decisions and outcomes
2. Identification of improvement opportunities
3. Self-critique and alternative approach generation
4. Knowledge distillation from experiences
5. Adaptive strategy refinement based on reflection
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.reflection")


class ReflectionType(Enum):
    """Types of reflection."""
    POST_EXECUTION = "post_execution"          # After task completion
    MID_EXECUTION = "mid_execution"            # During task execution
    ERROR_ANALYSIS = "error_analysis"           # After failure
    SUCCESS_ANALYSIS = "success_analysis"      # After success
    STRATEGY_REVIEW = "strategy_review"         # Review chosen approach
    ALTERNATIVE_GENERATION = "alternative"     # Generate alternatives


class ImprovementCategory(Enum):
    """Categories of improvements."""
    PLANNING = "planning"              # Better task decomposition
    EXECUTION = "execution"            # More efficient execution
    REASONING = "reasoning"           # Better logical deduction
    TOOL_USAGE = "tool_usage"          # More effective tool use
    COMMUNICATION = "communication"    # Clearer communication
    ERROR_HANDLING = "error_handling"  # Better error recovery
    RESOURCE_MANAGEMENT = "resources"  # Better cost/time management
    QUALITY = "quality"               # Higher quality outputs


@dataclass
class Reflection:
    """A reflection on a decision or execution."""
    reflection_id: str
    reflection_type: ReflectionType
    task_id: str
    decision_point: str
    decision_made: str
    outcome: str
    alternative_approaches: list[str]
    lessons_learned: list[str]
    confidence_in_decision: float
    would_repeat: bool
    reasoning: str
    improvements: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelfCritique:
    """A self-critique of performance."""
    critique_id: str
    task_id: str
    strengths: list[str]
    weaknesses: list[str]
    missed_opportunities: list[str]
    overcomplications: list[str]
    efficiency_issues: list[str]
    quality_issues: list[str]
    recommendations: list[str]


@dataclass
class Improvement:
    """An identified improvement opportunity."""
    improvement_id: str
    category: ImprovementCategory
    description: str
    priority: float  # 0-1, higher is more important
    impact_estimate: float  # Estimated improvement
    effort_estimate: float  # Effort to implement
    prerequisites: list[str]
    status: str  # "identified", "planned", "implemented"
    created_ts: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class ReflectionEngine:
    """Advanced self-reflection and improvement system."""
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        ids: IdGenerator,
        clock: Clock,
        reflection_depth: int = 3,
        improvement_tracking: bool = True,
        auto_apply_learnings: bool = False,
    ) -> None:
        self._gw = gateway
        self._ids = ids
        self._clock = clock
        self._depth = reflection_depth
        self._track_improvements = improvement_tracking
        self._auto_apply = auto_apply_learnings
        
        # Reflection history
        self._reflections: list[Reflection] = []
        self._max_reflections = 5000
        
        # Improvements registry
        self._improvements: dict[str, Improvement] = {}
        
        # Learning cache
        self._learnings: dict[str, list[str]] = {}
        
        # Statistics
        self._stats = {
            "total_reflections": 0,
            "improvements_identified": 0,
            "improvements_applied": 0,
            "lessons_learned": 0,
            "alternative_approaches_generated": 0,
        }

    async def reflect_on_execution(
        self,
        task_id: str,
        task_description: str,
        execution_trace: dict[str, Any],
        outcome: str,
    ) -> Reflection:
        """Reflect on a completed execution."""
        
        _log.info(
            "reflection.started",
            event_type="reflection",
            task_id=task_id,
            outcome=outcome[:50],
        )
        
        # Determine reflection type
        reflection_type = self._determine_reflection_type(execution_trace)
        
        # Generate reflection
        reflection = await self._generate_reflection(
            task_id=task_id,
            task_description=task_description,
            execution_trace=execution_trace,
            outcome=outcome,
            reflection_type=reflection_type,
        )
        
        # Store reflection
        self._reflections.append(reflection)
        if len(self._reflections) > self._max_reflections:
            self._reflections = self._reflections[-self._max_reflections:]
        
        # Extract improvements
        if self._track_improvements:
            improvements = await self._extract_improvements(reflection)
            for improvement in improvements:
                self._improvements[improvement.improvement_id] = improvement
                self._stats["improvements_identified"] += 1
        
        # Store learnings
        await self._store_learnings(reflection)
        
        self._stats["total_reflections"] += 1
        
        _log.info(
            "reflection.completed",
            event_type="reflection",
            task_id=task_id,
            lessons=len(reflection.lessons_learned),
            improvements=len(reflection.improvements),
        )
        
        return reflection

    async def mid_execution_reflection(
        self,
        task_id: str,
        current_state: dict[str, Any],
    ) -> Reflection:
        """Reflect during execution to adjust course."""
        
        prompt = f"""Reflect on current execution state and suggest adjustments:

TASK ID: {task_id}
CURRENT STATE: {json.dumps(current_state, indent=2)}

Provide:
1. Assessment of progress
2. Potential issues or risks
3. Suggested adjustments
4. Confidence in current approach

Output JSON:
{{
  "progress_assessment": "description",
  "issues": ["issue1", "issue2"],
  "adjustments": ["adjustment1", "adjustment2"],
  "confidence": 0.0-1.0,
  "continue_as_planned": true/false
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=self._mid_execution_system_prompt()),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=800,
                temperature=0.4,
            )
        )
        
        data = self._parse_json(resp.text)
        
        reflection = Reflection(
            reflection_id=self._ids.execution_id(),
            reflection_type=ReflectionType.MID_EXECUTION,
            task_id=task_id,
            decision_point="execution_check",
            decision_made=json.dumps(current_state),
            outcome=data.get("progress_assessment", ""),
            alternative_approaches=data.get("adjustments", []),
            lessons_learned=[],
            confidence_in_decision=data.get("confidence", 0.5),
            would_repeat=data.get("continue_as_planned", True),
            reasoning=json.dumps(data.get("issues", [])),
            improvements=data.get("adjustments", []),
        )
        
        return reflection

    async def self_critique(
        self,
        task_id: str,
        task_description: str,
        execution_trace: dict[str, Any],
    ) -> SelfCritique:
        """Generate a comprehensive self-critique."""
        
        prompt = f"""Perform a thorough self-critique of this execution:

TASK: {task_description}

EXECUTION TRACE:
{json.dumps(execution_trace, indent=2)[:2000]}

Critique:
1. What did I do well? (strengths)
2. What could I have done better? (weaknesses)
3. What opportunities did I miss? (missed_opportunities)
4. Where did I overcomplicate things? (overcomplications)
5. Where could I have been more efficient? (efficiency_issues)
6. Where was quality lacking? (quality_issues)
7. What would I do differently? (recommendations)

Output JSON:
{{
  "strengths": ["s1", "s2"],
  "weaknesses": ["w1", "w2"],
  "missed_opportunities": ["o1", "o2"],
  "overcomplications": ["oc1", "oc2"],
  "efficiency_issues": ["e1", "e2"],
  "quality_issues": ["q1", "q2"],
  "recommendations": ["r1", "r2"]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a self-improvement expert. Be honest and constructive."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=1000,
                temperature=0.3,
            )
        )
        
        data = self._parse_json(resp.text)
        
        critique = SelfCritique(
            critique_id=self._ids.execution_id(),
            task_id=task_id,
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            missed_opportunities=data.get("missed_opportunities", []),
            overcomplications=data.get("overcomplications", []),
            efficiency_issues=data.get("efficiency_issues", []),
            quality_issues=data.get("quality_issues", []),
            recommendations=data.get("recommendations", []),
        )
        
        return critique

    async def generate_alternatives(
        self,
        task_description: str,
        original_approach: str,
        outcome: str,
        num_alternatives: int = 3,
    ) -> list[str]:
        """Generate alternative approaches that might have worked better."""
        
        prompt = f"""Generate {num_alternatives} alternative approaches for this task:

TASK: {task_description}
ORIGINAL APPROACH: {original_approach}
ACTUAL OUTCOME: {outcome}

For each alternative:
1. Describe the approach
2. Explain why it might work better
3. Identify when it would be preferable

Output JSON:
{{
  "alternatives": [
    {{
      "approach": "description",
      "reasoning": "why it might work better",
      "best_for": "situations where this is optimal"
    }}
  ]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a creative problem solver. Think outside the box."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=1200,
                temperature=0.8,
            )
        )
        
        data = self._parse_json(resp.text)
        alternatives = [
            alt.get("approach", "")
            for alt in data.get("alternatives", [])
        ]
        
        self._stats["alternative_approaches_generated"] += len(alternatives)
        
        return alternatives

    async def get_improvement_priorities(
        self,
        limit: int = 10,
    ) -> list[Improvement]:
        """Get prioritized list of improvements to work on."""
        
        # Sort by priority (impact / effort)
        sorted_improvements = sorted(
            self._improvements.values(),
            key=lambda imp: (imp.impact_estimate / max(imp.effort_estimate, 0.1)),
            reverse=True,
        )
        
        return sorted_improvements[:limit]

    async def apply_learning(
        self,
        improvement_id: str,
    ) -> bool:
        """Mark a learning as applied and update statistics."""
        
        if improvement_id not in self._improvements:
            return False
        
        improvement = self._improvements[improvement_id]
        improvement.status = "implemented"
        
        self._stats["improvements_applied"] += 1
        
        _log.info(
            "reflection.improvement_applied",
            event_type="reflection",
            improvement_id=improvement_id,
            category=improvement.category.value,
        )
        
        return True

    def get_lessons_for_context(
        self,
        context: str,
        limit: int = 5,
    ) -> list[str]:
        """Get relevant lessons for a given context."""
        
        # Simple keyword matching
        relevant = []
        context_lower = context.lower()
        
        for key, lessons in self._learnings.items():
            if key.lower() in context_lower:
                relevant.extend(lessons)
        
        return relevant[:limit]

    async def _generate_reflection(
        self,
        task_id: str,
        task_description: str,
        execution_trace: dict[str, Any],
        outcome: str,
        reflection_type: ReflectionType,
    ) -> Reflection:
        """Generate a reflection using LLM."""
        
        prompt = f"""Reflect deeply on this execution:

TASK: {task_description}

EXECUTION:
{json.dumps(execution_trace, indent=2)[:2000]}

OUTCOME: {outcome}

Reflect on:
1. Key decisions made
2. Why those decisions were made
3. What was the result
4. What could have been done differently
5. Lessons learned
6. Improvements for future

Output JSON:
{{
  "decision_point": "what decision was made",
  "decision_made": "the specific decision",
  "outcome": "what happened",
  "alternative_approaches": ["alt1", "alt2"],
  "lessons_learned": ["lesson1", "lesson2"],
  "confidence": 0.0-1.0,
  "would_repeat": true/false,
  "reasoning": "detailed reasoning",
  "improvements": ["improvement1", "improvement2"]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=self._reflection_system_prompt()),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=1500,
                temperature=0.4,
            )
        )
        
        data = self._parse_json(resp.text)
        
        return Reflection(
            reflection_id=self._ids.execution_id(),
            reflection_type=reflection_type,
            task_id=task_id,
            decision_point=data.get("decision_point", ""),
            decision_made=data.get("decision_made", ""),
            outcome=data.get("outcome", outcome),
            alternative_approaches=data.get("alternative_approaches", []),
            lessons_learned=data.get("lessons_learned", []),
            confidence_in_decision=data.get("confidence", 0.5),
            would_repeat=data.get("would_repeat", True),
            reasoning=data.get("reasoning", ""),
            improvements=data.get("improvements", []),
        )

    async def _extract_improvements(
        self,
        reflection: Reflection,
    ) -> list[Improvement]:
        """Extract concrete improvements from a reflection."""
        
        improvements = []
        
        for desc in reflection.improvements:
            # Categorize improvement
            category = await self._categorize_improvement(desc)
            
            improvement = Improvement(
                improvement_id=self._ids.execution_id(),
                category=category,
                description=desc,
                priority=self._calculate_improvement_priority(reflection, category),
                impact_estimate=0.5,
                effort_estimate=0.5,
                prerequisites=[],
                status="identified",
                created_ts=self._clock.now(),
            )
            improvements.append(improvement)
        
        return improvements

    async def _categorize_improvement(
        self,
        description: str,
    ) -> ImprovementCategory:
        """Categorize an improvement."""
        
        keywords = {
            ImprovementCategory.PLANNING: ["plan", "decompose", "structure", "organize"],
            ImprovementCategory.EXECUTION: ["execute", "run", "perform", "implement"],
            ImprovementCategory.REASONING: ["reason", "think", "analyze", "deduce"],
            ImprovementCategory.TOOL_USAGE: ["tool", "use", "call", "invoke"],
            ImprovementCategory.COMMUNICATION: ["communicate", "explain", "clarify"],
            ImprovementCategory.ERROR_HANDLING: ["error", "fail", "recover", "handle"],
            ImprovementCategory.RESOURCE_MANAGEMENT: ["cost", "time", "resource", "efficient"],
            ImprovementCategory.QUALITY: ["quality", "better", "improve", "enhance"],
        }
        
        desc_lower = description.lower()
        
        for category, words in keywords.items():
            if any(w in desc_lower for w in words):
                return category
        
        return ImprovementCategory.QUALITY

    def _calculate_improvement_priority(
        self,
        reflection: Reflection,
        category: ImprovementCategory,
    ) -> float:
        """Calculate priority for an improvement."""
        
        # Higher priority if:
        # - Low confidence in decision
        # - Would not repeat
        # - From error analysis
        
        base_priority = 0.5
        
        if reflection.confidence_in_decision < 0.5:
            base_priority += 0.2
        
        if not reflection.would_repeat:
            base_priority += 0.2
        
        if reflection.reflection_type == ReflectionType.ERROR_ANALYSIS:
            base_priority += 0.3
        
        return min(base_priority, 1.0)

    async def _store_learnings(
        self,
        reflection: Reflection,
    ) -> None:
        """Store lessons learned for future reference."""
        
        for lesson in reflection.lessons_learned:
            # Extract key concept
            key = self._extract_key_concept(lesson)
            
            if key not in self._learnings:
                self._learnings[key] = []
            
            self._learnings[key].append(lesson)
            self._stats["lessons_learned"] += 1

    def _extract_key_concept(
        self,
        lesson: str,
    ) -> str:
        """Extract key concept from a lesson (simplified)."""
        
        # Use first few words as key
        words = lesson.split()
        return " ".join(words[:3]) if len(words) >= 3 else lesson

    def _determine_reflection_type(
        self,
        execution_trace: dict[str, Any],
    ) -> ReflectionType:
        """Determine the type of reflection based on execution trace."""
        
        success = execution_trace.get("success", True)
        
        if success:
            return ReflectionType.SUCCESS_ANALYSIS
        else:
            return ReflectionType.ERROR_ANALYSIS

    def _reflection_system_prompt(self) -> str:
        return """You are a reflective AI agent. Your role is to:
1. Analyze your decisions and their outcomes
2. Identify what worked and what didn't
3. Extract actionable lessons
4. Suggest concrete improvements
5. Be honest about mistakes

Focus on learning and improvement."""

    def _mid_execution_system_prompt(self) -> str:
        return """You are monitoring an ongoing execution. Your role is to:
1. Assess current progress
2. Identify potential issues early
3. Suggest course corrections
4. Maintain confidence calibration

Be proactive and honest."""

    def _parse_json(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Parse JSON from text with error handling."""
        
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end == 0:
                return {}
            return dict(json.loads(text[start:end]))
        except json.JSONDecodeError:
            return {}

    def get_statistics(self) -> dict[str, Any]:
        """Get reflection statistics."""
        
        return {
            **self._stats,
            "total_improvements": len(self._improvements),
            "improvements_by_category": {
                cat.value: sum(1 for imp in self._improvements.values() if imp.category == cat)
                for cat in ImprovementCategory
            },
        }
