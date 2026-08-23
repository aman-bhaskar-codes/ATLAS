"""HTTP contract tests for the approval endpoints.

These pin the HONEST current behavior rather than pretending approvals work:

* ``GET /approvals/pending`` always returns ``[]`` — approval storage is
  deferred (see ``facade.pending_approvals``). The Command Center's Approval
  Inbox therefore renders "Inbox Zero" against a real backend, and its
  approve/deny buttons are never reachable because no rows are ever listed.
* ``GET /approvals/{id}`` always 404s — ``AtlasTrustPlane.get_approval`` is
  unimplemented (documented in ``routes_trust.py``).
* ``POST /approvals/{id}/decide`` raises ``NotImplementedError`` →
  ``internal_error`` (500) — the decide flow is genuinely UNAVAILABLE today.

When approval storage lands (tracked as a Phase-3 backend gap), these
assertions must change — that is the intended tripwire.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_pending_approvals_is_empty_documented_deferral(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/approvals/pending")
    assert r.status_code == 200
    assert r.json() == []


async def test_get_single_approval_is_not_found_today(api_client: AsyncClient) -> None:
    r = await api_client.get("/api/v1/approvals/any-id")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


async def test_decide_approval_is_unavailable_today(api_client: AsyncClient) -> None:
    """decide_approval raises NotImplementedError -> stable 500 envelope.

    This documents the Zero-Dead-UI tension truthfully: the wired approve/deny
    buttons target an endpoint that is not implemented. It is not reachable in
    practice (no approvals are ever listed), but if it were called it would 500,
    not silently succeed.
    """
    r = await api_client.post(
        "/api/v1/approvals/any-id/decide",
        json={"decision": "approve", "idempotency_key": "e2e-key-decide-00001"},
    )
    assert r.status_code == 500
    assert r.json()["error"] == "internal_error"


async def test_decide_approval_validates_decision_enum(api_client: AsyncClient) -> None:
    """Invalid decision is rejected by validation (422) before reaching the
    unimplemented handler — the request contract is still enforced."""
    r = await api_client.post(
        "/api/v1/approvals/any-id/decide",
        json={"decision": "maybe", "idempotency_key": "e2e-key-decide-00002"},
    )
    assert r.status_code == 422
