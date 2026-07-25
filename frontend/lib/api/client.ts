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
