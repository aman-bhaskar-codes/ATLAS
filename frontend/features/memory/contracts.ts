/**
 * Zod contracts for all memory layer data shapes.
 * Mirrors the Pydantic models in routes_memory.py.
 */

import { z } from 'zod';

// ---------------------------------------------------------------------------
// Episodes
// ---------------------------------------------------------------------------

export const EpisodeSchema = z.object({
  id: z.number().nullable(),
  correlation_id: z.string(),
  task_id: z.string().nullable(),
  ts: z.string(),
  kind: z.string(),
  role: z.string(),
  content: z.string(),
  tool: z.string().nullable(),
  outcome: z.string().nullable(),
  salience: z.number(),
  tokens: z.number(),
  embedding_id: z.string().nullable(),
});
export type Episode = z.infer<typeof EpisodeSchema>;

// ---------------------------------------------------------------------------
// Facts
// ---------------------------------------------------------------------------

export const FactSchema = z.object({
  id: z.string(),
  version: z.number(),
  text: z.string(),
  kind: z.string(),
  confidence: z.number(),
  salience: z.number(),
  created_ts: z.string(),
  updated_ts: z.string(),
  superseded_by: z.string().nullable(),
});
export type Fact = z.infer<typeof FactSchema>;

// ---------------------------------------------------------------------------
// Knowledge
// ---------------------------------------------------------------------------

export const KnowledgeDocSchema = z.object({
  id: z.string(),
  title: z.string(),
  source_path: z.string(),
  source_type: z.string(),
  chunk_count: z.number(),
  indexed: z.boolean(),
  created_ts: z.string(),
  score: z.number().nullable(),
});
export type KnowledgeDoc = z.infer<typeof KnowledgeDocSchema>;

export const KnowledgeChunkSchema = z.object({
  chunk_id: z.string(),
  document_id: z.string(),
  document_title: z.string(),
  source_type: z.string(),
  content: z.string(),
  chunk_index: z.number(),
  total_chunks: z.number(),
  score: z.number(),
});
export type KnowledgeChunk = z.infer<typeof KnowledgeChunkSchema>;

// ---------------------------------------------------------------------------
// Memory stats
// ---------------------------------------------------------------------------

export const MemoryStatsSchema = z.object({
  episode_count: z.number(),
  fact_count: z.number(),
  document_count: z.number(),
  chunk_count: z.number(),
  preference_count: z.number(),
  active_ws_clients: z.number(),
});
export type MemoryStats = z.infer<typeof MemoryStatsSchema>;

// ---------------------------------------------------------------------------
// Preferences
// ---------------------------------------------------------------------------

/**
 * The user-model layer, returned as a flat mapping. Values are `unknown` because
 * the backend stores whatever the learner wrote; the previous `as
 * Record<string, string>` was a cast rather than a check, so a numeric or nested
 * value passed straight through untyped. The UI stringifies for display anyway.
 */
export const PreferencesSchema = z.record(z.string(), z.unknown());
export type Preferences = z.infer<typeof PreferencesSchema>;

// ---------------------------------------------------------------------------
// Live WebSocket MemoryEvent (mirrors orchestration/events.py::MemoryEvent)
// ---------------------------------------------------------------------------

export const MemoryEventKindSchema = z.enum([
  'memory.stored',
  'memory.retrieved',
  'memory.consolidated',
  'memory.pruned',
  'memory.user_model_updated',
  'memory.fact_added',
  'memory.knowledge_indexed',
] as const);
export type MemoryEventKind = z.infer<typeof MemoryEventKindSchema>;

export const MemoryTypeSchema = z.enum([
  'episodic',
  'semantic',
  'working',
  'user_model',
  'knowledge',
] as const);
export type MemoryType = z.infer<typeof MemoryTypeSchema>;

export const MemoryEventSchema = z.object({
  correlation_id: z.string(),
  task_id: z.string(),
  kind: z.string(),              // loose — backend may emit new kinds
  memory_type: z.string(),
  count: z.number(),
  query: z.string().nullable(),
  items: z.array(z.string()),
  metadata: z.record(z.string(), z.unknown()).optional(),
  _topic: z.literal('memory').optional(),
});
export type MemoryEvent = z.infer<typeof MemoryEventSchema>;

/** Initial snapshot sent on WebSocket connect. */
export const MemorySnapshotSchema = z.object({
  type: z.literal('snapshot'),
  _topic: z.literal('memory'),
  episode_count: z.number(),
  fact_count: z.number(),
  document_count: z.number(),
  preference_count: z.number(),
  recent_episode_kinds: z.array(z.string()),
  recent_fact_texts: z.array(z.string()),
});
export type MemorySnapshot = z.infer<typeof MemorySnapshotSchema>;

/** Union of all messages from /ws/memory/live */
export type MemoryWireMessage =
  | ({ type: 'snapshot' } & MemorySnapshot)
  | ({ type?: undefined } & MemoryEvent)
  | { type: 'ping'; timestamp?: number }
  | { type: 'replay_complete' };
