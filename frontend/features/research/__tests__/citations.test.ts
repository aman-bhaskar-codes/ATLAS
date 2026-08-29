// frontend/features/research/__tests__/citations.test.ts
//
// The citation-grounding parser is the UI half of the backend's deterministic
// check (evaluation.evaluators.check_citation_grounding). These tests use the SAME
// fixtures as the backend test (tests/evaluation/test_research_eval.py) to pin that
// the two agree on what "grounded" means — a marker resolves only to a footnote that
// leads its own line, and a use with no such definition is dangling and surfaced.

import { describe, expect, it } from "vitest";
import { parseCitedAnswer, tokenizeProse } from "@/features/research/contracts";

const GROUNDED =
  "The sky scatters blue light [1] and Rayleigh explains it [2].\n\n[1] Optics — a\n[2] Rayleigh — b";
const DANGLING = "It follows from prior work [1] and later work [3].\n\n[1] Only source — a";
const NO_CITES = "A plain answer with no citation markers at all.";

describe("parseCitedAnswer", () => {
  it("treats an answer whose every marker resolves as grounded", () => {
    const p = parseCitedAnswer(GROUNDED);
    expect(p.grounded).toBe(true);
    expect(p.hasCitations).toBe(true);
    expect(p.dangling).toEqual([]);
    expect(p.resolved).toEqual([1, 2]);
    expect(p.sources.map((s) => s.n)).toEqual([1, 2]);
    expect(p.sources[0].label).toBe("Optics — a");
    // Footnote lines are lifted out of the prose body.
    expect(p.body).not.toContain("[1] Optics");
  });

  it("flags a marker that points at an undefined source as dangling", () => {
    const p = parseCitedAnswer(DANGLING);
    expect(p.grounded).toBe(false);
    expect(p.dangling).toEqual([3]);
    expect(p.resolved).toEqual([1]);
  });

  it("reports no citations without claiming the answer is grounded well", () => {
    const p = parseCitedAnswer(NO_CITES);
    expect(p.hasCitations).toBe(false);
    // Vacuous: no citations means nothing resolves and nothing dangles, and the
    // banner must NOT read this as a pass.
    expect(p.grounded).toBe(false);
    expect(p.dangling).toEqual([]);
  });

  it("counts extra markers on a definition line as uses (matches the backend)", () => {
    // "[1] Source one, see also [2]" defines 1 and USES 2; 2 is undefined -> dangling.
    const p = parseCitedAnswer("Body cites [1].\n[1] Source one, see also [2]");
    expect(p.grounded).toBe(false);
    expect(p.dangling).toEqual([2]);
  });
});

describe("tokenizeProse", () => {
  it("splits a line into text runs and citation chips tagged by resolution", () => {
    const defined = new Set([1]);
    const tokens = tokenizeProse("A claim [1] and another [2].", defined);
    const cites = tokens.filter((t) => t.kind === "cite");
    expect(cites).toEqual([
      { kind: "cite", n: 1, resolved: true },
      { kind: "cite", n: 2, resolved: false },
    ]);
    // Surrounding prose is preserved verbatim across the split.
    const text = tokens
      .filter((t) => t.kind === "text")
      .map((t) => (t as { text: string }).text)
      .join("");
    expect(text).toBe("A claim  and another .");
  });
});
