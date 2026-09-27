// frontend/components/research/FollowUpComposer.tsx
//
// The Perplexity follow-up box: ask a linked question against the current session.
// It re-enters the SAME governed pipeline via useFollowUp (background) — the new
// session carries `parent_session_id` for thread lineage. On success the caller
// navigates the surface to the new session id (streamable at once).

"use client";

import { useState } from "react";
import { Loader2, CornerDownLeft } from "lucide-react";
import { ErrorRow } from "@/components/primitives/ErrorState";
import { useFollowUp } from "@/features/research/mutations";

export function FollowUpComposer({
  sessionId,
  onLaunched,
  disabled,
}: {
  sessionId: string;
  onLaunched: (newSessionId: string) => void;
  disabled?: boolean;
}) {
  const [question, setQuestion] = useState("");
  const followUp = useFollowUp();

  const submit = () => {
    const q = question.trim();
    if (!q || followUp.isPending || disabled) return;
    followUp.mutate(
      { sessionId, question: q },
      {
        onSuccess: (rec) => {
          setQuestion("");
          onLaunched(rec.session_id);
        },
      },
    );
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          gap: "0.5rem",
          alignItems: "flex-end",
          background: "var(--ink-850)",
          border: "1px solid var(--line)",
          borderRadius: "8px",
          padding: "0.6rem 0.7rem",
        }}
      >
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          placeholder="Ask a follow-up…"
          rows={1}
          disabled={disabled}
          style={{
            flex: 1,
            background: "transparent",
            border: "none",
            outline: "none",
            resize: "none",
            color: "var(--paper-100)",
            fontSize: "0.9rem",
            fontFamily: "inherit",
            lineHeight: 1.5,
          }}
        />
        <button
          onClick={submit}
          disabled={!question.trim() || followUp.isPending || disabled}
          className="ghost-btn"
          title="Ask follow-up · ⌘/Ctrl+Enter"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "0.35rem",
            padding: "0.4rem 0.7rem",
            fontSize: "0.8rem",
            borderColor: "var(--gold-500)",
            color: "var(--gold-400)",
            opacity: !question.trim() || followUp.isPending || disabled ? 0.5 : 1,
            cursor: !question.trim() || followUp.isPending || disabled ? "not-allowed" : "pointer",
          }}
        >
          {followUp.isPending ? <Loader2 size={13} className="animate-spin" /> : <CornerDownLeft size={13} />}
          Ask
        </button>
      </div>
      {followUp.isError && (
        <div style={{ marginTop: "0.5rem" }}>
          <ErrorRow error={followUp.error} onRetry={submit} />
        </div>
      )}
    </div>
  );
}
