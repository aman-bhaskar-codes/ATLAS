// frontend/features/agent-runs/mutations.ts
//
// Start / continue a governed agent run. Every run re-enters the existing
// AgentRunService (which drives the SafetyEngine-funnelled engine) — no second
// execution path. Starting a run bound to this workspace_id lets the run's `ide`
// tool read/edit/run inside the workbench's workspace.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { requestContract } from "@/lib/api/client";
import { RunResponseSchema, type StartRunBody } from "./contracts";

/**
 * Start a run bound to `workspaceId`. We always start it BACKGROUND so the POST
 * returns immediately with a streamable `running` stub — the Agent Panel then
 * streams the durable trace live instead of blocking on a synchronous loop.
 */
export function useStartRun(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { request: string; sessionId?: string | null; maxTools?: number | null }) => {
      const payload: StartRunBody = {
        request: body.request,
        workspace_id: workspaceId,
        session_id: body.sessionId ?? null,
        max_tools: body.maxTools ?? null,
        background: true,
      };
      return requestContract("/agent/runs", RunResponseSchema, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", "runs"] });
    },
  });
}

/**
 * Continue a finished run with a follow-up request. Returns a fresh, linked run
 * (`parent_run_id` set) carrying the rehydrated history — itself streamable.
 */
export function useContinueRun(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { runId: string; request: string; maxTools?: number | null }) =>
      requestContract(
        `/agent/runs/${encodeURIComponent(body.runId)}/continue`,
        RunResponseSchema,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request: body.request, max_tools: body.maxTools ?? null }),
        },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", "runs"] });
      void workspaceId;
    },
  });
}
