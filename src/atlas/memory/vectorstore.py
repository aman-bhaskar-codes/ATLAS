"""Vector store seam.

WHY a protocol: Chroma is our local, zero-cost default, but memory must not be
welded to it. If we ever outgrow it (Qdrant/pgvector), we swap the adapter, not
the memory layer. Embeddings come from the model gateway (bge-m3 via Ollama),
never a paid API.
"""
from __future__ import annotations

from typing import Any, Protocol

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
    async def add_episode(self, episode_id: int, content: str, embedding: list[float]) -> str: ...
    async def search_episodes(self, query_embedding: list[float], k: int) -> list[VectorHit]: ...
    async def add_knowledge_chunk(self, chunk_id: str, text: str, embedding: list[float], metadata: dict[str, Any]) -> str: ...
    async def search_knowledge(self, query_embedding: list[float], k: int) -> list[VectorHit]: ...
    async def delete_knowledge_chunk(self, embedding_id: str) -> None: ...


class ChromaVectorStore:
    """Local persistent Chroma. WHY persist to disk: memory must survive restarts.
    Collection is created once; embeddings are supplied by us (we do our own
    embedding via the gateway) so Chroma never calls a cloud embedder.
    
    Phase 3: Enhanced for real-time episode storage with separate collections.
    """

    def __init__(self, path: str, collection: str = "atlas_semantic") -> None:
        import chromadb
        self._client = chromadb.PersistentClient(path=path)
        self._col = self._client.get_or_create_collection(collection)
        # Phase 3: Separate collection for episodes
        self._episodes_col = self._client.get_or_create_collection("atlas_episodes")
        # Phase 3: Separate collection for knowledge documents
        self._knowledge_col = self._client.get_or_create_collection("atlas_knowledge")

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

    async def add_episode(self, episode_id: int, content: str, embedding: list[float]) -> str:
        """Add episode embedding to episodes collection. Returns embedding_id."""
        embedding_id = f"ep_{episode_id}"
        self._episodes_col.upsert(
            ids=[embedding_id],
            documents=[content],
            embeddings=[embedding],  # type: ignore[arg-type]
            metadatas=[{"episode_id": episode_id}]
        )
        return embedding_id

    async def search_episodes(self, query_embedding: list[float], k: int) -> list[VectorHit]:
        """Search episodes by semantic similarity."""
        res = self._episodes_col.query(query_embeddings=[query_embedding], n_results=k)  # type: ignore[arg-type]
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
            doc = docs[i] if i < len(docs) else ""
            dist = float(dists[i]) if i < len(dists) else 1.0
            hits.append(VectorHit(ref=ref, text=doc, score=1.0 - dist))
        return hits

    async def add_knowledge_chunk(self, chunk_id: str, text: str, embedding: list[float], metadata: dict[str, Any]) -> str:
        """Add knowledge chunk to knowledge collection. Returns embedding_id."""
        embedding_id = f"kc_{chunk_id}"
        self._knowledge_col.upsert(
            ids=[embedding_id],
            documents=[text],
            embeddings=[embedding],  # type: ignore[arg-type]
            metadatas=[metadata]
        )
        return embedding_id

    async def search_knowledge(self, query_embedding: list[float], k: int) -> list[VectorHit]:
        """Search knowledge chunks by semantic similarity."""
        res = self._knowledge_col.query(query_embeddings=[query_embedding], n_results=k)  # type: ignore[arg-type]
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
            doc = docs[i] if i < len(docs) else ""
            dist = float(dists[i]) if i < len(dists) else 1.0
            hits.append(VectorHit(ref=ref, text=doc, score=1.0 - dist))
        return hits

    async def delete_knowledge_chunk(self, embedding_id: str) -> None:
        """Delete knowledge chunk from vector store."""
        self._knowledge_col.delete(ids=[embedding_id])
