// frontend/lib/api/client.ts
import { z } from "zod";
import {
  ApprovalSchema, CapabilitySchema, CancelTaskResponseSchema,
  RuntimeHealthSchema, RuntimeStatusSchema, TaskEventSchema, TaskSchema,
} from "./contracts";

const API_BASE =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

async function request<T extends z.ZodTypeAny>(
  path: string,
  schema: T,
  init?: RequestInit,
): Promise<z.infer<T>> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`ATLAS API ${response.status}: ${detail.slice(0, 500)}`);
  }
  return schema.parse(await response.json());
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

  createTask: (payload: { request: string; idempotency_key: string }) =>
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
    const res = await fetch(`${API_BASE}/tasks?${params}`);
    if (!res.ok) throw new Error('Failed to fetch tasks');
    return res.json();
  },
  getTask: async (taskId: string) => {
    const res = await fetch(`${API_BASE}/tasks/${taskId}`);
    if (!res.ok) throw new Error('Failed to fetch task');
    return res.json();
  },

  // Approvals
  pendingApprovals: async () => {
    const res = await fetch(`${API_BASE}/approvals/pending`);
    if (!res.ok) throw new Error('Failed to fetch approvals');
    return res.json();
  },
  getApproval: async (approvalId: string) => {
    const res = await fetch(`${API_BASE}/approvals/${approvalId}`);
    if (!res.ok) throw new Error('Failed to fetch approval');
    return res.json();
  },
  decideApproval: async (approvalId: string, decision: 'approve' | 'deny', idempotencyKey: string, requestId: string) => {
    const res = await fetch(`${API_BASE}/approvals/${approvalId}/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: approvalId, decision, idempotency_key: idempotencyKey, request_id: requestId }),
    });
    if (!res.ok) throw new Error('Failed to decide approval');
    return res.json();
  },

  // Memory
  searchMemory: async (query: string, limit: number = 30) => {
    const params = new URLSearchParams({ q: query, limit: limit.toString() });
    const res = await fetch(`${API_BASE}/memory/search?${params}`);
    if (!res.ok) throw new Error('Failed to search memory');
    return res.json();
  },
  getMemoryFact: async (factId: string) => {
    const res = await fetch(`${API_BASE}/memory/facts/${factId}`);
    if (!res.ok) throw new Error('Failed to fetch memory fact');
    return res.json();
  },
  correctMemory: async (factId: string, replacementText: string, idempotencyKey: string, requestId: string) => {
    const res = await fetch(`${API_BASE}/memory/facts/${factId}/correct`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fact_id: factId, replacement_text: replacementText, idempotency_key: idempotencyKey, request_id: requestId }),
    });
    if (!res.ok) throw new Error('Failed to correct memory');
    return res.json();
  },

  // Audit
  getAuditLog: async (cursor?: string, limit: number = 50, filters?: { taskId?: string, correlationId?: string, executionId?: string }) => {
    const params = new URLSearchParams();
    if (cursor) params.append('cursor', cursor);
    params.append('limit', limit.toString());
    if (filters?.taskId) params.append('task_id', filters.taskId);
    if (filters?.correlationId) params.append('correlation_id', filters.correlationId);
    if (filters?.executionId) params.append('execution_id', filters.executionId);
    const res = await fetch(`${API_BASE}/audit?${params}`);
    if (!res.ok) throw new Error('Failed to fetch audit log');
    return res.json();
  }
};
