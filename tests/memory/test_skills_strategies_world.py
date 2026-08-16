"""Batch 4 tests — skills, promotion thresholds, strategies, world state,
and experience-informed planning."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.memory.skills import Skill, SkillStatus, SkillStore
from atlas.memory.skills_promotion import SkillPromoter
from atlas.memory.strategies import Strategy, StrategyStatus, StrategyStore
from atlas.memory.trajectory import (
    ActionRecord,
    Experience,
    ExperienceCategory,
    Trajectory,
)
from atlas.memory.trajectory_store import TrajectoryStore
from atlas.memory.world_state import WorldStateStore


@pytest.fixture
async def db(tmp_path: Path):
    d = Database(tmp_path / "batch4.db")
    await d.start()
    yield d
    await d.stop()


@pytest.fixture
def ids() -> UuidGenerator:
    return UuidGenerator()


def _skill(**kw: object) -> Skill:
    defaults: dict[str, object] = {
        "id": "sk-1",
        "name": "research: official sources",
        "description": "Prefer official feeds over blogs",
        "procedure_steps": ("check official feed", "cross-check", "cite"),
    }
    defaults.update(kw)
    return Skill(**defaults)  # type: ignore[arg-type]


class TestSkillStore:
    async def test_save_and_get(self, db: Database, ids: UuidGenerator) -> None:
        store = SkillStore(db, ids, SystemClock())
        await store.save(_skill())
        loaded = await store.get("sk-1")
        assert loaded is not None
        assert loaded.name == "research: official sources"
        assert loaded.procedure_steps == ("check official feed", "cross-check", "cite")

    async def test_promotion_threshold(self, db: Database, ids: UuidGenerator) -> None:
        store = SkillStore(db, ids, SystemClock())
        await store.save(_skill(status=SkillStatus.CANDIDATE))
        # Two successes: not yet promoted (needs 3).
        s = await store.record_application("sk-1", success=True)
        assert s is not None and s.status == SkillStatus.CANDIDATE
        s = await store.record_application("sk-1", success=True)
        assert s is not None and s.status == SkillStatus.CANDIDATE
        # Third success: promoted.
        s = await store.record_application("sk-1", success=True)
        assert s is not None and s.status == SkillStatus.ACTIVE

    async def test_promotion_requires_success_rate(self, db: Database, ids: UuidGenerator) -> None:
        store = SkillStore(db, ids, SystemClock())
        await store.save(_skill())
        for outcome in (True, False, True):  # 0.67 < 0.7 after 3
            await store.record_application("sk-1", success=outcome)
        loaded = await store.get("sk-1")
        assert loaded is not None and loaded.status == SkillStatus.CANDIDATE

    async def test_demotion_on_collapse(self, db: Database, ids: UuidGenerator) -> None:
        store = SkillStore(db, ids, SystemClock())
        await store.save(_skill())
        for _ in range(3):
            await store.record_application("sk-1", success=True)
        # Promote, then fail repeatedly -> success rate decays below 0.3 -> disabled.
        for _ in range(8):
            await store.record_application("sk-1", success=False)
        loaded = await store.get("sk-1")
        assert loaded is not None and loaded.status == SkillStatus.DISABLED

    async def test_new_version_supersedes(self, db: Database, ids: UuidGenerator) -> None:
        store = SkillStore(db, ids, SystemClock())
        await store.save(_skill())
        new = await store.new_version(
            _skill_get := (await store.get("sk-1")),  # type: ignore[union-attr]
            description="updated approach",
        )
        assert new.version == 2 and new.superseded_by is None
        old = await store.get("sk-1")
        assert old is not None and old.superseded_by == new.id
        latest = await store.find_by_name(_skill_get.name)  # type: ignore[union-attr]
        assert latest is not None and latest.id == new.id

    async def test_active_skills_listing(self, db: Database, ids: UuidGenerator) -> None:
        store = SkillStore(db, ids, SystemClock())
        await store.save(_skill(id="a", status=SkillStatus.ACTIVE, confidence=0.9))
        await store.save(_skill(id="b", status=SkillStatus.CANDIDATE, confidence=0.99))
        active = await store.active_skills()
        assert [s.id for s in active] == ["a"]


def _trajectory(task_id: str) -> Trajectory:
    now = datetime.now(UTC)
    return Trajectory(
        id=f"traj-{task_id}",
        task_id=task_id,
        correlation_id="corr-1",
        request="r",
        goal="g",
        plan_steps=("s1",),
        risk_level="low",
        plan_confidence=0.8,
        actions=(ActionRecord(step=1, kind="tool_call"),),
        observations=(),
        decision_traces=(),
        failure_records=(),
        replan_count=0,
        verification_passed=True,
        verification_score=0.9,
        success=True,
        answer="ok",
        steps_taken=1,
        latency_ms=10,
        tokens_used=5,
        cost_usd=0.0,
        model_calls=1,
        tool_calls=1,
        created_ts=now,
        completed_ts=now,
    )


def _experience(exp_id: str, **kw: object) -> Experience:
    defaults: dict[str, object] = {
        "id": exp_id,
        "trajectory_id": "traj-task-1",
        "task_id": "task-1",
        "correlation_id": "corr-1",
        "category": ExperienceCategory.TOOL_USAGE,
        "lesson_text": "Use filesystem.read with encoding=utf-8 for JSON files",
        "applicability_context": "reading json config files",
        "confidence": 0.8,
        "reuse_count": 3,
        "success_rate": 0.9,
        "extracted_ts": datetime.now(UTC),
    }
    defaults.update(kw)
    return Experience(**defaults)  # type: ignore[arg-type]


class TestSkillPromoter:
    async def test_promotes_proven_experiences(self, db: Database, ids: UuidGenerator) -> None:
        traj = TrajectoryStore(db=db, ids=ids, clock=SystemClock())
        skills = SkillStore(db, ids, SystemClock())
        await traj.save_trajectory(_trajectory("task-1"))
        await traj.save_experience(_experience("e1"))

        promoter = SkillPromoter(trajectory_store=traj, skill_store=skills, ids=ids)
        created = await promoter.promote_from_experiences()
        assert len(created) == 1
        assert created[0].status == SkillStatus.CANDIDATE
        assert "tool_usage" in created[0].name

        # Idempotent: second pass creates nothing.
        assert await promoter.promote_from_experiences() == []

    async def test_unproven_experiences_not_promoted(self, db: Database, ids: UuidGenerator) -> None:
        traj = TrajectoryStore(db=db, ids=ids, clock=SystemClock())
        skills = SkillStore(db, ids, SystemClock())
        await traj.save_trajectory(_trajectory("task-1"))
        await traj.save_experience(_experience("e1", reuse_count=0, success_rate=0.0))

        promoter = SkillPromoter(trajectory_store=traj, skill_store=skills, ids=ids)
        assert await promoter.promote_from_experiences() == []


class TestStrategyStore:
    async def test_activation_requires_eval_score(self, db: Database, ids: UuidGenerator) -> None:
        store = StrategyStore(db, ids, SystemClock())
        await store.save(
            Strategy(
                id="st-1",
                task_type_pattern="research.*",
                approach="official sources first",
            )
        )
        # Evidence alone is not enough without offline eval score.
        for _ in range(4):
            st = await store.record_outcome("st-1", success=True)
        assert st is not None and st.status == StrategyStatus.CANDIDATE

        # With an eval score set, next outcome activates.
        st_eval = await store.get("st-1")
        assert st_eval is not None
        await store.save(st_eval.model_copy(update={"eval_score": 0.85}))
        st = await store.record_outcome("st-1", success=True)
        assert st is not None and st.status == StrategyStatus.ACTIVE

    async def test_active_for_matching_pattern(self, db: Database, ids: UuidGenerator) -> None:
        store = StrategyStore(db, ids, SystemClock())
        await store.save(
            Strategy(
                id="st-2",
                task_type_pattern="coding.*",
                approach="reproduce first",
                status=StrategyStatus.ACTIVE,
            )
        )
        matches = await store.active_for("coding.debug")
        assert len(matches) == 1
        assert await store.active_for("research.summary") == []


class TestWorldState:
    async def test_upsert_get_patch(self, db: Database) -> None:
        ws = WorldStateStore(db, SystemClock())
        await ws.upsert("repository", "atlas", {"branch": "main", "tests": "green"})
        e = await ws.get("repository", "atlas")
        assert e is not None and e.attributes["branch"] == "main"

        await ws.patch("repository", "atlas", {"branch": "batch-4"})
        e = await ws.get("repository", "atlas")
        assert e is not None
        assert e.attributes["branch"] == "batch-4"
        assert e.attributes["tests"] == "green"

    async def test_by_type_and_fragment(self, db: Database) -> None:
        ws = WorldStateStore(db, SystemClock())
        await ws.upsert("service", "ollama", {"healthy": True})
        await ws.upsert("file", "/tmp/x", {"size": 10})
        entities = await ws.by_type("service")
        assert len(entities) == 1 and entities[0].entity_id == "ollama"
        fragment = await ws.to_prompt_fragment()
        assert "service/ollama" in fragment

    async def test_delete(self, db: Database) -> None:
        ws = WorldStateStore(db, SystemClock())
        await ws.upsert("file", "/tmp/y", {})
        await ws.delete("file", "/tmp/y")
        assert await ws.get("file", "/tmp/y") is None


class TestExperienceInformedPlanning:
    async def test_planner_receives_prior_knowledge(self) -> None:
        """Planner injects prior_knowledge into the prompt."""
        from atlas.infra.ids import CorrelationId
        from atlas.infra.types import ModelResponse, ModelTarget
        from atlas.orchestration.planner import Planner
        from atlas.orchestration.types import Capabilities

        captured: dict[str, str] = {}

        class _FakeGateway:
            async def complete(self, req: object) -> ModelResponse:
                import atlas.infra.types as t

                assert isinstance(req, t.ModelRequest)
                captured["prompt"] = req.prompt
                return ModelResponse(
                    text='{"goal":"g","steps":[{"index":0,"intent":"i"}]}',
                    target=ModelTarget.LOCAL_FAST,
                    model="m",
                )

        planner = Planner(_FakeGateway())  # type: ignore[arg-type]
        plan = await planner.plan(
            "do research",
            "ctx",
            Capabilities(),
            CorrelationId("c"),  # type: ignore[arg-type]
            prior_knowledge="Lesson (tool_usage): use official feeds",
        )
        assert "PRIOR KNOWLEDGE" in captured["prompt"]
        assert "official feeds" in captured["prompt"]
        assert plan.goal == "g"

    async def test_orchestrator_prior_knowledge_best_effort(self) -> None:
        """_build_prior_knowledge degrades to empty string on store failure."""
        from atlas.orchestration.orchestrator import Orchestrator

        class _BrokenStore:
            async def active_skills(self, limit: int = 5) -> list[Skill]:
                raise RuntimeError("db down")

        orch = Orchestrator.__new__(Orchestrator)
        orch._skill_store = _BrokenStore()
        orch._trajectory_store = None
        orch._world_state = None
        assert await orch._build_prior_knowledge() == ""
