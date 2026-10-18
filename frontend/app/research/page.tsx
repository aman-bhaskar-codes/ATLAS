"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import {
  FlaskConical,
  Search,
  Loader2,
} from "lucide-react";

import { ErrorRow } from "@/components/primitives/ErrorState";
import { useStartResearch } from "@/features/research/mutations";
import { openResearchWindow, researchSessionPath } from "@/lib/research/openWindow";
import { ResearchHistory } from "@/components/research/ResearchHistory";

export default function ResearchPage() {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [blockedId, setBlockedId] = useState<string | null>(null);

  const start = useStartResearch();

  // Launch a session's window; fall back to same-window navigation if blocked.
  const openSession = (id: string) => {
    const result = openResearchWindow(id);
    if (!result.opened) {
      setBlockedId(id);
      router.push(researchSessionPath(id));
    }
  };

  const submit = () => {
    const q = question.trim();
    if (!q || start.isPending) return;
    start.mutate(
      { question: q },
      {
        onSuccess: (rec) => {
          setQuestion("");
          openSession(rec.session_id);
        },
      },
    );
  };

  return (
    <>
      <div className="crumb mb-6">
        <h1>Research overview</h1>
        <p className="platform-description">Ask a question, follow the evidence, and revisit your research sessions.</p>
      </div>

      {/* Composer */}
      <section className="panel" style={{ marginBottom: "1.5rem" }}>
        <div className="section-head" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <FlaskConical size={16} style={{ color: "var(--gold-400)" }} />
          <h2>Research a question</h2>
        </div>
        <div style={{ padding: "1rem" }}>
          <textarea className="resize-none"
            aria-label="Research question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (!e.nativeEvent.isComposing && e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
            }}
            placeholder="e.g. What does recent work say about evaluation of autonomous agents? Cite sources."
            rows={3}
            style={{
              width: "100%",
              background: "var(--ink-850)",
              border: "1px solid var(--line)",
              borderRadius: "4px",
              padding: "0.75rem 1rem",
              color: "var(--paper-100)",
              outline: "none",
              fontSize: "0.9rem",
              resize: "none",
              fontFamily: "inherit",
            }}
          />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.75rem" }}>
            <span style={{ fontSize: "0.7rem", color: "var(--paper-600)" }}>
              Opens a live, streaming research surface · governed orchestrator · ⌘/Ctrl+Enter
            </span>
            <button
              onClick={submit}
              disabled={!question.trim() || start.isPending}
              className="ghost-btn"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.4rem",
                padding: "0.45rem 0.9rem",
                fontSize: "0.85rem",
                borderColor: "var(--gold-500)",
                color: "var(--gold-400)",
                opacity: !question.trim() || start.isPending ? 0.5 : 1,
                cursor: !question.trim() || start.isPending ? "not-allowed" : "pointer",
              }}
            >
              {start.isPending ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
              {start.isPending ? "Starting…" : "Research"}
            </button>
          </div>
          {blockedId && (
            <div style={{ marginTop: "0.6rem", fontSize: "0.75rem", color: "var(--paper-500)" }}>
              Popup was blocked — opened in this window instead.
            </div>
          )}
          {start.isError && (
            <div style={{ marginTop: "0.75rem" }}>
              <ErrorRow error={start.error} onRetry={submit} />
            </div>
          )}
        </div>
      </section>

      <ResearchHistory />

      <style>{`@keyframes spin { to { transform: rotate(360deg); } } .animate-spin { animation: spin 1s linear infinite; }`}</style>
    </>
  );
}
