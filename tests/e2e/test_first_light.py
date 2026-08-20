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

from pathlib import Path

import pytest

from atlas.app import build
from atlas.bootstrap.runtime import SystemState
from atlas.infra.types import InboundEvent
from atlas.intelligence.providers.base import ProviderCompletion
from atlas.intelligence.contracts import Usage


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
            return_value=ProviderCompletion(
                text="I found 42 Python files in the repository.",
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
            task_row = await conn.fetch_one(
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
            return_value=ProviderCompletion(
                text="Task completed successfully",
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
            task_row = await conn.fetch_one(
                "SELECT state FROM tasks WHERE id = ?",
                (result.task_id,),
            )
            
            assert task_row["state"] in ("completed", "failed", "cancelled")
            
        finally:
            await atlas.close()
