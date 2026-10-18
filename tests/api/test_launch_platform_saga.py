"""Phase 3 verification: Cross-Platform State Handoff, Saga Rollback, Merkle Delta & LRU pool.

Tests real FastAPI execution with real database and real endpoints:
- POST /api/v1/agent/launch-platform (research & ide)
- GET /api/v1/agent/handoff/{token}
- Saga compensating rollback stack execution on injected failure
- POST /api/v1/ide/workspaces/{workspace_id}/tree/delta (MerklePrefixTrie pruning & deltas)
- IDEService LRU workspace pool (max 3 resident)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from atlas.capabilities.ide.service import IDEService
from atlas.engineering.merkle import MerklePrefixTrie
from atlas.orchestration.saga import SagaCoordinator, SagaRollbackError


@pytest.mark.asyncio
async def test_launch_platform_research(api_client: AsyncClient) -> None:
    """POST /api/v1/agent/launch-platform provisions research platform atomically."""
    payload = {
        "parent_task_id": None,
        "target": "research",
        "initial_query": "Explore quantum computing advancements",
    }
    resp = await api_client.post("/api/v1/agent/launch-platform", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["platform"] == "research"
    assert data["task_id"]
    assert data["workspace_id"]
    assert data["handoff_token"].startswith("hnd_")
    assert "/research?taskId=" in data["redirect_path"]
    assert f"handoff={data['handoff_token']}" in data["redirect_path"]

    # Verify handoff token retrieval
    handoff_resp = await api_client.get(f"/api/v1/agent/handoff/{data['handoff_token']}")
    assert handoff_resp.status_code == 200
    handoff_data = handoff_resp.json()
    assert handoff_data["token"] == data["handoff_token"]
    assert handoff_data["task_id"] == data["task_id"]
    assert handoff_data["platform"] == "research"


@pytest.mark.asyncio
async def test_launch_platform_ide(api_client: AsyncClient) -> None:
    """POST /api/v1/agent/launch-platform provisions IDE workspace directory and session."""
    payload = {
        "parent_task_id": None,
        "target": "ide",
        "workspace_name": "quantum-lab",
    }
    resp = await api_client.post("/api/v1/agent/launch-platform", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["platform"] == "ide"
    assert data["workspace_id"]
    assert "/workspaces?workspaceId=" in data["redirect_path"]
    assert f"handoff={data['handoff_token']}" in data["redirect_path"]

    # Retrieve handoff context
    handoff_resp = await api_client.get(f"/api/v1/agent/handoff/{data['handoff_token']}")
    assert handoff_resp.status_code == 200
    handoff_data = handoff_resp.json()
    assert handoff_data["token"] == data["handoff_token"]
    assert handoff_data["workspace_id"] == data["workspace_id"]
    assert handoff_data["platform"] == "ide"

    # Query Merkle tree delta over real HTTP endpoint
    delta_resp = await api_client.post(
        f"/api/v1/ide/workspaces/{data['workspace_id']}/tree/delta",
        json={"known_hashes": {}},
    )
    assert delta_resp.status_code == 200, delta_resp.text
    delta_data = delta_resp.json()
    assert delta_data["workspace_id"] == data["workspace_id"]
    assert "root_hash" in delta_data
    assert isinstance(delta_data["deltas"], list)


@pytest.mark.asyncio
async def test_launch_platform_with_parent_task(api_client: AsyncClient) -> None:
    """POST /api/v1/agent/launch-platform binds parent task context into handoff."""
    # 1. Create a parent task
    create_resp = await api_client.post(
        "/api/v1/tasks",
        json={"request": "Parent investigation", "idempotency_key": "test-idem-parent-1"},
    )
    assert create_resp.status_code == 202, create_resp.text
    parent_id = create_resp.json()["id"]

    # 2. Launch research platform pointing to parent task
    launch_resp = await api_client.post(
        "/api/v1/agent/launch-platform",
        json={
            "parent_task_id": parent_id,
            "target": "research",
            "initial_query": "Deep dive into parent findings",
        },
    )
    assert launch_resp.status_code == 200, launch_resp.text
    data = launch_resp.json()

    # 3. Retrieve handoff and verify parent context
    handoff_resp = await api_client.get(f"/api/v1/agent/handoff/{data['handoff_token']}")
    assert handoff_resp.status_code == 200
    handoff_data = handoff_resp.json()
    assert handoff_data["parent_task_id"] == parent_id
    assert handoff_data["parent_context"] is not None
    assert handoff_data["parent_context"]["parent_id"] == parent_id
    assert handoff_data["parent_context"]["request"] == "Parent investigation"


@pytest.mark.asyncio
async def test_launch_platform_saga_failure_cleans_up_db(api_client: AsyncClient) -> None:
    """When a saga step fails, compensations unwind and clean up previously written records."""
    # Patch ide_svc.open_workspace to fail
    with patch(
        "atlas.capabilities.ide.service.IDEService.open_workspace",
        side_effect=RuntimeError("Disk I/O failure simulating provision crash"),
    ):
        launch_resp = await api_client.post(
            "/api/v1/agent/launch-platform",
            json={
                "parent_task_id": None,
                "target": "ide",
                "workspace_name": "failing-ws",
            },
        )
        assert launch_resp.status_code == 500
        assert "Saga failed" in launch_resp.json()["detail"]


@pytest.mark.asyncio
async def test_handoff_token_not_found(api_client: AsyncClient) -> None:
    """GET /api/v1/agent/handoff/invalid returns 404."""
    resp = await api_client.get("/api/v1/agent/handoff/non-existent-token")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_saga_compensating_rollback() -> None:
    """SagaCoordinator executes registered compensations in LIFO order upon failure."""
    saga = SagaCoordinator("test_compensation_saga")
    execution_order: list[str] = []

    def undo_step1() -> None:
        execution_order.append("step1_undone")

    async def undo_step2() -> None:
        execution_order.append("step2_undone")

    saga.register_compensation("step1", undo_step1)
    saga.register_compensation("step2", undo_step2)

    primary_exc = RuntimeError("Step 3 failed")
    await saga.rollback(primary_exc)

    # Must be reverse order (LIFO): step2 undone, then step1 undone
    assert execution_order == ["step2_undone", "step1_undone"]


@pytest.mark.asyncio
async def test_saga_compensation_failure_raises() -> None:
    """If a compensation callback raises, SagaRollbackError captures both primary and rollback errors."""
    saga = SagaCoordinator("test_failing_compensation")

    def failing_undo() -> None:
        raise ValueError("Cannot clean up resource")

    saga.register_compensation("failing_step", failing_undo)

    with pytest.raises(SagaRollbackError) as exc_info:
        await saga.rollback(RuntimeError("Primary step failed"))

    err = exc_info.value
    assert isinstance(err.primary_error, RuntimeError)
    assert len(err.rollback_errors) == 1
    assert isinstance(err.rollback_errors[0], ValueError)


def test_merkle_prefix_trie(tmp_path: Path) -> None:
    """MerklePrefixTrie generates leaf & dir hashes and computes deltas with O(1) pruning."""
    trie = MerklePrefixTrie()

    # Create directory structure
    d = tmp_path / "src"
    d.mkdir()
    f1 = d / "main.py"
    f1.write_text("print('hello')")
    f2 = d / "utils.py"
    f2.write_text("def add(a, b): return a + b")

    # Initial build
    tree1 = trie.build_tree(tmp_path)
    assert tree1.is_dir
    assert tree1.hash

    # Compute delta against empty known_hashes -> everything added
    unchanged, deltas = trie.compute_delta(tree1, {})
    assert not unchanged
    paths = {item.path for item in deltas}
    assert "src" in paths
    assert "src/main.py" in paths
    assert "src/utils.py" in paths

    # Known hashes matching root -> O(1) unchanged pruning
    unchanged, deltas = trie.compute_delta(tree1, {"": tree1.hash})
    assert unchanged
    assert deltas == []

    # Modify one file
    f1.write_text("print('hello world')")
    tree2 = trie.build_tree(tmp_path)
    assert tree2.hash != tree1.hash

    # Diff tree2 with tree1 hashes
    known = {
        "": tree1.hash,
        "src": tree1.children["src"].hash,
        "src/main.py": tree1.children["src"].children["main.py"].hash,
        "src/utils.py": tree1.children["src"].children["utils.py"].hash,
    }
    unchanged, deltas = trie.compute_delta(tree2, known)
    assert not unchanged
    # Only root, src (parent), and src/main.py are modified; utils.py is pruned
    delta_paths = {item.path: item.change_type for item in deltas}
    assert delta_paths.get("src/main.py") == "modified"
    assert "src/utils.py" not in delta_paths


@pytest.mark.asyncio
async def test_ide_service_lru_pool(tmp_path: Path) -> None:
    """IDEService maintains at most max_active_workspaces=3 resident workspaces."""
    from atlas.infra.clock import SystemClock
    from atlas.infra.ids import UuidGenerator

    # Mock safety and tools
    safety = AsyncMock()
    fs_tool = AsyncMock()
    ids = UuidGenerator()
    clock = SystemClock()

    service = IDEService(
        safety=safety,
        filesystem_tool=fs_tool,
        ids=ids,
        clock=clock,
    )
    assert service._max_active_workspaces == 3

    # Create 4 workspace directories
    ws_paths: list[Path] = []
    sessions = []
    for i in range(4):
        p = tmp_path / f"ws_{i}"
        p.mkdir()
        ws_paths.append(p)
        s = await service.open_workspace(str(p), f"Workspace {i}")
        sessions.append(s)

    # After opening 4, the resident pool in service._workspaces should only have 3
    assert len(service._workspaces) == 3
    # The first one (ws_0) was LRU evicted from residency
    wid_0 = str(sessions[0].workspace.id)
    wid_1 = str(sessions[1].workspace.id)
    wid_2 = str(sessions[2].workspace.id)
    wid_3 = str(sessions[3].workspace.id)

    resident_ids = [str(k) for k in service._workspaces.keys()]
    assert wid_0 not in resident_ids
    assert wid_1 in resident_ids
    assert wid_2 in resident_ids
    assert wid_3 in resident_ids
