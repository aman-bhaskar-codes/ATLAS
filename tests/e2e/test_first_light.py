"""First Light End-to-End Test

This test verifies that ATLAS can start, accept a simple task, and complete it
end-to-end. This is the most important test in the repository as it validates
the entire runtime path works correctly.

The test follows the exact scenario specified in the runtime contract:
1. Start ATLAS
2. Verify it becomes READY
3. Submit a simple local task
4. Verify task execution
5. Verify task completion
6. Verify the system returns to READY
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from atlas.app import Atlas, build
from atlas.bootstrap.runtime import SystemState
from atlas.infra.backends import SQLiteConnection
from atlas.infra.types import InboundEvent
from atlas.intelligence.contracts import Usage
from atlas.intelligence.providers.base import ProviderCompletion
from atlas.orchestration.types import TaskResult


@pytest.mark.asyncio
async def test_first_light_simple_task(tmp_path: Path) -> None:
    """Test that ATLAS can complete a simple filesystem task end-to-end.
    
    This is the "first light" test - if this fails, nothing else matters.
    The task is intentionally simple and local-only to avoid external dependencies.
    """
    # Setup: Use temporary data directory
    import os
    os.environ["ATLAS_DATA_DIR"] = str(tmp_path)
    os.environ["ATLAS_ENV"] = "dev"
    
    # Mock Ollama and other external dependencies
    from unittest.mock import AsyncMock, patch
    
    def _dummy_embedding() -> list[float]:
        return [0.0] * 1024
    
    with (
        patch(
            "atlas.memory.embedder.OllamaEmbedder.embed",
            new_callable=AsyncMock,
            return_value=_dummy_embedding(),
        ),
        patch(
            "atlas.intelligence.providers.ollama.OllamaProvider.complete",
            new_callable=AsyncMock,
            return_value=ProviderCompletion(
                text=(
                    '{"thought": "The listing is available.", "confidence": 0.9, '
                    '"action": {"kind": "final_answer", "tool": null, "operation": null, '
                    '"args": {}, "final_text": "I found 42 Python files in the repository."}}'
                ),
                usage=Usage(input_tokens=10, output_tokens=5, usd=0.0),
            ),
        ),
        patch(
            "atlas.safety.sandbox_docker.DockerSandbox.health",
            new_callable=AsyncMock,
            return_value=False,  # Force native sandbox
        ),
    ):
        # Step 1: Start ATLAS
        atlas = await build()
        
        try:
            # Step 2: Start runtime and verify READY state
            health_report = await atlas.start()
            
            assert health_report.overall_status in (
                SystemState.READY,
                SystemState.DEGRADED,
            ), f"Expected READY or DEGRADED, got {health_report.overall_status}"
            
            # Step 3: Submit a simple task
            task_request = "List the Python files in the current directory"
            event = InboundEvent(
                correlation_id="test_first_light",
                source="system",
                content=task_request,
            )
            
            # Step 4: Execute task through orchestrator
            result = await atlas.orchestrator.run(event)
            
            # Step 5: Verify task completed successfully
            assert result.ok, f"Task failed: {result.error if hasattr(result, 'error') else 'Unknown error'}"
            assert result.answer, "Task should return an answer"
            
            # Step 6: Verify system is still in operational state
            final_health = atlas.runtime_supervisor.get_health_report()
            assert final_health.overall_status in (
                SystemState.READY,
                SystemState.DEGRADED,
                SystemState.BUSY,
            ), f"System should remain operational, got {final_health.overall_status}"
            
            # Step 7: Verify task was persisted
            from atlas.infra.backends import SQLiteConnection
            conn = SQLiteConnection(atlas.db.conn)
            task_row = await conn.fetchone(
                "SELECT * FROM tasks WHERE id = ?",
                (result.task_id,),
            )
            assert task_row is not None, "Task should be persisted"
            assert task_row["state"] in ("completed", "failed"), f"Task should be terminal, got {task_row['state']}"
            
        finally:
            # Cleanup: Close atlas
            await atlas.close()


@pytest.mark.asyncio
async def test_runtime_health_endpoints(tmp_path: Path) -> None:
    """Test that the health endpoints work correctly."""
    import os
    os.environ["ATLAS_DATA_DIR"] = str(tmp_path)
    os.environ["ATLAS_ENV"] = "dev"
    
    from unittest.mock import AsyncMock, MagicMock, patch
    
    def _dummy_embedding() -> list[float]:
        return [0.0] * 1024
    
    with (
        patch(
            "atlas.memory.embedder.OllamaEmbedder.embed",
            new_callable=AsyncMock,
            return_value=_dummy_embedding(),
        ),
        patch(
            "atlas.intelligence.providers.ollama.OllamaProvider.complete",
            new_callable=AsyncMock,
            return_value=MagicMock(
                text="ok",
                usage=MagicMock(input_tokens=10, output_tokens=5, usd=0.0),
            ),
        ),
        patch(
            "atlas.safety.sandbox_docker.DockerSandbox.health",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        atlas = await build()
        
        try:
            await atlas.start()
            
            # Test health report through supervisor
            health = atlas.runtime_supervisor.get_health_report()
            
            assert health.overall_status in (SystemState.READY, SystemState.DEGRADED)
            assert health.uptime_seconds >= 0
            assert isinstance(health.components, dict)
            assert isinstance(health.degraded_components, list)
            assert isinstance(health.unavailable_capabilities, list)
            
            # Test degraded components query
            degraded = atlas.runtime_supervisor.get_degraded_components()
            assert isinstance(degraded, list)
            
            # Test unavailable capabilities query
            unavailable = atlas.runtime_supervisor.get_unavailable_capabilities()
            assert isinstance(unavailable, list)
            
        finally:
            await atlas.close()


@pytest.mark.asyncio
async def test_graceful_shutdown(tmp_path: Path) -> None:
    """Test that ATLAS can shut down gracefully."""
    import os
    os.environ["ATLAS_DATA_DIR"] = str(tmp_path)
    os.environ["ATLAS_ENV"] = "dev"
    
    from unittest.mock import AsyncMock, MagicMock, patch
    
    def _dummy_embedding() -> list[float]:
        return [0.0] * 1024
    
    with (
        patch(
            "atlas.memory.embedder.OllamaEmbedder.embed",
            new_callable=AsyncMock,
            return_value=_dummy_embedding(),
        ),
        patch(
            "atlas.intelligence.providers.ollama.OllamaProvider.complete",
            new_callable=AsyncMock,
            return_value=MagicMock(
                text="ok",
                usage=MagicMock(input_tokens=10, output_tokens=5, usd=0.0),
            ),
        ),
        patch(
            "atlas.safety.sandbox_docker.DockerSandbox.health",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        atlas = await build()
        
        try:
            await atlas.start()
            
            # Verify runtime is operational
            assert atlas.runtime_supervisor.state in (SystemState.READY, SystemState.DEGRADED)
            
            # Trigger graceful shutdown
            await atlas.runtime_supervisor.shutdown(timeout_seconds=5.0)
            
            # Verify shutdown completed
            assert atlas.runtime_supervisor.state == SystemState.SHUTTING_DOWN
            
        finally:
            await atlas.close()


@pytest.mark.asyncio
async def test_task_state_transitions(tmp_path: Path) -> None:
    """Test that tasks transition through proper states."""
    import os
    os.environ["ATLAS_DATA_DIR"] = str(tmp_path)
    os.environ["ATLAS_ENV"] = "dev"
    
    from unittest.mock import AsyncMock, patch
    
    def _dummy_embedding() -> list[float]:
        return [0.0] * 1024
    
    with (
        patch(
            "atlas.memory.embedder.OllamaEmbedder.embed",
            new_callable=AsyncMock,
            return_value=_dummy_embedding(),
        ),
        patch(
            "atlas.intelligence.providers.ollama.OllamaProvider.complete",
            new_callable=AsyncMock,
            return_value=ProviderCompletion(
                text=(
                    '{"thought": "The listing is available.", "confidence": 0.9, '
                    '"action": {"kind": "final_answer", "tool": null, "operation": null, '
                    '"args": {}, "final_text": "I found 42 Python files in the repository."}}'
                ),
                usage=Usage(input_tokens=10, output_tokens=5, usd=0.0),
            ),
        ),
        patch(
            "atlas.safety.sandbox_docker.DockerSandbox.health",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        atlas = await build()
        
        try:
            await atlas.start()
            
            # Submit a task
            event = InboundEvent(
                correlation_id="test_state",
                source="system",
                content="Test task",
            )
            
            result = await atlas.orchestrator.run(event)
            
            # Verify task reached terminal state
            from atlas.infra.backends import SQLiteConnection
            conn = SQLiteConnection(atlas.db.conn)
            task_row = await conn.fetchone(
                "SELECT state FROM tasks WHERE id = ?",
                (result.task_id,),
            )
            
            assert task_row["state"] in ("completed", "failed", "cancelled")

        finally:
            await atlas.close()


# ══════════════════════════════════════════════════════════════════════════
# Phase 38: end-to-end coverage of the guarantees Pass 1 introduced.
#
# These drive ATLAS through the SAME public path as "first light"
# (build -> start -> orchestrator.run) and assert that behaviours built in
# earlier phases actually hold end to end, not only in unit isolation:
#   * typed lifecycle events on the L0 bus, in canonical order   (Phase 2/22)
#   * execution telemetry on the result                          (Phase 4/37)
#   * the zero-cost / local-only guarantee on the real path      (Phase 4/5)
#   * more than one task per session + runtime readiness         (Phase 20/21)
#   * goal verification actually running against the answer      (Phase 12)
# ══════════════════════════════════════════════════════════════════════════

# A well-formed reasoning-loop response whose action is a final answer. The
# planner also receives this (it cannot parse it as a plan and falls back to an
# exploratory plan), exactly as in the first-light test above.
_FINAL_ANSWER_JSON = (
    '{"thought": "The answer is ready.", "confidence": 0.9, '
    '"action": {"kind": "final_answer", "tool": null, "operation": null, '
    '"args": {}, "final_text": "Done: I completed the requested task."}}'
)


def _completion(text: str) -> ProviderCompletion:
    return ProviderCompletion(text=text, usage=Usage(input_tokens=10, output_tokens=5, usd=0.0))


@contextlib.contextmanager
def _atlas_mocks(
    tmp_path: Path,
    *,
    provider_return: ProviderCompletion | None = None,
    provider_side_effect: Callable[..., ProviderCompletion] | None = None,
) -> Iterator[None]:
    """Patch every external dependency the E2E path touches.

    WHY patch ``SemanticCache.get`` -> ``None``: the embedder mock returns a
    constant zero vector, so a warm semantic cache scores every request as a
    perfect match and would replay the FIRST answer for every later gateway
    call. That silently defeats a discriminating provider mock (calls 2..N
    never arrive) and hides real work behind a cache hit. Forcing a miss makes
    each gateway call reach the (mocked) provider — which is what these tests
    assert on. It also guarantees the inference runtime records an ``llm_calls``
    row per call (a cache hit would bypass the runtime entirely).
    """
    os.environ["ATLAS_DATA_DIR"] = str(tmp_path)
    os.environ["ATLAS_ENV"] = "dev"

    complete_kwargs: dict[str, Any] = {}
    if provider_side_effect is not None:
        complete_kwargs["side_effect"] = provider_side_effect
    else:
        complete_kwargs["return_value"] = provider_return or _completion(_FINAL_ANSWER_JSON)

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                "atlas.memory.embedder.OllamaEmbedder.embed",
                new_callable=AsyncMock,
                return_value=[0.0] * 1024,
            )
        )
        stack.enter_context(
            patch(
                "atlas.intelligence.providers.ollama.OllamaProvider.complete",
                new_callable=AsyncMock,
                **complete_kwargs,
            )
        )
        stack.enter_context(
            patch(
                "atlas.safety.sandbox_docker.DockerSandbox.health",
                new_callable=AsyncMock,
                return_value=False,  # force the native sandbox
            )
        )
        stack.enter_context(
            patch(
                "atlas.intelligence.cache.SemanticCache.get",
                new_callable=AsyncMock,
                return_value=None,
            )
        )
        yield


async def _run_task(atlas: Atlas, correlation_id: str, content: str) -> TaskResult:
    return await atlas.orchestrator.run(
        InboundEvent(correlation_id=correlation_id, source="system", content=content)
    )


async def _orchestrator_events(atlas: Atlas, task_id: str) -> list[dict[str, Any]]:
    """Decode a task's orchestrator events in DB insert order.

    Ordered by ``rowid`` (strict insert order) rather than ``occurred_at``: ISO
    timestamps can collide at microsecond resolution for rapid sequential emits,
    but rowid is monotonic, so the canonical-order assertion never flakes.
    """
    rows = await SQLiteConnection(atlas.db.conn).fetchall(
        "SELECT payload FROM events WHERE type = 'orchestrator' AND causation_id = ? ORDER BY rowid ASC",
        (task_id,),
    )
    return [json.loads(r["payload"]) for r in rows]


@pytest.mark.asyncio
async def test_intent_created_event_is_typed_and_emitted(tmp_path: Path) -> None:
    """Phase 2/22: understanding emits exactly one typed ``intent.created`` event
    carrying the structured intent's fields (domain, reasoning level) on the bus.

    This is the observable proof that intent is extracted ONCE per task and
    published as structured data, not re-derived per stage.
    """
    with _atlas_mocks(tmp_path):
        atlas = await build()
        try:
            await atlas.start()
            result = await _run_task(atlas, "test_intent_event", "Summarise the project status")
            assert result.ok
            events = await _orchestrator_events(atlas, result.task_id)
            intent_events = [e for e in events if e["kind"] == "intent.created"]
            assert len(intent_events) == 1, "intent must be created exactly once per task"
            meta = intent_events[0]["metadata"]
            assert meta.get("domain"), "intent event must carry the resolved domain"
            assert isinstance(meta.get("reasoning_level"), int), "reasoning level must be a bounded int"
            assert "criteria_count" in meta
        finally:
            await atlas.close()


@pytest.mark.asyncio
async def test_lifecycle_events_emitted_in_canonical_order(tmp_path: Path) -> None:
    """Phase 22: the task lifecycle is observable and ordered — ``task.created``
    precedes ``task.started``, which precedes the terminal event."""
    with _atlas_mocks(tmp_path):
        atlas = await build()
        try:
            await atlas.start()
            result = await _run_task(atlas, "test_lifecycle_order", "Say hello")
            assert result.ok
            kinds = [e["kind"] for e in await _orchestrator_events(atlas, result.task_id)]
            assert "task.created" in kinds
            assert "task.started" in kinds
            terminal = {"task.completed", "task.failed"}
            assert terminal & set(kinds), "a terminal lifecycle event must be emitted"
            last_terminal = max(i for i, k in enumerate(kinds) if k in terminal)
            assert (
                kinds.index("task.created") < kinds.index("task.started") < last_terminal
            ), f"events out of canonical order: {kinds}"
        finally:
            await atlas.close()


@pytest.mark.asyncio
async def test_result_reports_execution_telemetry(tmp_path: Path) -> None:
    """Phase 4/37: a completed task reports real telemetry — at least one model
    call and step, with non-negative token and latency counters."""
    with _atlas_mocks(tmp_path):
        atlas = await build()
        try:
            await atlas.start()
            result = await _run_task(atlas, "test_telemetry", "What is 2 + 2?")
            assert result.ok
            assert result.model_calls >= 1, "a real run makes at least one model call"
            assert result.steps_taken >= 1
            assert result.tokens_used >= 0
            assert result.latency_ms >= 0
        finally:
            await atlas.close()


@pytest.mark.asyncio
async def test_local_free_profile_costs_zero_and_uses_no_paid_provider(tmp_path: Path) -> None:
    """Phase 4/5: under the default local_free / zero_cost profile every inference
    call is recorded against the LOCAL provider at exactly $0.00.

    This is the zero-cost-first guarantee on the production path: the selector's
    hard policy filter leaves only the local provider eligible, so no paid or
    cloud provider is ever invoked. The DB row — not a mock assertion — is the
    evidence.
    """
    with _atlas_mocks(tmp_path):
        atlas = await build()
        try:
            await atlas.start()
            result = await _run_task(atlas, "test_zero_cost", "List three prime numbers")
            assert result.ok
            rows = await SQLiteConnection(atlas.db.conn).fetchall(
                "SELECT provider, cost_usd FROM llm_calls", ()
            )
            assert rows, "the run must have recorded at least one inference call"
            providers = {r["provider"] for r in rows}
            assert providers == {"ollama"}, f"local_free must only use the local provider, saw: {sorted(providers)}"
            assert all(float(r["cost_usd"]) == 0.0 for r in rows), "a zero_cost profile must never spend"
        finally:
            await atlas.close()


@pytest.mark.asyncio
async def test_two_sequential_tasks_complete_and_runtime_stays_operational(tmp_path: Path) -> None:
    """Phase 20/21: ATLAS handles more than one task in a session — two
    sequential tasks each complete with distinct ids and the runtime remains in
    an operational state throughout."""
    with _atlas_mocks(tmp_path):
        atlas = await build()
        try:
            await atlas.start()
            first = await _run_task(atlas, "test_seq_1", "First task")
            second = await _run_task(atlas, "test_seq_2", "Second task")
            assert first.ok and second.ok, "both sequential tasks must complete"
            assert first.task_id != second.task_id, "each task gets a distinct id"
            health = atlas.runtime_supervisor.get_health_report()
            assert health.overall_status in (
                SystemState.READY,
                SystemState.DEGRADED,
                SystemState.BUSY,
            )
        finally:
            await atlas.close()


@pytest.mark.asyncio
async def test_verification_runs_when_intent_has_success_criteria(tmp_path: Path) -> None:
    """Phase 12: when the extracted intent carries success criteria, the goal
    verifier actually runs against the delivered answer and its verdict is
    reflected on the result — replacing the old unconditional pass.

    The provider mock discriminates by prompt: the understanding call yields an
    intent WITH criteria in the ``communication`` domain (which routes to the
    model judge, not a mechanical verifier); the judge call ("strict evaluator")
    returns a passing verdict; every other call returns the final answer.
    """
    intent_json = _completion(
        '{"objective": "Draft a short project status update", '
        '"domain": "communication", "constraints": [], '
        '"success_criteria": ["mentions the current milestone", "under 200 words"], '
        '"risk": "low", "privacy_level": "internal", "urgency": "normal", '
        '"complexity": "simple", "required_capabilities": [], '
        '"likely_side_effects": [], "verification_requirements": [], "confidence": 0.9}'
    )
    verdict_json = _completion(
        '{"passed": true, "score": 0.9, "criteria_results": ['
        '{"criterion": "mentions the current milestone", "passed": true, "detail": "present"}, '
        '{"criterion": "under 200 words", "passed": true, "detail": "ok"}], '
        '"failure_reason": null, "suggested_next_action": null}'
    )
    final_json = _completion(_FINAL_ANSWER_JSON)

    def _respond(**kwargs: Any) -> ProviderCompletion:
        text = " ".join(m.content for m in kwargs["messages"])
        if "extract structured intent" in text:
            return intent_json
        if "strict evaluator" in text:
            return verdict_json
        return final_json

    with _atlas_mocks(tmp_path, provider_side_effect=_respond):
        atlas = await build()
        try:
            await atlas.start()
            result = await _run_task(atlas, "test_verify", "Draft a project status update for the team")
            assert result.ok
            assert result.verification_passed is True, "verification must actually run and pass"
            assert result.verification_score is not None
            assert result.verification_score >= 0.5
        finally:
            await atlas.close()
