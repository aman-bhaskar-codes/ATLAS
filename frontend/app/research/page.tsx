"use client";

/**
 * Research home (§17–§19, R5) — the in-chrome composer + history that LAUNCHES a
 * Perplexity-class research session.
 *
 * This page stays inside the Command Center frame. Asking a question starts a
 * background research session (the SAME governed `knowledge` pipeline, no second
 * execution path) and opens the full-window surface at `/research/{sessionId}` —
 * mirroring the IDE workbench launcher, with a same-window fallback when the popup
 * is blocked. Prior sessions are listed so any can be reopened.
 */

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import {
  FlaskConical,
  Search,
  Loader2,
  ExternalLink,
  History,
  CheckCircle2,
  Flag,
  XCircle,
} from "lucide-react";

import { ErrorRow } from "@/components/primitives/ErrorState";
import { useStartResearch } from "@/features/research/mutations";
import { useResearchSessions } from "@/features/research/sessions";
import { openResearchWindow, researchSessionPath } from "@/lib/research/openWindow";
import type { ResearchSessionSummary } from "@/features/research/contracts";

function statusIcon(status: string) {
  if (status === "completed") return <CheckCircle2 size={13} style={{ color: "#22c55e" }} />;
  if (status === "refused") return <Flag size={13} style={{ color: "#facc15" }} />;
  if (status === "failed") return <XCircle size={13} style={{ color: "#f87171" }} />;
  return <Loader2 size={13} className="animate-spin" style={{ color: "var(--gold-400)" }} />;
}

function HistoryRow({ row, onOpen }: { row: ResearchSessionSummary; onOpen: (id: string) => void }) {
  return (
    <button
      onClick={() => onOpen(row.session_id)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.6rem",
        width: "100%",
        textAlign: "left",
        background: "var(--ink-850)",
        border: "1px solid var(--line)",
        borderRadius: "6px",
        padding: "0.6rem 0.75rem",
        cursor: "pointer",
        color: "var(--paper-200)",
      }}
      title="Open this research session"
    >
      {statusIcon(row.status)}
      <span style={{ flex: 1, fontSize: "0.85rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {row.question || "Untitled question"}
      </span>
      <span style={{ fontSize: "0.7rem", color: "var(--paper-600)" }}>
        {row.source_count} src{row.parent_session_id ? " · follow-up" : ""}
      </span>
      <ExternalLink size={12} style={{ color: "var(--paper-500)" }} />
    </button>
  );
}

export default function ResearchPage() {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [blockedId, setBlockedId] = useState<string | null>(null);

  const start = useStartResearch();
  const { data: history } = useResearchSessions();

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
        ATLAS / <strong>Research</strong>
      </div>

      {/* Composer */}
      <section className="panel" style={{ marginBottom: "1.5rem" }}>
        <div className="section-head" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <FlaskConical size={16} style={{ color: "var(--gold-400)" }} />
          <h2>Research a question</h2>
        </div>
        <div style={{ padding: "1rem" }}>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
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
              resize: "vertical",
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

      {/* History */}
      {history && history.sessions.length > 0 && (
        <section className="panel">
          <div className="section-head" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <History size={15} style={{ color: "var(--paper-500)" }} />
            <h2>Recent sessions</h2>
          </div>
          <div style={{ padding: "1rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            {history.sessions.map((row) => (
              <HistoryRow key={row.session_id} row={row} onOpen={openSession} />
            ))}
          </div>
        </section>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } } .animate-spin { animation: spin 1s linear infinite; }`}</style>
    </>
  );
}
