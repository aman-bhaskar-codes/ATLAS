import { API_BASE, AtlasContractError, AtlasTimeoutError, throwForStatus } from "./client";
import { authHeaders, subscribeCredentialChange } from "./auth";
import { shouldRetry } from "./retry";

export interface StreamFailure { error: unknown; retrying: boolean }
type Listener = (event: MessageEvent<string>) => void;

/** Authenticated SSE with a replay cursor and one cancellable connection/retry. */
export class AtlasEventStream {
  onopen: (() => void) | null = null;
  onerror: ((failure: StreamFailure) => void) | null = null;
  private listeners = new Map<string, Set<Listener>>();
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private generation = 0;
  private cursor = "";
  private delay = 500;
  private unsubscribe: () => void;

  constructor(private path: string) {
    this.unsubscribe = subscribeCredentialChange(() => {
      if (this.stopped) return;
      this.cancelAttempt();
      this.delay = 500;
      void this.connect();
    });
    void this.connect();
  }
  addEventListener(type: string, listener: Listener): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }
  close(): void {
    this.stopped = true;
    this.cancelAttempt();
    this.unsubscribe();
  }
  private cancelAttempt(): void {
    this.generation++;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.controller?.abort();
    this.controller = null;
  }
  private async connect(): Promise<void> {
    if (this.stopped) return;
    const generation = ++this.generation;
    const controller = new AbortController();
    this.controller = controller;
    const active = () => !this.stopped && generation === this.generation;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 8000);
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    try {
      const headers = authHeaders({ Accept: "text/event-stream" });
      if (this.cursor) headers["Last-Event-ID"] = this.cursor;
      const url = this.path.startsWith("http") ? this.path : `${API_BASE}${this.path}`;
      const response = await fetch(url, { headers, cache: "no-store", signal: controller.signal });
      clearTimeout(timeout);
      if (!active()) { await response.body?.cancel(); return; }
      if (!response.ok) await throwForStatus(this.path, response);
      if (!response.headers.get("Content-Type")?.toLowerCase().includes("text/event-stream") || !response.body) {
        throw new AtlasContractError(this.path, "expected an event stream");
      }
      reader = response.body.getReader();
      this.onopen?.();
      const decoder = new TextDecoder();
      let buffer = "";
      let data: string[] = [];
      let type = "message";
      let pendingCursor = this.cursor;
      let skipLF = false;
      const line = (value: string) => {
        if (!value) {
          // Commit only a complete frame; an interrupted frame must be replayed.
          this.cursor = pendingCursor;
          if (data.length) {
            const event = new MessageEvent<string>(type, { data: data.join("\n"), lastEventId: this.cursor });
            if (type === "stream_closed") this.close();
            for (const listener of this.listeners.get(type) ?? []) listener(event);
          }
          data = []; type = "message";
          return;
        }
        if (value.startsWith(":")) return;
        const colon = value.indexOf(":");
        const field = colon < 0 ? value : value.slice(0, colon);
        const raw = colon < 0 ? "" : value.slice(colon + 1);
        const content = raw.startsWith(" ") ? raw.slice(1) : raw;
        if (field === "data") data.push(content);
        if (field === "event") type = content || "message";
        if (field === "id" && !content.includes("\0")) pendingCursor = content;
      };
      while (active()) {
        const { done, value } = await reader.read();
        if (!active()) return;
        if (done) throw new TypeError("Event stream disconnected");
        buffer += decoder.decode(value, { stream: true });
        if (skipLF && buffer) {
          if (buffer.startsWith("\n")) buffer = buffer.slice(1);
          skipLF = false;
        }
        while (active()) {
          const match = /[\r\n]/.exec(buffer);
          if (!match) break;
          const index = match.index;
          skipLF = buffer[index] === "\r" && index === buffer.length - 1;
          const width = buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
          const value = buffer.slice(0, index);
          buffer = buffer.slice(index + width);
          line(value);
        }
      }
    } catch (caught) {
      if (!active()) return;
      const error = timedOut ? new AtlasTimeoutError(this.path, 8000) : caught;
      const retrying = shouldRetry(0, error);
      this.onerror?.({ error, retrying });
      if (!active()) return;
      if (!retrying) return; // Wait for a credential change or explicit disposal.
      const delay = this.delay;
      this.delay = Math.min(delay * 2, 10000);
      this.timer = setTimeout(() => { this.timer = null; if (active()) void this.connect(); }, delay);
    } finally {
      clearTimeout(timeout);
      if (reader) { try { await reader.cancel(); } catch {} reader.releaseLock(); }
      if (generation === this.generation) this.controller = null;
    }
  }
}
