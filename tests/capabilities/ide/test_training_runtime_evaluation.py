from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from atlas.capabilities.ide.training.datasets import DatasetError, validate_jsonl
from atlas.capabilities.ide.training.service import TrainingNotReady, TrainingService
from atlas.infra.types import CostClass, CostPolicy, NetworkPolicy, PrivacyClass, ProviderToolCall
from atlas.intelligence.contracts import InferenceResponse, ModelSpec
from tests.capabilities.ide.test_training_pipeline import jsonl, trace
from tests.capabilities.ide.test_training_service import FakeIDE


async def test_runtime_evaluation_generates_local_predictions_without_gold_answer_leakage(tmp_path: Path) -> None:
    requests: list[Any] = []

    class Gateway:
        async def infer(self, request: Any) -> InferenceResponse:
            requests.append(request)
            assert request.constraints.privacy_class == PrivacyClass.SECRET
            assert request.constraints.network_policy == NetworkPolicy.OFFLINE
            assert request.constraints.cost_policy == CostPolicy.ZERO_COST
            assert request.messages[-1].role.value != "assistant"
            assert all(message.role.value != "assistant" or not message.content for message in request.messages)
            model = request.constraints.pinned_model
            task = request.messages[0].content.split()[2]
            if request.messages[-1].role.value == "tool":
                return InferenceResponse(text=f"Read {task}", model_id=model, provider="ollama")
            if model == "baseline":
                return InferenceResponse(text="incorrect", model_id=model, provider="ollama")
            return InferenceResponse(
                text="",
                model_id=model,
                provider="ollama",
                tool_calls=(
                    ProviderToolCall(
                        id="fresh-id",
                        name="filesystem",
                        arguments={"operation": "read", "args": {"path": f"{task}.txt"}},
                    ),
                ),
            )

    specs = {
        name: ModelSpec(
            id=name,
            provider="ollama",
            provider_model=name,
            context_length=8192,
            usd_per_1m_input=0,
            usd_per_1m_output=0,
            cost_class=CostClass.LOCAL,
            supports_tool_calling=True,
            artifact_sha256="a" * 64 if name == "candidate" else None,
        )
        for name in ("baseline", "candidate")
    }
    service = TrainingService(ide=FakeIDE(tmp_path), gateway=Gateway(), model_registry=SimpleNamespace(get=specs.get))
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    exported = await service.prepare("workspace", "traces.jsonl", approved=True)
    # Simulate already verified local artifact provenance, without generating weights.
    service._candidate_artifact = lambda *args: "a" * 64  # type: ignore[method-assign]
    result = await service.evaluate_runtime(
        "workspace", exported.export_id, "baseline", "candidate", "adapter", approved=True
    )
    assert result["gate"]["passed"] is True
    assert result["source"] == "local_runtime"
    assert requests
    assert result["candidate_artifact_sha256"] == "a" * 64


async def test_runtime_evaluation_refuses_cloud_or_missing_artifact_before_inference(tmp_path: Path) -> None:
    class Gateway:
        async def infer(self, request: Any) -> Any:
            pytest.fail("unverified/cloud candidate must never be invoked")

    spec = ModelSpec(
        id="cloud",
        provider="gemini",
        provider_model="cloud",
        context_length=8192,
        usd_per_1m_input=0,
        usd_per_1m_output=0,
        supports_tool_calling=True,
    )
    service = TrainingService(
        ide=FakeIDE(tmp_path), gateway=Gateway(), model_registry=SimpleNamespace(get=lambda _: spec)
    )
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    exported = await service.prepare("workspace", "traces.jsonl", approved=True)
    with pytest.raises(DatasetError, match="approval"):
        await service.evaluate_runtime("workspace", exported.export_id, "cloud", "cloud", "adapter", approved=False)
    with pytest.raises(TrainingNotReady, match="local"):
        await service.evaluate_runtime("workspace", exported.export_id, "cloud", "candidate", "adapter", approved=True)


async def test_runtime_replay_never_executes_or_replays_output_for_wrong_tool_arguments() -> None:
    from atlas.capabilities.ide.training.runtime_evaluation import predict

    gold = validate_jsonl(jsonl([trace("task")]))
    calls = 0

    class Gateway:
        async def infer(self, request: Any) -> InferenceResponse:
            nonlocal calls
            calls += 1
            return InferenceResponse(
                text="",
                model_id="local",
                provider="ollama",
                tool_calls=(
                    ProviderToolCall(
                        id="x", name="filesystem", arguments={"operation": "delete", "args": {"path": "notes.txt"}}
                    ),
                ),
            )

    result = await predict(Gateway(), gold, "local", "ollama")
    assert calls == 1
    assert result[0]["policy_safe"] is False


async def test_local_label_cannot_admit_cloud_provider_to_private_holdout(tmp_path: Path) -> None:
    spec = ModelSpec(
        id="cloud",
        provider="gemini",
        provider_model="cloud",
        context_length=8192,
        usd_per_1m_input=0,
        usd_per_1m_output=0,
        cost_class=CostClass.LOCAL,
        supports_tool_calling=True,
    )
    service = TrainingService(
        ide=FakeIDE(tmp_path), gateway=object(), model_registry=SimpleNamespace(get=lambda _: spec)
    )
    with pytest.raises(TrainingNotReady, match="local"):
        await service.evaluate_runtime("workspace", "e", "cloud", "candidate", "adapter", approved=True)


def test_runtime_evaluation_strict_pin_never_substitutes_another_model() -> None:
    from atlas.intelligence.contracts import Constraints
    from atlas.intelligence.errors import RoutingError
    from atlas.intelligence.selection.selector import ModelSelector

    spec = ModelSpec(
        id="other",
        provider="ollama",
        provider_model="other",
        context_length=8192,
        usd_per_1m_input=0,
        usd_per_1m_output=0,
        cost_class=CostClass.LOCAL,
    )
    selector = ModelSelector(
        SimpleNamespace(candidates=lambda _: [spec]),
        SimpleNamespace(is_available=lambda _: True, reliability=lambda _: 1),
    )
    with pytest.raises(RoutingError):
        selector.select(frozenset(), Constraints(pinned_model="missing", require_pinned_model=True))
