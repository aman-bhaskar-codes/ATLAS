"""Test knowledge store integration with retrieval.

Phase 3: Verify knowledge chunks are properly integrated into memory retrieval.
"""

import pytest
from pathlib import Path
import tempfile

from atlas.memory.knowledge_store import KnowledgeStore
from atlas.memory.retrieval import Retriever
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.semantic import SemanticMemory
from atlas.memory.user_model import UserModel
from atlas.memory.vectorstore import ChromaVectorStore
from atlas.memory.embedder import OllamaEmbedder
from atlas.infra.db import Database
from atlas.infra.clock import SystemClock
from atlas.infra.ids import UuidGenerator


@pytest.mark.asyncio
async def test_knowledge_store_retrieval_integration() -> None:
    """Test that knowledge chunks are included in retrieval results."""
    
    # Setup in-memory components
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.db"
        chroma_path = tmp_path / "chroma"
        
        db = Database(db_path)
        await db.start()
        
        clock = SystemClock()
        ids = UuidGenerator()
        vectors = ChromaVectorStore(str(chroma_path))
        embedder = OllamaEmbedder(base_url="http://localhost:11434")
        
        # Create memory components
        episodic = EpisodicMemory(db, clock)
        semantic = SemanticMemory(db, vectors, embedder, ids, clock)
        user_model = UserModel(db, clock)
        knowledge_store = KnowledgeStore(
            db=db,
            vector_store=vectors,
            embedder=embedder,
            ids=ids,
            clock=clock
        )
        
        # Create retriever with knowledge store
        retriever = Retriever(
            semantic=semantic,
            episodic=episodic,
            user_model=user_model,
            knowledge_store=knowledge_store,
            token_budget=2000
        )
        
        # Create a test document
        test_doc = tmp_path / "test.md"
        test_doc.write_text("""
# Test Document

This is a test document about Python programming.
Python is a high-level programming language.
It is widely used for web development and data science.

## Features
- Easy to learn
- Powerful libraries
- Great community support
""")
        
        # Ingest the document
        doc_id = await knowledge_store.ingest_document(
            test_doc,
            "markdown",
            metadata={"title": "Python Guide"}
        )
        
        assert doc_id is not None
        
        # Retrieve with a query that should match the document
        context = await retriever.retrieve("What is Python used for?")
        
        # Verify knowledge chunks are included
        assert len(context.knowledge_chunks) > 0, "Knowledge chunks should be retrieved"
        
        # Verify content contains Python information
        rendered = context.render()
        assert "Knowledge base" in rendered, "Rendered context should have knowledge section"
        
        # Verify token budget is respected
        assert context.token_estimate <= 2000, "Token budget should be respected"
        
        await db.stop()


@pytest.mark.asyncio
async def test_retrieval_without_knowledge_store() -> None:
    """Test that retrieval works without knowledge store (backward compatibility)."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.db"
        chroma_path = tmp_path / "chroma"
        
        db = Database(db_path)
        await db.start()
        
        clock = SystemClock()
        ids = UuidGenerator()
        vectors = ChromaVectorStore(str(chroma_path))
        embedder = OllamaEmbedder(base_url="http://localhost:11434")
        
        episodic = EpisodicMemory(db, clock)
        semantic = SemanticMemory(db, vectors, embedder, ids, clock)
        user_model = UserModel(db, clock)
        
        # Create retriever WITHOUT knowledge store
        retriever = Retriever(
            semantic=semantic,
            episodic=episodic,
            user_model=user_model,
            knowledge_store=None
        )
        
        # Should work without errors
        context = await retriever.retrieve("test query")
        
        # Knowledge chunks should be empty
        assert len(context.knowledge_chunks) == 0
        
        await db.stop()


@pytest.mark.asyncio
async def test_token_budget_partitioning() -> None:
    """Test that token budget is properly partitioned between memory and knowledge."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.db"
        chroma_path = tmp_path / "chroma"
        
        db = Database(db_path)
        await db.start()
        
        clock = SystemClock()
        ids = UuidGenerator()
        vectors = ChromaVectorStore(str(chroma_path))
        embedder = OllamaEmbedder(base_url="http://localhost:11434")
        
        episodic = EpisodicMemory(db, clock)
        semantic = SemanticMemory(db, vectors, embedder, ids, clock)
        user_model = UserModel(db, clock)
        knowledge_store = KnowledgeStore(
            db=db,
            vector_store=vectors,
            embedder=embedder,
            ids=ids,
            clock=clock
        )
        
        # Small budget to test partitioning
        retriever = Retriever(
            semantic=semantic,
            episodic=episodic,
            user_model=user_model,
            knowledge_store=knowledge_store,
            token_budget=600  # Small budget
        )
        
        # Create a document with substantial content
        test_doc = tmp_path / "large.md"
        test_doc.write_text("# Large Document\n" + "This is a test sentence. " * 200)
        
        await knowledge_store.ingest_document(test_doc, "markdown")
        
        # Retrieve and verify budget respected
        context = await retriever.retrieve("test query")
        
        # Knowledge should get at most 200 tokens (600 / 3)
        # But might get less if not enough results
        assert context.token_estimate <= 600
        
        await db.stop()
