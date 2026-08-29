"""Research-corpus `forget` REST surface — preview (read-only) + commit (funnel).

`app.state.atlas` is a SimpleNamespace: the two routes touch only `tools`,
`safety`, `ids`, and `knowledge_fabric.research_memory`, so a full `Atlas` build
is unnecessary. The point of these tests is the SEAM, not the deletion logic
(that lives in ResearchMemory and is tested there):
  * preview never routes through the funnel and never mutates (dry_run=True);
  * commit ALWAYS routes through `safety.guard` — a denial/halt surfaces as an
    HTTP error, and the exact ToolRequest handed to the funnel is asserted.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.infra.types import ToolResult
from atlas.interfaces.api.routes_knowledge import router as knowledge_router
from atlas.safety.engine import DeniedError, HaltedError


class FakeReport:
    """Structural stand-in for DeletionReport — honest per-store counts."""

    def __init__(self, *, scope: str, target: str, dry_run: bool) -> None:
        self.scope = SimpleNamespace(value=scope)
        self.target = target
        self.dry_run = dry_run
        self.documents = 2
        self.chunks = 7
        self.evidence = 3
        self.sessions = 0
        self.vectors = 7
        self.vectors_failed = 0
        self.lexical = 7
        self.notes = ["preview" if dry_run else "removed"]
        verb = "Would remove" if dry_run else "Removed"
        self.summary = f"{verb} docs=2 for {scope}={target}"


class FakeMemory:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, str, bool, bool]] = []

    async def forget(
        self, scope: Any, target: str = "", *, cascade_documents: bool = False, dry_run: bool = False
    ) -> FakeReport:
        self.calls.append((scope, target, cascade_documents, dry_run))
        return FakeReport(scope=getattr(scope, "value", str(scope)), target=target, dry_run=dry_run)


def _payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scope": "document",
        "target": "d1",
        "dry_run": False,
        "documents": 2,
        "chunks": 7,
        "evidence": 3,
        "sessions": 0,
        "vectors": 7,
        "vectors_failed": 0,
        "lexical": 7,
        "notes": ["removed"],
        "summary": "Removed docs=2 for document=d1",
    }
    base.update(over)
    return base


class FakeSafety:
    """Records the ToolRequest and returns a canned outcome, or raises."""

    def __init__(self, *, result: Any = None, raises: Exception | None = None) -> None:
        self._result = result if result is not None else ToolResult(ok=True, output=_payload())
        self._raises = raises
        self.guarded: list[Any] = []

    async def guard(self, req: Any, tool: Any) -> Any:
        self.guarded.append(req)
        if self._raises is not None:
            raise self._raises
        return self._result


def _client(
    *,
    memory: FakeMemory | None = None,
    safety: FakeSafety | None = None,
    has_tool: bool = True,
) -> tuple[TestClient, SimpleNamespace]:
    app = FastAPI()
    app.include_router(knowledge_router, prefix="")  # router carries its own /api/v1/knowledge prefix
    fabric = SimpleNamespace(research_memory=memory) if memory is not None else None
    state = SimpleNamespace(
        tools={"knowledge": object()} if has_tool else {},
        safety=safety or FakeSafety(),
        ids=SimpleNamespace(correlation_id=lambda: "cid-forget-1"),
        knowledge_fabric=fabric,
    )
    app.state.atlas = state
    return TestClient(app), state


BASE = "/api/v1/knowledge"


class TestPreview:
    def test_previews_honest_counts_without_mutating(self) -> None:
        memory = FakeMemory()
        client, _ = _client(memory=memory)
        resp = client.post(f"{BASE}/research/forget/preview", json={"scope": "document", "target": "d1"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is True
        assert body["documents"] == 2 and body["chunks"] == 7
        assert body["summary"].startswith("Would remove")
        # dry_run=True flows to the coordinator; nothing else runs.
        assert memory.calls == [(memory.calls[0][0], "d1", False, True)]
        assert getattr(memory.calls[0][0], "value", None) == "document"

    def test_cascade_flag_is_forwarded(self) -> None:
        memory = FakeMemory()
        client, _ = _client(memory=memory)
        resp = client.post(
            f"{BASE}/research/forget/preview",
            json={"scope": "session", "target": "rs_1", "cascade_documents": True},
        )
        assert resp.status_code == 200
        assert memory.calls[0][2] is True  # cascade_documents
        assert memory.calls[0][3] is True  # dry_run

    def test_unknown_scope_is_400(self) -> None:
        memory = FakeMemory()
        client, _ = _client(memory=memory)
        resp = client.post(f"{BASE}/research/forget/preview", json={"scope": "everything", "target": ""})
        assert resp.status_code == 400
        assert "unknown forget scope" in resp.json()["detail"]
        assert memory.calls == []  # rejected at the edge, coordinator untouched

    def test_unavailable_fabric_is_503(self) -> None:
        client, _ = _client(memory=None)  # knowledge_fabric is None
        resp = client.post(f"{BASE}/research/forget/preview", json={"scope": "all", "target": ""})
        assert resp.status_code == 503


class TestCommit:
    def test_commit_routes_through_the_funnel(self) -> None:
        safety = FakeSafety(result=ToolResult(ok=True, output=_payload()))
        client, _ = _client(memory=FakeMemory(), safety=safety)
        resp = client.request("DELETE", f"{BASE}/research", json={"scope": "document", "target": "d1"})

        assert resp.status_code == 200
        assert resp.json()["dry_run"] is False
        assert resp.json()["summary"].startswith("Removed")
        # The destructive path went through guard exactly once.
        assert len(safety.guarded) == 1

    def test_toolrequest_carries_scope_target_cascade(self) -> None:
        safety = FakeSafety()
        client, _ = _client(memory=FakeMemory(), safety=safety)
        client.request(
            "DELETE",
            f"{BASE}/research",
            json={"scope": "session", "target": "rs_1", "cascade_documents": True},
        )
        req = safety.guarded[0]
        assert req.tool == "knowledge" and req.operation == "forget"
        assert req.args["operation"] == "forget"  # tools read args["operation"]
        assert req.args["scope"] == "session"
        assert req.args["target"] == "rs_1"
        assert req.args["cascade_documents"] is True
        assert req.correlation_id == "cid-forget-1"

    def test_denied_forget_is_403_with_tier_and_reason(self) -> None:
        decision = SimpleNamespace(reason="needs confirmation code", tier=SimpleNamespace(name="DANGEROUS"))
        safety = FakeSafety(raises=DeniedError(decision))  # type: ignore[arg-type]
        client, _ = _client(memory=FakeMemory(), safety=safety)
        resp = client.request("DELETE", f"{BASE}/research", json={"scope": "all", "target": ""})
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "DANGEROUS" in detail and "needs confirmation code" in detail

    def test_halted_forget_is_503(self) -> None:
        safety = FakeSafety(raises=HaltedError("kill switch active"))
        client, _ = _client(memory=FakeMemory(), safety=safety)
        resp = client.request("DELETE", f"{BASE}/research", json={"scope": "document", "target": "d1"})
        assert resp.status_code == 503
        assert "halted" in resp.json()["detail"]

    def test_failed_toolresult_is_500(self) -> None:
        safety = FakeSafety(result=ToolResult(ok=False, error="store offline"))
        client, _ = _client(memory=FakeMemory(), safety=safety)
        resp = client.request("DELETE", f"{BASE}/research", json={"scope": "document", "target": "d1"})
        assert resp.status_code == 500
        assert "store offline" in resp.json()["detail"]

    def test_unknown_scope_never_reaches_the_funnel(self) -> None:
        safety = FakeSafety()
        client, _ = _client(memory=FakeMemory(), safety=safety)
        resp = client.request("DELETE", f"{BASE}/research", json={"scope": "nonsense", "target": ""})
        assert resp.status_code == 400
        assert safety.guarded == []  # rejected before any classification

    def test_missing_tool_is_503(self) -> None:
        safety = FakeSafety()
        client, _ = _client(memory=FakeMemory(), safety=safety, has_tool=False)
        resp = client.request("DELETE", f"{BASE}/research", json={"scope": "document", "target": "d1"})
        assert resp.status_code == 503
        assert safety.guarded == []
