"""build_ide — the ADE optional-subsystem bootstrap.

Mirrors bootstrap/voice + computer_use: returns service=None (never raises) when
the subsystem is off or its one hard dependency (the filesystem tool) is missing;
otherwise wires a real IDEService onto the SAME safety funnel + filesystem tool
the rest of the runtime uses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from atlas.bootstrap.ide import build_ide
from atlas.capabilities.ide.service import IDEService
from atlas.infra.config import AppConfig, IDECfg
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId


class FakeFilesystemTool:
    name = "filesystem"

    def dry_run(self, args: dict[str, Any]) -> str:
        return "WRITE"

    async def execute(self, args: dict[str, Any]) -> Any:  # pragma: no cover - not exercised here
        raise NotImplementedError


class FakeSafety:
    async def guard(self, req: Any, tool: Any) -> Any:  # pragma: no cover - not exercised here
        raise NotImplementedError


class FakeIds:
    def task_id(self) -> TaskId:
        return TaskId("id1")

    def correlation_id(self) -> CorrelationId:
        return CorrelationId("cid1")

    def execution_id(self) -> ExecutionId:
        return ExecutionId("exec1")


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 29, tzinfo=UTC)


def _config(*, enabled: bool) -> AppConfig:
    return AppConfig(ide=IDECfg(enabled=enabled))


def test_disabled_returns_none() -> None:
    comps = build_ide(
        _config(enabled=False),
        safety=FakeSafety(),  # type: ignore[arg-type]
        filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
        ids=FakeIds(),  # type: ignore[arg-type]
        clock=FakeClock(),  # type: ignore[arg-type]
    )
    assert comps.service is None


def test_enabled_without_filesystem_degrades_to_none() -> None:
    comps = build_ide(
        _config(enabled=True),
        safety=FakeSafety(),  # type: ignore[arg-type]
        filesystem_tool=None,
        ids=FakeIds(),  # type: ignore[arg-type]
        clock=FakeClock(),  # type: ignore[arg-type]
    )
    assert comps.service is None


def test_enabled_with_filesystem_builds_service() -> None:
    comps = build_ide(
        _config(enabled=True),
        safety=FakeSafety(),  # type: ignore[arg-type]
        filesystem_tool=FakeFilesystemTool(),  # type: ignore[arg-type]
        ids=FakeIds(),  # type: ignore[arg-type]
        clock=FakeClock(),  # type: ignore[arg-type]
    )
    assert isinstance(comps.service, IDEService)
