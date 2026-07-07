"""Vector store seam.

WHY a protocol: Chroma is our local, zero-cost default, but memory must not be
welded to it. If we ever outgrow it (Qdrant/pgvector), we swap the adapter, not
the memory layer. Embeddings come from the model gateway (bge-m3 via Ollama),
never a paid API.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class VectorHit(BaseModel):
    model_config = {"frozen": True}
    ref: str
    score: float
    text: str


class VectorStore(Protocol):
    async def upsert(self, ref: str, text: str, embedding: list[float]) -> None: ...
    async def query(self, embedding: list[float], k: int) -> list[VectorHit]: ...
    async def delete(self, ref: str) -> None: ...


class ChromaVectorStore:
    """Local persistent Chroma. WHY persist to disk: memory must survive restarts.
    Collection is created once; embeddings are supplied by us (we do our own
    embedding via the gateway) so Chroma never calls a cloud embedder."""

    def __init__(self, path: str, collection: str = "atlas_semantic") -> None:
        import chromadb
        self._client = chromadb.PersistentClient(path=path)
        self._col = self._client.get_or_create_collection(collection)

    async def upsert(self, ref: str, text: str, embedding: list[float]) -> None:
        self._col.upsert(ids=[ref], documents=[text], embeddings=[embedding]) # type: ignore

    async def query(self, embedding: list[float], k: int) -> list[VectorHit]:
        res = self._col.query(query_embeddings=[embedding], n_results=k) # type: ignore
        hits: list[VectorHit] = []
        ids_res = res.get("ids")
        if not ids_res:
            return []
        ids = ids_res[0]
        
        docs_res = res.get("documents")
        docs = docs_res[0] if docs_res else []
        
        dists_res = res.get("distances")
        dists = dists_res[0] if dists_res else []
        
        for i, ref in enumerate(ids):
            # cosine distance -> similarity score
            doc = docs[i] if i < len(docs) else ""
            dist = float(dists[i]) if i < len(dists) else 1.0
            hits.append(VectorHit(ref=ref, text=doc, score=1.0 - dist))
        return hits

    async def delete(self, ref: str) -> None:
        self._col.delete(ids=[ref])
