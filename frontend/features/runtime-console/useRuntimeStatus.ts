"use client";
// frontend/features/runtime-console/useRuntimeStatus.ts
//
// App-scoped runtime status — the SINGLE, un-fakeable source for the "ATLAS
// STATUS" pill in the Topbar and Home. Polls GET /runtime/status (zod-validated
// via atlasApi) every 3s. Per the product mandate: this state must come from
// backend runtime state and must never be faked.
import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "../../lib/api/client";
import type { RuntimeStatus } from "../../lib/api/contracts";

export function useRuntimeStatus() {
  return useQuery<RuntimeStatus>({
    queryKey: ["runtimeStatus"],
    queryFn: atlasApi.runtimeStatus,
    refetchInterval: 3000,
    retry: 2,
  });
}

export type AtlasTone =
  | "ready"
  | "busy"
  | "attention"
  | "degraded"
  | "halted"
  | "stopped"
  | "unknown";

export interface AtlasDisplayState {
  label: string;
  tone: AtlasTone;
  detail: string;
}

/**
 * Derive the display state ONLY from real backend fields.
 *
 * - Kill switch takes precedence over everything (most safety-critical signal).
 * - BUSY / WAITING APPROVAL are derived from the live counts because the backend
 *   `state` enum (starting|ready|degraded|stopping|stopped) does not encode them.
 * - We never emit "RECOVERING": the backend does not expose it, so fabricating it
 *   would violate the truth mandate.
 */
export function deriveAtlasState(
  status: RuntimeStatus | undefined,
  isError?: boolean,
): AtlasDisplayState {
  if (isError)
    return { label: "UNREACHABLE", tone: "stopped", detail: "Cannot reach the ATLAS runtime." };
  if (!status)
    return { label: "CONNECTING", tone: "unknown", detail: "Contacting the ATLAS runtime…" };

  if (status.kill_switch_active) {
    return {
      label: "HALTED",
      tone: "halted",
      detail: "Kill switch is active — new actions are blocked.",
    };
  }

  switch (status.state) {
    case "stopped":
      return { label: "STOPPED", tone: "stopped", detail: "Runtime is stopped." };
    case "stopping":
      return { label: "STOPPING", tone: "stopped", detail: "Runtime is shutting down." };
    case "starting":
      return { label: "STARTING", tone: "unknown", detail: "Runtime is starting up." };
    case "degraded":
      return {
        label: "DEGRADED",
        tone: "degraded",
        detail: "Running with one or more capabilities disabled or unhealthy.",
      };
    case "ready":
      if (status.pending_approval_count > 0) {
        const n = status.pending_approval_count;
        return {
          label: "WAITING APPROVAL",
          tone: "attention",
          detail: `${n} approval${n === 1 ? "" : "s"} awaiting your decision.`,
        };
      }
      if (status.active_task_count > 0) {
        const n = status.active_task_count;
        return {
          label: "BUSY",
          tone: "busy",
          detail: `${n} task${n === 1 ? "" : "s"} running.`,
        };
      }
      return { label: "READY", tone: "ready", detail: "Idle and ready for a task." };
    default:
      return { label: "UNKNOWN", tone: "unknown", detail: "" };
  }
}

/** Map a tone to a design token for dots/pills. */
export function toneColor(tone: AtlasTone): string {
  switch (tone) {
    case "ready":
      return "var(--jade-400)";
    case "busy":
      return "var(--royal-400)";
    case "attention":
    case "degraded":
      return "var(--gold-400)";
    case "halted":
      return "var(--danger-400)";
    case "stopped":
      return "var(--ember-400)";
    default:
      return "var(--paper-500)";
  }
}
