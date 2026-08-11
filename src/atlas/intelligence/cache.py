"""Semantic Response Cache (Phase 11 Speed & Cost)

Intercepts LLM requests at the gateway. If a request is identical or highly semantically
similar to a recent request, return the cached InferenceResponse instantly ($0 cost).
"""
import hashlib
from datetime import datetime, timedelta, timezone
import uuid

from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import InferenceRequest, InferenceResponse
from atlas.memory.embedder import Embedder
from atlas.memory.vectorstore import VectorStore

_log = get_logger("atlas.cache")

class SemanticCache:
    def __init__(self, db: Database, vectors: VectorStore, embedder: Embedder) -> None:
        self._db = db
        self._vectors = vectors
        self._embedder = embedder

    def _hash_prompt(self, req: InferenceRequest) -> str:
        prompt_text = "\n".join(f"{m.role}: {m.content}" for m in req.messages)
        return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

    async def get(self, req: InferenceRequest) -> InferenceResponse | None:
        # Avoid caching if streaming is requested, as we don't store the stream generator
        if req.stream:
            return None
        
        prompt_hash = self._hash_prompt(req)
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # 1. Exact Match Fast-Path
        cur = await self._db.conn.execute(
            "SELECT response_json, ttl_expires_ts FROM semantic_cache WHERE prompt_hash = ?",
            (prompt_hash,)
        )
        row = await cur.fetchone()
        if row:
            if row["ttl_expires_ts"] and row["ttl_expires_ts"] < now_iso:
                # Expired, clean up later or ignore
                pass
            else:
                _log.info("cache.hit_exact", event_type="cache", prompt_hash=prompt_hash)
                resp = InferenceResponse.model_validate_json(row["response_json"])
                # Return the cached response
                return resp

        # 2. Semantic Similarity Match
        # Only do semantic matching if it's a pure knowledge/reasoning capability, 
        # or we rely on high similarity threshold (>0.97) for safe tool-less queries.
        # Too low threshold can cause the agent to reuse arguments meant for a similar query.
        prompt_text = "\n".join(f"{m.role}: {m.content}" for m in req.messages)
        embedding = await self._embedder.embed(prompt_text)
        hits = await self._vectors.query(embedding, k=1)
        
        if hits and hits[0].score > 0.97:  # Very high threshold to avoid hallucinations
            ref = hits[0].ref
            cur = await self._db.conn.execute(
                "SELECT response_json, ttl_expires_ts FROM semantic_cache WHERE embedding_ref = ?",
                (ref,)
            )
            row = await cur.fetchone()
            if row:
                if row["ttl_expires_ts"] and row["ttl_expires_ts"] < now_iso:
                    pass
                else:
                    _log.info("cache.hit_semantic", event_type="cache", score=hits[0].score)
                    resp = InferenceResponse.model_validate_json(row["response_json"])
                    return resp
                    
        return None

    async def put(self, req: InferenceRequest, resp: InferenceResponse) -> None:
        if req.stream:
            return
            
        # Determine TTL based on capabilities
        # Time-sensitive capabilities get short TTL
        time_sensitive = {"calendar", "mail", "web", "browser", "search", "contacts"}
        req_caps = set(req.required_capabilities)
        
        if req_caps.intersection(time_sensitive):
            ttl = timedelta(minutes=5)
        else:
            ttl = timedelta(days=1)
            
        expires_ts = (datetime.now(timezone.utc) + ttl).isoformat()
        
        prompt_hash = self._hash_prompt(req)
        prompt_text = "\n".join(f"{m.role}: {m.content}" for m in req.messages)
        
        # We need an ID for Chroma
        ref = str(uuid.uuid4())
        embedding = await self._embedder.embed(prompt_text)
        
        # Save to Chroma
        await self._vectors.upsert(ref, prompt_text, embedding)
        
        # Save to SQLite
        resp_json = resp.model_dump_json()
        now_iso = datetime.now(timezone.utc).isoformat()
        
        await self._db.conn.execute(
            "INSERT INTO semantic_cache (id, prompt_hash, embedding_ref, response_json, ttl_expires_ts, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), prompt_hash, ref, resp_json, expires_ts, now_iso)
        )
        await self._db.conn.commit()
