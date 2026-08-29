"use client";

/**
 * Research Workspace — R9 (§17–§19).
 *
 * Poses a research question to the SAME task API the command centre uses, polls the
 * resulting task, and renders its answer with a citation-grounding view: every inline
 * `[n]` marker is resolved against the answer's own footnote list, and any marker that
 * resolves to nothing is flagged rather than hidden (§22). Answer text is rendered as
 * text, never HTML (§23) — retrieved content is data, not markup.
 */

import React, { useMemo, useState } from "react";
import { FlaskConical, Search, AlertTriangle, CheckCircle2, Loader2, BookOpen, Info } from "lucide-react";

import { ErrorRow } from "@/components/primitives/ErrorState";
import { isTerminal } from "@/lib/api/contracts";
import { useCreateResearch, useResearchTask } from "../../features/research/queries";
import { parseCitedAnswer, tokenizeProse, type CitedAnswer } from "../../features/research/contracts";

// ---------------------------------------------------------------------------
// Grounding banner — the honest verdict about the answer's citations
// ---------------------------------------------------------------------------

function GroundingBanner({ parsed }: { parsed: CitedAnswer }) {
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
    <div style={{
      display: "flex", alignItems: "center", gap: "0.5rem",
      background: tone.bg, border: `1px solid ${tone.border}`, color: tone.fg,
      borderRadius: "4px", padding: "0.6rem 0.85rem", fontSize: "0.8rem", marginBottom: "1rem",
    }}>
      {icon}
      <span>{message}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cited answer — prose with inline citation chips + a resolved source list
// ---------------------------------------------------------------------------

function CiteChip({ n, resolved }: { n: number; resolved: boolean }) {
  return (
    <sup
      title={resolved ? `Resolves to source [${n}]` : `Unresolved — no source [${n}] was listed`}
      style={{
        margin: "0 0.1rem", padding: "0 0.25rem", borderRadius: "3px",
        fontSize: "0.7rem", fontWeight: 700, cursor: "help",
        background: resolved ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.18)",
        color: resolved ? "#22c55e" : "#f87171",
        border: `1px solid ${resolved ? "#22c55e55" : "#ef444455"}`,
      }}
    >
      [{n}]{resolved ? "" : "?"}
    </sup>
  );
}

function CitedAnswerView({ answer }: { answer: string }) {
  const parsed = useMemo(() => parseCitedAnswer(answer), [answer]);
  const defined = useMemo(() => new Set(parsed.sources.map((s) => s.n)), [parsed.sources]);

  return (
    <div>
      <GroundingBanner parsed={parsed} />

      {/* Prose: each line tokenised so inline [n] render as chips, text stays text. */}
      <div style={{ fontSize: "0.9rem", color: "var(--paper-200)", lineHeight: 1.65, whiteSpace: "pre-wrap" }}>
        {parsed.body.split("\n").map((line, i) => (
          <p key={i} style={{ margin: line.trim() ? "0 0 0.6rem" : "0 0 0.3rem" }}>
            {tokenizeProse(line, defined).map((tok, j) =>
              tok.kind === "text"
                ? <span key={j}>{tok.text}</span>
                : <CiteChip key={j} n={tok.n} resolved={tok.resolved} />
            )}
          </p>
        ))}
      </div>

      {/* Sources */}
      {parsed.sources.length > 0 && (
        <div style={{ marginTop: "1.25rem", borderTop: "1px solid var(--line)", paddingTop: "1rem" }}>
          <div style={{
            fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.06em",
            color: "var(--paper-500)", marginBottom: "0.6rem", display: "flex", alignItems: "center", gap: "0.4rem",
          }}>
            <BookOpen size={12} /> Sources
          </div>
          <ol style={{ margin: 0, paddingLeft: "1.25rem", display: "flex", flexDirection: "column", gap: "0.35rem" }}>
            {parsed.sources.map((s) => (
              <li key={s.n} value={s.n} style={{ fontSize: "0.82rem", color: "var(--paper-300)" }}>
                {s.label || <span style={{ fontStyle: "italic", color: "var(--paper-500)" }}>(untitled source)</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main workspace
// ---------------------------------------------------------------------------

export default function ResearchPage() {
  const [question, setQuestion] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);

  const create = useCreateResearch();
  const { data: task, isError: taskLoadError, error: taskError, refetch } = useResearchTask(taskId);

  const submit = () => {
    const q = question.trim();
    if (!q || create.isPending) return;
    create.mutate(q, { onSuccess: (t) => setTaskId(t.id) });
  };

  const running = taskId !== null && (!task || !isTerminal(task.state));

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Research</strong>
      </div>

      {/* Question composer */}
      <section className="panel" style={{ marginBottom: "1.5rem" }}>
        <div className="section-head" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <FlaskConical size={16} style={{ color: "var(--gold-400)" }} />
          <h2>Research a question</h2>
        </div>
        <div style={{ padding: "1rem" }}>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
            placeholder="e.g. What does recent work say about evaluation of autonomous agents? Cite sources."
            rows={3}
            style={{
              width: "100%", background: "var(--ink-850)", border: "1px solid var(--line)",
              borderRadius: "4px", padding: "0.75rem 1rem", color: "var(--paper-100)",
              outline: "none", fontSize: "0.9rem", resize: "vertical", fontFamily: "inherit",
            }}
          />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.75rem" }}>
            <span style={{ fontSize: "0.7rem", color: "var(--paper-600)" }}>
              Runs through the safety-governed orchestrator · ⌘/Ctrl+Enter to send
            </span>
            <button
              onClick={submit}
              disabled={!question.trim() || create.isPending}
              className="ghost-btn"
              style={{
                display: "inline-flex", alignItems: "center", gap: "0.4rem",
                padding: "0.45rem 0.9rem", fontSize: "0.85rem",
                borderColor: "var(--gold-500)", color: "var(--gold-400)",
                opacity: !question.trim() || create.isPending ? 0.5 : 1,
                cursor: !question.trim() || create.isPending ? "not-allowed" : "pointer",
              }}
            >
              {create.isPending ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
              {create.isPending ? "Submitting…" : "Research"}
            </button>
          </div>
          {create.isError && (
            <div style={{ marginTop: "0.75rem" }}>
              <ErrorRow error={create.error} onRetry={submit} />
            </div>
          )}
        </div>
      </section>

      {/* Result */}
      {taskId && (
        <section className="panel">
          <div className="section-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2>Answer</h2>
            <span style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem", fontSize: "0.75rem", color: "var(--paper-500)" }}>
              {running && <Loader2 size={12} className="animate-spin" />}
              <span className="badge">{task?.state ?? "created"}</span>
            </span>
          </div>
          <div style={{ padding: "1rem" }}>
            {taskLoadError && !task ? (
              <ErrorRow error={taskError} onRetry={() => void refetch()} />
            ) : running ? (
              <div style={{ padding: "2rem 1rem", textAlign: "center", color: "var(--paper-500)", fontSize: "0.875rem" }}>
                Investigating — gathering evidence and synthesising a cited answer…
              </div>
            ) : task?.ok && task.answer ? (
              <CitedAnswerView answer={task.answer} />
            ) : (
              // Terminal but not a usable answer: report it honestly, don't fabricate one.
              <div style={{ fontSize: "0.85rem", color: "#f87171" }}>
                {typeof task?.error === "string"
                  ? task.error
                  : task?.error && typeof task.error === "object" && "message" in task.error
                    ? task.error.message
                    : "The task finished without producing an answer."}
              </div>
            )}
          </div>
        </section>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } } .animate-spin { animation: spin 1s linear infinite; }`}</style>
    </>
  );
}


