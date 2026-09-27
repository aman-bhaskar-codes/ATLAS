// frontend/components/research/SourceRail.tsx
//
// The Perplexity-style source rail: the sources the fabric actually retrieved,
// each a numbered card linking to its origin. These come from the persisted
// session record (backend-resolved, may exceed the cited set) — NOT invented on
// the client (§69). Titles/quotes are untrusted retrieved data, rendered as text
// (§23); the link opens with rel="noopener noreferrer" and never runs page script.

"use client";

import { BookOpen, ExternalLink } from "lucide-react";
import type { ResearchSource } from "@/features/research/contracts";

function hostOf(uri: string): string {
  try {
    return new URL(uri).host;
  } catch {
    return uri;
  }
}

export function SourceRail({ sources }: { sources: readonly ResearchSource[] }) {
  return (
    <aside
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.6rem",
      }}
    >
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
        <BookOpen size={12} /> Sources {sources.length > 0 ? `(${sources.length})` : ""}
      </div>

      {sources.length === 0 ? (
        <div style={{ fontSize: "0.8rem", color: "var(--paper-600)", fontStyle: "italic" }}>
          No sources retrieved yet.
        </div>
      ) : (
        <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {sources.map((s, i) => (
            <li
              key={`${s.uri}-${i}`}
              style={{
                background: "var(--ink-850)",
                border: "1px solid var(--line)",
                borderRadius: "6px",
                padding: "0.6rem 0.7rem",
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: "0.4rem" }}>
                <span style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--gold-400)" }}>
                  {i + 1}
                </span>
                <span style={{ fontSize: "0.82rem", color: "var(--paper-200)", fontWeight: 500 }}>
                  {s.title || <span style={{ fontStyle: "italic", color: "var(--paper-500)" }}>(untitled source)</span>}
                </span>
              </div>
              {s.uri && (
                <a
                  href={s.uri}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "0.3rem",
                    marginTop: "0.25rem",
                    fontSize: "0.72rem",
                    color: "var(--paper-500)",
                    textDecoration: "none",
                  }}
                >
                  <ExternalLink size={11} /> {hostOf(s.uri)}
                </a>
              )}
              {s.quote && (
                <p
                  style={{
                    margin: "0.4rem 0 0",
                    fontSize: "0.76rem",
                    color: "var(--paper-400)",
                    lineHeight: 1.5,
                    borderLeft: "2px solid var(--line)",
                    paddingLeft: "0.5rem",
                  }}
                >
                  {s.quote}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
