"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, TerminalSquare, CircleAlert } from "lucide-react";
import {
  useOpenTerminal,
  useRunTerminalCommand,
  isCommandExecutionDisabled,
} from "@/features/ide-terminal/mutations";
import { useTerminalStream, type TerminalLine } from "@/features/ide-terminal/useTerminalStream";

// A prompt echo carries a fractional seq that sorts just before the command's
// first real chunk (first chunk seq = afterSeq + 1), so the transcript interleaves
// prompts and output in true order without colliding with any real event seq.
interface PromptLine {
  seq: number;
  kind: "prompt";
  text: string;
}
type Row = (TerminalLine & { kind?: undefined }) | PromptLine;

/**
 * Interactive streaming terminal (Slice 6). Opens a governed session lazily, runs
 * a command through the SAME SafetyEngine funnel as any shell dispatch, and tails
 * its output live over SSE. A denied command surfaces honestly as a red exit line
 * — nothing is faked, and there is no second execution path.
 */
export function TerminalView({ workspaceId }: { workspaceId: string }) {
  const open = useOpenTerminal(workspaceId);
  const run = useRunTerminalCommand(workspaceId);
  const openMutate = open.mutate;

  const [terminalId, setTerminalId] = useState<string | null>(null);
  const [runKey, setRunKey] = useState(0);
  const [afterSeq, setAfterSeq] = useState(0);
  const [prompts, setPrompts] = useState<PromptLine[]>([]);
  const [command, setCommand] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const { lines, exit, status, lastSeq, reset } = useTerminalStream(
    workspaceId,
    terminalId,
    runKey,
    afterSeq,
  );

  // Open a session once, on first mount for this workspace. The 503 (no command
  // tool wired) is left in `open.error` and rendered honestly below.
  useEffect(() => {
    openMutate(undefined, { onSuccess: (res) => setTerminalId(res.terminal_id) });
  }, [openMutate]);

  const busy = status === "connecting" || (status === "live" && exit === null && runKey > 0);

  const submit = useCallback(() => {
    const trimmed = command.trim();
    if (!trimmed || !terminalId || busy) return;
    if (trimmed === "clear") {
      reset();
      setPrompts([]);
      setCommand("");
      return;
    }
    // Capture the cursor BEFORE launching so the new stream resumes exactly at
    // this command's first line, past every prior command's buffered output.
    const cursor = lastSeq;
    setAfterSeq(cursor);
    setPrompts((prev) => [...prev, { seq: cursor + 0.5, kind: "prompt", text: trimmed }]);
    setRunKey((k) => k + 1);
    setCommand("");
    run.mutate({ terminalId, command: trimmed });
  }, [command, terminalId, busy, lastSeq, run, reset]);

  const rows: Row[] = useMemo(() => {
    const merged: Row[] = [...prompts, ...lines];
    merged.sort((a, b) => a.seq - b.seq);
    return merged;
  }, [prompts, lines]);

  // Autoscroll to the newest output as it streams in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [rows, exit]);

  if (open.isError && isCommandExecutionDisabled(open.error)) {
    return (
      <div className="wb-term wb-term-msg">
        <CircleAlert size={15} />
        Command execution is not enabled on this ATLAS runtime — no terminal
        available.
      </div>
    );
  }

  const started = run.data?.started;
  const runFailed = run.isSuccess && started === false;

  return (
    <div className="wb-term">
      <div className="wb-term-body" ref={scrollRef}>
        {rows.length === 0 && !open.isPending ? (
          <div className="wb-term-hint">
            Session ready. Type a command (e.g. <code>git status</code>) and press Enter.
            Only allowlisted commands run — everything passes through the safety funnel.
          </div>
        ) : null}
        {rows.map((row) =>
          row.kind === "prompt" ? (
            <div key={`p${row.seq}`} className="wb-term-line wb-term-prompt">
              <span className="wb-term-sigil">$</span>
              {row.text}
            </div>
          ) : (
            <div
              key={row.seq}
              className={row.stream === "stderr" ? "wb-term-line wb-term-err" : "wb-term-line"}
            >
              {row.data}
            </div>
          ),
        )}
        {exit ? (
          <div
            className={
              exit.denied || (exit.exit_code ?? 0) !== 0
                ? "wb-term-line wb-term-exit wb-term-err"
                : "wb-term-line wb-term-exit"
            }
          >
            {exit.denied
              ? `⚠ denied by policy${exit.error ? `: ${exit.error}` : ""}`
              : exit.error
                ? `✕ ${exit.error}`
                : `↳ exit ${exit.exit_code ?? "?"}`}
          </div>
        ) : null}
        {runFailed ? (
          <div className="wb-term-line wb-term-err">
            ⚠ terminal busy — a command is already running in this session.
          </div>
        ) : null}
      </div>

      <div className="wb-term-input-row">
        <span className="wb-term-sigil">$</span>
        <input
          className="wb-term-input"
          value={command}
          placeholder={terminalId ? "Run a command…" : "Opening session…"}
          disabled={!terminalId}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
        />
        {busy ? (
          <Loader2 size={13} className="wb-spin wb-term-status" />
        ) : (
          <TerminalSquare size={13} className="wb-term-status" />
        )}
      </div>
    </div>
  );
}
