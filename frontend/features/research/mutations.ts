// frontend/features/research/mutations.ts
//
// Start / follow-up a governed research session. Every session re-enters the
// existing ResearchService (which drives the SafetyEngine-funnelled `knowledge`
// tool) — no second execution path, no second retrieval system. Sessions are
// always started BACKGROUND so the POST returns immediately with a streamable
// `running` stub; the Perplexity surface then streams the durable phase trace
// live instead of blocking on the synchronous governed run.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { requestContract } from "@/lib/api/client";
import {
  ResearchSessionSchema,
  type StartResearchBody,
  type FollowUpResearchBody,
} from "./contracts";

/**
 * Start a research session for `question`. Background by default: the response is
 * a `running` stub whose `session_id` is immediately addressable + streamable.
 */
export function useStartResearch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { question: string; mode?: string | null }) => {
      const payload: StartResearchBody = {
        question: body.question,
        mode: body.mode ?? null,
        background: true,
      };
      return requestContract("/research/sessions", ResearchSessionSchema, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["research", "sessions"] });
    },
  });
}

/**
 * Ask a follow-up against `sessionId` — a fresh, linked session
 * (`parent_session_id` set) that inherits the parent's mode unless overridden.
 * Itself streamable, background by default.
 */
export function useFollowUp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { sessionId: string; question: string; mode?: string | null }) => {
      const payload: FollowUpResearchBody = {
        question: body.question,
        mode: body.mode ?? null,
        background: true,
      };
      return requestContract(
        `/research/sessions/${encodeURIComponent(body.sessionId)}/follow-up`,
        ResearchSessionSchema,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["research", "sessions"] });
    },
  });
}
