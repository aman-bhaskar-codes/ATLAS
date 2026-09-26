// frontend/features/ide-terminal/useTerminalStream.ts
//
// Live SSE client for a terminal session's output
// (GET /ide/workspaces/{id}/terminal/{tid}/stream). The backend frames named
// events — `connected`, `chunk` (with `id: {seq}`), `exit` (with `id: {seq}`),
// `stream_closed` — and resumes from the `Last-Event-ID` header on a browser
// reconnect.
//
// WHY a per-command cursor (`after`) but a CUMULATIVE line buffer: a terminal
// SESSION outlives one command — its seq counter keeps advancing across commands,
// and each command's SSE stream returns at ITS OWN exit event. So every command
// opens a FRESH stream at the cursor captured right before it started (`afterSeq`),
// while the accumulated transcript is preserved across those reconnections. Lines
// are deduped by their globally-unique seq, so both the browser's own
// `Last-Event-ID` replay and a fresh per-command stream are idempotent.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TerminalChunkSchema, TerminalExitSchema, type TerminalExit } from "./contracts";

const API_BASE =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

export type TerminalStreamStatus = "idle" | "connecting" | "live" | "reconnecting" | "closed";

/** One rendered output line, tagged by stream so the view can style stderr. */
export interface TerminalLine {
  seq: number;
  stream: string; // "stdout" | "stderr"
  data: string;
}

export interface TerminalStream {
  lines: TerminalLine[];
  exit: TerminalExit | null;
  status: TerminalStreamStatus;
  /** The highest seq observed — the cursor to resume the NEXT command after. */
  lastSeq: number;
  /** Clear the transcript (e.g. on `clear`), keeping the session id/cursor. */
  reset: () => void;
}

/**
 * Stream output for the command launched at `afterSeq` in
 * `(workspaceId, terminalId)`. Each distinct `runKey` opens a fresh connection at
 * `afterSeq` for the command just started; the transcript accumulates across runs
 * until `reset()`. Pass `terminalId === null` before a session is opened.
 */
export function useTerminalStream(
  workspaceId: string,
  terminalId: string | null,
  runKey: number,
  afterSeq: number,
): TerminalStream {
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [exit, setExit] = useState<TerminalExit | null>(null);
  const [status, setStatus] = useState<TerminalStreamStatus>("idle");
  const [lastSeq, setLastSeq] = useState(0);
  const lastSeqRef = useRef(0);
  // seq dedup persists across per-command reconnects so the cumulative buffer
  // never double-appends a replayed line.
  const seenRef = useRef<Set<number>>(new Set());
  const [trackedKey, setTrackedKey] = useState<string>(`${terminalId ?? ""}:${runKey}`);

  const reset = useCallback(() => {
    setLines([]);
    setExit(null);
    seenRef.current = new Set();
  }, []);

  // A new command (or a first-opened session) resets the previous command's
  // terminal `exit` marker and the transport status. Adjusted DURING render (the
  // documented React pattern for deriving state from changed inputs), NOT in an
  // effect — so we never trip the set-state-in-effect cascade and the old exit
  // line never flashes on the next command.
  const key = `${terminalId ?? ""}:${runKey}`;
  if (key !== trackedKey) {
    setTrackedKey(key);
    setExit(null);
    setStatus(terminalId ? "connecting" : "idle");
  }

  useEffect(() => {
    if (!terminalId) return;

    const url =
      `${API_BASE}/ide/workspaces/${encodeURIComponent(workspaceId)}` +
      `/terminal/${encodeURIComponent(terminalId)}/stream?after=${afterSeq}`;
    const source = new EventSource(url);
    let closedByUs = false;

    const bumpSeq = (seq: number) => {
      if (seq > lastSeqRef.current) {
        lastSeqRef.current = seq;
        setLastSeq(seq);
      }
    };

    source.addEventListener("connected", () => setStatus("live"));

    source.addEventListener("chunk", (msg) => {
      let json: unknown;
      try {
        json = JSON.parse((msg as MessageEvent).data);
      } catch {
        return;
      }
      const parsed = TerminalChunkSchema.safeParse(json);
      if (!parsed.success) return;
      const seq = Number((msg as MessageEvent).lastEventId);
      if (!Number.isFinite(seq) || seenRef.current.has(seq)) return;
      seenRef.current.add(seq);
      bumpSeq(seq);
      setStatus("live");
      setLines((prev) => [...prev, { seq, stream: parsed.data.stream, data: parsed.data.data }]);
    });

    source.addEventListener("exit", (msg) => {
      let json: unknown;
      try {
        json = JSON.parse((msg as MessageEvent).data);
      } catch {
        return;
      }
      const parsed = TerminalExitSchema.safeParse(json);
      if (!parsed.success) return;
      const seq = Number((msg as MessageEvent).lastEventId);
      if (Number.isFinite(seq)) bumpSeq(seq);
      setExit(parsed.data);
    });

    source.addEventListener("stream_closed", () => {
      closedByUs = true;
      source.close();
      setStatus("closed");
    });

    source.onerror = () => {
      if (!closedByUs) setStatus("reconnecting");
    };

    return () => {
      closedByUs = true;
      source.close();
    };
    // Keyed on runKey (+ ids): a new command launch reconnects at its cursor.
    // afterSeq is read at connect time and intentionally not a trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, terminalId, runKey]);

  return useMemo(
    () => ({ lines, exit, status, lastSeq, reset }),
    [lines, exit, status, lastSeq, reset],
  );
}
