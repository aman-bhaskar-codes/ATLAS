// frontend/features/agent-runs/contracts.ts
//
// Zod contracts for the agent-run surface (`/api/v1/agent/*`) and its SSE trace.
// These mirror the backend source of truth exactly (routes_agent.py +
// orchestration/agent_engine/records.py + event_reader.py) — no invented fields,
// no `any`. The workbench Agent Panel drives a run bound to THIS workspace_id and
// folds the durable event trace into operational state; nothing is fabricated.

import { z } from "zod";

/* ── engine trace records (records.py) ─────────────────────────────────────── */

/** One governed tool invocation the engine recorded. */
export const ToolCallRecordSchema = z.object({
  call_id: z.string(),
  tool: z.string(),
  operation: z.string().nullable().default(null),
  args: z.record(z.string(), z.unknown()).default({}),
  ok: z.boolean().default(false),
  output: z.unknown().default(null),
  error: z.string().nullable().default(null),
  latency_ms: z.number().int().default(0),
});
export type ToolCallRecord = z.infer<typeof ToolCallRecordSchema>;

/** One loop iteration: a model turn plus any tools it drove. */
export const AgentStepSchema = z.object({
  index: z.number().int(),
  assistant_text: z.string().default(""),
  tool_calls: z.array(ToolCallRecordSchema).default([]),
});
export type AgentStep = z.infer<typeof AgentStepSchema>;

export const StopReasonSchema = z.enum(["finished", "limit", "error"]);

/** The terminal outcome of an agent-engine run. */
export const AgentEngineResultSchema = z.object({
  ok: z.boolean(),
  stop_reason: StopReasonSchema,
  final_text: z.string().default(""),
  steps: z.array(AgentStepSchema).default([]),
  model_calls: z.number().int().default(0),
  tool_calls: z.number().int().default(0),
  tokens_used: z.number().int().default(0),
  error: z.string().nullable().default(null),
});
export type AgentEngineResult = z.infer<typeof AgentEngineResultSchema>;

/* ── run records (routes_agent.py) ─────────────────────────────────────────── */

/** A persisted run's lifecycle state; RUNNING is the only non-terminal one. */
export const RunStatusSchema = z.enum(["running", "finished", "limit", "error"]);
export type RunStatus = z.infer<typeof RunStatusSchema>;

export const TERMINAL_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  "finished",
  "limit",
  "error",
]);

/** A full run: metadata + the complete engine trace (`result`). */
export const RunResponseSchema = z.object({
  run_id: z.string(),
  status: RunStatusSchema,
  request: z.string(),
  correlation_id: z.string(),
  workspace_id: z.string().nullable().default(null),
  session_id: z.string().nullable().default(null),
  parent_run_id: z.string().nullable().default(null),
  tool_names: z.array(z.string()).default([]),
  created_ts: z.string(),
  updated_ts: z.string(),
  result: AgentEngineResultSchema.nullable().default(null),
});
export type RunResponse = z.infer<typeof RunResponseSchema>;

/** A compact run row for lists — no step trace. */
export const RunSummarySchema = z.object({
  run_id: z.string(),
  status: RunStatusSchema,
  request: z.string(),
  workspace_id: z.string().nullable().default(null),
  session_id: z.string().nullable().default(null),
  parent_run_id: z.string().nullable().default(null),
  created_ts: z.string(),
  updated_ts: z.string(),
  model_calls: z.number().int().default(0),
  tool_calls: z.number().int().default(0),
  final_text: z.string().default(""),
});
export type RunSummary = z.infer<typeof RunSummarySchema>;

export const RunListSchema = z.object({ runs: z.array(RunSummarySchema).default([]) });

/* ── SSE trace events (event_reader.py → AgentRunEvent) ─────────────────────── */

// A single `agent_event` frame's `data`. `payload` is the full typed bus event
// (OrchestratorEvent | ToolEvent) as a dict; its `kind` discriminates. We keep
// payload permissive (unknown values) and read the fields the reducer needs via a
// narrow accessor rather than a brittle exhaustive union — the backend may add
// metadata keys and the trace must survive that.
export const AgentRunEventSchema = z.object({
  sequence: z.number().int(),
  type: z.string(), // bus topic: "orchestrator" | "tool"
  correlation_id: z.string().default(""),
  causation_id: z.string().nullable().default(null),
  occurred_at: z.string().default(""),
  payload: z.record(z.string(), z.unknown()).default({}),
});
export type AgentRunEvent = z.infer<typeof AgentRunEventSchema>;

/* ── request bodies ────────────────────────────────────────────────────────── */

export interface StartRunBody {
  request: string;
  workspace_id?: string | null;
  session_id?: string | null;
  max_tools?: number | null;
  background?: boolean;
}
