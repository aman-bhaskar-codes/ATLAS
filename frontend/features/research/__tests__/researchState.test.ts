// frontend/features/research/__tests__/researchState.test.ts
//
// The reducer is the ONLY place the durable phase trace becomes UI state, so it is
// pinned in isolation: it must derive lifecycle, real counts, and the grounding
// verdict ONLY from facts the backend actually emitted (§69), and never fabricate.
// Fixtures mirror the REAL phase trace a governed run emits
// (orchestration/research/service.py::_run_research), the same shape the SSE stream
// carries and the backend test test_research_service.py asserts.

import { describe, expect, it } from "vitest";
import { ResearchEventSchema, type ResearchEvent } from "@/features/research/contracts";
import {
  EMPTY_RESEARCH_PROJECTION,
  lifecycleFromStatus,
  projectResearchState,
} from "@/features/research/projectResearchState";

let seq = 0;
function ev(phase: string, payload: Record<string, unknown> = {}): ResearchEvent {
  seq += 1;
  // Round-trip through the contract so the test uses ONLY validated shapes (§70).
  return ResearchEventSchema.parse({
    sequence: seq,
    session_id: "sess-1",
    phase,
    payload,
    ts: `t${seq}`,
  });
}

function groundedTrace(): ResearchEvent[] {
  seq = 0;
  return [
    ev("started"),
    ev("retrieving"),
    ev("round", { index: 0 }),
    ev("round", { index: 1 }),
    ev("sources_found", { count: 5 }),
    ev("synthesizing"),
    ev("answer", { answered: true }),
    ev("citations", { count: 2 }),
    ev("grounding", { answered: true, confidence: 0.82 }),
    ev("completed", { status: "completed" }),
  ];
}

describe("projectResearchState", () => {
  it("returns the empty projection for no events", () => {
    expect(projectResearchState([])).toEqual(EMPTY_RESEARCH_PROJECTION);
  });

  it("folds a full grounded trace into real counts + a completed lifecycle", () => {
    const p = projectResearchState(groundedTrace());
    expect(p.lifecycle).toBe("completed");
    expect(p.currentPhase).toBe("completed");
    expect(p.rounds).toBe(2); // one per real `round` event
    expect(p.sourceCount).toBe(5); // the REAL count the backend emitted
    expect(p.citationCount).toBe(2);
    expect(p.answered).toBe(true);
    expect(p.confidence).toBeCloseTo(0.82);
    expect(p.phases.map((s) => s.phase)).toEqual([
      "started",
      "retrieving",
      "round",
      "round",
      "sources_found",
      "synthesizing",
      "answer",
      "citations",
      "grounding",
      "completed",
    ]);
    expect(p.lastSequence).toBe(10);
  });

  it("is running mid-trace before any terminal phase", () => {
    const p = projectResearchState([ev("started"), ev("retrieving")]);
    expect(p.lifecycle).toBe("running");
    expect(p.currentPhase).toBe("retrieving");
    // Counts stay null until their phase actually arrives — never fabricated.
    expect(p.sourceCount).toBeNull();
    expect(p.citationCount).toBeNull();
    expect(p.answered).toBeNull();
  });

  it("honours an honest refusal: answered=false and a refused lifecycle", () => {
    seq = 0;
    const p = projectResearchState([
      ev("started"),
      ev("retrieving"),
      ev("sources_found", { count: 0 }),
      ev("answer", { answered: false }),
      ev("grounding", { answered: false, confidence: 0 }),
      ev("refused", { status: "refused" }),
    ]);
    expect(p.lifecycle).toBe("refused");
    expect(p.answered).toBe(false);
    expect(p.sourceCount).toBe(0);
  });

  it("closes a failed dispatch trace as failed", () => {
    seq = 0;
    const p = projectResearchState([
      ev("started"),
      ev("retrieving"),
      ev("failed", { error: "denied (tier CONFIRM): web egress blocked" }),
    ]);
    expect(p.lifecycle).toBe("failed");
    expect(p.currentPhase).toBe("failed");
  });

  it("ignores a malformed count rather than fabricating one", () => {
    seq = 0;
    const p = projectResearchState([
      ev("started"),
      ev("sources_found", { count: "lots" }), // not a number
    ]);
    expect(p.sourceCount).toBeNull();
  });

  it("records an unknown future phase without deriving state from it", () => {
    seq = 0;
    const p = projectResearchState([ev("started"), ev("reranking")]);
    expect(p.currentPhase).toBe("reranking");
    expect(p.phases.map((s) => s.phase)).toContain("reranking");
    expect(p.lifecycle).toBe("running");
  });
});

describe("lifecycleFromStatus", () => {
  it("maps every persisted status onto the reducer's lifecycle vocabulary", () => {
    expect(lifecycleFromStatus("running")).toBe("running");
    expect(lifecycleFromStatus("completed")).toBe("completed");
    expect(lifecycleFromStatus("refused")).toBe("refused");
    expect(lifecycleFromStatus("failed")).toBe("failed");
  });
});
