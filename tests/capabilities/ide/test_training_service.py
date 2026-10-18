from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from atlas.capabilities.ide.contracts import CommandResult
from atlas.capabilities.ide.contracts import TestReport as IDETestReport
from tests.capabilities.ide.test_training_pipeline import jsonl, trace


class FakeIDE:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.commands: list[str] = []

    async def list_workspaces(self) -> tuple[Any, ...]:
        return (SimpleNamespace(id="workspace", root_paths=(str(self.root),)),)

    async def run_tests(self, workspace_id: str, command: str, **kwargs: Any) -> IDETestReport:
        self.commands.append(command)
        return IDETestReport(command=command, ok=True, exit_code=0, passed=3, failed=0, total=3)

    async def run_command(self, workspace_id: str, command: str, **kwargs: Any) -> CommandResult:
        self.commands.append(command)
        return CommandResult(command=command, ok=True, exit_code=0)


async def test_readiness_does_not_confuse_ollama_weights_with_local_training_artifacts(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.service import TrainingService

    service = TrainingService(ide=FakeIDE(tmp_path))
    (tmp_path / "model.gguf").write_bytes(b"GGUF")
    ready = await service.readiness("workspace", "model.gguf")
    assert ready.ready is False
    assert ready.reasons


async def test_prepare_requires_approval_and_verification_is_real_ide_output(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError
    from atlas.capabilities.ide.training.service import TrainingService

    ide = FakeIDE(tmp_path)
    service = TrainingService(ide=ide)
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    with pytest.raises(DatasetError, match="approval"):
        await service.prepare("workspace", "traces.jsonl", approved=False)
    assert not (tmp_path / ".atlas-training").exists()
    exported = await service.prepare("workspace", "traces.jsonl", approved=True)
    assert exported.training_completed is False
    proof = await service.verify("workspace", "pytest -q")
    assert proof["passed"] == 3
    assert ide.commands == ["pytest -q"]
    assert json.loads((tmp_path / proof["path"]).read_text())["ok"] is True


async def test_training_start_refuses_missing_runtime_and_never_reports_completion(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError
    from atlas.capabilities.ide.training.service import TrainingNotReady, TrainingService

    ide = FakeIDE(tmp_path)
    service = TrainingService(ide=ide)
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    exported = await service.prepare("workspace", "traces.jsonl", approved=True)
    with pytest.raises(DatasetError, match="approval"):
        await service.start_training("workspace", exported.export_id, "model", approved=False)
    with pytest.raises(TrainingNotReady):
        await service.start_training("workspace", exported.export_id, "model", approved=True)
    assert ide.commands == []


async def test_evaluation_gate_is_persisted_and_cannot_promote_without_real_adapter(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError, validate_jsonl
    from atlas.capabilities.ide.training.service import TrainingService

    service = TrainingService(ide=FakeIDE(tmp_path))
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    exported = await service.prepare("workspace", "traces.jsonl", approved=True)
    gold = validate_jsonl((tmp_path / exported.path / "holdout.jsonl").read_text())
    baseline = [{"case_id": row.identity, "tool_calls": [], "final_text": "incorrect"} for row in gold]
    candidate = [
        {
            "case_id": row.identity,
            "tool_calls": [row.messages[1].tool_calls[0].model_dump()],
            "final_text": row.messages[-1].content,
        }
        for row in gold
    ]
    (tmp_path / "baseline.jsonl").write_text(jsonl(baseline))
    (tmp_path / "candidate.jsonl").write_text(jsonl(candidate))
    report = await service.evaluate("workspace", exported.export_id, "baseline.jsonl", "candidate.jsonl")
    assert report["gate"]["passed"] is True
    with pytest.raises(DatasetError, match="artifact"):
        await service.promote("workspace", report["evaluation_id"], "missing-adapter", approved=True)


async def test_workspace_file_cannot_fabricate_a_passing_ide_verification(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError
    from atlas.capabilities.ide.training.service import TrainingService

    service = TrainingService(ide=FakeIDE(tmp_path))
    identity = "a" * 32
    path = tmp_path / ".atlas-training" / "verifications" / f"{identity}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"workspace_id": "workspace", "ok": True, "passed": 100}))
    with pytest.raises(DatasetError, match="integrity"):
        await service.export_runs("workspace", [f"run-{i}" for i in range(5)], identity, approved=True)


async def test_queued_job_cancellation_and_shutdown_do_not_leave_running_state(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.service import TrainerReadiness, TrainingService

    class BlockingIDE(FakeIDE):
        async def run_command(self, workspace_id: str, command: str, **kwargs: Any) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(command=command, ok=True, exit_code=0)

    service = TrainingService(ide=BlockingIDE(tmp_path))

    async def ready(*args: Any) -> TrainerReadiness:
        return TrainerReadiness(ready=True, reasons=[], installed=True, native_runtime=True, base_artifact_valid=True)

    service.readiness = ready  # type: ignore[method-assign]
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    exported = await service.prepare("workspace", "traces.jsonl", approved=True)
    first = await service.start_training("workspace", exported.export_id, "model", approved=True)
    assert first["status"] == "queued"
    cancelled = await service.cancel("workspace", first["job_id"])
    assert cancelled["status"] == "cancelled"
    second = await service.start_training("workspace", exported.export_id, "model", approved=True)
    await service.close()
    assert (
        next(job for job in await service.jobs("workspace") if job["job_id"] == second["job_id"])["status"]
        == "cancelled"
    )


async def test_training_recovery_settles_more_than_the_ui_page_limit(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.service import TrainingService

    service = TrainingService(ide=FakeIDE(tmp_path))
    for index in range(60):
        identity = f"{index:032x}"
        service._save(
            tmp_path,
            f".atlas-training/jobs/{identity}.json",
            {
                "job_id": identity,
                "workspace_id": "workspace",
                "status": "running",
                "training_completed": False,
            },
        )
    assert await service.recover_interrupted() == 60


async def test_continuations_remain_in_the_same_task_group_for_export(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError
    from atlas.capabilities.ide.training.service import TrainingService
    from atlas.intelligence.contracts import Message, Role
    from atlas.orchestration.agent_engine.records import (
        AgentEngineResult,
        AgentRunRecord,
        AgentStep,
        StopReason,
        ToolCallRecord,
    )

    records: dict[str, AgentRunRecord] = {}
    for index in range(6):
        records[f"run-{index}"] = AgentRunRecord(
            run_id=f"run-{index}",
            task_id=f"task-{index}",
            workspace_id="workspace",
            parent_run_id="run-0" if index else None,
            tool_names=("filesystem",),
            seed_messages=(Message(role=Role.USER, content=f"Read document {index}"),),
            result=AgentEngineResult(
                ok=True,
                stop_reason=StopReason.FINISHED,
                steps=(
                    AgentStep(
                        index=0,
                        tool_calls=(
                            ToolCallRecord(
                                call_id=f"call-{index}",
                                tool="filesystem",
                                operation="read",
                                args={"path": f"document-{index}.txt"},
                                ok=True,
                                output="synthetic document",
                            ),
                        ),
                    ),
                    AgentStep(index=1, assistant_text="done"),
                ),
            ),
        )

    class Runs:
        async def get_run(self, identity: str) -> AgentRunRecord | None:
            return records.get(identity)

    service = TrainingService(ide=FakeIDE(tmp_path), agent_runs=Runs())
    proof = await service.verify("workspace", "pytest -q")
    with pytest.raises(DatasetError, match="independent task groups"):
        await service.export_runs(
            "workspace", [f"run-{i}" for i in range(1, 6)], proof["verification_id"], approved=True
        )


async def test_training_manifest_key_survives_restart_and_exports_are_private(tmp_path: Path) -> None:
    import stat

    from atlas.capabilities.ide.training.service import TrainingService

    state_dir = tmp_path / "application-state"
    first = TrainingService(ide=FakeIDE(tmp_path), state_dir=state_dir)
    proof = await first.verify("workspace", "pytest -q")
    restarted = TrainingService(ide=FakeIDE(tmp_path), state_dir=state_dir)
    assert restarted._load(tmp_path, "verifications", proof["verification_id"])["ok"] is True
    assert stat.S_IMODE((state_dir / "manifest.key").stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE((tmp_path / proof["path"]).stat().st_mode) & 0o077 == 0
    assert (tmp_path / ".atlas-training" / ".gitignore").read_text().startswith("*")


async def test_other_service_does_not_recover_live_job_or_admit_parallel_training(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError
    from atlas.capabilities.ide.training.service import TrainerReadiness, TrainingService

    class BlockingIDE(FakeIDE):
        async def run_command(self, workspace_id: str, command: str, **kwargs: Any) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(command=command, ok=True, exit_code=0)

    state = tmp_path / "private-state"
    first = TrainingService(ide=BlockingIDE(tmp_path), state_dir=state)
    second = TrainingService(ide=BlockingIDE(tmp_path), state_dir=state)

    async def ready(*args: Any) -> TrainerReadiness:
        return TrainerReadiness(ready=True, reasons=[], installed=True, native_runtime=True, base_artifact_valid=True)

    first.readiness = second.readiness = ready  # type: ignore[method-assign]
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    exported = await first.prepare("workspace", "traces.jsonl", approved=True)
    try:
        job = await first.start_training("workspace", exported.export_id, "model", approved=True)
        assert await second.recover_interrupted() == 0
        assert second._load(tmp_path, "jobs", job["job_id"])["status"] in {"queued", "running"}
        with pytest.raises(DatasetError, match="active"):
            await second.start_training("workspace", exported.export_id, "model", approved=True)
    finally:
        await first.close()
        await second.close()


async def test_shutdown_is_bounded_and_retains_ownership_until_execution_stops(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from atlas.capabilities.ide.training import service as module
    from atlas.capabilities.ide.training.datasets import DatasetError
    from atlas.capabilities.ide.training.service import TrainerReadiness, TrainingService

    started, finish = asyncio.Event(), asyncio.Event()

    class StubbornIDE(FakeIDE):
        async def run_command(self, workspace_id: str, command: str, **kwargs: Any) -> CommandResult:
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                await finish.wait()
            return CommandResult(command=command, ok=False, exit_code=1)

    monkeypatch.setattr(module, "CANCEL_WAIT_S", 0.02, raising=False)
    state = tmp_path / "private-state"
    first = TrainingService(ide=StubbornIDE(tmp_path), state_dir=state)
    second = TrainingService(ide=FakeIDE(tmp_path), state_dir=state)

    async def ready(*args: Any) -> TrainerReadiness:
        return TrainerReadiness(ready=True, reasons=[], installed=True, native_runtime=True, base_artifact_valid=True)

    first.readiness = second.readiness = ready  # type: ignore[method-assign]
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    exported = await first.prepare("workspace", "traces.jsonl", approved=True)
    job = await first.start_training("workspace", exported.export_id, "model", approved=True)
    await started.wait()
    try:
        await asyncio.wait_for(first.close(), timeout=0.2)
        assert first._load(tmp_path, "jobs", job["job_id"])["status"] == "running"
        assert await second.recover_interrupted() == 0
        with pytest.raises(DatasetError, match="active"):
            await second.start_training("workspace", exported.export_id, "model", approved=True)
    finally:
        finish.set()
        await asyncio.gather(*first._jobs.values(), return_exceptions=True)
        await second.close()


def test_native_training_ownership_is_shared_across_processes(tmp_path: Path) -> None:
    import subprocess
    import sys

    from atlas.capabilities.ide.training.ownership import claim, release

    path = tmp_path / "execution.lock"
    descriptor = claim(path)
    assert descriptor is not None
    probe = 'import fcntl,sys; f=open(sys.argv[1],"r+"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
    try:
        blocked = subprocess.run([sys.executable, "-I", "-c", probe, str(path)], capture_output=True, timeout=5)
        assert blocked.returncode != 0
        assert b"BlockingIOError" in blocked.stderr
    finally:
        release(descriptor)
    available = subprocess.run([sys.executable, "-I", "-c", probe, str(path)], capture_output=True, timeout=5)
    assert available.returncode == 0
