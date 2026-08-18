// frontend/lib/api/client.ts
import { z } from "zod";
import {
  ApprovalSchema, CapabilitySchema, CancelTaskResponseSchema,
  RuntimeHealthSchema, RuntimeStatusSchema, TaskEventSchema, TaskSchema,
} from "./contracts";

const API_BASE =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

async function fetchWithTimeout(url: string, init?: RequestInit, timeoutMs = 8000): Promise<Response> {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    return response;
  } catch (error: unknown) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("ATLAS Backend Timeout: Request took longer than 8 seconds.");
    }
    throw error;
  } finally {
    clearTimeout(id);
  }
}

async function request<T extends z.ZodTypeAny>(
  path: string,
  schema: T,
  init?: RequestInit,
): Promise<z.infer<T>> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`ATLAS API ${response.status}: ${detail.slice(0, 500)}`);
  }
  return schema.parse(await response.json());
}

async function requestJSON(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`ATLAS API ${response.status}: ${detail.slice(0, 500)}`);
  }
  return response.json();
}

export const atlasApi = {
  runtimeStatus: () => request("/runtime/status", RuntimeStatusSchema),
  runtimeHealth: () => request("/runtime/health", RuntimeHealthSchema),

  tasks: () => request("/tasks?limit=20", z.array(TaskSchema)),
  task: (id: string) => request(`/tasks/${encodeURIComponent(id)}`, TaskSchema),

  taskEvents: (id: string, afterSequence?: number) => {
    const qs = afterSequence !== undefined ? `?after_sequence=${afterSequence}` : "";
    return request(`/tasks/${encodeURIComponent(id)}/events${qs}`, z.array(TaskEventSchema));
  },

  approvals: () => request("/approvals/pending", z.array(ApprovalSchema)),
  capabilities: () => request("/capabilities", z.array(CapabilitySchema)),

  createTask: (payload: { request: string; idempotency_key: string; attachments?: { id: string; type: string }[] }) =>
    request("/tasks", TaskSchema, {
      method: "POST",
      body: JSON.stringify({ ...payload, source: "api" }),
    }),

  cancelTask: (taskId: string, payload: { idempotency_key: string; reason?: string }) =>
    request(`/tasks/${encodeURIComponent(taskId)}/cancel`, CancelTaskResponseSchema, {
      method: "POST",
      body: JSON.stringify({ reason: "user_requested", ...payload }),
    }),

  decideApproval: (
    approvalId: string,
    decision: "approve" | "deny",
    idempotency_key: string,
  ) =>
    request(`/approvals/${encodeURIComponent(approvalId)}/decide`, ApprovalSchema, {
      method: "POST",
      body: JSON.stringify({ decision, idempotency_key }),
    }),
};

// --- Trust Center Endpoints (Phase 3) ---
export const trustApi = {
  // Tasks
  listTasks: async (cursor?: string, limit: number = 50) => {
    const params = new URLSearchParams();
    if (cursor) params.append('cursor', cursor);
    params.append('limit', limit.toString());
    return requestJSON(`/tasks?${params}`);
  },
  getTask: async (taskId: string) => {
    return requestJSON(`/tasks/${taskId}`);
  },

  // Approvals
  pendingApprovals: async () => {
    return requestJSON(`/approvals/pending`);
  },
  getApproval: async (approvalId: string) => {
    return requestJSON(`/approvals/${approvalId}`);
  },
  decideApproval: async (approvalId: string, decision: 'approve' | 'deny', idempotencyKey: string, requestId: string) => {
    return requestJSON(`/approvals/${approvalId}/decide`, {
      method: 'POST',
      body: JSON.stringify({ approval_id: approvalId, decision, idempotency_key: idempotencyKey, request_id: requestId }),
    });
  },

  // Memory
  searchMemory: async (query: string, limit: number = 30) => {
    const params = new URLSearchParams({ q: query, limit: limit.toString() });
    return requestJSON(`/memory/search?${params}`);
  },
  getMemoryFact: async (factId: string) => {
    return requestJSON(`/memory/facts/${factId}`);
  },
  correctMemory: async (factId: string, replacementText: string, idempotencyKey: string, requestId: string) => {
    return requestJSON(`/memory/facts/${factId}/correct`, {
      method: 'POST',
      body: JSON.stringify({ fact_id: factId, replacement_text: replacementText, idempotency_key: idempotencyKey, request_id: requestId }),
    });
  },

  // Audit
  getAuditLog: async (cursor?: string, limit: number = 50, filters?: { taskId?: string, correlationId?: string, executionId?: string }) => {
    const params = new URLSearchParams();
    if (cursor) params.append('cursor', cursor);
    params.append('limit', limit.toString());
    if (filters?.taskId) params.append('task_id', filters.taskId);
    if (filters?.correlationId) params.append('correlation_id', filters.correlationId);
    if (filters?.executionId) params.append('execution_id', filters.executionId);
    return requestJSON(`/audit?${params}`);
  }
};

// --- Learning & Ops endpoints (Batch 6) — typed via runtime validation ---
export interface AtlasSkill {
  id: string; name: string; description: string; version: number; status: string;
  success_rate: number; usage_count: number; confidence: number;
  preferred_tools: string[]; known_failure_modes: string[]; procedure_steps: string[];
  updated_ts: string;
}
export interface AtlasStrategy {
  id: string; task_type_pattern: string; approach: string; model_preference: string | null;
  tool_preference: string[]; status: string; success_rate: number; evidence_count: number;
  eval_score: number | null; updated_ts: string;
}
export interface AtlasWorldEntity {
  entity_type: string; entity_id: string; attributes: Record<string, unknown>; updated_ts: string;
}
export interface AtlasEvalResult {
  golden_id: string; run_id: string; evaluator: string; passed: boolean; score: number; created_ts: string;
}
export interface AtlasLearningAnalytics {
  trajectory_success_rate: number | null; total_trajectories: number;
  total_experiences: number; active_skills: number; candidate_skills: number;
  active_strategies: number; recent_verification_pass_rate: number | null; generated_at: string;
}
export interface AtlasTool {
  name: string; operations: string[]; description: string;
  estimated_latency_ms: number | null; estimated_cost_usd: number | null;
  idempotent: boolean | null; side_effects: boolean | null; supports_rollback: boolean | null;
  health: number; latency_ewma_ms: number;
}
export interface AtlasModel {
  id: string; provider: string; context_length: number;
  usd_per_1m_input: number; usd_per_1m_output: number; latency_estimate_ms: number;
  capabilities: string[]; supports_streaming: boolean; supports_tool_calling: boolean;
  quality_score: number; enabled: boolean; cost_class?: string;
}
export interface AtlasProvider { name: string; is_local: boolean; available: boolean }
export interface AtlasSchedule { id: string; name: string; cron: string; enabled: boolean }

export interface AtlasExperience {
  id: string; category: string; lesson_text: string; applicability_context: string;
  confidence: number; reuse_count: number; success_rate: number; extracted_ts: string;
}

export const learningApi = {
  skills: (status?: string) =>
    requestJSON(`/learning/skills${status ? `?status=${status}` : ""}`) as Promise<AtlasSkill[]>,
  disableSkill: (skillId: string) =>
    requestJSON(`/learning/skills/${encodeURIComponent(skillId)}/disable`, { method: "POST" }) as Promise<AtlasSkill>,
  strategies: (activeOnly = false) =>
    requestJSON(`/learning/strategies?active_only=${activeOnly}`) as Promise<AtlasStrategy[]>,
  world: (entityType?: string) =>
    requestJSON(`/learning/world${entityType ? `?entity_type=${encodeURIComponent(entityType)}` : ""}`) as Promise<AtlasWorldEntity[]>,
  evaluations: (limit = 50) =>
    requestJSON(`/learning/evaluation/recent?limit=${limit}`) as Promise<AtlasEvalResult[]>,
  analytics: () =>
    requestJSON(`/learning/analytics`) as Promise<AtlasLearningAnalytics>,
};

export const opsApi = {
  tools: () => requestJSON(`/ops/tools`) as Promise<AtlasTool[]>,
  models: (includeDisabled = false) =>
    requestJSON(`/ops/models?include_disabled=${includeDisabled}`) as Promise<AtlasModel[]>,
  providers: () => requestJSON(`/ops/providers`) as Promise<AtlasProvider[]>,
  schedules: () => requestJSON(`/ops/schedules`) as Promise<AtlasSchedule[]>,
  toggleSchedule: (id: string) =>
    requestJSON(`/ops/schedules/${encodeURIComponent(id)}/toggle`, { method: "POST" }) as Promise<AtlasSchedule>,
};

export const trajectoryApi = {
  experiences: (limit = 50) =>
    requestJSON(`/api/v1/trajectory/experiences?limit=${limit}`) as Promise<AtlasExperience[]>,
};

// --- Zero-Cost-First: Provider/Cost/Profile API ---
export interface ProviderHealth {
  name: string; healthy: boolean; avg_latency_ms: number; is_local: boolean;
  quota_pct?: number; quota_requests_remaining?: number; quota_tokens_remaining?: number;
}
export interface ProfileInfo {
  profile: string; cost_policy: string; network_policy: string;
  allow_cloud: boolean; enable_quota_governor: boolean; daily_usd: number;
  allowed_cost_classes: string[];
}
export interface QuotaSnapshot {
  enabled: boolean;
  providers: Record<string, {
    requests_remaining: number; tokens_remaining: number;
    requests_used: number; tokens_used: number;
    daily_requests_limit: number; daily_tokens_limit: number;
    pct_remaining: number;
  }>;
}
export interface CapabilityMatrix {
  matrix: Record<string, { local: string[]; free_quota: string[]; paid: string[] }>;
  total_models: number;
}

export const providersApi = {
  health: () =>
    requestJSON(`/providers/health`) as Promise<ProviderHealth[]>,
  free: () =>
    requestJSON(`/providers/free`) as Promise<ProviderHealth[]>,
  profile: () =>
    requestJSON(`/profile`) as Promise<ProfileInfo>,
  quota: () =>
    requestJSON(`/providers/quota`) as Promise<QuotaSnapshot>,
  capabilityMatrix: () =>
    requestJSON(`/capabilities/matrix`) as Promise<CapabilityMatrix>,
};

