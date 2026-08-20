"""Batch 6 route tests — learning and ops endpoints against a real temp DB."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.interfaces.api.routes_learning import router as learning_router
from atlas.interfaces.api.routes_ops import router as ops_router
from atlas.memory.skills import Skill, SkillStore
from atlas.memory.strategies import Strategy, StrategyStore
from atlas.memory.world_state import WorldStateStore
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.tool_routing import ToolHealthTracker, ToolRouter


@pytest.fixture
async def db(tmp_path: Path):
    d = Database(tmp_path / "routes.db")
    await d.start()
    yield d
    await d.stop()


def _make_app(db: Database, **extra: object) -> TestClient:
    ids = UuidGenerator()
    clock = SystemClock()
    skill_store = SkillStore(db, ids, clock)
    strategy_store = StrategyStore(db, ids, clock)
    world_state = WorldStateStore(db, clock)
    registry = ToolRegistry()

    class _T:
        name = "filesystem"

        def dry_run(self, a: dict) -> str:
            return "p"

        async def execute(self, a: dict) -> None:
            raise NotImplementedError

    registry.register(
        _T(),
        ("read", "write"),
        ToolMetadata(name="filesystem", description="files", idempotent=False, side_effects=True),
    )
    health = ToolHealthTracker()
    health.record("filesystem", ok=True, latency_ms=25)
    tool_router = ToolRouter(registry, health)

    app = FastAPI()
    app.include_router(learning_router, prefix="/api/v1")
    app.include_router(ops_router, prefix="/api/v1")
    app.state.atlas = SimpleNamespace(
        db=db,
        skill_store=skill_store,
        strategy_store=strategy_store,
        world_state=world_state,
        tool_router=tool_router,
        **extra,
    )
    return TestClient(app)


class TestLearningRoutes:
    @pytest.mark.asyncio
    async def test_skills_roundtrip(self, db: Database) -> None:
        await SkillStore(db, UuidGenerator(), SystemClock()).save(
            Skill(
                id="sk-1",
                name="n",
                description="d",
                status="active",
                confidence=0.9,
                procedure_steps=("s1",),
            )
        )
        client = _make_app(db)
        resp = client.get("/api/v1/learning/skills")
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["id"] == "sk-1" and body[0]["status"] == "active"
        assert body[0]["procedure_steps"] == ["s1"]

        single = client.get("/api/v1/learning/skills/sk-1")
        assert single.status_code == 200

        missing = client.get("/api/v1/learning/skills/nope")
        assert missing.status_code == 404

    @pytest.mark.asyncio
    async def test_disable_skill(self, db: Database) -> None:
        await SkillStore(db, UuidGenerator(), SystemClock()).save(
            Skill(
                id="sk-2",
                name="n",
                description="d",
                status="active",
            )
        )
        client = _make_app(db)
        resp = client.post("/api/v1/learning/skills/sk-2/disable")
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_strategies_and_world(self, db: Database) -> None:
        await StrategyStore(db, UuidGenerator(), SystemClock()).save(
            Strategy(
                id="st-1",
                task_type_pattern="research.*",
                approach="official first",
                status="active",
            )
        )
        ws = WorldStateStore(db, SystemClock())
        await ws.upsert("service", "ollama", {"healthy": True})
        client = _make_app(db)
        strategies = client.get("/api/v1/learning/strategies?active_only=true").json()
        assert strategies[0]["task_type_pattern"] == "research.*"
        world = client.get("/api/v1/learning/world").json()
        assert world[0]["entity_id"] == "ollama"

    @pytest.mark.asyncio
    async def test_evaluation_and_analytics(self, db: Database) -> None:
        await db.conn.execute(
            "INSERT INTO evaluation_results (id, golden_id, run_id, evaluator, passed, "
            "score, created_ts) VALUES ('e1','g1','r1','deterministic',1,1.0,?)",
            (datetime.now(UTC).isoformat(),),
        )
        await db.conn.commit()
        client = _make_app(db)
        evals = client.get("/api/v1/learning/evaluation/recent").json()
        assert evals[0]["golden_id"] == "g1" and evals[0]["passed"] is True
        analytics = client.get("/api/v1/learning/analytics").json()
        assert analytics["total_trajectories"] == 0
        assert "generated_at" in analytics


class TestOpsRoutes:
    @pytest.mark.asyncio
    async def test_tools_listing(self, db: Database) -> None:
        client = _make_app(db)
        tools = client.get("/api/v1/ops/tools").json()
        assert tools[0]["name"] == "filesystem"
        assert tools[0]["side_effects"] is True
        assert 0 < tools[0]["health"] <= 1.0

    def test_models_listing(self, db: Database) -> None:
        # Ensure model_registry is populated so the route can render even
        # when the full atlas build graph isn't wired (matches real startup).
        from atlas.intelligence.registry.model_registry import ModelRegistry

        registry = ModelRegistry.from_yaml(Path(__file__).resolve().parents[2] / "config" / "models.yaml")
        app = FastAPI()
        app.include_router(ops_router, prefix="/api/v1")
        app.state.atlas = SimpleNamespace(model_registry=registry)
        TestClient(app).get("/api/v1/ops/models")  # must not 500 without atlas

    @pytest.mark.asyncio
    async def test_schedule_toggle(self, db: Database) -> None:
        now = datetime.now(UTC).isoformat()
        await db.conn.execute(
            "INSERT INTO schedules (id, description, cron_expression, task_template, "
            "enabled, next_run_ts, created_ts) "
            "VALUES ('sc-1','nightly consolidation','0 2 * * *','{}',1,?,?)",
            (now, now),
        )
        await db.conn.commit()
        client = _make_app(db)
        resp = client.post("/api/v1/ops/schedules/sc-1/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        missing = client.post("/api/v1/ops/schedules/nope/toggle")
        assert missing.status_code == 404
