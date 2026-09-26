// frontend/features/agent-runs/projectRunState.ts
//
// Pure fold: AgentRunEvent[] → operational run state for the Agent Panel. This is
// the ONLY place raw trace events become UI state, so it is unit-testable in
// isolation and never leaks raw chain-of-thought — it surfaces lifecycle,
// discrete actions (tool calls), and file touches, all from the durable trace.
//
// Event shapes (orchestration/events.py), carried in each event's `payload`:
//   type "orchestrator": { kind: agent.started|completed|limited|error, state }
//   type "tool":         { kind: tool.requested|completed|failed, tool, operation,
//                          args, result, error, latency_ms }
// Tool events carry no call_id, but the engine loop is strictly sequential
// (requested → dispatch → completed/failed), so a requested opens the newest
// unresolved record for that (tool, operation) and the next terminal event closes it.

import type { AgentRunEvent } from "./contracts";

export type RunLifecycle = "idle" | "running" | "completed" | "limited" | "error";
export type ToolStatus = "running" | "ok" | "failed";

export interface ToolInvocation {
  seq: number;
  tool: string;
  operation: string | null;
  args: Record<string, unknown>;
  status: ToolStatus;
  error: string | null;
  latencyMs: number;
  /** Workspace-relative path when the call targets a file (ide read/apply_change). */
  path: string | null;
}

export interface FileTouch {
  seq: number;
  path: string;
  operation: string; // "apply_change"
  ok: boolean;
}

export interface CommandRun {
  seq: number;
  command: string;
  status: ToolStatus;
  error: string | null;
  latencyMs: number;
}

export interface RunProjection {
  lifecycle: RunLifecycle;
  /** Count of discrete actions (tool.requested) seen — the "plan progress" signal. */
  actionCount: number;
  tools: ToolInvocation[];
  /** Distinct files the agent wrote in this workspace, latest write per path last. */
  files: FileTouch[];
  /** Commands the agent ran through `ide · run_command`, in order (tests/builds). */
  commands: CommandRun[];
  /** The most recent still-running tool, or the last resolved one. */
  currentAction: ToolInvocation | null;
  lastSequence: number;
}

export const EMPTY_PROJECTION: RunProjection = {
  lifecycle: "idle",
  actionCount: 0,
  tools: [],
  files: [],
  commands: [],
  currentAction: null,
  lastSequence: 0,
};

/* payload accessors — unknown-safe, no `any` */
function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}
function record(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
}

const LIFECYCLE_BY_KIND: Record<string, RunLifecycle> = {
  "agent.started": "running",
  "agent.completed": "completed",
  "agent.limited": "limited",
  "agent.error": "error",
};

/** Fold the full ordered trace into the current projection. Deterministic. */
export function projectRunState(events: readonly AgentRunEvent[]): RunProjection {
  let lifecycle: RunLifecycle = "idle";
  let actionCount = 0;
  let lastSequence = 0;
  const tools: ToolInvocation[] = [];
  const files: FileTouch[] = [];

  for (const ev of events) {
    lastSequence = Math.max(lastSequence, ev.sequence);
    const p = ev.payload;
    const kind = str(p.kind) ?? "";

    if (ev.type === "orchestrator") {
      const next = LIFECYCLE_BY_KIND[kind];
      if (next) lifecycle = next;
      continue;
    }

    if (ev.type !== "tool") continue;

    const tool = str(p.tool) ?? "";
    const operation = str(p.operation);
    const args = record(p.args);
    const path = str(args.path);

    if (kind === "tool.requested") {
      actionCount += 1;
      tools.push({
        seq: ev.sequence,
        tool,
        operation,
        args,
        status: "running",
        error: null,
        latencyMs: 0,
        path,
      });
      continue;
    }

    if (kind === "tool.completed" || kind === "tool.failed") {
      const ok = kind === "tool.completed";
      // Resolve the newest still-running record for this (tool, operation).
      const idx = findLastUnresolved(tools, tool, operation);
      if (idx >= 0) {
        tools[idx] = {
          ...tools[idx],
          status: ok ? "ok" : "failed",
          error: str(p.error),
          latencyMs: num(p.latency_ms),
        };
      } else {
        // A terminal event with no open request (resumed mid-trace): record it.
        tools.push({
          seq: ev.sequence,
          tool,
          operation,
          args,
          status: ok ? "ok" : "failed",
          error: str(p.error),
          latencyMs: num(p.latency_ms),
          path,
        });
      }
      if (ok && tool === "ide" && operation === "apply_change" && path) {
        files.push({ seq: ev.sequence, path, operation, ok });
      }
    }
  }

  const running = [...tools].reverse().find((t) => t.status === "running");
  const currentAction = running ?? (tools.length ? tools[tools.length - 1] : null);

  const commands: CommandRun[] = [];
  for (const t of tools) {
    if (t.tool !== "ide" || t.operation !== "run_command") continue;
    const command = str(t.args.command);
    if (command === null) continue;
    commands.push({
      seq: t.seq,
      command,
      status: t.status,
      error: t.error,
      latencyMs: t.latencyMs,
    });
  }

  return {
    lifecycle,
    actionCount,
    tools,
    files: dedupeFiles(files),
    commands,
    currentAction,
    lastSequence,
  };
}

function findLastUnresolved(
  tools: readonly ToolInvocation[],
  tool: string,
  operation: string | null,
): number {
  for (let i = tools.length - 1; i >= 0; i -= 1) {
    const t = tools[i];
    if (t.status === "running" && t.tool === tool && t.operation === operation) return i;
  }
  return -1;
}

/** One row per path, keeping the latest write (highest seq). */
function dedupeFiles(files: readonly FileTouch[]): FileTouch[] {
  const byPath = new Map<string, FileTouch>();
  for (const f of files) byPath.set(f.path, f);
  return [...byPath.values()].sort((a, b) => a.seq - b.seq);
}
