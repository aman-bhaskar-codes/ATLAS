// frontend/lib/api/contracts.ts
// These Zod schemas mirror the backend Pydantic models exactly.
// When the backend schema_version changes, update here and bump the version check.

import { z } from "zod";

export const RuntimeStatusSchema = z.object({
  schema_version: z.number().int().optional().default(1),
  state: z.enum(["starting", "ready", "degraded", "stopping", "stopped"]),
  version: z.string(),
  environment: z.string(),
  kill_switch_active: z.boolean(),
  active_task_count: z.number().int().nonnegative(),
  pending_approval_count: z.number().int().nonnegative(),
  last_audit_at: z.string().datetime().nullable(),
});

export const HealthCheckSchema = z.object({
  name: z.string(),
  status: z.enum(["pass", "warn", "fail"]),
  detail: z.string(),
  checked_at: z.string().datetime(),
});

export const RuntimeHealthSchema = z.object({
  schema_version: z.number().int().optional().default(1),
  overall: z.enum(["healthy", "degraded", "unavailable"]),
  checks: z.array(HealthCheckSchema),
});

export const TASK_STATES = [
  "created", "ready", "building_context", "planning", "reasoning",
  "waiting_tool", "executing", "observing", "completed", "failed", "cancelled",
] as const;
export type TaskState = typeof TASK_STATES[number];

export const TERMINAL_STATES = new Set<TaskState>(["completed", "failed", "cancelled"]);
export const ACTIVE_STATES = new Set<TaskState>(
  TASK_STATES.filter((s) => !TERMINAL_STATES.has(s))
);

// SafeError matches backend schemas_trust.py SafeError
export const SafeErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  retryable: z.boolean().default(false),
});

export const TaskSchema = z.object({
  schema_version: z.number().int().optional().default(1),
  id: z.string(),
  correlation_id: z.string(),
  source: z.enum(["cli", "file", "whatsapp", "api", "scheduler", "system"]),
  request: z.string(),
  state: z.enum(TASK_STATES),
  ok: z.boolean().nullable(),
  answer: z.string().nullable(),
  error: z.union([z.string(), SafeErrorSchema]).nullable(),
  steps_taken: z.number().int().nonnegative(),
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),
  // Fields from TaskView that backend sends but were missing here
  duration_ms: z.number().int().nullable().optional().default(null),
  approval_count: z.number().int().nonnegative().optional().default(0),
  artifact_count: z.number().int().nonnegative().optional().default(0),
  memory_write_count: z.number().int().nonnegative().optional().default(0),
  retryability: z.enum(["safe", "unsafe", "unknown"]).optional().default("unknown"),
});

export const TaskEventSchema = z.object({
  schema_version: z.number().int().optional().default(1),
  event_id: z.string(),
  event_type: z.string(),
  ts: z.string().datetime(),
  task_id: z.string(),
  correlation_id: z.string(),
  execution_id: z.string().nullable(),
  // sequence: used for gap detection per Phase Two spec
  sequence: z.number().int().nonnegative(),
  state: z.string(),
  summary: z.string(),
  capability: z.string().nullable(),
  operation: z.string().nullable(),
  provider: z.string().nullable(),
  tier: z.number().int().nullable().optional(),
  requires_approval: z.boolean().default(false),
  safe_metadata: z.record(z.string(), z.string()).default({}),
});

export const ApprovalSchema = z.object({
  schema_version: z.number().int().optional().default(1),
  id: z.string(),
  task_id: z.string().nullable(),
  correlation_id: z.string(),
  execution_id: z.string().nullable(),
  capability: z.string(),
  operation: z.string(),
  tier: z.number().int(),
  prompt: z.string(),
  preview: z.string(),
  warnings: z.array(z.string()),
  expires_at: z.string().datetime(),
  status: z.enum(["pending", "approved", "denied", "expired"]),
});

export const CapabilitySchema = z.object({
  schema_version: z.number().int().optional().default(1),
  name: z.string(),
  state: z.enum(["ready", "degraded", "unavailable", "planned"]),
  operations: z.array(z.string()),
  providers: z.number().int().nonnegative(),
  healthy_providers: z.number().int().nonnegative(),
  requires_auth: z.boolean(),
});

export const CancelTaskResponseSchema = z.object({
  schema_version: z.number().int().optional().default(1),
  task_id: z.string(),
  accepted: z.boolean(),
  state: z.string(),
  message: z.string(),
});

export const CreateTaskSchema = z.object({
  request: z.string().min(1).max(20_000),
  source: z.literal("api"),
  idempotency_key: z.string().min(16),
});

export const ApprovalDecisionSchema = z.object({
  decision: z.enum(["approve", "deny"]),
  idempotency_key: z.string().min(16),
});

// --- Automations ---
export const TriggerConfigSchema = z.object({
  event_type: z.string(),
  filters: z.record(z.string(), z.any()).default({}),
});

export const ActionConfigSchema = z.object({
  type: z.string(),
  request_template: z.string(),
});

export const AutomationSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  enabled: z.boolean().default(true),
  trigger_config: TriggerConfigSchema,
  action_config: ActionConfigSchema,
  created_ts: z.string().datetime().optional(),
  updated_ts: z.string().datetime().optional(),
});

// ─── Derived types ────────────────────────────────────────────────────────────
export type RuntimeStatus = z.infer<typeof RuntimeStatusSchema>;
export type RuntimeHealth = z.infer<typeof RuntimeHealthSchema>;
export type Task = z.infer<typeof TaskSchema>;
export type TaskEvent = z.infer<typeof TaskEventSchema>;
export type Approval = z.infer<typeof ApprovalSchema>;
export type Capability = z.infer<typeof CapabilitySchema>;
export type CancelTaskResponse = z.infer<typeof CancelTaskResponseSchema>;
export type Automation = z.infer<typeof AutomationSchema>;

// ─── Domain selectors (single authoritative source — update here if backend vocab changes) ──
export type ConnectionState = "connected" | "reconnecting" | "stale" | "offline";

export function canCancel(task: Task): boolean {
  return !TERMINAL_STATES.has(task.state);
}

export function isTerminal(state: TaskState | string): boolean {
  return TERMINAL_STATES.has(state as TaskState);
}

export function pendingApprovalEvent(events: TaskEvent[]): TaskEvent | null {
  return (
    [...events].reverse().find(
      (e) => e.requires_approval && !isTerminal(e.state)
    ) ?? null
  );
}

export function currentEvent(events: TaskEvent[]): TaskEvent | null {
  return events.length > 0 ? events[events.length - 1] : null;
}

export function elapsedSeconds(task: Task): number {
  const created = new Date(task.created_at).getTime();
  const updated = new Date(task.updated_at).getTime();
  return Math.floor((updated - created) / 1000);
}

// ─── Learning & Ops contracts (Batch 6) ────────────────────────────────────────
// These mirror the used subset of the backend response models (routes_learning.py,
// routes_ops.py, routes_trajectory.py). Zod strips unknown keys by default, so a
// backend that returns a superset (e.g. ExperienceOut has more fields than we read)
// still validates — we only assert the fields the UI depends on.

export const SkillSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  version: z.number().int(),
  status: z.string(),
  success_rate: z.number(),
  usage_count: z.number().int(),
  confidence: z.number(),
  preferred_tools: z.array(z.string()),
  known_failure_modes: z.array(z.string()),
  procedure_steps: z.array(z.string()),
  updated_ts: z.string(),
});

export const StrategySchema = z.object({
  id: z.string(),
  task_type_pattern: z.string(),
  approach: z.string(),
  model_preference: z.string().nullable(),
  tool_preference: z.array(z.string()),
  status: z.string(),
  success_rate: z.number(),
  evidence_count: z.number().int(),
  eval_score: z.number().nullable(),
  updated_ts: z.string(),
});

export const WorldEntitySchema = z.object({
  entity_type: z.string(),
  entity_id: z.string(),
  attributes: z.record(z.string(), z.unknown()),
  updated_ts: z.string(),
});

export const EvalResultSchema = z.object({
  golden_id: z.string(),
  run_id: z.string(),
  evaluator: z.string(),
  passed: z.boolean(),
  score: z.number(),
  created_ts: z.string(),
});

export const LearningAnalyticsSchema = z.object({
  trajectory_success_rate: z.number().nullable(),
  total_trajectories: z.number().int(),
  total_experiences: z.number().int(),
  active_skills: z.number().int(),
  candidate_skills: z.number().int(),
  active_strategies: z.number().int(),
  recent_verification_pass_rate: z.number().nullable(),
  generated_at: z.string(),
});

export const OpsToolSchema = z.object({
  name: z.string(),
  operations: z.array(z.string()),
  description: z.string(),
  estimated_latency_ms: z.number().nullable(),
  estimated_cost_usd: z.number().nullable(),
  idempotent: z.boolean().nullable(),
  side_effects: z.boolean().nullable(),
  supports_rollback: z.boolean().nullable(),
  health: z.number(),
  latency_ewma_ms: z.number(),
});

export const OpsModelSchema = z.object({
  id: z.string(),
  provider: z.string(),
  context_length: z.number().int(),
  usd_per_1m_input: z.number(),
  usd_per_1m_output: z.number(),
  latency_estimate_ms: z.number().int(),
  capabilities: z.array(z.string()),
  supports_streaming: z.boolean(),
  supports_tool_calling: z.boolean(),
  quality_score: z.number(),
  enabled: z.boolean(),
  cost_class: z.string().optional(),
});

export const OpsProviderSchema = z.object({
  name: z.string(),
  is_local: z.boolean(),
  available: z.boolean(),
});

export const OpsScheduleSchema = z.object({
  id: z.string(),
  name: z.string(),
  cron: z.string(),
  enabled: z.boolean(),
});

export const ExperienceSchema = z.object({
  id: z.string(),
  category: z.string(),
  lesson_text: z.string(),
  applicability_context: z.string(),
  confidence: z.number(),
  reuse_count: z.number().int(),
  success_rate: z.number(),
  extracted_ts: z.string(),
});

// ─── Providers / cost / profile contracts (Zero-Cost-First) ────────────────────
export const ProviderHealthSchema = z.object({
  name: z.string(),
  healthy: z.boolean(),
  avg_latency_ms: z.number(),
  is_local: z.boolean(),
  quota_pct: z.number().optional(),
  quota_requests_remaining: z.number().optional(),
  quota_tokens_remaining: z.number().optional(),
});

export const ProfileInfoSchema = z.object({
  profile: z.string(),
  cost_policy: z.string(),
  network_policy: z.string(),
  allow_cloud: z.boolean(),
  enable_quota_governor: z.boolean(),
  daily_usd: z.number(),
  allowed_cost_classes: z.array(z.string()),
});

export const QuotaSnapshotSchema = z.object({
  enabled: z.boolean(),
  providers: z.record(
    z.string(),
    z.object({
      requests_remaining: z.number(),
      tokens_remaining: z.number(),
      requests_used: z.number(),
      tokens_used: z.number(),
      daily_requests_limit: z.number(),
      daily_tokens_limit: z.number(),
      pct_remaining: z.number(),
    }),
  ),
});

export const CapabilityMatrixSchema = z.object({
  matrix: z.record(
    z.string(),
    z.object({
      local: z.array(z.string()),
      free_quota: z.array(z.string()),
      paid: z.array(z.string()),
    }),
  ),
  total_models: z.number().int(),
});

export type Skill = z.infer<typeof SkillSchema>;
export type Strategy = z.infer<typeof StrategySchema>;
export type WorldEntity = z.infer<typeof WorldEntitySchema>;
export type EvalResult = z.infer<typeof EvalResultSchema>;
export type LearningAnalytics = z.infer<typeof LearningAnalyticsSchema>;
export type OpsTool = z.infer<typeof OpsToolSchema>;
export type OpsModel = z.infer<typeof OpsModelSchema>;
export type OpsProvider = z.infer<typeof OpsProviderSchema>;
export type OpsSchedule = z.infer<typeof OpsScheduleSchema>;
export type Experience = z.infer<typeof ExperienceSchema>;
export type ProviderHealth = z.infer<typeof ProviderHealthSchema>;
export type ProfileInfo = z.infer<typeof ProfileInfoSchema>;
export type QuotaSnapshot = z.infer<typeof QuotaSnapshotSchema>;
export type CapabilityMatrix = z.infer<typeof CapabilityMatrixSchema>;

/**
 * One row from `/events/search`. The per-event payload is an untyped bus event
 * (see `AtlasEvent` in `lib/websocket`) — the backend serialises the raw event
 * and injects `_topic`/`_timestamp` metadata. We model the fields the legacy
 * event cards read and keep the rest with `looseObject` so nothing is dropped;
 * the two card-required fields default so the inferred type satisfies
 * `AtlasEvent`. Validating the envelope (below) is the §70 win — an error
 * envelope or a shape drift now surfaces as a typed contract error, not a crash
 * deep in the render.
 */
export const EventSearchEventSchema = z.looseObject({
  correlation_id: z.string().default(""),
  task_id: z.string().optional(),
  kind: z.string().default(""),
  state: z.string().optional(),
  metadata: z.record(z.string(), z.unknown()).optional(),
  _timestamp: z.string().optional(),
  _topic: z.string().optional(),
  historical: z.boolean().optional(),
});

export const EventSearchResultSchema = z.object({
  events: z.array(EventSearchEventSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

export type EventSearchEvent = z.infer<typeof EventSearchEventSchema>;
export type EventSearchResult = z.infer<typeof EventSearchResultSchema>;
