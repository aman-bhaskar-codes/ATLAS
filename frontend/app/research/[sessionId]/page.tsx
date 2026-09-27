"use client";

import { use } from "react";
import { ResearchWorkspace } from "@/components/research/ResearchWorkspace";

/**
 * `/research/[sessionId]` — the Perplexity-class Research surface entry point.
 * Opened in a separate window from the Command Center. The dynamic segment is the
 * backend `session_id`; the surface resolves it against the persisted session +
 * the live SSE phase trace, so the URL alone rehydrates the full run (grounded
 * answer, source rail, citations) after a close/reopen or a mid-run reconnect.
 */
export default function ResearchSessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  return <ResearchWorkspace sessionId={decodeURIComponent(sessionId)} />;
}
