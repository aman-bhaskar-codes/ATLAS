"""Experience-to-skill promotion — evidence-gated behavior learning.

Scans proven experiences (reused and still succeeding) and promotes them into
CANDIDATE skills. Candidates only become ACTIVE through SkillStore's
application evidence thresholds. One failure can demote, never auto-delete;
nothing here touches safety policy.
"""

from __future__ import annotations

from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.memory.skills import Skill, SkillStore
from atlas.memory.trajectory import Experience, ExperienceQuery
from atlas.memory.trajectory_store import TrajectoryStore

_log = get_logger("atlas.memory.promotion")

# An experience must have been applied (reused) and still succeed before it
# can seed a skill — single-trajectory lessons stay experiences.
PROMOTION_MIN_REUSE = 2
PROMOTION_MIN_SUCCESS_RATE = 0.6


class SkillPromoter:
    def __init__(
        self,
        *,
        trajectory_store: TrajectoryStore,
        skill_store: SkillStore,
        ids: IdGenerator,
    ) -> None:
        self._trajectories = trajectory_store
        self._skills = skill_store
        self._ids = ids

    async def promote_from_experiences(self, limit: int = 20) -> list[Skill]:
        """Promote proven experiences into candidate skills (idempotent per experience)."""
        query = ExperienceQuery(min_confidence=0.6, limit=limit)
        experiences = await self._trajectories.query_experiences(query)
        created: list[Skill] = []
        for exp in experiences:
            if not self._is_promotable(exp):
                continue
            existing = await self._skills.find_by_name(self._skill_name(exp))
            if existing is not None:
                continue  # already seeded from a sibling experience
            skill = Skill(
                id=self._ids.execution_id(),
                name=self._skill_name(exp),
                description=exp.lesson_text,
                procedure_steps=(exp.applicability_context,),
                confidence=exp.confidence * min(1.0, exp.success_rate or 0.5),
                preferred_tools=(),
                known_failure_modes=(),
                source_experience_ids=(exp.id,),
            )
            await self._skills.save(skill)
            created.append(skill)
            _log.info("skill.seeded", event_type="memory", skill_id=skill.id, from_experience=exp.id)
        return created

    @staticmethod
    def _is_promotable(exp: Experience) -> bool:
        return (
            exp.reuse_count >= PROMOTION_MIN_REUSE
            and exp.success_rate >= PROMOTION_MIN_SUCCESS_RATE
            and exp.superseded_by is None
        )

    @staticmethod
    def _skill_name(exp: Experience) -> str:
        # Stable name per (category, applicability) so sibling experiences
        # converge on one skill instead of forking duplicates.
        scope = exp.applicability_context[:60].strip().lower()
        return f"{exp.category.value}: {scope}"
