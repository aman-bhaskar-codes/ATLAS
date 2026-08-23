"""HTTP contract tests for the task create/read/cancel endpoints.

Covers the exact paths the Command Center and Live Run pages drive:
``POST /tasks`` (202 + idempotency), ``GET /tasks`` (TaskPage), ``GET
/tasks/{id}`` (TaskView), and cancel's not-found decision. Assertions match the
REAL wiring verified in ``facade.py`` / ``routes_trust.py`` — e.g. ``GET
/tasks`` returns a *paginated object*, not a bare list — rather than an assumed
shape.
"""

from __future__ import annotations

from httpx import AsyncClient

# CreateTaskRequest requires idempotency_key with min_length=16.
_KEY_A = "e2e-key-000000000001"
_KEY_B = "e2e-key-000000000002"
_KEY_C = "e2e-key-000000000003"
_KEY_D = "e2e-key-000000000004"


async def test_create_task_returns_202_with_task(api_client: AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/tasks",
        json={"request": "List three prime numbers", "idempotency_key": _KEY_A, "source": "api"},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["id"]
    assert body["source"] == "api"
    assert body["request"] == "List three prime numbers"
    assert body["state"] == "created"


async def test_create_task_is_idempotent(api_client: AsyncClient) -> None:
    payload = {"request": "same task twice", "idempotency_key": _KEY_B, "source": "api"}
    r1 = await api_client.post("/api/v1/tasks", json=payload)
    r2 = await api_client.post("/api/v1/tasks", json=payload)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"], "same idempotency_key must map to the same task"


async def test_create_task_rejects_short_idempotency_key(api_client: AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/tasks",
        json={"request": "x", "idempotency_key": "short", "source": "api"},
    )
    assert r.status_code == 422  # pydantic validation: idempotency_key min_length=16


async def test_create_task_rejects_empty_request(api_client: AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/tasks",
        json={"request": "", "idempotency_key": "e2e-key-empty-req-001", "source": "api"},
    )
    assert r.status_code == 422  # request min_length=1


async def test_list_tasks_returns_taskpage_object(api_client: AsyncClient) -> None:
    # Seed one task so the page is non-trivial.
    await api_client.post(
        "/api/v1/tasks",
        json={"request": "task for listing", "idempotency_key": _KEY_C, "source": "api"},
    )
    r = await api_client.get("/api/v1/tasks")
    assert r.status_code == 200
    body = r.json()
    # routes_trust returns TaskPage (a paginated object), NOT a bare list.
    assert isinstance(body, dict)
    list_fields = [v for v in body.values() if isinstance(v, list)]
    assert list_fields, "TaskPage must expose a list of tasks"


async def test_get_task_by_id_returns_the_created_task(api_client: AsyncClient) -> None:
    created = await api_client.post(
        "/api/v1/tasks",
        json={"request": "readable task", "idempotency_key": _KEY_D, "source": "api"},
    )
    task_id = created.json()["id"]
    r = await api_client.get(f"/api/v1/tasks/{task_id}")
    assert r.status_code == 200
    # TaskView shape is owned by schemas_trust; assert the id round-trips rather
    # than over-fitting field names the frontend may evolve.
    assert task_id in r.text


async def test_cancel_unknown_task_returns_404(api_client: AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/tasks/does-not-exist/cancel",
        json={"idempotency_key": "e2e-key-cancel-00001", "reason": "user_requested"},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"
