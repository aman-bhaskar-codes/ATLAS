"""HTTP contract tests for the runtime status/health endpoints.

The Command Center's un-fakeable ATLAS STATUS pill is derived entirely from
``GET /runtime/status``; the health popover from ``GET /runtime/health``. These
tests assert the REAL shape returned over HTTP — the same bytes the frontend's
``RuntimeStatusSchema`` / ``RuntimeHealthSchema`` parse — so a drift in either
layer fails CI instead of shipping a dead or lying pill.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_runtime_status_contract(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/runtime/status")
    assert r.status_code == 200
    body = r.json()

    # Exact field set + types the frontend RuntimeStatusSchema requires.
    assert body["schema_version"] == 1
    assert body["state"] in {"starting", "ready", "degraded", "stopping", "stopped"}
    assert isinstance(body["version"], str) and body["version"]
    assert isinstance(body["environment"], str) and body["environment"]
    assert isinstance(body["kill_switch_active"], bool)
    assert isinstance(body["active_task_count"], int) and body["active_task_count"] >= 0
    assert isinstance(body["pending_approval_count"], int) and body["pending_approval_count"] >= 0
    assert "last_audit_at" in body  # nullable, but the key must be present


async def test_runtime_status_ready_when_killswitch_inactive(api_client: AsyncClient) -> None:
    """With no kill switch tripped, the honest derived state is 'ready'."""
    body = (await api_client.get("/api/v1/runtime/status")).json()
    assert body["kill_switch_active"] is False
    assert body["state"] == "ready"


async def test_pending_approval_count_is_zero_documented_deferral(api_client: AsyncClient) -> None:
    """TRUTH pin: approval storage is deferred (facade hardcodes 0), so the
    Command Center's 'WAITING APPROVAL' state is known-unreachable via this
    endpoint today. When approval storage lands, this assertion must change —
    that is the intended tripwire, not a bug.
    """
    body = (await api_client.get("/api/v1/runtime/status")).json()
    assert body["pending_approval_count"] == 0


async def test_runtime_health_contract(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/runtime/health")
    assert r.status_code == 200
    body = r.json()

    assert body["schema_version"] == 1
    assert body["overall"] in {"healthy", "degraded", "unavailable"}
    assert isinstance(body["checks"], list) and body["checks"], "health must report >=1 check"

    names = {c["name"] for c in body["checks"]}
    assert "database" in names, "the database check is the minimum viable health signal"
    for c in body["checks"]:
        assert c["status"] in {"pass", "warn", "fail"}
        assert isinstance(c["detail"], str)
        assert isinstance(c["checked_at"], str)  # ISO-8601 string over the wire
