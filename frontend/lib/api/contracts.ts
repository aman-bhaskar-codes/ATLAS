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

export const TaskSchema = z.object({
  schema_version: z.number().int().optional().default(1),
  id: z.string(),
  correlation_id: z.string(),
  source: z.enum(["cli", "file", "whatsapp", "api", "scheduler", "system"]),
  request: z.string(),
  state: z.enum(TASK_STATES),
  ok: z.boolean().nullable(),
  answer: z.string().nullable(),
  error: z.string().nullable(),
  steps_taken: z.number().int().nonnegative(),
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),
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

// ─── Derived types ────────────────────────────────────────────────────────────
export type RuntimeStatus = z.infer<typeof RuntimeStatusSchema>;
export type RuntimeHealth = z.infer<typeof RuntimeHealthSchema>;
export type Task = z.infer<typeof TaskSchema>;
export type TaskEvent = z.infer<typeof TaskEventSchema>;
export type Approval = z.infer<typeof ApprovalSchema>;
export type Capability = z.infer<typeof CapabilitySchema>;
export type CancelTaskResponse = z.infer<typeof CancelTaskResponseSchema>;

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
