// frontend/components/research/ResearchWorkspace.tsx
//
// The full-window Perplexity-class Research surface for one session. Composes the
// TWO backend truths, never a third: `useResearchSession` (the persisted record —
// question, grounded answer, source rail, terminal status) and `useResearchStream`
// (the live durable phase trace, folded by `projectResearchState`). The stream
// drives the live "Activity" feed while the run is in flight; the record backs the
// settled grounded answer + source rail. Follow-ups navigate the surface to the
// new linked session. Nothing here re-runs retrieval or invents state (§69).

"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { FlaskConical, Loader2, AlertTriangle, ArrowLeft } from "lucide-react";

import { ErrorState } from "@/components/primitives/ErrorState";
import { AtlasApiError } from "@/lib/api/client";
import { useResearchSession } from "@/features/research/sessions";
import { useResearchStream } from "@/features/research/useResearchStream";
import {
  projectResearchState,
  lifecycleFromStatus,
} from "@/features/research/projectResearchState";
import { CitedAnswerView } from "./CitedAnswer";
import { SourceRail } from "./SourceRail";
import { PhaseIndicator } from "./PhaseIndicator";
import { FollowUpComposer } from "./FollowUpComposer";

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "completed"
      ? { fg: "#22c55e", bd: "#22c55e55" }
      : status === "refused"
        ? { fg: "#facc15", bd: "#facc1555" }
        : status === "failed"
          ? { fg: "#f87171", bd: "#ef444455" }
          : { fg: "var(--gold-400)", bd: "var(--gold-500)" };
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.35rem",
        fontSize: "0.72rem",
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        color: tone.fg,
        border: `1px solid ${tone.bd}`,
        borderRadius: "999px",
        padding: "0.15rem 0.6rem",
      }}
    >
      {status === "running" && <Loader2 size={11} className="animate-spin" />}
      {status}
    </span>
  );
}

export function ResearchWorkspace({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const { data: session, isLoading, isError, error, refetch } = useResearchSession(sessionId);
  const { events, status: streamStatus } = useResearchStream(sessionId);

  const projection = useMemo(() => projectResearchState(events), [events]);

  // Prefer the persisted terminal status once the record settles; otherwise show
  // the live-stream lifecycle. Both are the SAME backend truth, just different
  // freshness — never a fabricated status.
  const lifecycle = session ? lifecycleFromStatus(session.status) : projection.lifecycle;
  const running = lifecycle === "running";

  if (isError && error instanceof AtlasApiError && error.status === 503) {
    return (
      <ResearchShell>
        <div className="panel" style={{ padding: "3rem", textAlign: "center" }}>
          <AlertTriangle size={44} style={{ color: "var(--gold-500)", marginBottom: "1rem" }} />
          <h2 style={{ color: "var(--paper-100)" }}>Research is disabled</h2>
          <p style={{ color: "var(--paper-400)", fontSize: "0.9rem" }}>
            The research subsystem is turned off in this deployment&apos;s config.
          </p>
        </div>
      </ResearchShell>
    );
  }

  if (isError && !session) {
    return (
      <ResearchShell>
        <ErrorState title="Could not load this research session" error={error} onRetry={() => void refetch()} />
      </ResearchShell>
    );
  }

  const answerText = session?.answer?.text ?? "";
  const refusalReason = session?.answer?.refusal_reason ?? "";
  const coverageWarning = session?.answer?.coverage_warning ?? "";

  return (
    <ResearchShell>
      {/* Query hero */}
      <header style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.6rem" }}>
          <FlaskConical size={18} style={{ color: "var(--gold-400)" }} />
          <span style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--paper-500)" }}>
            Research
          </span>
          <span style={{ fontSize: "0.72rem", color: "var(--paper-600)" }}>· {streamStatus}</span>
          {session?.parent_session_id && (
            <Link
              href={`/research/${encodeURIComponent(session.parent_session_id)}`}
              style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem", fontSize: "0.72rem", color: "var(--paper-500)", textDecoration: "none" }}
            >
              <ArrowLeft size={11} /> parent question
            </Link>
          )}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem" }}>
          <h1 style={{ fontSize: "1.5rem", fontWeight: 600, color: "var(--paper-100)", margin: 0, lineHeight: 1.3 }}>
            {isLoading && !session ? "Loading…" : session?.question || "Untitled question"}
          </h1>
          {session && <StatusBadge status={session.status} />}
        </div>
      </header>

      {/* Two-column: answer + rail */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 320px", gap: "1.5rem", alignItems: "start" }}>
        {/* Answer column */}
        <main style={{ minWidth: 0 }}>
          {running && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                color: "var(--paper-400)",
                fontSize: "0.85rem",
                marginBottom: "1rem",
              }}
            >
              <Loader2 size={14} className="animate-spin" />
              Investigating — gathering evidence and synthesising a cited answer…
            </div>
          )}

          {coverageWarning && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                background: "rgba(250,204,21,0.10)",
                border: "1px solid #facc1540",
                color: "#facc15",
                borderRadius: "4px",
                padding: "0.6rem 0.85rem",
                fontSize: "0.8rem",
                marginBottom: "1rem",
              }}
            >
              <AlertTriangle size={14} /> {coverageWarning}
            </div>
          )}

          {lifecycle === "failed" ? (
            <div style={{ fontSize: "0.88rem", color: "#f87171" }}>
              {session?.error || "The research run failed without producing an answer."}
            </div>
          ) : lifecycle === "refused" ? (
            <div style={{ fontSize: "0.88rem", color: "#facc15" }}>
              This question was refused honestly — no fabricated answer.
              {refusalReason ? ` Reason: ${refusalReason}` : ""}
            </div>
          ) : answerText ? (
            <CitedAnswerView answer={answerText} />
          ) : !running ? (
            <div style={{ fontSize: "0.85rem", color: "var(--paper-500)" }}>
              No answer was produced.
            </div>
          ) : null}

          {/* Follow-up composer once the run has settled */}
          {session && !running && (
            <div style={{ marginTop: "1.75rem" }}>
              <FollowUpComposer
                sessionId={session.session_id}
                onLaunched={(id) => router.push(`/research/${encodeURIComponent(id)}`)}
              />
            </div>
          )}
        </main>

        {/* Rail column: live activity + sources */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", position: "sticky", top: "1rem" }}>
          <PhaseIndicator projection={projection} />
          <SourceRail sources={session?.sources ?? []} />
        </div>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } } .animate-spin { animation: spin 1s linear infinite; }`}</style>
    </ResearchShell>
  );
}

/** Bare full-window shell — no Command Center chrome (branched off in AppChrome). */
function ResearchShell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--ink-950)",
        color: "var(--paper-100)",
        padding: "2rem clamp(1rem, 5vw, 4rem)",
      }}
    >
      <div style={{ maxWidth: "1100px", margin: "0 auto" }}>{children}</div>
    </div>
  );
}
