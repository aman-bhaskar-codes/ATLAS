"""Explicit local training pipeline over IDE governance and durable file manifests."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import platform
import secrets
import shlex
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from atlas.capabilities.ide.training.artifacts import verify_base, verify_safetensors
from atlas.capabilities.ide.training.datasets import (
    DatasetError,
    DatasetExport,
    TrainingExample,
    contains_sensitive,
    ensure_private_training_dir,
    export_split,
    read_workspace_file,
    split_dataset,
    validate_jsonl,
    workspace_path,
    write_private,
)
from atlas.capabilities.ide.training.evaluation import compare_predictions
from atlas.capabilities.ide.training.ownership import claim, release
from atlas.infra.tasks import spawn
from atlas.infra.types import CostClass, ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import Message, Role
from atlas.intelligence.errors import IntelligenceError

CANCEL_WAIT_S = 2.0


class TrainingNotReadyError(DatasetError):
    """Actual local training requirements are unavailable (HTTP 503)."""


TrainingNotReady = TrainingNotReadyError


def build_training_command(base: Path, data: Path, adapter: Path, iterations: int) -> str:
    # -I removes cwd/PYTHONPATH shadowing; the script is our installed source,
    # never a trainer script selected from an untrusted workspace.
    return shlex.join(
        [
            "python",
            "-I",
            str(Path(__file__).with_name("runner.py").resolve()),
            "--model",
            str(base),
            "--data",
            str(data),
            "--adapter-path",
            str(adapter),
            "--iters",
            str(iterations),
        ]
    )


def _interpreter_matches() -> bool:
    interpreter = shutil.which("python")
    if interpreter is None:
        return False
    actual, expected = Path(interpreter).absolute(), Path(sys.executable).absolute()
    return actual.parent == expected.parent and actual.resolve() == expected.resolve()


def _normalized_training_messages(record: Any) -> list[Message]:
    """Transform a stored run into trainable turns, excluding opaque vendor state.

    This is a training export, distinct from exact provider conversation replay.
    It consumes the run's structural fields without importing a higher layer.
    """
    messages: list[Message] = list(record.seed_messages)
    for step in record.result.steps:
        calls = tuple(
            ProviderToolCall(
                id=call.call_id,
                name=call.tool,
                arguments={"operation": call.operation, "args": call.args},
            )
            for call in step.tool_calls
        )
        messages.append(Message(role=Role.ASSISTANT, content=step.assistant_text, tool_calls=calls))
        for call in step.tool_calls:
            payload = {"ok": True, "output": call.output} if call.ok else {"ok": False, "error": call.error}
            messages.append(
                Message(
                    role=Role.TOOL, content=json.dumps(payload, default=str), name=call.tool, tool_call_id=call.call_id
                )
            )
    return messages


class TrainerReadiness(BaseModel):
    ready: bool
    reasons: list[str]
    trainer: str = "mlx_lm LoRA"
    installed: bool
    native_runtime: bool
    base_artifact_valid: bool
    training_completed: bool = False


class TrainingService:
    def __init__(
        self,
        *,
        ide: Any,
        agent_runs: Any = None,
        native_runtime: bool = False,
        state_dir: Path | None = None,
        gateway: Any = None,
        model_registry: Any = None,
    ) -> None:
        self._gateway = gateway
        self._registry = model_registry
        self._ide = ide
        self._runs = agent_runs
        self._native_runtime = native_runtime
        self._jobs: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self._job_contexts: dict[str, tuple[Path, str]] = {}
        self._closed = False
        self._state_dir = state_dir
        self._ownership: dict[str, int] = {}
        if state_dir is None:
            self._manifest_key = secrets.token_bytes(32)
        else:
            state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            state_dir.chmod(0o700)
            key_path = state_dir / "manifest.key"
            if key_path.is_symlink():
                raise DatasetError("training state key must not be a symlink")
            if not key_path.exists():
                descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(secrets.token_bytes(32))
            with key_path.open("rb") as stream:
                self._manifest_key = stream.read(33)
            if len(self._manifest_key) != 32:
                raise DatasetError("training state key is invalid")

    def _signature(self, value: dict[str, Any]) -> str:
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self._manifest_key, body, hashlib.sha256).hexdigest()

    async def _root(self, workspace_id: str) -> Path:
        for workspace in await self._ide.list_workspaces():
            if str(workspace.id) == workspace_id and workspace.root_paths:
                root = await asyncio.to_thread(Path(workspace.root_paths[0]).resolve, strict=True)
                if await asyncio.to_thread(root.is_dir):
                    return root
        raise DatasetError("select an existing IDE workspace")

    def _save(self, root: Path, relative: str, value: dict[str, Any]) -> None:
        ensure_private_training_dir(root)
        path = workspace_path(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
        payload = dict(value)
        payload.pop("_signature", None)
        write_private(temporary, json.dumps(payload | {"_signature": self._signature(payload)}, indent=2))
        temporary.replace(path)

    def _verified_file(self, root: Path, relative: str) -> dict[str, Any]:
        value = json.loads(read_workspace_file(root, relative))
        if not isinstance(value, dict):
            raise DatasetError("training manifest integrity check failed")
        signature = value.pop("_signature", None)
        if not isinstance(signature, str) or not hmac.compare_digest(signature, self._signature(value)):
            raise DatasetError("training manifest integrity check failed")
        return value

    def _load(self, root: Path, kind: str, identity: str) -> dict[str, Any]:
        if len(identity) != 32 or any(char not in "0123456789abcdef" for char in identity):
            raise DatasetError("invalid training record identifier")
        return self._verified_file(root, f".atlas-training/{kind}/{identity}.json")

    def _export(self, root: Path, export_id: str) -> dict[str, Any]:
        if len(export_id) != 32 or any(char not in "0123456789abcdef" for char in export_id):
            raise DatasetError("invalid dataset export identifier")
        relative = f".atlas-training/exports/{export_id}"
        manifest = self._verified_file(root, f"{relative}/manifest.json")

        for name in ("train.jsonl", "valid.jsonl", "holdout.jsonl"):
            text = read_workspace_file(root, f"{relative}/{name}")
            if hashlib.sha256(text.encode()).hexdigest() != manifest["file_hashes"][name]:
                raise DatasetError("approved dataset changed; validate and export it again")
            if any(contains_sensitive(json.loads(line)) for line in text.splitlines() if line.strip()):
                raise DatasetError("dataset contains sensitive data")
        return manifest

    async def readiness(self, workspace_id: str, base_path: str = "") -> TrainerReadiness:
        root = await self._root(workspace_id)
        reasons: list[str] = []
        installed = importlib.util.find_spec("mlx_lm") is not None
        if not installed:
            reasons.append("mlx_lm is not installed in the API interpreter; no dependency is installed automatically")
        if platform.system() != "Darwin" or platform.machine() not in {"arm64", "aarch64"}:
            reasons.append(
                "local MLX training requires Apple silicon; preparation and evaluation work on all platforms"
            )
        if not self._native_runtime:
            reasons.append("governed native execution is required; a Docker/Linux sandbox cannot run this Mac trainer")
        if not await asyncio.to_thread(_interpreter_matches):
            reasons.append("governed python on PATH must match the API interpreter")
        valid = False
        if base_path:
            try:
                await asyncio.to_thread(verify_base, root, base_path)
                valid = True
            except DatasetError as exc:
                reasons.append(str(exc))
        else:
            reasons.append("select an existing local quantized MLX checkpoint directory")
        return TrainerReadiness(
            ready=not reasons,
            reasons=reasons,
            installed=installed,
            native_runtime=self._native_runtime,
            base_artifact_valid=valid,
        )

    async def validate(self, workspace_id: str, path: str) -> dict[str, Any]:
        root = await self._root(workspace_id)
        examples = validate_jsonl(await asyncio.to_thread(read_workspace_file, root, path))
        split = split_dataset(examples)
        return {
            "valid": True,
            "examples": len(examples),
            "groups": len({row.group_id for row in examples}),
            "train_count": len(split.train),
            "validation_count": len(split.validation),
            "holdout_count": len(split.holdout),
        }

    async def prepare(self, workspace_id: str, path: str, *, approved: bool) -> DatasetExport:
        if not approved:
            raise DatasetError("explicit local dataset export approval is required")
        root = await self._root(workspace_id)
        examples = validate_jsonl(await asyncio.to_thread(read_workspace_file, root, path))
        exported = await asyncio.to_thread(export_split, root, split_dataset(examples), approved=True)
        self._save(
            root,
            f"{exported.path}/manifest.json",
            json.loads(read_workspace_file(root, f"{exported.path}/manifest.json")),
        )
        return exported

    async def verify(self, workspace_id: str, command: str) -> dict[str, Any]:
        root = await self._root(workspace_id)
        if contains_sensitive(command):
            raise DatasetError("verification command contains a credential or private identifier")
        report = await self._ide.run_tests(workspace_id, command, timeout_s=300)
        identity = uuid4().hex
        value = {
            "verification_id": identity,
            "workspace_id": workspace_id,
            "ok": report.ok
            and report.exit_code == 0
            and (report.passed or 0) > 0
            and report.failed == 0
            and not report.denied
            and report.error is None,
            "passed": report.passed,
            "failed": report.failed,
            "command": command,
            "created_at": datetime.now(UTC).isoformat(),
            "path": f".atlas-training/verifications/{identity}.json",
        }
        self._save(root, value["path"], value)
        return value

    async def _task_group(self, record: Any, workspace_id: str) -> str:
        seen: set[str] = set()
        current = record
        for _ in range(64):
            if current.run_id in seen:
                raise DatasetError("agent-run lineage contains a cycle")
            seen.add(current.run_id)
            if not current.parent_run_id:
                return str(current.task_id or current.run_id)
            parent = await self._runs.get_run(current.parent_run_id)
            if parent is None or parent.workspace_id != workspace_id:
                raise DatasetError("agent-run lineage is missing or crosses a workspace boundary")
            current = parent
        raise DatasetError("agent-run lineage is too deep for bounded export")

    async def export_runs(
        self,
        workspace_id: str,
        run_ids: list[str],
        verification_id: str,
        *,
        approved: bool,
    ) -> DatasetExport:
        if not approved:
            raise DatasetError("explicit review and local export approval is required")
        root = await self._root(workspace_id)
        proof = self._load(root, "verifications", verification_id)
        if proof.get("workspace_id") != workspace_id or not proof.get("ok"):
            raise DatasetError("a passing IDE verification from this workspace is required")
        if self._runs is None:
            raise TrainingNotReady("agent-run storage is unavailable")
        if not 5 <= len(set(run_ids)) <= 500:
            raise DatasetError("select 5-500 explicitly reviewed run IDs")
        examples: list[TrainingExample] = []
        for run_id in dict.fromkeys(run_ids):
            record = await self._runs.get_run(run_id)
            if record is None or record.workspace_id != workspace_id or record.result is None or not record.result.ok:
                raise DatasetError("selected run is unavailable, unfinished, failed, or belongs to another workspace")
            messages = _normalized_training_messages(record)
            messages = [
                message.model_copy(
                    update={
                        "tool_calls": tuple(
                            call.model_copy(update={"thought_signature": None}) for call in message.tool_calls
                        )
                    }
                )
                for message in messages
            ]
            row = TrainingExample(
                group_id=await self._task_group(record, workspace_id),
                messages=tuple(messages),
                tools=tuple(ToolCallSpec(name=name, parameters={"type": "object"}) for name in record.tool_names),
            )
            examples.extend(validate_jsonl(row.model_dump_json()))
        exported = await asyncio.to_thread(export_split, root, split_dataset(examples), approved=True)
        self._save(
            root,
            f"{exported.path}/manifest.json",
            json.loads(read_workspace_file(root, f"{exported.path}/manifest.json")),
        )
        source = {"run_ids": run_ids, "verification_id": verification_id, "approved_local_export": True}
        self._save(root, f"{exported.path}/provenance.json", source)
        return exported

    async def evaluate(
        self,
        workspace_id: str,
        export_id: str,
        baseline_path: str,
        candidate_path: str,
        adapter_path: str = "",
    ) -> dict[str, Any]:
        root = await self._root(workspace_id)
        exported = self._export(root, export_id)
        gold = validate_jsonl(read_workspace_file(root, f"{exported['path']}/holdout.jsonl"))
        baseline = [json.loads(line) for line in read_workspace_file(root, baseline_path).splitlines() if line.strip()]
        candidate = [
            json.loads(line) for line in read_workspace_file(root, candidate_path).splitlines() if line.strip()
        ]
        gate = compare_predictions(gold, baseline, candidate)
        digest = None
        if adapter_path:
            digest = await asyncio.to_thread(
                verify_safetensors, workspace_path(root, f"{adapter_path}/adapters.safetensors")
            )
        identity = uuid4().hex
        value = {
            "evaluation_id": identity,
            "source": "prediction_files",
            "export_id": export_id,
            "dataset_digest": exported["digest"],
            "gate": gate.model_dump(),
            "candidate_artifact_sha256": digest,
            "path": f".atlas-training/evaluations/{identity}.json",
            "training_completed": False,
        }
        self._save(root, value["path"], value)
        return value

    def _candidate_artifact(self, root: Path, export_id: str, adapter_path: str) -> str:
        digest = verify_safetensors(workspace_path(root, f"{adapter_path}/adapters.safetensors"))
        dataset = self._export(root, export_id)
        for path in workspace_path(root, ".atlas-training/jobs").glob("*.json"):
            job = self._verified_file(root, path.relative_to(root).as_posix())
            if (
                job.get("status") == "trained"
                and job.get("training_completed") is True
                and job.get("artifact_sha256") == digest
                and job.get("export_id") == export_id
                and job.get("dataset_digest") == dataset["digest"]
            ):
                return digest
        raise TrainingNotReady("candidate must be an artifact from a verified completed local training job")

    async def evaluate_runtime(
        self,
        workspace_id: str,
        export_id: str,
        baseline_model: str,
        candidate_model: str,
        adapter_path: str,
        *,
        approved: bool,
    ) -> dict[str, Any]:
        from atlas.capabilities.ide.training.runtime_evaluation import predict

        if not approved:
            raise DatasetError("explicit local evaluation compute approval is required")
        if self._gateway is None or self._registry is None:
            raise TrainingNotReady("local inference evaluation runtime is unavailable")
        specs = [self._registry.get(identity) for identity in (baseline_model, candidate_model)]
        if any(
            spec is None
            or not spec.enabled
            or spec.cost_class != CostClass.LOCAL
            or spec.provider != "ollama"
            or not spec.supports_tool_calling
            for spec in specs
        ):
            raise TrainingNotReady("evaluation requires enabled local tool-calling baseline and candidate models")
        if baseline_model == candidate_model:
            raise DatasetError("baseline and candidate must be distinct registered models")
        root = await self._root(workspace_id)
        exported = self._export(root, export_id)
        digest = await asyncio.to_thread(self._candidate_artifact, root, export_id, adapter_path)
        if specs[1].artifact_sha256 != digest:
            raise TrainingNotReady("candidate serving model must be explicitly bound to the verified adapter SHA-256")
        gold = validate_jsonl(read_workspace_file(root, f"{exported['path']}/holdout.jsonl"))
        try:
            async with asyncio.timeout(120):
                baseline = await predict(self._gateway, gold, baseline_model, specs[0].provider)
                candidate = await predict(self._gateway, gold, candidate_model, specs[1].provider)
        except TimeoutError as exc:
            raise TrainingNotReady("local runtime evaluation exceeded its 120-second deadline") from exc
        except IntelligenceError as exc:
            raise TrainingNotReady("local inference evaluation failed; verify registered model availability") from exc
        gate = compare_predictions(gold, baseline, candidate)
        identity = uuid4().hex
        value = {
            "evaluation_id": identity,
            "export_id": export_id,
            "source": "local_runtime",
            "dataset_digest": exported["digest"],
            "gate": gate.model_dump(),
            "baseline_model": baseline_model,
            "candidate_model": candidate_model,
            "candidate_artifact_sha256": digest,
            "path": f".atlas-training/evaluations/{identity}.json",
            "training_completed": False,
        }
        self._save(root, value["path"], value)
        return value

    async def promote(
        self, workspace_id: str, evaluation_id: str, adapter_path: str, *, approved: bool
    ) -> dict[str, Any]:
        if not approved:
            raise DatasetError("explicit candidate promotion approval is required")
        root = await self._root(workspace_id)
        report = self._load(root, "evaluations", evaluation_id)
        if report.get("source") != "local_runtime":
            raise DatasetError("candidate artifact promotion requires actual local runtime evaluation")
        dataset = self._export(root, report["export_id"])
        if dataset["digest"] != report["dataset_digest"]:
            raise DatasetError("evaluated dataset changed; candidate promotion is refused")
        artifact = workspace_path(root, f"{adapter_path}/adapters.safetensors")
        digest = await asyncio.to_thread(verify_safetensors, artifact)
        if not report["gate"]["passed"] or digest != report.get("candidate_artifact_sha256"):
            raise DatasetError("candidate artifact must match an improved, safe held-out evaluation")
        jobs = workspace_path(root, ".atlas-training/jobs")
        verified = False
        for path in jobs.glob("*.json"):
            job = self._verified_file(root, str(path.relative_to(root)))
            if (
                job.get("artifact_sha256") == digest
                and job.get("status") == "trained"
                and job.get("training_completed") is True
                and job.get("export_id") == report["export_id"]
                and job.get("dataset_digest") == report["dataset_digest"]
            ):
                verified = True
                break
        if not verified:
            raise DatasetError("candidate artifact must come from a verified completed training job")
        value = {
            "status": "candidate_approved",
            "artifact_sha256": digest,
            "adapter_path": adapter_path,
            "evaluation_id": evaluation_id,
            "active_model_registered": False,
        }
        self._save(root, f".atlas-training/promotions/{uuid4().hex}.json", value)
        return value

    async def start_training(
        self,
        workspace_id: str,
        export_id: str,
        base_path: str,
        *,
        approved: bool,
        iterations: int = 100,
        timeout_s: float = 900,
    ) -> dict[str, Any]:
        if not approved:
            raise DatasetError("explicit dataset and compute approval is required before weight training")
        if not 1 <= iterations <= 500 or not 1 <= timeout_s <= 1800:
            raise DatasetError("training iteration/runtime bounds are invalid")
        root = await self._root(workspace_id)
        async with self._lock:
            if self._closed:
                raise TrainingNotReady("training workflow is shutting down")
            if any(not task.done() for task in self._jobs.values()):
                raise DatasetError("another local training job is active")
            ready = await self.readiness(workspace_id, base_path)
            if self._closed:
                raise TrainingNotReady("training workflow is shutting down")
            if not ready.ready:
                raise TrainingNotReady("; ".join(ready.reasons))
            exported = self._export(root, export_id)
            job_id = uuid4().hex
            relative = f".atlas-training/adapters/{job_id}"
            command = build_training_command(
                workspace_path(root, base_path),
                workspace_path(root, exported["path"]),
                workspace_path(root, relative),
                iterations,
            )
            value = {
                "job_id": job_id,
                "workspace_id": workspace_id,
                "export_id": export_id,
                "status": "queued",
                "base_path": base_path,
                "adapter_path": relative,
                "training_completed": False,
                "dataset_digest": exported["digest"],
                "iterations": iterations,
                "timeout_s": timeout_s,
                "memory_limit_bytes": 4 * 1024**3,
                "batch_size": 1,
                "max_sequence_length": 512,
                "trainable_layers": 4,
            }
            descriptor = self._claim(root)
            if descriptor is None:
                raise DatasetError("another local training job is active")
            try:
                self._save(root, f".atlas-training/jobs/{job_id}.json", value)
            except BaseException:
                release(descriptor)
                raise
            self._ownership[job_id] = descriptor
            task = spawn(self._train(root, value, command, timeout_s), name=f"training:{job_id}")
            self._jobs[job_id] = task
            self._job_contexts[job_id] = (root, workspace_id)
            task.add_done_callback(lambda done: self._settle(job_id, done))
            return value

    async def _train(self, root: Path, job: dict[str, Any], command: str, timeout_s: float) -> None:
        relative = f".atlas-training/jobs/{job['job_id']}.json"
        job = dict(job, status="running")
        self._save(root, relative, job)
        try:
            result = await self._ide.run_command(job["workspace_id"], command, timeout_s=timeout_s)
            if not result.ok or result.exit_code != 0 or result.denied:
                job.update(status="denied" if result.denied else "failed", exit_code=result.exit_code)
            else:
                artifact = workspace_path(root, f"{job['adapter_path']}/adapters.safetensors")
                digest = await asyncio.to_thread(verify_safetensors, artifact)
                job.update(status="trained", artifact_sha256=digest, training_completed=True)
        except asyncio.CancelledError:
            job.update(status="cancelled")
            raise
        except Exception:
            job.update(status="failed", error="training did not produce a verified local adapter artifact")
        finally:
            self._save(root, relative, job)

    async def jobs(self, workspace_id: str) -> list[dict[str, Any]]:
        root = await self._root(workspace_id)
        directory = workspace_path(root, ".atlas-training/jobs")
        return [
            self._verified_file(root, str(path.relative_to(root)))
            for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]
        ]

    async def exports(self, workspace_id: str) -> list[DatasetExport]:
        """Previously service-approved preparation metadata; no transcript payloads."""
        root = await self._root(workspace_id)
        directory = workspace_path(root, ".atlas-training/exports")
        result: list[DatasetExport] = []
        for path in sorted(directory.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                value = self._verified_file(root, path.relative_to(root).as_posix())
            except DatasetError:
                # CLI/untrusted manifests never acquire runtime approval on GET.
                continue
            result.append(DatasetExport.model_validate(value))
            if len(result) >= 50:
                break
        return result

    def _claim(self, root: Path) -> int | None:
        if self._state_dir is not None:
            return claim(self._state_dir / "execution.lock")
        ensure_private_training_dir(root)
        return claim(workspace_path(root, ".atlas-training/execution.lock"))

    def _settle(self, job_id: str, task: asyncio.Task[Any]) -> None:
        # Also handles cancellation before _train enters its try/finally.
        try:
            root, _ = self._job_contexts[job_id]
            job = self._load(root, "jobs", job_id)
            if job.get("status") in {"queued", "running"}:
                job.update(status="cancelled" if task.cancelled() else "failed", training_completed=False)
                self._save(root, f".atlas-training/jobs/{job_id}.json", job)
        finally:
            descriptor = self._ownership.pop(job_id, None)
            if descriptor is not None:
                release(descriptor)

    async def cancel(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        root = await self._root(workspace_id)
        job = self._load(root, "jobs", job_id)
        task = self._jobs.get(job_id)
        if task is None and job.get("status") in {"queued", "running"}:
            raise TrainingNotReady("training is owned by another process; cancel it through its owning runtime")
        if task is not None and not task.done():
            job.update(cancel_requested=True)
            self._save(root, f".atlas-training/jobs/{job_id}.json", job)
            task.cancel()
            await asyncio.wait({task}, timeout=CANCEL_WAIT_S)
        if task is not None and task.done() and job_id in self._ownership:
            self._settle(job_id, task)
        return self._load(root, "jobs", job_id)

    async def close(self) -> None:
        self._closed = True
        tasks = {task for task in self._jobs.values() if not task.done()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=CANCEL_WAIT_S)
        for job_id, task in self._jobs.items():
            if task.done() and job_id in self._ownership:
                self._settle(job_id, task)
        # Pending tasks retain ownership. Never claim that unconfirmed child
        # execution has stopped or admit replacement work after a timeout.

    async def recover_interrupted(self) -> int:
        recovered = 0
        for workspace in await self._ide.list_workspaces():
            root = await self._root(str(workspace.id))
            descriptor = self._claim(root)
            if descriptor is None:
                continue
            try:
                directory = workspace_path(root, ".atlas-training/jobs")
                for path in directory.glob("*.json"):
                    job = self._verified_file(root, str(path.relative_to(root)))
                    if job.get("status") in {"queued", "running"} and job["job_id"] not in self._jobs:
                        job.update(status="interrupted", training_completed=False)
                        self._save(root, f".atlas-training/jobs/{job['job_id']}.json", job)
                        recovered += 1
            finally:
                release(descriptor)
        return recovered
