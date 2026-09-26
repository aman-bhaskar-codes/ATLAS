"use client";

import { useState } from "react";
import { SendHorizonal, Loader2 } from "lucide-react";

/**
 * The run composer. Submitting starts a background agent run bound to the
 * workbench's workspace; while a run is live it drives a follow-up (continue).
 * Disabled honestly while a request is in flight or the run is still running.
 */
export function RunComposer({
  mode,
  pending,
  disabled,
  onSubmit,
}: {
  mode: "start" | "continue";
  pending: boolean;
  disabled: boolean;
  onSubmit: (request: string) => void;
}) {
  const [text, setText] = useState("");
  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || pending || disabled) return;
    onSubmit(trimmed);
    setText("");
  };
  return (
    <div className="wb-agent-composer">
      <textarea
        className="wb-agent-input"
        value={text}
        placeholder={
          mode === "start"
            ? "Describe a task for the agent to do in this workspace…"
            : "Send a follow-up to continue this run…"
        }
        rows={3}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="wb-agent-composer-foot">
        <span className="wb-agent-hint">⌘⏎ to send</span>
        <button
          type="button"
          className="wb-save-btn"
          disabled={disabled || pending || text.trim() === ""}
          onClick={submit}
        >
          {pending ? <Loader2 size={12} className="wb-spin" /> : <SendHorizonal size={12} />}
          {mode === "start" ? "Start run" : "Continue"}
        </button>
      </div>
    </div>
  );
}
