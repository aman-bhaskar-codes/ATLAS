"""build_agent_engine — the agent-run optional-subsystem bootstrap (M2.1).

Mirrors bootstrap/ide: returns service=None (never raises) when the subsystem is
off or its one durable dependency (a run store / shared DB) is missing; otherwise
wires a real AgentRunService onto the orchestration dispatcher + router and the
shared SQLite substrate the rest of the runtime uses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atlas.bootstrap.agent_engine import build_agent_engine
from atlas.infra.config import AgentEngineCfg, AppConfig
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId
from atlas.infra.types import ToolCallSpec
from atlas.orchestration.agent_engine.service import AgentRunService


class FakeSettings:
    # Hermetic: pin the Supabase seam empty so the store choice depends only on the
    # ``db`` arg, never on the ambient atlas/.env (the stale-.env trap).
    supabase_db_connection_string = ""


class FakeGateway:
    async def infer(self, request: Any) -> Any:  # pragma: no cover - not exercised here
        raise NotImplementedError


class FakeDispatcher:
    async def dispatch(self, action: Any, correlation_id: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


class FakeRouter:
    def shortlist_specs(
        self, intent: str = "", *, max_tools: int = 8, needs_side_effects: bool = False
    ) -> tuple[ToolCallSpec, ...]:
        return ()


class FakeIds:
    def task_id(self) -> TaskId:
        return TaskId("id1")

    def correlation_id(self) -> CorrelationId:
        return CorrelationId("cid1")

    def execution_id(self) -> ExecutionId:
        return ExecutionId("run1")


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 25, tzinfo=UTC)


def _config(*, enabled: bool) -> AppConfig:
    return AppConfig(agent_engine=AgentEngineCfg(enabled=enabled))


def _build(config: AppConfig, *, db: Database | None) -> Any:
    return build_agent_engine(
        FakeSettings(),  # type: ignore[arg-type]
        config,
        gateway=FakeGateway(),  # type: ignore[arg-type]
        dispatcher=FakeDispatcher(),  # type: ignore[arg-type]
        tool_router=FakeRouter(),  # type: ignore[arg-type]
        events=None,
        ids=FakeIds(),  # type: ignore[arg-type]
        clock=FakeClock(),  # type: ignore[arg-type]
        db=db,
    )


def test_disabled_returns_none() -> None:
    assert _build(_config(enabled=False), db=None).service is None


def test_enabled_without_store_degrades_to_none() -> None:
    # No shared DB and no Supabase connection (Settings() default is empty) -> no
    # durable store -> degrade cleanly rather than serve an amnesiac agent.
    assert _build(_config(enabled=True), db=None).service is None


def test_enabled_with_db_builds_service(tmp_path: Path) -> None:
    # Construction does no I/O (SqliteAgentRunStore just holds the db handle), so an
    # unstarted Database is enough to prove the service is wired.
    db = Database(tmp_path / "atlas.db")
    comps = _build(_config(enabled=True), db=db)
    assert isinstance(comps.service, AgentRunService)
