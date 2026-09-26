"use client";

import { useEffect, useMemo, useRef } from "react";
import { X } from "lucide-react";
import { useTerminalStream } from "@/features/ide-terminal/useTerminalStream";

/**
 * Read-only live tail of a managed dev process's output (Slice 7). A process id IS
 * a terminal id, so this reuses the terminal SSE client verbatim — there is no
 * separate process stream. `afterSeq=0` replays the session buffer from the start,
 * so opening a process that has been running shows its full output, then live-tails
 * new lines. No input row: a managed process is driven from the Run panel, not typed
 * into here.
 */
export function ProcessOutputView({
  workspaceId,
  processId,
  command,
  onClose,
}: {
  workspaceId: string;
  processId: string;
  command: string;
  onClose: () => void;
}) {
  const { lines, exit, status } = useTerminalStream(workspaceId, processId, 1, 0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => lines, [lines]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [rows, exit]);

  return (
    <div className="wb-term">
      <div className="wb-proc-out-head">
        <code className="wb-proc-out-cmd" title={command}>{command}</code>
        <span className="wb-proc-out-meta">{status}</span>
        <button type="button" className="wb-proc-out-close" onClick={onClose} title="Close output">
          <X size={13} />
        </button>
      </div>
      <div className="wb-term-body" ref={scrollRef}>
        {rows.length === 0 ? (
          <div className="wb-term-hint">Waiting for output…</div>
        ) : (
          rows.map((row) => (
            <div
              key={row.seq}
              className={row.stream === "stderr" ? "wb-term-line wb-term-err" : "wb-term-line"}
            >
              {row.data}
            </div>
          ))
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
              : exit.error === "stopped"
                ? "■ stopped"
                : exit.error
                  ? `✕ ${exit.error}`
                  : `↳ exit ${exit.exit_code ?? "?"}`}
          </div>
        ) : null}
      </div>
    </div>
  );
}
