"""Memory bootstrap — vectors, episodic, semantic, user_model, working, knowledge, retriever."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.infra.clock import Clock
from atlas.infra.config import Settings
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.consolidation import Consolidator
from atlas.memory.embedder import EmbeddingWorker, OllamaEmbedder
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.experience_extractor import ExperienceExtractor  # Phase 2
from atlas.memory.knowledge_store import KnowledgeStore
from atlas.memory.pruning import Pruner
from atlas.memory.retrieval import Retriever
from atlas.memory.semantic import SemanticMemory
from atlas.memory.trajectory_store import TrajectoryStore  # Phase 2
from atlas.memory.user_model import UserModel
from atlas.memory.vectorstore import ChromaVectorStore
from atlas.memory.working import WorkingMemory


@dataclass
class MemoryComponents:
    vectors: ChromaVectorStore
    embedding_worker: EmbeddingWorker
    episodic: EpisodicMemory
    semantic: SemanticMemory
    user_model: UserModel
    working: WorkingMemory
    knowledge_store: KnowledgeStore
    retriever: Retriever
    consolidator: Consolidator
    pruner: Pruner
    trajectory_store: TrajectoryStore  # Phase 2
    experience_extractor: ExperienceExtractor  # Phase 2


def build_memory(
    *,
    settings: Settings,
    db: Database,
    ids: IdGenerator,
    clock: Clock,
    embedder: OllamaEmbedder,
    gateway: ModelGateway,
) -> MemoryComponents:
    """Build memory subsystems. Bus wiring is done later in Atlas.start()."""
    vectors = ChromaVectorStore(str(settings.data_dir / "chroma"))

    # Phase 0: Wire EmbeddingWorker so episode semantic search works
    embedding_worker = EmbeddingWorker(
        embedder=embedder,
        vector_store=vectors,
        db=db,
        batch_size=10,
        max_queue_size=1000,
    )
    episodic = EpisodicMemory(db, clock, embedding_worker=embedding_worker)
    semantic = SemanticMemory(db, vectors, embedder, ids, clock)
    user_model = UserModel(db, clock)
    working = WorkingMemory()

    knowledge_store = KnowledgeStore(
        db=db,
        vector_store=vectors,
        embedder=embedder,
        ids=ids,
        clock=clock,
    )
    retriever = Retriever(
        semantic=semantic,
        episodic=episodic,
        user_model=user_model,
        knowledge_store=knowledge_store,
    )
    consolidator = Consolidator(
        episodic=episodic,
        semantic=semantic,
        gateway=gateway,
        db=db,
        ids=ids,
        clock=clock,
    )
    pruner = Pruner(db=db, gateway=gateway, ids=ids, clock=clock)

    # Phase 2: Trajectory store and experience extractor for durable learning
    trajectory_store = TrajectoryStore(db=db, ids=ids, clock=clock)
    experience_extractor = ExperienceExtractor(
        gateway=gateway,
        trajectory_store=trajectory_store,
        ids=ids,
        clock=clock,
    )

    return MemoryComponents(
        vectors=vectors,
        embedding_worker=embedding_worker,
        episodic=episodic,
        semantic=semantic,
        user_model=user_model,
        working=working,
        knowledge_store=knowledge_store,
        retriever=retriever,
        consolidator=consolidator,
        pruner=pruner,
        trajectory_store=trajectory_store,
        experience_extractor=experience_extractor,
    )
