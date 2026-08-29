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
