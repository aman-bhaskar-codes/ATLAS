// frontend/features/research/projectResearchState.ts
//
// Pure fold: ResearchEvent[] → operational phase state for the Perplexity surface.
// This is the ONLY place the raw durable phase trace becomes UI state, so it is
// unit-testable in isolation. Every value it surfaces has a real backend origin
// (§69) — the reducer NEVER fabricates a phase, a count, or a grounding verdict; it
// only reads the facts the backend actually emitted in each event's `payload`.
//
// The stream carries lifecycle + progress, NOT the full answer body: the grounded
// answer text + source rail land via the session record (useResearchSession). So
// this projection surfaces phase progress (what the run is DOING right now), the
// real source/round/citation counts as they arrive, and the terminal status — the
// live "thinking" feed beside the settled answer.
//
// Phases (records.py::ResearchPhase), each in event order:
//   started → retrieving → round(×N) → sources_found{count} → synthesizing →
//   answer{answered} → citations{count} → grounding{answered,confidence} →
//   (completed | refused | failed{error})

import type { ResearchEvent, ResearchStatus } from "./contracts";

export type ResearchLifecycle = "idle" | "running" | "completed" | "refused" | "failed";

/** One phase step in the live feed — its name plus the real facts it carried. */
export interface PhaseStep {
  seq: number;
  phase: string;
  ts: string;
  payload: Record<string, unknown>;
}

export interface ResearchProjection {
  lifecycle: ResearchLifecycle;
  /** The most recent phase the run entered — the "doing X now" label. */
  currentPhase: string | null;
  /** Every phase step in sequence order — the streamed activity feed. */
  phases: PhaseStep[];
  /** Real supervisor rounds observed (one `round` event each). */
  rounds: number;
  /** Real count of retrieved sources, once `sources_found` arrives (else null). */
  sourceCount: number | null;
  /** Real count of resolved inline citations, once `citations` arrives (else null). */
  citationCount: number | null;
  /** Whether the run produced a grounded answer (from `grounding`/`answer`; else null). */
  answered: boolean | null;
  /** Model confidence from the `grounding` phase (else null). */
  confidence: number | null;
  lastSequence: number;
}

export const EMPTY_RESEARCH_PROJECTION: ResearchProjection = {
  lifecycle: "idle",
  currentPhase: null,
  phases: [],
  rounds: 0,
  sourceCount: null,
  citationCount: null,
  answered: null,
  confidence: null,
  lastSequence: 0,
};

/* payload accessors — unknown-safe, no `any` (§70) */
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

// A terminal phase name maps 1:1 onto the persisted session status.
const TERMINAL_LIFECYCLE: Record<string, ResearchLifecycle> = {
  completed: "completed",
  refused: "refused",
  failed: "failed",
};

/** Fold the full ordered phase trace into the current projection. Deterministic. */
export function projectResearchState(events: readonly ResearchEvent[]): ResearchProjection {
  let lifecycle: ResearchLifecycle = "idle";
  let currentPhase: string | null = null;
  let rounds = 0;
  let sourceCount: number | null = null;
  let citationCount: number | null = null;
  let answered: boolean | null = null;
  let confidence: number | null = null;
  let lastSequence = 0;
  const phases: PhaseStep[] = [];

  for (const ev of events) {
    lastSequence = Math.max(lastSequence, ev.sequence);
    const phase = ev.phase;
    const p = ev.payload;
    currentPhase = phase;
    phases.push({ seq: ev.sequence, phase, ts: ev.ts, payload: p });

    switch (phase) {
      case "started":
      case "retrieving":
      case "synthesizing":
        if (lifecycle === "idle") lifecycle = "running";
        break;
      case "round":
        lifecycle = "running";
        rounds += 1;
        break;
      case "sources_found": {
        const c = num(p.count);
        if (c !== null) sourceCount = c;
        break;
      }
      case "answer": {
        const a = bool(p.answered);
        if (a !== null) answered = a;
        break;
      }
      case "citations": {
        const c = num(p.count);
        if (c !== null) citationCount = c;
        break;
      }
      case "grounding": {
        const a = bool(p.answered);
        if (a !== null) answered = a;
        const conf = num(p.confidence);
        if (conf !== null) confidence = conf;
        break;
      }
      case "completed":
      case "refused":
      case "failed":
        lifecycle = TERMINAL_LIFECYCLE[phase];
        break;
      default:
        // Unknown/new phase: recorded in `phases` above, no state derived from it.
        break;
    }
  }

  return {
    lifecycle,
    currentPhase,
    phases,
    rounds,
    sourceCount,
    citationCount,
    answered,
    confidence,
    lastSequence,
  };
}

/** Map a persisted session `status` onto the same lifecycle vocabulary. */
export function lifecycleFromStatus(status: ResearchStatus): ResearchLifecycle {
  return status === "running" ? "running" : TERMINAL_LIFECYCLE[status];
}
