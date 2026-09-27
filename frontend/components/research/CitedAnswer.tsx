// frontend/components/research/CitedAnswer.tsx
//
// The grounded answer, rendered honestly (§22/§23): inline `[n]` markers become
// chips tagged by whether they resolve to a footnote the answer defined for
// itself, dangling markers are surfaced (never hidden), and every character of
// answer text is rendered as TEXT by React — never HTML (retrieved content is
// untrusted data, not markup). The parser is the pure UI half of the backend's
// deterministic citation-grounding check (features/research/contracts.ts).

"use client";

import React, { useMemo } from "react";
import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import {
  parseCitedAnswer,
  tokenizeProse,
  type CitedAnswer,
} from "@/features/research/contracts";

export function GroundingBanner({ parsed }: { parsed: CitedAnswer }) {
  let tone: { bg: string; border: string; fg: string };
  let icon: React.ReactNode;
  let message: string;

  if (!parsed.hasCitations) {
    tone = { bg: "rgba(250,204,21,0.10)", border: "#facc1540", fg: "#facc15" };
    icon = <Info size={14} />;
    message = "This answer cites no sources — treat it as unverified.";
  } else if (parsed.dangling.length > 0) {
    tone = { bg: "rgba(239,68,68,0.12)", border: "#ef444440", fg: "#f87171" };
    icon = <AlertTriangle size={14} />;
    const list = parsed.dangling.map((n) => `[${n}]`).join(", ");
    message = `${parsed.dangling.length} citation(s) point to sources that were never listed: ${list}. Those claims are unverified.`;
  } else {
    tone = { bg: "rgba(34,197,94,0.10)", border: "#22c55e40", fg: "#22c55e" };
    icon = <CheckCircle2 size={14} />;
    message = `${parsed.resolved.length} citation(s) — all resolve to a listed source.`;
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.5rem",
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        color: tone.fg,
        borderRadius: "4px",
        padding: "0.6rem 0.85rem",
        fontSize: "0.8rem",
        marginBottom: "1rem",
      }}
    >
      {icon}
      <span>{message}</span>
    </div>
  );
}

export function CiteChip({ n, resolved }: { n: number; resolved: boolean }) {
  return (
    <sup
      title={resolved ? `Resolves to source [${n}]` : `Unresolved — no source [${n}] was listed`}
      style={{
        margin: "0 0.1rem",
        padding: "0 0.25rem",
        borderRadius: "3px",
        fontSize: "0.7rem",
        fontWeight: 700,
        cursor: "help",
        background: resolved ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.18)",
        color: resolved ? "#22c55e" : "#f87171",
        border: `1px solid ${resolved ? "#22c55e55" : "#ef444455"}`,
      }}
    >
      [{n}]{resolved ? "" : "?"}
    </sup>
  );
}

/**
 * Render the answer prose with inline citation chips + the grounding banner.
 * The source rail (backend-resolved citations/uris) is rendered separately by
 * SourceRail — this view owns only the prose + the self-defined footnote check.
 */
export function CitedAnswerView({ answer }: { answer: string }) {
  const parsed = useMemo(() => parseCitedAnswer(answer), [answer]);
  const defined = useMemo(() => new Set(parsed.sources.map((s) => s.n)), [parsed.sources]);

  return (
    <div>
      <GroundingBanner parsed={parsed} />
      <div
        style={{
          fontSize: "0.95rem",
          color: "var(--paper-200)",
          lineHeight: 1.7,
          whiteSpace: "pre-wrap",
        }}
      >
        {parsed.body.split("\n").map((line, i) => (
          <p key={i} style={{ margin: line.trim() ? "0 0 0.75rem" : "0 0 0.35rem" }}>
            {tokenizeProse(line, defined).map((tok, j) =>
              tok.kind === "text" ? (
                <span key={j}>{tok.text}</span>
              ) : (
                <CiteChip key={j} n={tok.n} resolved={tok.resolved} />
              ),
            )}
          </p>
        ))}
      </div>
    </div>
  );
}
