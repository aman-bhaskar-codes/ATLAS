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
    """
    -- 016 — Prompt 3 Knowledge Fabric: canonical documents, chunks, evidence,
    -- research sessions, RAG telemetry, retrieval feedback, adapter registry.
    CREATE TABLE IF NOT EXISTS fabric_documents (
        document_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        title TEXT NOT NULL,
        uri TEXT NOT NULL DEFAULT '',
        canonical_uri TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        content_type TEXT NOT NULL DEFAULT 'text/plain',
        language TEXT NOT NULL DEFAULT 'en',
        author TEXT NOT NULL DEFAULT '',
        published_at TEXT,
        retrieved_at TEXT NOT NULL,
        modified_at TEXT,
        content_hash TEXT NOT NULL,
        authority REAL NOT NULL DEFAULT 0.5,
        trust_score REAL NOT NULL DEFAULT 0.5,
        freshness REAL NOT NULL DEFAULT 0.5,
        license TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        provenance_json TEXT NOT NULL DEFAULT '{}',
        security_status TEXT NOT NULL DEFAULT 'SAFE',
        security_flags_json TEXT NOT NULL DEFAULT '[]',
        pipeline_version TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'READY',
        chunk_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_fdoc_hash ON fabric_documents(content_hash);
    CREATE INDEX IF NOT EXISTS idx_fdoc_type ON fabric_documents(source_type);
    CREATE INDEX IF NOT EXISTS idx_fdoc_status ON fabric_documents(status);
    CREATE INDEX IF NOT EXISTS idx_fdoc_retrieved ON fabric_documents(retrieved_at DESC);

    CREATE TABLE IF NOT EXISTS fabric_chunks (
        chunk_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        content TEXT NOT NULL,
        heading TEXT NOT NULL DEFAULT '',
        chunk_index INTEGER NOT NULL,
        total_chunks INTEGER NOT NULL,
        char_start INTEGER NOT NULL DEFAULT 0,
        char_end INTEGER NOT NULL DEFAULT 0,
        token_estimate INTEGER NOT NULL DEFAULT 0,
        embedding_id TEXT,
        kind TEXT NOT NULL DEFAULT 'text',           -- text | table | code
        FOREIGN KEY (document_id) REFERENCES fabric_documents(document_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_fchunk_doc ON fabric_chunks(document_id, chunk_index);

    CREATE TABLE IF NOT EXISTS fabric_evidence (
        evidence_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        quote TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT '',
        authority REAL NOT NULL DEFAULT 0.5,
        confidence REAL NOT NULL DEFAULT 0.5,
        provenance_json TEXT NOT NULL DEFAULT '{}',
        hash TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_fevid_doc ON fabric_evidence(document_id);
    CREATE INDEX IF NOT EXISTS idx_fevid_chunk ON fabric_evidence(chunk_id);

    CREATE TABLE IF NOT EXISTS research_sessions (
        session_id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',          -- OPEN | IN_PROGRESS | ANSWERED | BLOCKED | DISPUTED
        questions_json TEXT NOT NULL DEFAULT '[]',
        visited_urls_json TEXT NOT NULL DEFAULT '[]',
        document_ids_json TEXT NOT NULL DEFAULT '[]',
        budget_used_json TEXT NOT NULL DEFAULT '{}',
        started_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rsess_status ON research_sessions(status, updated_ts DESC);

    CREATE TABLE IF NOT EXISTS rag_records (
        id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        mode TEXT NOT NULL,
        route TEXT NOT NULL,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        retrieve_ms INTEGER NOT NULL DEFAULT 0,
        rerank_ms INTEGER NOT NULL DEFAULT 0,
        synthesize_ms INTEGER NOT NULL DEFAULT 0,
        candidate_count INTEGER NOT NULL DEFAULT 0,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        answered INTEGER NOT NULL DEFAULT 0,
        degraded INTEGER NOT NULL DEFAULT 0,
        failure TEXT,                                  -- failure taxonomy code
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rag_mode ON rag_records(mode, created_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_rag_failure ON rag_records(failure, created_ts DESC);

    CREATE TABLE IF NOT EXISTS retrieval_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        label TEXT NOT NULL,                           -- §125 feedback labels
        used_in_answer INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rf_chunk ON retrieval_feedback(chunk_id);
    CREATE INDEX IF NOT EXISTS idx_rf_label ON retrieval_feedback(label, created_ts DESC);

    CREATE TABLE IF NOT EXISTS knowledge_adapters (
        name TEXT NOT NULL,
        kind TEXT NOT NULL,                            -- retriever | reranker | router | rewriter
        version TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'EXPERIMENTAL',    -- EXPERIMENTAL | VALIDATED | ACTIVE | DEPRECATED
        metrics_json TEXT NOT NULL DEFAULT '{}',
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL,
        PRIMARY KEY (kind, name, version)
    );
    CREATE INDEX IF NOT EXISTS idx_kadapter_state ON knowledge_adapters(kind, state);
    """,
    # 015 — Prompt 4: adaptation control plane (learning objects, §1-§24)
    """
    ALTER TABLE trajectories ADD COLUMN atlas_version TEXT;
    ALTER TABLE trajectories ADD COLUMN git_commit TEXT;
    ALTER TABLE trajectories ADD COLUMN config_hash TEXT;
    ALTER TABLE trajectories ADD COLUMN strategy_id TEXT;
    ALTER TABLE trajectories ADD COLUMN strategy_version INTEGER;
    ALTER TABLE trajectories ADD COLUMN model_version TEXT;
    ALTER TABLE trajectories ADD COLUMN capability_snapshot_version TEXT;
    ALTER TABLE trajectories ADD COLUMN safety_events TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE trajectories ADD COLUMN completion_confidence REAL;

    CREATE TABLE IF NOT EXISTS failure_taxonomy (
        failure_id TEXT PRIMARY KEY,
        trajectory_id TEXT NOT NULL,
        failure_class TEXT NOT NULL,
        step_id INTEGER,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        root_cause_candidate INTEGER NOT NULL DEFAULT 0,
        recoverable INTEGER NOT NULL DEFAULT 0,
        recovery_attempts INTEGER NOT NULL DEFAULT 0,
        final_resolution TEXT NOT NULL DEFAULT 'FAILED',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ft_traj ON failure_taxonomy(trajectory_id);
    CREATE INDEX IF NOT EXISTS idx_ft_class ON failure_taxonomy(failure_class, created_ts DESC);

    CREATE TABLE IF NOT EXISTS failure_analyses (
        trajectory_id TEXT PRIMARY KEY,
        primary_cause TEXT NOT NULL,
        secondary_causes_json TEXT NOT NULL DEFAULT '[]',
        evidence_json TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL DEFAULT 0,
        avoidable INTEGER NOT NULL DEFAULT 0,
        recommended_intervention TEXT NOT NULL DEFAULT '',
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS trajectory_evaluations (
        trajectory_id TEXT PRIMARY KEY,
        scores_json TEXT NOT NULL,
        evaluator_levels_json TEXT NOT NULL DEFAULT '[]',
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS outcome_evaluations (
        trajectory_id TEXT PRIMARY KEY,
        verdict TEXT NOT NULL,
        overall_score REAL NOT NULL,
        rationale TEXT NOT NULL DEFAULT '',
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS hypotheses (
        hypothesis_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        problem_statement TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        affected_component TEXT NOT NULL,
        proposed_change TEXT NOT NULL,
        change_type TEXT NOT NULL,
        expected_effect TEXT NOT NULL DEFAULT '',
        risk TEXT NOT NULL DEFAULT 'LOW',
        constraints_json TEXT NOT NULL DEFAULT '[]',
        evaluation_plan TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'PROPOSED',
        experiment_id TEXT,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_hyp_status ON hypotheses(status, updated_ts DESC);

    CREATE TABLE IF NOT EXISTS experiments (
        experiment_id TEXT PRIMARY KEY,
        hypothesis_id TEXT NOT NULL,
        baseline_json TEXT NOT NULL,
        candidate_json TEXT NOT NULL,
        dataset_version TEXT NOT NULL,
        pipeline_version TEXT NOT NULL,
        atlas_version TEXT NOT NULL,
        metrics_json TEXT NOT NULL DEFAULT '[]',
        resource_limits_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        created_ts TEXT NOT NULL,
        completed_ts TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_exp_status ON experiments(status, created_ts DESC);

    CREATE TABLE IF NOT EXISTS comparison_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experiment_id TEXT NOT NULL,
        metric TEXT NOT NULL,
        baseline_version TEXT NOT NULL,
        candidate_version TEXT NOT NULL,
        dataset_version TEXT NOT NULL,
        model_version TEXT NOT NULL DEFAULT '',
        atlas_version TEXT NOT NULL,
        n INTEGER NOT NULL,
        baseline_mean REAL NOT NULL,
        candidate_mean REAL NOT NULL,
        baseline_median REAL,
        candidate_median REAL,
        baseline_variance REAL,
        candidate_variance REAL,
        ci_low REAL,
        ci_high REAL,
        effect_size REAL,
        paired INTEGER NOT NULL DEFAULT 0,
        significant INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_cmp_exp ON comparison_results(experiment_id, metric);

    CREATE TABLE IF NOT EXISTS generalization_results (
        experiment_id TEXT PRIMARY KEY,
        baseline_score REAL NOT NULL,
        candidate_score REAL NOT NULL,
        n_tasks INTEGER NOT NULL,
        holds_on_unseen INTEGER NOT NULL DEFAULT 0,
        score_by_domain_json TEXT NOT NULL DEFAULT '{}',
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS promotion_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experiment_id TEXT NOT NULL,
        hypothesis_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        reasons_json TEXT NOT NULL DEFAULT '[]',
        safety_regression INTEGER NOT NULL DEFAULT 0,
        promotion_state TEXT NOT NULL DEFAULT 'PROPOSED',
        promoted_strategy_id TEXT,
        promoted_version INTEGER,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_promo_exp ON promotion_decisions(experiment_id);

    CREATE TABLE IF NOT EXISTS strategy_versions (
        strategy_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        definition TEXT NOT NULL,
        task_type_pattern TEXT NOT NULL DEFAULT '*',
        skills_json TEXT NOT NULL DEFAULT '[]',
        retrieval_policy TEXT NOT NULL DEFAULT '',
        model_preference TEXT NOT NULL DEFAULT '',
        tool_preference TEXT NOT NULL DEFAULT '',
        verification_policy TEXT NOT NULL DEFAULT '',
        change_reason TEXT NOT NULL DEFAULT '',
        source_experiments_json TEXT NOT NULL DEFAULT '[]',
        created_ts TEXT NOT NULL,
        PRIMARY KEY (strategy_id, version)
    );

    CREATE TABLE IF NOT EXISTS strategy_performance (
        strategy_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        runs INTEGER NOT NULL DEFAULT 0,
        success_rate REAL NOT NULL DEFAULT 0,
        quality_score REAL NOT NULL DEFAULT 0,
        latency_ms_avg REAL NOT NULL DEFAULT 0,
        cost_usd_avg REAL NOT NULL DEFAULT 0,
        recovery_rate REAL NOT NULL DEFAULT 0,
        verification_rate REAL NOT NULL DEFAULT 0,
        generalization REAL NOT NULL DEFAULT 0,
        user_feedback REAL NOT NULL DEFAULT 0,
        updated_ts TEXT NOT NULL,
        PRIMARY KEY (strategy_id, version)
    );

    CREATE TABLE IF NOT EXISTS decision_preferences (
        preference_id TEXT PRIMARY KEY,
        adaptation_point TEXT NOT NULL,
        context_key TEXT NOT NULL DEFAULT '',
        preferred_option TEXT NOT NULL,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        success_rate REAL NOT NULL DEFAULT 0,
        source_experiment TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pref_point ON decision_preferences(adaptation_point, context_key, active);

    CREATE TABLE IF NOT EXISTS adaptation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        ref_id TEXT NOT NULL DEFAULT '',
        detail_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_ae_ts ON adaptation_events(ts);

    CREATE TABLE IF NOT EXISTS negative_experiences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trajectory_id TEXT NOT NULL,
        lesson TEXT NOT NULL,
        why_rejected TEXT NOT NULL DEFAULT '',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_neg_traj ON negative_experiences(trajectory_id);
    """,
    # 016 — Prompt 4: structured experiences + skill lifecycle (§9-§11)
    """
    CREATE TABLE IF NOT EXISTS structured_experiences (
        experience_id TEXT PRIMARY KEY,
        trajectory_id TEXT NOT NULL,
        problem_pattern TEXT NOT NULL,
        what_worked TEXT NOT NULL DEFAULT '',
        what_failed TEXT NOT NULL DEFAULT '',
        successful_actions_json TEXT NOT NULL DEFAULT '[]',
        failed_actions_json TEXT NOT NULL DEFAULT '[]',
        recovery_pattern TEXT NOT NULL DEFAULT '',
        useful_evidence_json TEXT NOT NULL DEFAULT '[]',
        lesson_candidate TEXT NOT NULL DEFAULT '',
        validated INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_se_pattern ON structured_experiences(problem_pattern, created_ts DESC);

    CREATE TABLE IF NOT EXISTS skill_lifecycle (
        skill_name TEXT PRIMARY KEY,
        state TEXT NOT NULL DEFAULT 'EXPERIMENTAL',
        applications INTEGER NOT NULL DEFAULT 0,
        successes INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        updated_ts TEXT NOT NULL
    );
    """,
    # 017 — Prompt 4: shadow, canary, counterfactual, decision quality (§25-§31)
    """
    CREATE TABLE IF NOT EXISTS shadow_comparisons (
        comparison_id TEXT PRIMARY KEY,
        trajectory_id TEXT NOT NULL,
        strategy_id TEXT NOT NULL,
        baseline_version INTEGER NOT NULL,
        candidate_version INTEGER NOT NULL,
        decision_agreement REAL NOT NULL DEFAULT 0,
        plan_similarity REAL NOT NULL DEFAULT 0,
        tool_choice_agreement REAL NOT NULL DEFAULT 0,
        retrieval_similarity REAL NOT NULL DEFAULT 0,
        expected_result_delta REAL NOT NULL DEFAULT 0,
        verdict TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_shadow_traj ON shadow_comparisons(trajectory_id);

    CREATE TABLE IF NOT EXISTS canary_deployments (
        deployment_id TEXT PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        percentage REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'CANARY',
        tasks_seen INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_canary_strategy ON canary_deployments(strategy_id, status);

    CREATE TABLE IF NOT EXISTS canary_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deployment_id TEXT NOT NULL,
        trajectory_id TEXT NOT NULL,
        success INTEGER NOT NULL DEFAULT 0,
        regression INTEGER NOT NULL DEFAULT 0,
        safety_event INTEGER NOT NULL DEFAULT 0,
        latency_ms REAL NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0,
        ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_canary_obs ON canary_observations(deployment_id);

    CREATE TABLE IF NOT EXISTS counterfactuals (
        counterfactual_id TEXT PRIMARY KEY,
        trajectory_id TEXT NOT NULL,
        adaptation_point TEXT NOT NULL,
        original_option TEXT NOT NULL,
        alternative_option TEXT NOT NULL,
        original_outcome TEXT NOT NULL,
        alternative_outcome TEXT NOT NULL DEFAULT '',
        mode TEXT NOT NULL DEFAULT 'SIMULATION',
        delta REAL NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_cf_point
        ON counterfactuals(adaptation_point, original_option, alternative_option);

    CREATE TABLE IF NOT EXISTS decision_quality (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trajectory_id TEXT NOT NULL,
        dimension TEXT NOT NULL,
        score REAL NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        better_alternative TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_dq_traj ON decision_quality(trajectory_id);
    """,
    # 018 — Prompt 4: cognitive telemetry, calibration, adaptive routing (§32-§37)
    """
    CREATE TABLE IF NOT EXISTS cognitive_telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trajectory_id TEXT NOT NULL,
        planning_quality REAL,
        tool_selection_accuracy REAL,
        model_selection_quality REAL,
        retrieval_usefulness REAL,
        memory_usefulness REAL,
        verification_quality REAL,
        recovery_quality REAL,
        research_efficiency REAL,
        strategy_transfer REAL,
        confidence_calibration REAL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ct_traj ON cognitive_telemetry(trajectory_id);

    CREATE TABLE IF NOT EXISTS calibration_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trajectory_id TEXT NOT NULL DEFAULT '',
        predicted_confidence REAL NOT NULL,
        actual_success INTEGER NOT NULL,
        ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS routing_stats (
        arm_kind TEXT NOT NULL,
        arm TEXT NOT NULL,
        task_class TEXT NOT NULL,
        runs INTEGER NOT NULL DEFAULT 0,
        successes INTEGER NOT NULL DEFAULT 0,
        quality_sum REAL NOT NULL DEFAULT 0,
        latency_sum REAL NOT NULL DEFAULT 0,
        cost_sum REAL NOT NULL DEFAULT 0,
        exploration_runs INTEGER NOT NULL DEFAULT 0,
        updated_ts TEXT NOT NULL,
        PRIMARY KEY (arm_kind, arm, task_class)
    );
    """,
    # 019 — Prompt 4: generalization, adversarial, recovery, long-horizon,
    # evaluation dataset, synthetic variants (§38-§44)
    """
    CREATE TABLE IF NOT EXISTS generalization_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experiment_id TEXT NOT NULL,
        in_domain REAL NOT NULL,
        unseen REAL NOT NULL,
        transfer REAL,
        robustness REAL,
        gate_passed INTEGER NOT NULL DEFAULT 0,
        reasons_json TEXT NOT NULL DEFAULT '[]',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_gen_exp ON generalization_reports(experiment_id);

    CREATE TABLE IF NOT EXISTS adversarial_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        perturbation TEXT NOT NULL,
        n_tasks INTEGER NOT NULL,
        survived INTEGER NOT NULL,
        survival_rate REAL NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_adv_strategy ON adversarial_results(strategy_id, perturbation);

    CREATE TABLE IF NOT EXISTS recovery_evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trajectory_id TEXT NOT NULL,
        initial_failure INTEGER NOT NULL,
        recovered INTEGER NOT NULL,
        recovery_steps INTEGER NOT NULL DEFAULT 0,
        additional_cost_usd REAL NOT NULL DEFAULT 0,
        quality_after_recovery REAL,
        score REAL NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rec_traj ON recovery_evaluations(trajectory_id);

    CREATE TABLE IF NOT EXISTS long_horizon_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trajectory_id TEXT NOT NULL,
        steps INTEGER NOT NULL,
        goal_completion REAL NOT NULL,
        error_accumulation REAL NOT NULL,
        plan_drift REAL NOT NULL,
        verification_quality REAL,
        recovery REAL,
        score REAL NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_lh_traj ON long_horizon_results(trajectory_id);

    CREATE TABLE IF NOT EXISTS eval_samples (
        sample_id TEXT PRIMARY KEY,
        task TEXT NOT NULL,
        domain TEXT NOT NULL DEFAULT '',
        difficulty TEXT NOT NULL DEFAULT 'medium',
        success_criteria TEXT NOT NULL DEFAULT '',
        allowed_capabilities_json TEXT NOT NULL DEFAULT '[]',
        risk TEXT NOT NULL DEFAULT 'low',
        evaluation_method TEXT NOT NULL DEFAULT 'automated',
        source TEXT NOT NULL DEFAULT 'golden',
        approved INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS synthetic_variants (
        variant_id TEXT PRIMARY KEY,
        source_sample_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        task TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'DRAFT',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sv_source ON synthetic_variants(source_sample_id, status);
    """,
    # 020 — Prompt 4: learning budget, adaptation curve, learning cycles,
    # regression protection (§45-§53)
    """
    CREATE TABLE IF NOT EXISTS learning_budget_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id TEXT NOT NULL,
        cpu_seconds REAL NOT NULL DEFAULT 0,
        model_calls INTEGER NOT NULL DEFAULT 0,
        tokens INTEGER NOT NULL DEFAULT 0,
        time_minutes REAL NOT NULL DEFAULT 0,
        disk_mb REAL NOT NULL DEFAULT 0,
        network_mb REAL NOT NULL DEFAULT 0,
        memory_mb REAL NOT NULL DEFAULT 0,
        aborted INTEGER NOT NULL DEFAULT 0,
        abort_reason TEXT,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_lbu_cycle ON learning_budget_usage(cycle_id);

    CREATE TABLE IF NOT EXISTS adaptation_curve (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id TEXT NOT NULL,
        success_rate REAL,
        error_rate REAL,
        latency_ms REAL,
        cost_usd REAL,
        step_count REAL,
        recovery_success_rate REAL,
        verification_rate REAL,
        tokens_per_task REAL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ac_cycle ON adaptation_curve(cycle_id);

    CREATE TABLE IF NOT EXISTS learning_cycles (
        cycle_id TEXT PRIMARY KEY,
        trigger_kind TEXT NOT NULL,
        trajectories_analyzed INTEGER NOT NULL DEFAULT 0,
        clusters_found INTEGER NOT NULL DEFAULT 0,
        hypotheses_proposed INTEGER NOT NULL DEFAULT 0,
        experiments_run INTEGER NOT NULL DEFAULT 0,
        promotions INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        notes_json TEXT NOT NULL DEFAULT '[]',
        created_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS regression_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experiment_id TEXT NOT NULL,
        suite TEXT NOT NULL,
        domain TEXT NOT NULL DEFAULT '',
        passed INTEGER NOT NULL,
        score REAL,
        detail TEXT NOT NULL DEFAULT '',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rr_exp ON regression_results(experiment_id, suite);
    """,
    # 021 — Prompt 4: domain feedback loops, verification, capability stats,
    # autonomy modes (§54-§74)
    """
    CREATE TABLE IF NOT EXISTS tool_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool_name TEXT NOT NULL,
        task_class TEXT NOT NULL DEFAULT '',
        success INTEGER NOT NULL,
        latency_ms REAL NOT NULL DEFAULT 0,
        failure_reason TEXT,
        recovered INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tp_tool ON tool_performance(tool_name, task_class);

    CREATE TABLE IF NOT EXISTS source_trust (
        source TEXT PRIMARY KEY,
        usefulness REAL NOT NULL DEFAULT 0.5,
        claim_correctness REAL NOT NULL DEFAULT 0.5,
        citation_acceptance REAL NOT NULL DEFAULT 0.5,
        freshness_score REAL NOT NULL DEFAULT 0.5,
        contradiction_rate REAL NOT NULL DEFAULT 0,
        trust REAL NOT NULL DEFAULT 0.5,
        n_observations INTEGER NOT NULL DEFAULT 0,
        updated_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS memory_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        memory_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        helped INTEGER NOT NULL DEFAULT 0,
        distracted INTEGER NOT NULL DEFAULT 0,
        stale INTEGER NOT NULL DEFAULT 0,
        rating REAL NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_mf_memory ON memory_feedback(memory_id);

    CREATE TABLE IF NOT EXISTS human_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        ref_kind TEXT NOT NULL,
        ref_id TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        reliability REAL NOT NULL DEFAULT 0.5,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_hf_ref ON human_feedback(ref_kind, ref_id);

    CREATE TABLE IF NOT EXISTS user_corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_class TEXT NOT NULL,
        preferred_source_strategy TEXT NOT NULL,
        context TEXT NOT NULL DEFAULT '',
        count INTEGER NOT NULL DEFAULT 1,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_uc_class ON user_corrections(task_class);

    CREATE TABLE IF NOT EXISTS research_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        sources_searched INTEGER NOT NULL,
        unique_information REAL NOT NULL,
        answer_quality REAL NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_rf_task ON research_feedback(task_id);

    CREATE TABLE IF NOT EXISTS verification_preferences (
        task_class TEXT PRIMARY KEY,
        level TEXT NOT NULL,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        policy_locked INTEGER NOT NULL DEFAULT 0,
        updated_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS capability_stats (
        capability TEXT PRIMARY KEY,
        success_rate REAL NOT NULL DEFAULT 0,
        avg_latency_ms REAL NOT NULL DEFAULT 0,
        n_samples INTEGER NOT NULL DEFAULT 0,
        updated_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS autonomy_modes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL,
        changed_by TEXT NOT NULL DEFAULT 'system',
        reason TEXT NOT NULL DEFAULT '',
        created_ts TEXT NOT NULL
    );
    """,
    # 022 — Prompt 5: engineering intelligence / incident response layer
    # (§3 incidents, §7 correlation, §12 baselines, §15 dedup, §16 diagnosis,
    #  §27 repair, §33 gate, §40 frontend errors, §43 worker health, §52
    #  security incidents, §86/§116 timeline, §88 known patterns).
    #
    # WHY the seven correlation ids are real columns and not one JSON blob: §7
    # asks for a join across them, and SQLite cannot join into JSON. This table
    # is the join that did not exist.
    """
    CREATE TABLE IF NOT EXISTS incidents (
        incident_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'LOW',
        status TEXT NOT NULL DEFAULT 'DETECTED',
        component TEXT NOT NULL DEFAULT '',
        failure_class TEXT,
        detector TEXT NOT NULL DEFAULT '',
        request_id TEXT,
        correlation_id TEXT,
        task_id TEXT,
        trajectory_id TEXT,
        step_id TEXT,
        tool_call_id TEXT,
        workflow_run_id TEXT,
        parent_incident_id TEXT,
        related_json TEXT NOT NULL DEFAULT '[]',
        occurrence_count INTEGER NOT NULL DEFAULT 1,
        first_seen_ts TEXT NOT NULL,
        last_seen_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL,
        resolved_ts TEXT,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        measurements_json TEXT NOT NULL DEFAULT '[]',
        diagnosis_id TEXT,
        repair_id TEXT,
        repair_attempts INTEGER NOT NULL DEFAULT 0,
        escalated INTEGER NOT NULL DEFAULT 0,
        escalation_reason TEXT NOT NULL DEFAULT '',
        notes_json TEXT NOT NULL DEFAULT '[]'
    );
    -- §13/§15: "20 errors from the same event are not 20 incidents" is enforced
    -- by the database, not by application discipline. One OPEN incident per
    -- fingerprint; a recurrence after resolution is a NEW incident on purpose,
    -- because that is a regression and §103 needs to see it as one.
    CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_open_fp
        ON incidents(fingerprint) WHERE resolved_ts IS NULL;
    CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, severity);
    CREATE INDEX IF NOT EXISTS idx_incidents_seen ON incidents(last_seen_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_incidents_source ON incidents(source);
    CREATE INDEX IF NOT EXISTS idx_incidents_component ON incidents(component);
    CREATE INDEX IF NOT EXISTS idx_incidents_task ON incidents(task_id);
    CREATE INDEX IF NOT EXISTS idx_incidents_traj ON incidents(trajectory_id);
    CREATE INDEX IF NOT EXISTS idx_incidents_corr ON incidents(correlation_id);
    CREATE INDEX IF NOT EXISTS idx_incidents_request ON incidents(request_id);
    CREATE INDEX IF NOT EXISTS idx_incidents_parent ON incidents(parent_incident_id);

    CREATE TABLE IF NOT EXISTS security_incidents (
        security_incident_id TEXT PRIMARY KEY,
        incident_id TEXT,
        kind TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'HIGH',
        status TEXT NOT NULL DEFAULT 'DETECTED',
        source_component TEXT NOT NULL DEFAULT '',
        detector TEXT NOT NULL DEFAULT '',
        request_id TEXT,
        correlation_id TEXT,
        task_id TEXT,
        trajectory_id TEXT,
        step_id TEXT,
        tool_call_id TEXT,
        workflow_run_id TEXT,
        containment TEXT NOT NULL DEFAULT 'NONE',
        contained_ts TEXT,
        evidence_preserved INTEGER NOT NULL DEFAULT 0,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        indicator_summary TEXT NOT NULL DEFAULT '',
        human_notified INTEGER NOT NULL DEFAULT 0,
        first_seen_ts TEXT NOT NULL,
        last_seen_ts TEXT NOT NULL,
        resolved_ts TEXT,
        notes_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX IF NOT EXISTS idx_secinc_status ON security_incidents(status, severity);
    CREATE INDEX IF NOT EXISTS idx_secinc_kind ON security_incidents(kind);
    CREATE INDEX IF NOT EXISTS idx_secinc_incident ON security_incidents(incident_id);
    CREATE INDEX IF NOT EXISTS idx_secinc_seen ON security_incidents(last_seen_ts DESC);

    CREATE TABLE IF NOT EXISTS incident_diagnoses (
        diagnosis_id TEXT PRIMARY KEY,
        incident_id TEXT NOT NULL,
        method TEXT NOT NULL DEFAULT '',
        passes_json TEXT NOT NULL DEFAULT '[]',
        candidates_json TEXT NOT NULL DEFAULT '[]',
        inconclusive_reason TEXT NOT NULL DEFAULT '',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_dx_incident ON incident_diagnoses(incident_id, created_ts DESC);

    CREATE TABLE IF NOT EXISTS repair_hypotheses (
        repair_id TEXT PRIMARY KEY,
        incident_id TEXT NOT NULL,
        diagnosis_id TEXT,
        title TEXT NOT NULL,
        problem_statement TEXT NOT NULL DEFAULT '',
        proposed_change TEXT NOT NULL DEFAULT '',
        repair_type TEXT NOT NULL,
        affected_component TEXT NOT NULL DEFAULT '',
        target_paths_json TEXT NOT NULL DEFAULT '[]',
        expected_effect TEXT NOT NULL DEFAULT '',
        risk TEXT NOT NULL DEFAULT 'MEDIUM',
        evidence_json TEXT NOT NULL DEFAULT '[]',
        verification_plan TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'PROPOSED',
        gate_decision_id TEXT,
        branch TEXT,
        repair_chain_id TEXT NOT NULL,
        depth INTEGER NOT NULL DEFAULT 0,
        parent_incident_id TEXT,
        attempt INTEGER NOT NULL DEFAULT 1,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_repair_incident ON repair_hypotheses(incident_id, created_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_repair_status ON repair_hypotheses(status);
    CREATE INDEX IF NOT EXISTS idx_repair_chain ON repair_hypotheses(repair_chain_id);

    -- §86: recorded whether or not it allowed anything. A DENY is as auditable
    -- as an ALLOW, mirroring the safety engine's AUDIT-before-branch order.
    CREATE TABLE IF NOT EXISTS repair_gate_decisions (
        decision_id TEXT PRIMARY KEY,
        repair_id TEXT NOT NULL,
        verdict TEXT NOT NULL,
        reasons_json TEXT NOT NULL DEFAULT '[]',
        blocked_paths_json TEXT NOT NULL DEFAULT '[]',
        sensitive_areas_json TEXT NOT NULL DEFAULT '[]',
        autonomy_level INTEGER NOT NULL DEFAULT 1,
        severity TEXT NOT NULL DEFAULT 'LOW',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_gate_repair ON repair_gate_decisions(repair_id, created_ts DESC);

    -- §86/§116: append-only. There is deliberately no UPDATE or DELETE path in
    -- the store for this table — "nothing is silently changed" means the
    -- timeline itself cannot be rewritten.
    CREATE TABLE IF NOT EXISTS incident_timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT 'system',
        kind TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        from_status TEXT,
        to_status TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_timeline_incident ON incident_timeline(incident_id, id);

    -- §12: baselines are durable rows, not in-process state. A rolling baseline
    -- that resets on restart is not a baseline.
    CREATE TABLE IF NOT EXISTS engineering_baselines (
        scope TEXT NOT NULL,
        key TEXT NOT NULL,
        metric TEXT NOT NULL,
        n INTEGER NOT NULL DEFAULT 0,
        mean REAL NOT NULL DEFAULT 0,
        median REAL,
        stdev REAL,
        p50 REAL,
        p90 REAL,
        p99 REAL,
        mad REAL,
        window_start_ts TEXT NOT NULL DEFAULT '',
        window_end_ts TEXT NOT NULL DEFAULT '',
        provenance TEXT NOT NULL DEFAULT 'MEASURED',
        updated_ts TEXT NOT NULL,
        PRIMARY KEY (scope, key, metric)
    );

    -- §43: heartbeat, last success, last failure, restart count, queue lag —
    -- none of which the runtime worker registry tracked.
    CREATE TABLE IF NOT EXISTS worker_health (
        worker TEXT PRIMARY KEY,
        last_heartbeat_ts TEXT,
        last_success_ts TEXT,
        last_failure_ts TEXT,
        last_failure_detail TEXT NOT NULL DEFAULT '',
        restart_count INTEGER NOT NULL DEFAULT 0,
        queue_lag INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'UNKNOWN',
        updated_ts TEXT NOT NULL
    );

    -- §40: frontend errors ingested from the browser. Carries a status/code/
    -- request id and a TRUNCATED detail — never page state, never a payload.
    CREATE TABLE IF NOT EXISTS frontend_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL,
        kind TEXT NOT NULL,
        route TEXT NOT NULL DEFAULT '',
        status INTEGER,
        code TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        request_id TEXT,
        app_version TEXT NOT NULL DEFAULT '',
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_fe_errors_fp ON frontend_errors(fingerprint, created_ts DESC);

    -- §88: a pattern library. `verified` starts 0 — §89: knowledge derived from
    -- a past root cause is a hint, and still has to be re-verified.
    CREATE TABLE IF NOT EXISTS known_failure_patterns (
        pattern_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        component TEXT NOT NULL DEFAULT '',
        typical_cause TEXT NOT NULL DEFAULT '',
        known_repair TEXT NOT NULL DEFAULT '',
        repair_type TEXT NOT NULL DEFAULT '',
        occurrences INTEGER NOT NULL DEFAULT 1,
        successful_repairs INTEGER NOT NULL DEFAULT 0,
        failed_repairs INTEGER NOT NULL DEFAULT 0,
        verified INTEGER NOT NULL DEFAULT 0,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_kfp_fingerprint ON known_failure_patterns(fingerprint);
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
        """Apply pending migrations, recording progress after each one.

        The version used to be written ONCE after the whole loop. Because
        `executescript` implicitly commits, a failure partway through left the
        earlier migrations' DDL durably applied while `schema_version` still said
        0 — so the next boot replayed them from the start, and the many
        `ALTER TABLE ... ADD COLUMN` steps in this list are not idempotent. The
        database then failed to open on every subsequent boot with no way back
        short of deleting it. Writing the version per step makes a failed
        migration resumable: fix the script, restart, continue from where it
        stopped.
        """
        assert self._conn is not None
        cur = await self._conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        current = int(row["version"]) if row else 0
        if row is None:
            # Seed at the CURRENT version, not the target: the row must exist so
            # the per-step UPDATE below has something to write, but claiming the
            # target before running anything is the bug described above.
            await self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (current,))
            await self._conn.commit()
        for i, script in enumerate(_MIGRATIONS[current:], start=current + 1):
            await self._conn.executescript(script)
            await self._conn.execute("UPDATE schema_version SET version=?", (i,))
            await self._conn.commit()
            _log.info("db.migrate", event_type="db", to_version=i)

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
        """Verify the database actually answers a query.

        This used to be `return self._conn is not None`, which reports healthy for
        every failure that does not drop the connection object: a locked file, a
        corrupt page, a disk that has gone read-only, a WAL that cannot be
        checkpointed. `SELECT 1` is the cheapest statement that proves the
        connection is usable, which is what every caller was already assuming.
        """
        if self._conn is None:
            return False
        try:
            cur = await self._conn.execute("SELECT 1")
            row = await cur.fetchone()
            return row is not None
        except Exception:
            # A health check must never raise — its whole job is to answer the
            # question "is this usable?" and an exception IS the answer "no".
            return False
