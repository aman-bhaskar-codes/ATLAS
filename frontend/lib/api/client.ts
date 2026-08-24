// frontend/lib/api/client.ts
import { z } from "zod";
import {
  ApprovalSchema, CapabilitySchema, CancelTaskResponseSchema,
  RuntimeHealthSchema, RuntimeStatusSchema, TaskEventSchema, TaskSchema,
} from "./contracts";

const API_BASE =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

const TIMEOUT_MS = 8000;

/* ─────────────────────────────── error types ────────────────────────────────
 *
 * WHY these exist: every failure used to be `new Error("ATLAS API 429: …")`.
 * The status was in the message string, so no caller could branch on it — which
 * is why the retry predicate retried 404s and why every query site could only
 * render one undifferentiated "error" state. Untyped failures also made the
 * three genuinely different problems below indistinguishable:
 *
 *   AtlasApiError      the backend answered, and said no        → show the reason
 *   AtlasTimeoutError  the backend never answered               → worth retrying
 *   AtlasContractError the backend answered with the wrong shape → retrying cannot help
 */

/** The backend answered with a non-2xx status. */
export class AtlasApiError extends Error {
  readonly status: number;
  /** The `error` field of the ATLAS envelope, when the response carried one. */
  readonly code: string | null;
  readonly detail: string;
  /** Correlates with the server log line. From the envelope or the X-Request-ID header. */
  readonly requestId: string | null;
  readonly path: string;

  constructor(args: {
    status: number;
    code: string | null;
    detail: string;
    requestId: string | null;
    path: string;
  }) {
    super(`ATLAS API ${args.status} on ${args.path}: ${args.detail}`);
    this.name = "AtlasApiError";
    this.status = args.status;
    this.code = args.code;
    this.detail = args.detail;
    this.requestId = args.requestId;
    this.path = args.path;
  }
}

/** The request was aborted after TIMEOUT_MS with no response. */
export class AtlasTimeoutError extends Error {
  readonly timeoutMs: number;
  readonly path: string;

  constructor(path: string, timeoutMs: number) {
    super(`ATLAS backend timeout: ${path} took longer than ${timeoutMs}ms`);
    this.name = "AtlasTimeoutError";
    this.timeoutMs = timeoutMs;
    this.path = path;
  }
}

/** A 2xx response whose body did not match the schema this client expects. */
export class AtlasContractError extends Error {
  readonly path: string;
  /** Human-readable summary of the zod issues, or the JSON parse failure. */
  readonly issues: string;

  constructor(path: string, issues: string) {
    super(`ATLAS contract mismatch on ${path}: ${issues}`);
    this.name = "AtlasContractError";
    this.path = path;
    this.issues = issues;
  }
}

/** True for the three error types this module throws (and nothing else). */
export function isAtlasError(
  error: unknown,
): error is AtlasApiError | AtlasTimeoutError | AtlasContractError {
  return (
    error instanceof AtlasApiError ||
    error instanceof AtlasTimeoutError ||
    error instanceof AtlasContractError
  );
}

/* ───────────────────────────────── plumbing ──────────────────────────────── */

/**
 * Pull `{code, detail}` out of an error body without ever throwing.
 *
 * Three shapes are in play and all of them are real:
 *   `{error, detail, request_id}`  the ATLAS envelope (domain errors, 500, 429)
 *   `{detail: string}`             FastAPI's HTTPException — e.g. the 401 from
 *                                  require_principal, which does NOT use the envelope
 *   `{detail: [{msg, loc}, …]}`    FastAPI request validation (422)
 * Anything else (HTML from a proxy, an empty body) falls back to the raw text.
 */
function parseErrorBody(text: string): { code: string | null; detail: string; requestId: string | null } {
  const fallback = { code: null, detail: text.slice(0, 500) || "no response body", requestId: null };
  if (!text) return fallback;
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return fallback;
  }
  if (typeof body !== "object" || body === null) return fallback;

  const record = body as Record<string, unknown>;
  const code = typeof record.error === "string" ? record.error : null;
  const rawDetail = record.detail;

  let detail: string;
  if (typeof rawDetail === "string") {
    detail = rawDetail;
  } else if (Array.isArray(rawDetail)) {
    // FastAPI validation errors: join the messages rather than showing "[object Object]".
    detail = rawDetail
      .map((item) =>
        typeof item === "object" && item !== null && typeof (item as { msg?: unknown }).msg === "string"
          ? String((item as { msg: string }).msg)
          : JSON.stringify(item),
      )
      .join("; ");
  } else {
    detail = code ?? fallback.detail;
  }
  const requestId = typeof record.request_id === "string" ? record.request_id : null;
  return { code, detail: detail.slice(0, 500), requestId };
}

/** Validate a payload, converting a ZodError into AtlasContractError. */
export function parseContract<T extends z.ZodTypeAny>(
  path: string,
  schema: T,
  value: unknown,
): z.infer<T> {
  const result = schema.safeParse(value);
  if (result.success) return result.data;
  const issues = result.error.issues
    .map((issue) => `${issue.path.join(".") || "(root)"}: ${issue.message}`)
    .join("; ");
  throw new AtlasContractError(path, issues.slice(0, 500));
}

async function fetchWithTimeout(
  path: string,
  init?: RequestInit,
  timeoutMs = TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(`${API_BASE}${path}`, {
      // Every endpoint here is live runtime state on a polling interval. Without
      // this the browser's heuristic cache is free to serve a response the
      // backend sent no Cache-Control for, and the UI shows a stale status pill.
      cache: "no-store",
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      signal: controller.signal,
    });
  } catch (error: unknown) {
    // An abort surfaces as a DOMException named "AbortError"; a genuine network
    // failure surfaces as a TypeError and is rethrown as-is, because "the server
    // is not there" is not the same fact as "the server was too slow".
    if (error instanceof Error && error.name === "AbortError") {
      throw new AtlasTimeoutError(path, timeoutMs);
    }
    throw error;
  } finally {
    clearTimeout(id);
  }
}

/** Throw the typed error for a non-2xx response. Reads the body at most once. */
async function throwForStatus(path: string, response: Response): Promise<never> {
  let text = "";
  try {
    text = await response.text();
  } catch {
    // A body that cannot be read must not mask the status, which is the useful part.
  }
  const { code, detail, requestId } = parseErrorBody(text);
  throw new AtlasApiError({
    status: response.status,
    code,
    detail,
    // The envelope carries request_id; FastAPI's own errors do not, so fall back to
    // the header the API sets on every response (CORS-exposed for exactly this).
    requestId: response.headers.get("X-Request-ID") ?? requestId,
    path,
  });
}

async function readJSON(path: string, response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new AtlasContractError(path, "response body was not valid JSON");
  }
}

async function request<T extends z.ZodTypeAny>(
  path: string,
  schema: T,
  init?: RequestInit,
): Promise<z.infer<T>> {
  const response = await fetchWithTimeout(path, init);
  if (!response.ok) await throwForStatus(path, response);
  return parseContract(path, schema, await readJSON(path, response));
}

async function requestJSON(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetchWithTimeout(path, init);
  if (!response.ok) await throwForStatus(path, response);
  return readJSON(path, response);
}

/**
 * The schema-validated request, exported for feature modules that keep their own
 * contracts (`features/memory/*`).
 *
 * WHY exported instead of each feature hand-rolling a fetch: a private copy throws
 * a bare `Error` for a 404 and a raw `ZodError` for a shape mismatch. The retry
 * predicate then treats the 404 as retryable, and `describeError` renders the
 * ZodError's own message — a blob naming internal backend fields — into the UI.
 */
export { request as requestContract };

export const atlasApi = {
  runtimeStatus: () => request("/runtime/status", RuntimeStatusSchema),
  runtimeHealth: () => request("/runtime/health", RuntimeHealthSchema),

  tasks: async () => {
    const path = "/tasks?limit=20";
    const data = await requestJSON(path) as { items: unknown[] };
    // parseContract, not .parse(): a bare ZodError here would escape as an
    // untyped error and the retry predicate would treat it as retryable.
    return parseContract(path, z.array(TaskSchema), data?.items);
  },
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

// --- Autonomy Fabric Endpoints (Phase 4) ---
import { Automation } from "./contracts";
export const autonomyApi = {
  listAutomations: async (enabledOnly: boolean = false) => {
    return requestJSON(`/automations?enabled_only=${enabledOnly}`) as Promise<Automation[]>;
  },
  getAutomation: async (id: string) => {
    return requestJSON(`/automations/${encodeURIComponent(id)}`) as Promise<Automation>;
  },
  createAutomation: async (auto: Partial<Automation>) => {
    return requestJSON(`/automations`, {
      method: "POST",
      body: JSON.stringify(auto),
    }) as Promise<Automation>;
  },
  updateAutomation: async (id: string, auto: Partial<Automation>) => {
    return requestJSON(`/automations/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(auto),
    }) as Promise<Automation>;
  },
  deleteAutomation: async (id: string) => {
    return requestJSON(`/automations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
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
  // NOT `/api/v1/trajectory/...`: API_BASE already ends in /api/v1, so the old
  // path requested /api/v1/api/v1/trajectory/experiences — a guaranteed 404.
  experiences: (limit = 50) =>
    requestJSON(`/trajectory/experiences?limit=${limit}`) as Promise<AtlasExperience[]>,
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

