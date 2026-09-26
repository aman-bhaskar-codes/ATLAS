"use client";

import { Bot, Loader2, Radio, WifiOff } from "lucide-react";
import type { StreamStatus } from "@/features/agent-runs/useAgentRunStream";
import type { RunLifecycle } from "@/features/agent-runs/projectRunState";

const STREAM_LABEL: Record<StreamStatus, string> = {
  idle: "idle",
  connecting: "connecting…",
  live: "live",
  reconnecting: "reconnecting…",
  closed: "closed",
};

const LIFECYCLE_LABEL: Record<RunLifecycle, string> = {
  idle: "Idle",
  running: "Running",
  completed: "Completed",
  limited: "Stopped (limit)",
  error: "Error",
};

const LIFECYCLE_COLOR: Record<RunLifecycle, string> = {
  idle: "var(--paper-500)",
  running: "var(--gold-400)",
  completed: "var(--jade-400, #22c55e)",
  limited: "var(--gold-500)",
  error: "var(--danger-400)",
};

/**
 * The Agent Panel header: real run identity + real stream health. `lifecycle`
 * comes from the folded durable trace; `stream` is the live SSE connection state.
 * Both are backend-derived — no fabricated status.
 */
export function AgentHeader({
  lifecycle,
  stream,
  runId,
}: {
  lifecycle: RunLifecycle;
  stream: StreamStatus;
  runId: string | null;
}) {
  return (
    <div className="wb-panel-head">
      <Bot size={13} />
      <span>Agent</span>
      {runId && (
        <span
          className="wb-agent-life"
          style={{ color: LIFECYCLE_COLOR[lifecycle] }}
        >
          {lifecycle === "running" ? (
            <Loader2 size={11} className="wb-spin" />
          ) : null}
          {LIFECYCLE_LABEL[lifecycle]}
        </span>
      )}
      <span className="wb-meta-spacer" style={{ flex: 1 }} />
      {runId && (
        <span
          className="wb-agent-stream"
          title={`run ${runId}`}
          style={{
            color:
              stream === "live"
                ? "var(--jade-400, #22c55e)"
                : stream === "reconnecting"
                  ? "var(--danger-400)"
                  : "var(--paper-500)",
          }}
        >
          {stream === "live" ? (
            <Radio size={11} />
          ) : stream === "reconnecting" ? (
            <WifiOff size={11} />
          ) : null}
          {STREAM_LABEL[stream]}
        </span>
      )}
    </div>
  );
}
