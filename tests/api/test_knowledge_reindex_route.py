from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api.auth import require_admin
from atlas.interfaces.api.routes_knowledge import router


def test_reindex_routes_bounded_batch_to_shared_pipeline() -> None:
    calls = []

    async def reindex_embeddings(**kwargs):
        calls.append(kwargs)
        return {"documents": 2, "embedded": 6, "errors": 0, "timed_out": 0}

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: None
    app.include_router(router)
    app.state.atlas = SimpleNamespace(
        knowledge_fabric=SimpleNamespace(
            pipeline=SimpleNamespace(reindex_embeddings=reindex_embeddings, embedding_reindex_available=True)
        )
    )
    client = TestClient(app)
    assert client.post("/api/v1/knowledge/reindex", json={"limit_documents": 51}).status_code == 422
    response = client.post("/api/v1/knowledge/reindex", json={"limit_documents": 2})
    assert response.status_code == 200
    assert response.json()["documents"] == 2
    assert calls == [{"limit_documents": 2, "timeout_s": 30.0}]
