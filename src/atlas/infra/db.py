"""SQLite persistence substrate.

WHY one connection: single-user, single-process; WAL mode handles our
concurrency. WHY numbered migrations now: schema evolution across 13+ phases
must be ordered and inspectable, never ad-hoc. Phase 1 creates audit + queue
placeholder tables; memory/KG tables arrive as later-numbered migrations.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from atlas.infra.errors import FatalError
from atlas.infra.logging import get_logger

_log = get_logger("atlas.db")

_MIGRATIONS: tuple[str, ...] = (
    # 001 — audit (two-table split: compact index + fat payloads)
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        correlation_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        tool TEXT,
        tier INTEGER,
        decision TEXT,
        outcome TEXT,
        payload_id INTEGER,
        cost_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_audit_corr ON audit_events(correlation_id);
    CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
    CREATE TABLE IF NOT EXISTS payloads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        body TEXT NOT NULL
    );
    """,
    # 002 — durable task queue placeholder (activated Phase 4; created now for stability)
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        source TEXT,
        state TEXT NOT NULL,
        payload TEXT NOT NULL,
        idempotency_key TEXT UNIQUE,
        attempts INTEGER NOT NULL DEFAULT 0,
        not_before TEXT,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dead_letters (
        id TEXT PRIMARY KEY, task_id TEXT, reason TEXT,
        last_error TEXT, payload TEXT, ts TEXT NOT NULL
    );
    """,
    # 003 — memory (Phase 3: episodic, semantic, user-model, consolidation, archive)
    """
    CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        correlation_id TEXT NOT NULL,
        task_id TEXT,
        step INTEGER NOT NULL DEFAULT 0,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        role TEXT,
        content TEXT NOT NULL,
        tool TEXT,
        outcome TEXT,
        salience REAL NOT NULL DEFAULT 0,
        consolidated INTEGER NOT NULL DEFAULT 0,
        tokens INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_ep_corr ON episodes(correlation_id);
    CREATE INDEX IF NOT EXISTS idx_ep_ts ON episodes(ts);
    CREATE INDEX IF NOT EXISTS idx_ep_consolidated
        ON episodes(consolidated, salience);

    CREATE TABLE IF NOT EXISTS semantic_facts (
        id TEXT PRIMARY KEY,
        version INTEGER NOT NULL DEFAULT 1,
        text TEXT NOT NULL,
        kind TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0.5,
        salience REAL NOT NULL DEFAULT 0.5,
        source_episode_ids TEXT,
        superseded_by TEXT,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL,
        embedding_ref TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sem_kind
        ON semantic_facts(kind, superseded_by);

    CREATE TABLE IF NOT EXISTS user_model (
        section TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS consolidation_proposals (
        id TEXT PRIMARY KEY,
        created_ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
    );

    CREATE TABLE IF NOT EXISTS memory_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        summary TEXT NOT NULL,
        episode_count INTEGER NOT NULL,
        created_ts TEXT NOT NULL
    );
    """,
    # 004 — identity platform (Phase 6.2)
    """
    CREATE TABLE IF NOT EXISTS secrets (
        id TEXT PRIMARY KEY,
        ciphertext TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS identities (
        id TEXT PRIMARY KEY,
        kind TEXT,
        provider_hint TEXT,
        expires_at TEXT,
        scopes TEXT,
        rotated_ts TEXT
    );
    """,
    # 005 — notification platform (Phase 6.4)
    """
    CREATE TABLE IF NOT EXISTS notif_queue (
        id TEXT PRIMARY KEY,
        priority INTEGER NOT NULL,
        payload TEXT NOT NULL,
        dedup_key TEXT,
        not_before TEXT,
        expires_at TEXT,
        digest INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notif_queue_fetch 
        ON notif_queue(state, digest, not_before, expires_at, priority DESC, created_ts);

    CREATE TABLE IF NOT EXISTS notif_dead_letter (
        id TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS notif_history (
        id TEXT PRIMARY KEY,
        correlation_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        priority INTEGER NOT NULL,
        channels TEXT NOT NULL,
        delivered INTEGER NOT NULL,
        final_provider TEXT,
        receipt TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notif_history_correlation 
        ON notif_history(correlation_id);
    """,
    # 006 — task event log (Phase 2 Live Run Console)
    # Distinct from episodes/memory. event_id deduplicates; sequence detects gaps on the client.
    """
    CREATE TABLE IF NOT EXISTS task_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        state TEXT NOT NULL,
        summary TEXT NOT NULL,
        capability TEXT,
        operation TEXT,
        provider TEXT,
        tier INTEGER,
        requires_approval INTEGER NOT NULL DEFAULT 0,
        safe_metadata TEXT NOT NULL DEFAULT '{}',
        ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, sequence);
    CREATE INDEX IF NOT EXISTS idx_task_events_event_id ON task_events(event_id);
    """,
    # 007 — Vamos alignment: hash chain audit + feedback + schedules + llm_calls + workflow_templates + idempotency_keys
    """
    ALTER TABLE audit_events ADD COLUMN prev_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE audit_events ADD COLUMN row_hash TEXT NOT NULL DEFAULT '';

    CREATE TABLE IF NOT EXISTS feedback (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        rating INTEGER CHECK (rating IN (-1, 1)),
        comment TEXT,
        original_output TEXT,
        edited_output TEXT,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_feedback_task ON feedback(task_id);
    CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating);

    CREATE TABLE IF NOT EXISTS schedules (
        id TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        cron_expression TEXT NOT NULL,
        task_template TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_run_ts TEXT,
        next_run_ts TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS llm_calls (
        id TEXT PRIMARY KEY,
        task_id TEXT,
        step_index INTEGER,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        tokens_in INTEGER NOT NULL,
        tokens_out INTEGER NOT NULL,
        cost_usd REAL NOT NULL,
        latency_ms INTEGER NOT NULL,
        cached INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_llm_calls_task ON llm_calls(task_id);
    CREATE INDEX IF NOT EXISTS idx_llm_calls_model ON llm_calls(model);
    CREATE INDEX IF NOT EXISTS idx_llm_calls_created ON llm_calls(created_ts);

    CREATE TABLE IF NOT EXISTS workflow_templates (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        steps TEXT NOT NULL,
        variables TEXT NOT NULL DEFAULT '[]',
        derived_from TEXT NOT NULL DEFAULT '[]',
        use_count INTEGER NOT NULL DEFAULT 0,
        success_rate REAL,
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS idempotency_keys (
        key TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_idempotency_keys_created_ts ON idempotency_keys(created_ts);
    """,
    # 008 — semantic response cache (Phase 11)
    """
    CREATE TABLE IF NOT EXISTS semantic_cache (
        id TEXT PRIMARY KEY,
        prompt_hash TEXT,
        embedding_ref TEXT,
        response_json TEXT NOT NULL,
        ttl_expires_ts TEXT,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sem_cache_hash ON semantic_cache(prompt_hash);
    CREATE INDEX IF NOT EXISTS idx_sem_cache_ttl ON semantic_cache(ttl_expires_ts);
    """,
    # 009 — durable event bus queue (Phase 11)
    """
    CREATE TABLE IF NOT EXISTS event_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_event_queue_topic ON event_queue(topic);
    """,
    # 010 — event log for WebSocket replay (Phase 1)
    """
    CREATE TABLE IF NOT EXISTS event_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        task_id TEXT,
        correlation_id TEXT,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_event_log_task ON event_log(task_id, created_ts);
    CREATE INDEX IF NOT EXISTS idx_event_log_correlation ON event_log(correlation_id);
    CREATE INDEX IF NOT EXISTS idx_event_log_topic ON event_log(topic);
    """,
    # 011 — Phase 3: Real-time memory performance indexes
    """
    -- Episodes: Optimized for fast retrieval by task, salience, and kind
    CREATE INDEX IF NOT EXISTS idx_episodes_task_id ON episodes(task_id);
    CREATE INDEX IF NOT EXISTS idx_episodes_task_salience 
        ON episodes(task_id, salience DESC, ts DESC);
    CREATE INDEX IF NOT EXISTS idx_episodes_kind_ts ON episodes(kind, ts DESC);
    CREATE INDEX IF NOT EXISTS idx_episodes_salience ON episodes(salience DESC);
    
    -- Semantic facts: Fast lookups by kind, confidence, and recency
    CREATE INDEX IF NOT EXISTS idx_sem_facts_confidence 
        ON semantic_facts(kind, confidence DESC, updated_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_sem_facts_updated ON semantic_facts(updated_ts DESC);
    
    -- Add embedding_id column for vector store reference
    ALTER TABLE episodes ADD COLUMN embedding_id TEXT DEFAULT NULL;
    CREATE INDEX IF NOT EXISTS idx_episodes_embedding ON episodes(embedding_id);
    """,
    # 012 — Phase 3: Knowledge documents for Live RAG
    """
    -- Knowledge documents: External documents ingested for RAG
    CREATE TABLE IF NOT EXISTS knowledge_documents (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        source_path TEXT NOT NULL,
        source_type TEXT NOT NULL,
        chunk_count INTEGER NOT NULL,
        file_hash TEXT NOT NULL UNIQUE,
        indexed INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_knowledge_docs_hash ON knowledge_documents(file_hash);
    CREATE INDEX IF NOT EXISTS idx_knowledge_docs_type ON knowledge_documents(source_type);
    CREATE INDEX IF NOT EXISTS idx_knowledge_docs_created ON knowledge_documents(created_ts DESC);
    
    -- Knowledge chunks: Individual chunks of documents with metadata
    CREATE TABLE IF NOT EXISTS knowledge_chunks (
        chunk_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        content TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        total_chunks INTEGER NOT NULL,
        embedding_id TEXT,
        metadata_json TEXT,
        created_ts TEXT NOT NULL,
        FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_doc ON knowledge_chunks(document_id);
    CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding ON knowledge_chunks(embedding_id);
    """,
    # 013 — Phase 2: Trajectory & Experience Store (durable learning foundation)
    """
    -- Trajectories: Complete task execution history
    CREATE TABLE IF NOT EXISTS trajectories (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL UNIQUE,
        correlation_id TEXT NOT NULL,
        request TEXT NOT NULL,
        goal TEXT NOT NULL,
        plan_steps TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        plan_confidence REAL NOT NULL,
        actions TEXT NOT NULL,
        observations TEXT NOT NULL,
        decision_trace_ids TEXT NOT NULL DEFAULT '[]',
        failure_record_ids TEXT NOT NULL DEFAULT '[]',
        replan_count INTEGER NOT NULL DEFAULT 0,
        verification_passed INTEGER,
        verification_score REAL,
        success INTEGER NOT NULL,
        answer TEXT,
        error TEXT,
        steps_taken INTEGER NOT NULL,
        latency_ms INTEGER NOT NULL,
        tokens_used INTEGER NOT NULL,
        cost_usd REAL NOT NULL,
        model_calls INTEGER NOT NULL,
        tool_calls INTEGER NOT NULL,
        created_ts TEXT NOT NULL,
        completed_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_traj_task ON trajectories(task_id);
    CREATE INDEX IF NOT EXISTS idx_traj_correlation ON trajectories(correlation_id);
    CREATE INDEX IF NOT EXISTS idx_traj_success ON trajectories(success, completed_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_traj_replan ON trajectories(replan_count, completed_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_traj_completed ON trajectories(completed_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_traj_latency ON trajectories(latency_ms DESC);
    
    -- Decision Traces: Records of model/tool/strategy choices
    CREATE TABLE IF NOT EXISTS decision_traces (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        decision_point TEXT NOT NULL,
        options_considered TEXT NOT NULL,
        chosen_option TEXT NOT NULL,
        rationale TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}',
        outcome TEXT NOT NULL DEFAULT 'unknown',
        outcome_detail TEXT,
        confidence REAL NOT NULL DEFAULT 0.5,
        latency_ms INTEGER,
        cost_usd REAL NOT NULL DEFAULT 0.0,
        FOREIGN KEY (task_id) REFERENCES trajectories(task_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_dt_task ON decision_traces(task_id, ts);
    CREATE INDEX IF NOT EXISTS idx_dt_point ON decision_traces(decision_point, outcome);
    CREATE INDEX IF NOT EXISTS idx_dt_outcome ON decision_traces(outcome, ts DESC);
    CREATE INDEX IF NOT EXISTS idx_dt_option ON decision_traces(chosen_option, outcome);
    
    -- Failure Records: Structured error taxonomy for pattern detection
    CREATE TABLE IF NOT EXISTS failure_records (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        category TEXT NOT NULL,
        step INTEGER NOT NULL,
        component TEXT NOT NULL,
        error_message TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}',
        recovered INTEGER NOT NULL DEFAULT 0,
        recovery_method TEXT,
        recovery_succeeded INTEGER NOT NULL DEFAULT 0,
        similar_failure_ids TEXT NOT NULL DEFAULT '[]',
        mitigation_suggested TEXT,
        mitigation_applied INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (task_id) REFERENCES trajectories(task_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_fr_task ON failure_records(task_id, ts);
    CREATE INDEX IF NOT EXISTS idx_fr_category ON failure_records(category, ts DESC);
    CREATE INDEX IF NOT EXISTS idx_fr_component ON failure_records(component, category);
    CREATE INDEX IF NOT EXISTS idx_fr_recovered ON failure_records(recovered, recovery_succeeded);
    CREATE INDEX IF NOT EXISTS idx_fr_pattern ON failure_records(category, component, recovered);
    
    -- Experiences: Extracted lessons from trajectory analysis
    CREATE TABLE IF NOT EXISTS experiences (
        id TEXT PRIMARY KEY,
        trajectory_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        category TEXT NOT NULL,
        lesson_text TEXT NOT NULL,
        applicability_context TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0.5,
        supporting_actions TEXT NOT NULL DEFAULT '[]',
        supporting_observations TEXT NOT NULL DEFAULT '[]',
        counter_examples TEXT NOT NULL DEFAULT '[]',
        reuse_count INTEGER NOT NULL DEFAULT 0,
        success_rate REAL NOT NULL DEFAULT 0.0,
        avg_improvement_ms INTEGER NOT NULL DEFAULT 0,
        avg_cost_savings_usd REAL NOT NULL DEFAULT 0.0,
        extracted_ts TEXT NOT NULL,
        last_applied_ts TEXT,
        superseded_by TEXT,
        FOREIGN KEY (trajectory_id) REFERENCES trajectories(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_exp_trajectory ON experiences(trajectory_id);
    CREATE INDEX IF NOT EXISTS idx_exp_category ON experiences(category, confidence DESC);
    CREATE INDEX IF NOT EXISTS idx_exp_reuse ON experiences(reuse_count DESC, success_rate DESC);
    CREATE INDEX IF NOT EXISTS idx_exp_confidence ON experiences(confidence DESC, extracted_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_exp_superseded ON experiences(superseded_by, extracted_ts DESC);
    
    -- Experience applications: Track when and how experiences are reused
    CREATE TABLE IF NOT EXISTS experience_applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experience_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        applied_ts TEXT NOT NULL,
        success INTEGER NOT NULL,
        improvement_ms INTEGER,
        cost_savings_usd REAL,
        FOREIGN KEY (experience_id) REFERENCES experiences(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_ea_experience ON experience_applications(experience_id, applied_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_ea_task ON experience_applications(task_id);
    """,
    """
    -- Evaluation results: outcomes of golden-task / trajectory evaluations.
    -- One row per (golden task, run); regression gates compare across runs.
    CREATE TABLE IF NOT EXISTS evaluation_results (
        id TEXT PRIMARY KEY,
        golden_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        evaluator TEXT NOT NULL,             -- deterministic | llm_judge | ...
        passed INTEGER NOT NULL,
        score REAL NOT NULL,
        detail TEXT,                          -- JSON: criteria results, judge rationale
        answer TEXT,
        latency_ms INTEGER,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_eval_golden ON evaluation_results(golden_id, created_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_eval_run ON evaluation_results(run_id);
    """,
    """
    -- Skills: versioned, evidence-scored reusable procedures promoted from
    -- repeated successful experiences. Never auto-modifies behavior beyond
    -- prompt context; promotion requires evidence thresholds.
    CREATE TABLE IF NOT EXISTS skills (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        procedure_steps TEXT NOT NULL DEFAULT '[]',     -- JSON array of steps
        version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'candidate',       -- candidate | active | disabled
        success_rate REAL NOT NULL DEFAULT 0.0,
        usage_count INTEGER NOT NULL DEFAULT 0,
        confidence REAL NOT NULL DEFAULT 0.5,
        preferred_tools TEXT NOT NULL DEFAULT '[]',     -- JSON array
        known_failure_modes TEXT NOT NULL DEFAULT '[]', -- JSON array
        source_experience_ids TEXT NOT NULL DEFAULT '[]',
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL,
        superseded_by TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name, version DESC);
    CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status, confidence DESC);

    -- Strategies: governed task-approach preferences. Promotion to 'active'
    -- requires offline evaluation evidence; safety policy is never derived
    -- from strategies.
    CREATE TABLE IF NOT EXISTS strategies (
        id TEXT PRIMARY KEY,
        task_type_pattern TEXT NOT NULL,
        approach TEXT NOT NULL,
        model_preference TEXT,
        tool_preference TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'candidate',       -- candidate | active | retired
        success_rate REAL NOT NULL DEFAULT 0.0,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        eval_score REAL,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_strategies_pattern ON strategies(task_type_pattern);

    -- World state: lightweight entity tracking so the agent stops
    -- rediscovering its environment every task.
    CREATE TABLE IF NOT EXISTS world_state (
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        attributes TEXT NOT NULL DEFAULT '{}',           -- JSON object
        updated_ts TEXT NOT NULL,
        PRIMARY KEY (entity_type, entity_id)
    );
    """,
    """
    -- Batch 7: durable execution checkpoints. Saved after every reasoning
    -- step so an interrupted task's progress survives restarts. Resume is
    -- opt-in (side-effect safety); crash recovery at minimum marks orphaned
    -- running tasks instead of leaving them 'reasoning' forever.
    CREATE TABLE IF NOT EXISTS execution_checkpoints (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL DEFAULT 'local',
        step INT NOT NULL,
        state_json TEXT NOT NULL,          -- goal, plan, history summary
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ckpt_task ON execution_checkpoints(task_id, created_ts DESC);

    -- Tenant awareness seed: every tenant-scoped table gains tenant_id with
    -- a safe single-user default. Existing rows backfill to 'local'.
    ALTER TABLE tasks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local';
    ALTER TABLE trajectories ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local';
    """,
    """
    -- Phase 9: durable task queue for worker-process execution. Claiming is a
    -- single conditional UPDATE (atomic per row), which is correct under both
    -- SQLite WAL (single writer) and PostgreSQL (row-level locks).
    CREATE TABLE IF NOT EXISTS task_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT NOT NULL,               -- JSON InboundEvent
        tenant_id TEXT NOT NULL DEFAULT 'local',
        state TEXT NOT NULL DEFAULT 'pending',  -- pending | claimed | done | failed | dead
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        claimed_by TEXT,
        claimed_ts TEXT,
        created_ts TEXT NOT NULL,
        completed_ts TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tq_state ON task_queue(state, created_ts);
    """,
    """
    -- 014 — Phase 2 Autonomy Fabric: Canonical event storage
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        source TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        causation_id TEXT,
        deduplication_key TEXT UNIQUE,
        occurred_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        schema_version INTEGER NOT NULL DEFAULT 1,
        durability TEXT NOT NULL,           -- ephemeral | durable | replayable
        delivery_status TEXT NOT NULL,      -- pending | delivered | dead_letter
        attempt_count INTEGER NOT NULL DEFAULT 0,
        next_retry_at TEXT,
        dead_letter_reason TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
    CREATE INDEX IF NOT EXISTS idx_events_status ON events(delivery_status, next_retry_at);
    CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at DESC);
    """,
    """
    -- 015 — Phase 3 Autonomy Fabric: Automation Registry
    CREATE TABLE IF NOT EXISTS automations (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        trigger_config TEXT NOT NULL,
        action_config TEXT NOT NULL,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_automations_enabled ON automations(enabled);
    """,
)


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def start(self) -> None:
        if self._conn is not None:
            _log.warning("db.start_duplicate", event_type="db", detail="closing existing connection before re-start")
            await self._conn.close()
            self._conn = None
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        # Phase 3.8: Performance PRAGMAs
        # WAL mode: concurrent readers don't block writers (critical for real-time memory)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # 64 MB page cache — keeps hot index pages in RAM, avoids repeat disk reads
        await self._conn.execute("PRAGMA cache_size=-65536")
        # Memory-mapped I/O: 256 MB — OS handles prefetch, zero-copy reads
        await self._conn.execute("PRAGMA mmap_size=268435456")
        # synchronous=NORMAL: safe with WAL (no full fsync on each write)
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        # temp_store=MEMORY: sort/group-by scratch space stays in RAM
        await self._conn.execute("PRAGMA temp_store=MEMORY")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        await self._apply_migrations()
        await self._conn.commit()
        _log.info("db.ready", event_type="db", path=str(self._path), version=len(_MIGRATIONS))

    async def _apply_migrations(self) -> None:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        current = int(row["version"]) if row else 0
        for i, script in enumerate(_MIGRATIONS[current:], start=current + 1):
            await self._conn.executescript(script)
            _log.info("db.migrate", event_type="db", to_version=i)
        target = len(_MIGRATIONS)
        if row is None:
            await self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (target,))
        else:
            await self._conn.execute("UPDATE schema_version SET version=?", (target,))

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise FatalError("database not started")
        return self._conn

    async def stop(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def health(self) -> bool:
        return self._conn is not None
