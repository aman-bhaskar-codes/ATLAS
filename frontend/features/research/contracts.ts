/**
 * Research workspace contracts + the pure citation-grounding parser (R9, §17–§19).
 *
 * WHY the parser lives here and is pure: the distinguishing quality of a research
 * answer is that its citations RESOLVE. The backend already pins this
 * deterministically (`evaluation.evaluators.check_citation_grounding`); this module
 * is the *same* structural check re-expressed for the UI so the workspace can render
 * which `[n]` markers point at a real footnote and which point at nothing — without
 * a round-trip and without overclaiming. It must stay byte-for-byte faithful to the
 * backend algorithm: a marker is a *definition* only when it leads its own line
 * (`[n] Title — url`); every other occurrence is a *use*; a use with no matching
 * definition is *dangling*.
 *
 * §22 (honesty): the workspace surfaces dangling citations, never hides them.
 * §23 (untrusted data): answer text is rendered as text by React — never as HTML.
 */

import { z } from "zod";

const CITATION = /\[(\d{1,3})\]/g;

/** One `[n] …` footnote line the answer defined for itself. */
export interface Source {
  n: number;
  /** The footnote text minus its leading `[n]` marker, e.g. "Optics — a". */
  label: string;
}

/** A run of prose split into plain text and inline citation markers, for rendering. */
export type AnswerToken =
  | { kind: "text"; text: string }
  | { kind: "cite"; n: number; resolved: boolean };

export interface CitedAnswer {
  /** Prose lines (everything that is not a footnote definition), joined with "\n". */
  body: string;
  /** Footnote definitions, in first-seen order. */
  sources: Source[];
  /** Distinct citation numbers used in prose that resolve to a defined source. */
  resolved: number[];
  /** Distinct citation numbers used that resolve to NOTHING — the failure that matters. */
  dangling: number[];
  /** True when no `[n]` markers appear at all (vacuously grounded — NOT "well sourced"). */
  hasCitations: boolean;
  /** True when there are citations and none dangle. Mirrors backend `grounded`. */
  grounded: boolean;
}

function markersIn(line: string): number[] {
  const out: number[] = [];
  for (const m of line.matchAll(CITATION)) out.push(Number(m[1]));
  return out;
}

/**
 * Parse an answer into prose + sources + a grounding verdict.
 *
 * Line-scan identical to the backend: on each line, the FIRST marker defines a
 * source iff the line (left-trimmed) starts with it; any remaining markers on that
 * line are uses; markers on non-definition lines are all uses.
 */
export function parseCitedAnswer(answer: string): CitedAnswer {
  const defined = new Set<number>();
  const used = new Set<number>();
  const sources: Source[] = [];
  const bodyLines: string[] = [];

  for (const line of answer.split("\n")) {
    const markers = markersIn(line);
    if (markers.length === 0) {
      bodyLines.push(line);
      continue;
    }
    const head = line.replace(/^\s+/, "");
    if (head.startsWith(`[${markers[0]}]`)) {
      const n = markers[0];
      if (!defined.has(n)) {
        defined.add(n);
        // Strip the leading "[n]" (and a following space) to get the human label.
        sources.push({ n, label: head.replace(/^\[\d{1,3}\]\s*/, "").trim() });
      }
      for (const m of markers.slice(1)) used.add(m);
    } else {
      for (const m of markers) used.add(m);
      bodyLines.push(line);
    }
  }

  const resolved = [...used].filter((n) => defined.has(n)).sort((a, b) => a - b);
  const dangling = [...used].filter((n) => !defined.has(n)).sort((a, b) => a - b);
  const hasCitations = used.size > 0 || defined.size > 0;
  return {
    body: bodyLines.join("\n").trim(),
    sources,
    resolved,
    dangling,
    hasCitations,
    grounded: hasCitations && dangling.length === 0,
  };
}

/**
 * Tokenise a single prose string into text runs and inline citation chips, tagging
 * each chip with whether its number resolves to one of `definedNumbers`. Pure — the
 * page maps tokens to elements.
 */
export function tokenizeProse(text: string, definedNumbers: Set<number>): AnswerToken[] {
  const tokens: AnswerToken[] = [];
  let last = 0;
  for (const m of text.matchAll(CITATION)) {
    const start = m.index ?? 0;
    if (start > last) tokens.push({ kind: "text", text: text.slice(last, start) });
    const n = Number(m[1]);
    tokens.push({ kind: "cite", n, resolved: definedNumbers.has(n) });
    last = start + m[0].length;
  }
  if (last < text.length) tokens.push({ kind: "text", text: text.slice(last) });
  return tokens;
}

/* ════════════════════════════════════════════════════════════════════════════
 * Session surface contracts (`/api/v1/research/*`) + SSE phase trace (R3/R4).
 *
 * These mirror the backend source of truth EXACTLY — orchestration/research/
 * records.py + interfaces/api/routes_research.py — no invented fields, no `any`
 * (§70). The persisted, streamed research SESSION is distinct from the legacy
 * poll-based task view above: a question runs the governed `knowledge` pipeline in
 * the background, emits a REAL phase trace (§69), and lands a grounded answer.
 * ════════════════════════════════════════════════════════════════════════════ */

/** A persisted session's lifecycle state; RUNNING is the only non-terminal one. */
export const ResearchStatusSchema = z.enum(["running", "completed", "refused", "failed"]);
export type ResearchStatus = z.infer<typeof ResearchStatusSchema>;

export const TERMINAL_RESEARCH_STATUSES: ReadonlySet<ResearchStatus> = new Set([
  "completed",
  "refused",
  "failed",
]);

/** Every REAL phase a streamed run passes through (records.py::ResearchPhase). */
export const ResearchPhaseSchema = z.enum([
  "started",
  "retrieving",
  "round",
  "sources_found",
  "synthesizing",
  "answer",
  "citations",
  "grounding",
  "completed",
  "refused",
  "failed",
]);
export type ResearchPhase = z.infer<typeof ResearchPhaseSchema>;

/** One inline citation the fabric resolved — the anchor a `[n]` marker points at. */
export const ResearchCitationSchema = z.object({
  index: z.number().int().default(0),
  title: z.string().default(""),
  uri: z.string().default(""),
  quote: z.string().default(""),
});
export type ResearchCitation = z.infer<typeof ResearchCitationSchema>;

/** One surfaced disagreement between sources — carried verbatim, never averaged. */
export const ResearchContradictionSchema = z.object({
  key: z.string().default(""),
  description: z.string().default(""),
});
export type ResearchContradiction = z.infer<typeof ResearchContradictionSchema>;

/** One retrieved source for the source rail (may exceed the cited set). */
export const ResearchSourceSchema = z.object({
  title: z.string().default(""),
  uri: z.string().default(""),
  quote: z.string().default(""),
});
export type ResearchSource = z.infer<typeof ResearchSourceSchema>;

/** The grounded, cited answer. `answered=false` is an HONEST refusal (§54/§69). */
export const ResearchAnswerSchema = z.object({
  text: z.string().default(""),
  answered: z.boolean().default(false),
  confidence: z.number().default(0),
  mode: z.string().default(""),
  citations: z.array(ResearchCitationSchema).default([]),
  refusal_reason: z.string().default(""),
  contradictions: z.array(ResearchContradictionSchema).default([]),
  degraded: z.boolean().default(false),
  degradation_reason: z.string().default(""),
  coverage_warning: z.string().default(""),
});
export type ResearchAnswer = z.infer<typeof ResearchAnswerSchema>;

/** A full session: metadata + the grounded answer + source rail + trace summary. */
export const ResearchSessionSchema = z.object({
  session_id: z.string(),
  status: ResearchStatusSchema,
  question: z.string(),
  mode: z.string(),
  correlation_id: z.string(),
  parent_session_id: z.string().nullable().default(null),
  answer: ResearchAnswerSchema.nullable().default(null),
  sources: z.array(ResearchSourceSchema).default([]),
  stop_reason: z.string().default(""),
  total_rounds: z.number().int().default(0),
  total_discovered: z.number().int().default(0),
  open_questions: z.number().int().default(0),
  error: z.string().nullable().default(null),
  created_ts: z.string(),
  updated_ts: z.string(),
});
export type ResearchSession = z.infer<typeof ResearchSessionSchema>;

/** A compact session row for lists — no answer body or source rail. */
export const ResearchSessionSummarySchema = z.object({
  session_id: z.string(),
  status: ResearchStatusSchema,
  question: z.string(),
  mode: z.string(),
  parent_session_id: z.string().nullable().default(null),
  answered: z.boolean().default(false),
  source_count: z.number().int().default(0),
  created_ts: z.string(),
  updated_ts: z.string(),
});
export type ResearchSessionSummary = z.infer<typeof ResearchSessionSummarySchema>;

export const ResearchSessionListSchema = z.object({
  sessions: z.array(ResearchSessionSummarySchema).default([]),
});

/**
 * One `research_event` SSE frame's `data` (records.py::ResearchEvent). `payload`
 * carries that phase's REAL facts (e.g. `{count: 5}` for sources_found); kept
 * permissive and read through the reducer's narrow accessors so a new metadata key
 * never breaks the trace.
 */
export const ResearchEventSchema = z.object({
  sequence: z.number().int(),
  session_id: z.string().default(""),
  phase: z.string().default(""),
  payload: z.record(z.string(), z.unknown()).default({}),
  ts: z.string().default(""),
});
export type ResearchEvent = z.infer<typeof ResearchEventSchema>;

/* ── request bodies ────────────────────────────────────────────────────────── */

export interface StartResearchBody {
  question: string;
  mode?: string | null;
  background?: boolean;
}

export interface FollowUpResearchBody {
  question: string;
  mode?: string | null;
  background?: boolean;
}
