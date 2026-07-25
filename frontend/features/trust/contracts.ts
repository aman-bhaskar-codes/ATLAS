import { z } from 'zod';

export const SafeErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  retryable: z.boolean(),
});

export const TaskViewSchema = z.object({
  id: z.string(),
  correlation_id: z.string(),
  request: z.string(),
  state: z.string(),
  answer: z.string().nullable(),
  error: SafeErrorSchema.nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  duration_ms: z.number().nullable(),
  steps_taken: z.number(),
  approval_count: z.number(),
  artifact_count: z.number(),
  memory_write_count: z.number(),
  retryability: z.enum(['safe', 'unsafe', 'unknown']),
});
export type TaskView = z.infer<typeof TaskViewSchema>;

export const TaskPageSchema = z.object({
  items: z.array(TaskViewSchema),
  next_cursor: z.string().nullable(),
});
export type TaskPage = z.infer<typeof TaskPageSchema>;

export const ApprovalViewSchema = z.object({
  id: z.string(),
  task_id: z.string().nullable(),
  execution_id: z.string().nullable(),
  capability: z.string(),
  operation: z.string(),
  tier: z.number(),
  prompt: z.string(),
  exact_preview: z.string(),
  warnings: z.array(z.string()),
  policy_version: z.string(),
  created_at: z.string(),
  expires_at: z.string(),
  status: z.enum(['pending', 'approved', 'denied', 'expired']),
  decision_source: z.enum(['dashboard', 'telegram', 'cli']).nullable(),
});
export type ApprovalView = z.infer<typeof ApprovalViewSchema>;

export const MemoryFactViewSchema = z.object({
  id: z.string(),
  version: z.number(),
  text: z.string(),
  kind: z.string(),
  confidence: z.number(),
  salience: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
  superseded_by: z.string().nullable(),
  provenance_count: z.number(),
  status: z.enum(['active', 'superseded', 'deleted']),
});
export type MemoryFactView = z.infer<typeof MemoryFactViewSchema>;

export const ProvenanceViewSchema = z.object({
  source_type: z.enum(['episode', 'task', 'user_edit', 'external']),
  source_id: z.string(),
  summary: z.string(),
  captured_at: z.string(),
  provider: z.string().nullable(),
  confidence: z.number().nullable(),
});
export type ProvenanceView = z.infer<typeof ProvenanceViewSchema>;

export const AuditEventViewSchema = z.object({
  id: z.string(),
  ts: z.string(),
  actor: z.string(),
  action: z.string(),
  tool: z.string().nullable(),
  capability: z.string().nullable(),
  tier: z.number().nullable(),
  decision: z.string().nullable(),
  outcome: z.string().nullable(),
  task_id: z.string().nullable(),
  correlation_id: z.string().nullable(),
  execution_id: z.string().nullable(),
  redaction: z.enum(['none', 'partial', 'full']),
  safe_payload_summary: z.string().nullable(),
});
export type AuditEventView = z.infer<typeof AuditEventViewSchema>;

export const AuditPageSchema = z.object({
  items: z.array(AuditEventViewSchema),
  next_cursor: z.string().nullable(),
});
export type AuditPage = z.infer<typeof AuditPageSchema>;

export const MemoryMutationReceiptSchema = z.object({
  accepted: z.boolean(),
  fact: MemoryFactViewSchema,
  request_id: z.string(),
  idempotency_key: z.string(),
});
export type MemoryMutationReceipt = z.infer<typeof MemoryMutationReceiptSchema>;
