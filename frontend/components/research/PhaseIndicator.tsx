// frontend/components/research/PhaseIndicator.tsx
//
// The live "thinking" feed: the REAL phase trace a governed run emits, folded by
// projectResearchState. Every row has a real backend origin (§69) — this renders
// what the run is actually DOING (retrieving → rounds → sources_found(n) →
// synthesizing → answer → citations → grounding → terminal), never a fabricated
// progress animation detached from the backend.

"use client";

import {
  CheckCircle2,
  Loader2,
  Search,
  Layers,
  FileText,
  Sparkles,
  Quote,
  ShieldCheck,
  Flag,
  XCircle,
  Circle,
} from "lucide-react";
import type { ReactNode } from "react";
import type { PhaseStep, ResearchProjection } from "@/features/research/projectResearchState";

const PHASE_LABEL: Record<string, string> = {
  started: "Started",
  retrieving: "Retrieving evidence",
  round: "Investigation round",
  sources_found: "Sources found",
  synthesizing: "Synthesizing answer",
  answer: "Drafting answer",
  citations: "Resolving citations",
  grounding: "Checking grounding",
  completed: "Completed",
  refused: "Refused (honest)",
  failed: "Failed",
};

function iconFor(phase: string): ReactNode {
  switch (phase) {
    case "retrieving":
      return <Search size={13} />;
    case "round":
      return <Layers size={13} />;
    case "sources_found":
      return <FileText size={13} />;
    case "synthesizing":
    case "answer":
      return <Sparkles size={13} />;
    case "citations":
      return <Quote size={13} />;
    case "grounding":
      return <ShieldCheck size={13} />;
    case "completed":
      return <CheckCircle2 size={13} />;
    case "refused":
      return <Flag size={13} />;
    case "failed":
      return <XCircle size={13} />;
    default:
      return <Circle size={13} />;
  }
}

function detailFor(step: PhaseStep): string | null {
  const p = step.payload;
  if (step.phase === "sources_found" && typeof p.count === "number") return `${p.count} source(s)`;
  if (step.phase === "citations" && typeof p.count === "number") return `${p.count} citation(s)`;
  if (step.phase === "round" && typeof p.index === "number") return `round ${Number(p.index) + 1}`;
  if (step.phase === "grounding" && typeof p.confidence === "number")
    return `confidence ${(Number(p.confidence) * 100).toFixed(0)}%`;
  if (step.phase === "failed" && typeof p.error === "string") return p.error;
  return null;
}

export function PhaseIndicator({ projection }: { projection: ResearchProjection }) {
  const live = projection.lifecycle === "running";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      <div
        style={{
          fontSize: "0.7rem",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--paper-500)",
          display: "flex",
          alignItems: "center",
          gap: "0.4rem",
        }}
      >
        {live ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
        Activity
      </div>

      <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "0.3rem" }}>
        {projection.phases.map((step) => {
          const detail = detailFor(step);
          const tone =
            step.phase === "failed"
              ? "#f87171"
              : step.phase === "refused"
                ? "#facc15"
                : step.phase === "completed"
                  ? "#22c55e"
                  : "var(--paper-300)";
          return (
            <li
              key={step.seq}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                fontSize: "0.78rem",
                color: tone,
              }}
            >
              <span style={{ display: "inline-flex", color: tone }}>{iconFor(step.phase)}</span>
              <span>{PHASE_LABEL[step.phase] ?? step.phase}</span>
              {detail && <span style={{ color: "var(--paper-600)", fontSize: "0.72rem" }}>· {detail}</span>}
            </li>
          );
        })}
        {projection.phases.length === 0 && (
          <li style={{ fontSize: "0.78rem", color: "var(--paper-600)", fontStyle: "italic" }}>
            Waiting for the run to start…
          </li>
        )}
      </ol>
    </div>
  );
}
