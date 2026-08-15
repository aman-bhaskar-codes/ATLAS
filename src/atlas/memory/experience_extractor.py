"""Experience Extractor — LLM-based lesson extraction from trajectories.

Phase 2: Analyzes completed task trajectories to extract reusable lessons.
Uses LLM to identify patterns, successful strategies, failure mitigations,
and optimization opportunities. Follows Consolidator pattern for consistency.

WHY post-task analysis: Experiences are meta-learnings about the agent's own
behavior, not facts about the world. They answer "how should I approach X?"
rather than "what is X?". This requires seeing the full task context.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.trajectory import (
    Experience,
    ExperienceCategory,
    Trajectory,
)

if TYPE_CHECKING:
    from atlas.memory.trajectory_store import TrajectoryStore

_log = get_logger("atlas.memory.experience")

# Extraction prompt: Guides LLM to produce structured experience JSON
_EXTRACT_PROMPT = """You are analyzing a completed task execution to extract reusable lessons.

Given the trajectory below, identify practical lessons the agent should remember for future tasks.

Output ONLY valid JSON with this structure:
{
  "experiences": [
    {
      "category": "tool_usage|planning_pattern|error_recovery|user_preference|domain_knowledge|optimization|constraint",
      "lesson_text": "Clear, actionable lesson (1-2 sentences)",
      "applicability_context": "When does this apply? (be specific)",
      "confidence": 0.0-1.0,
      "supporting_steps": [step_numbers_that_support_this]
    }
  ]
}

RULES:
1. Only extract lessons with clear supporting evidence from the trajectory
2. Be specific: "Use tool X with arg Y when Z" > "Tool X is useful"
3. Focus on actionable patterns, not one-off details
4. Higher confidence for lessons supported by multiple steps or successful outcomes
5. Lower confidence for lessons from failed tasks (might be wrong approach)
6. Include 0-3 lessons per trajectory (quality over quantity)

TRAJECTORY:
Goal: {goal}
Success: {success}
Steps taken: {steps}
Replan count: {replans}
Verification score: {verification_score}

Actions:
{actions}

Observations:
{observations}

Final outcome: {outcome}
"""

_MIN_CONFIDENCE_TO_SAVE = 0.5  # Don't save low-confidence guesses


class ExperienceExtractor:
    """Extracts experiences from completed trajectories using LLM analysis.
    
    Performance target: < 3s per trajectory (async, doesn't block task execution).
    """
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        trajectory_store: TrajectoryStore,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._gw = gateway
        self._store = trajectory_store
        self._ids = ids
        self._clock = clock

    async def extract_from_trajectory(
        self,
        trajectory: Trajectory,
    ) -> list[Experience]:
        """Extract experiences from a single trajectory.
        
        Returns list of extracted experiences (may be empty if no clear lessons).
        Saves experiences to store automatically.
        """
        # Build extraction prompt
        actions_text = self._format_actions(trajectory.actions)
        observations_text = self._format_observations(trajectory.observations)
        outcome_text = (
            f"SUCCESS: {trajectory.answer}" if trajectory.success
            else f"FAILED: {trajectory.error}"
        )
        
        prompt = _EXTRACT_PROMPT.format(
            goal=trajectory.goal,
            success="✓" if trajectory.success else "✗",
            steps=trajectory.steps_taken,
            replans=trajectory.replan_count,
            verification_score=trajectory.verification_score or "N/A",
            actions=actions_text,
            observations=observations_text,
            outcome=outcome_text,
        )
        
        # LLM extraction with structured output
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=trajectory.correlation_id,
                system="You extract structured lessons from task trajectories. Output ONLY valid JSON.",
                prompt=prompt,
                required_capabilities=frozenset({
                    ModelCapability.REASONING,
                    ModelCapability.SUMMARIZATION,
                    ModelCapability.JSON_GENERATION,
                }),
                needs_deep_reasoning=True,  # This is analytical work, use o1/thinking
                max_tokens=1500,
            ))
            
            # Parse JSON response
            parsed = json.loads(self._extract_json(resp.text))
            
        except (json.JSONDecodeError, ValueError) as exc:
            _log.error(
                "experience.parse_failed",
                event_type="memory",
                error=repr(exc),
                trajectory_id=trajectory.id,
            )
            return []
        
        # Convert parsed JSON to Experience objects
        experiences: list[Experience] = []
        for exp_data in parsed.get("experiences", []):
            try:
                category = ExperienceCategory(exp_data.get("category", "domain_knowledge"))
                lesson_text = str(exp_data.get("lesson_text", "")).strip()
                applicability_context = str(exp_data.get("applicability_context", "")).strip()
                confidence = float(exp_data.get("confidence", 0.5))
                supporting_steps = tuple(int(s) for s in exp_data.get("supporting_steps", []))
                
                # Filter low-quality extractions
                if not lesson_text or not applicability_context:
                    continue
                if confidence < _MIN_CONFIDENCE_TO_SAVE:
                    continue
                if len(lesson_text) < 20:  # Too short to be useful
                    continue
                
                # Create Experience object
                experience = Experience(
                    id=self._ids.execution_id(),
                    trajectory_id=trajectory.id,
                    task_id=trajectory.task_id,
                    correlation_id=trajectory.correlation_id,
                    category=category,
                    lesson_text=lesson_text,
                    applicability_context=applicability_context,
                    confidence=confidence,
                    supporting_actions=supporting_steps,
                    supporting_observations=supporting_steps,  # Same steps for now
                    counter_examples=(),
                    reuse_count=0,
                    success_rate=0.0,
                    avg_improvement_ms=0,
                    avg_cost_savings_usd=0.0,
                    extracted_ts=self._clock.now(),
                    last_applied_ts=None,
                    superseded_by=None,
                )
                
                # Save to store
                await self._store.save_experience(experience)
                experiences.append(experience)
                
                _log.info(
                    "experience.extracted",
                    event_type="memory",
                    experience_id=experience.id,
                    category=category.value,
                    confidence=confidence,
                    trajectory_id=trajectory.id,
                )
                
            except (ValueError, KeyError) as exc:
                _log.warning(
                    "experience.item_invalid",
                    event_type="memory",
                    error=repr(exc),
                    item=exp_data,
                )
                continue
        
        if experiences:
            _log.info(
                "experience.extraction_complete",
                event_type="memory",
                trajectory_id=trajectory.id,
                count=len(experiences),
                categories=[e.category.value for e in experiences],
            )
        else:
            _log.debug(
                "experience.no_lessons",
                event_type="memory",
                trajectory_id=trajectory.id,
                reason="No high-quality lessons extracted",
            )
        
        return experiences

    async def extract_batch(
        self,
        trajectories: list[Trajectory],
        max_concurrency: int = 3,
    ) -> dict[str, int]:
        """Extract experiences from multiple trajectories with concurrency control.
        
        Returns stats: {"processed": N, "extracted": M, "failed": K}
        """
        import asyncio
        
        semaphore = asyncio.Semaphore(max_concurrency)
        
        async def _extract_one(traj: Trajectory) -> int:
            async with semaphore:
                try:
                    experiences = await self.extract_from_trajectory(traj)
                    return len(experiences)
                except Exception as exc:
                    _log.error(
                        "experience.batch_error",
                        event_type="memory",
                        error=repr(exc),
                        trajectory_id=traj.id,
                    )
                    return -1  # Mark as failed
        
        results = await asyncio.gather(*[_extract_one(t) for t in trajectories])
        
        extracted = sum(r for r in results if r >= 0)
        failed = sum(1 for r in results if r < 0)
        
        return {
            "processed": len(trajectories),
            "extracted": extracted,
            "failed": failed,
        }

    async def extract_from_recent_trajectories(
        self,
        limit: int = 10,
        only_successful: bool = False,
    ) -> dict[str, int]:
        """Extract experiences from recent trajectories.
        
        Useful for periodic batch processing (e.g., nightly consolidation job).
        """
        # Query recent trajectories
        from atlas.memory.trajectory import TrajectoryQuery
        
        query = TrajectoryQuery(
            success=True if only_successful else None,
            limit=limit,
        )
        
        trajectories = await self._store.query_trajectories(query)
        
        if not trajectories:
            _log.info("experience.no_recent_trajectories", event_type="memory")
            return {"processed": 0, "extracted": 0, "failed": 0}
        
        # Extract in batch
        return await self.extract_batch(trajectories)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Formatting Helpers
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _format_actions(actions: tuple[object, ...]) -> str:
        """Format actions for prompt (truncate for context budget)."""
        if not actions:
            return "(no actions recorded)"
        
        lines = []
        for action in actions[:20]:  # Limit to first 20 actions
            # Duck-type ActionRecord attributes
            step = getattr(action, "step", "?")
            kind = getattr(action, "kind", "unknown")
            tool = getattr(action, "tool", None)
            
            if kind == "tool_call" and tool:
                args = getattr(action, "args", {})
                args_str = str(args)[:100]  # Truncate long args
                lines.append(f"  Step {step}: {kind} → {tool}({args_str})")
            elif kind in ("final_answer", "ask_user"):
                final_text = getattr(action, "final_text", "")
                text_preview = str(final_text)[:150]
                lines.append(f"  Step {step}: {kind} → {text_preview}")
            else:
                lines.append(f"  Step {step}: {kind}")
        
        if len(actions) > 20:
            lines.append(f"  ... and {len(actions) - 20} more actions")
        
        return "\n".join(lines)

    @staticmethod
    def _format_observations(observations: tuple[object, ...]) -> str:
        """Format observations for prompt (truncate for context budget)."""
        if not observations:
            return "(no observations recorded)"
        
        lines = []
        for obs in observations[:20]:  # Limit to first 20 observations
            # Duck-type ObservationRecord attributes
            step = getattr(obs, "step", "?")
            ok = getattr(obs, "ok", False)
            
            if ok:
                content = getattr(obs, "content", "")
                content_preview = str(content)[:150]
                lines.append(f"  Step {step}: ✓ {content_preview}")
            else:
                error = getattr(obs, "error", "unknown error")
                lines.append(f"  Step {step}: ✗ {error}")
        
        if len(observations) > 20:
            lines.append(f"  ... and {len(observations) - 20} more observations")
        
        return "\n".join(lines)

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON object from model response (handles markdown, thinking, etc.)."""
        # Try to find JSON object boundaries
        start = text.find("{")
        end = text.rfind("}")
        
        if start == -1 or end == -1:
            raise ValueError("no JSON object in model output")
        
        return text[start : end + 1]
