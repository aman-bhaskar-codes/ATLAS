import { afterEach, expect, it, vi } from "vitest";
import { AtlasEventStream } from "../stream";
import { AtlasApiError } from "../client";

const streams: AtlasEventStream[] = [];
afterEach(() => { streams.forEach((stream) => stream.close()); streams.length = 0; vi.unstubAllGlobals(); vi.useRealTimers(); });
function response(parts: string[]) {
  return new Response(new ReadableStream({ start(controller) { parts.forEach((part) => controller.enqueue(new TextEncoder().encode(part))); controller.close(); } }), { headers: { "Content-Type": "text/event-stream" } });
}
it("parses split frames, CRLF, multiline data and ignores heartbeat comments", async () => {
  vi.stubGlobal("fetch", async () => response([": hello\r\n\r\nid: 7\r\nevent: task_", "event\r\ndata: first\r\ndata: second\r\n\r\nevent: stream_closed\r\ndata: {}\r\n\r\n"]));
  const stream = new AtlasEventStream("/tasks/a/events/stream"); streams.push(stream);
  const received: MessageEvent[] = [];
  stream.addEventListener("task_event", (event) => received.push(event));
  await vi.waitFor(() => expect(received).toHaveLength(1));
  expect(received[0].data).toBe("first\nsecond");
  expect(received[0].lastEventId).toBe("7");
});
it("resumes transient disconnects from Last-Event-ID and stops all retry work on close", async () => {
  vi.useFakeTimers();
  const headers: Headers[] = [];
  vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => { headers.push(new Headers(init.headers)); return response(["id: 9\nevent: task_event\ndata: {}\n\n"]); });
  const stream = new AtlasEventStream("/tasks/a/events/stream"); streams.push(stream);
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(500);
  expect(headers.length).toBe(2);
  expect(headers[1].get("Last-Event-ID")).toBe("9");
  stream.close();
  await vi.advanceTimersByTimeAsync(20000);
  expect(headers.length).toBe(2);
});
it("surfaces a typed authentication failure without retrying", async () => {
  vi.useFakeTimers();
  let attempts = 0;
  vi.stubGlobal("fetch", async () => { attempts++; return new Response('{"detail":"invalid API key"}', { status: 401 }); });
  const stream = new AtlasEventStream("/tasks/a/events/stream"); streams.push(stream);
  let failure: unknown;
  let retrying: boolean | undefined;
  stream.onerror = (event) => { failure = event.error; retrying = event.retrying; };
  await vi.advanceTimersByTimeAsync(10000);
  expect(failure).toBeInstanceOf(AtlasApiError);
  expect(retrying).toBe(false);
  expect(attempts).toBe(1);
});
it("aborts a pending stream when disposed and ignores its late response", async () => {
  let signal: AbortSignal | undefined;
  let complete!: (response: Response) => void;
  vi.stubGlobal("fetch", (_url: string, init: RequestInit) => { signal = init.signal as AbortSignal; return new Promise<Response>((resolve) => { complete = resolve; }); });
  const stream = new AtlasEventStream("/tasks/a/events/stream"); streams.push(stream);
  let opened = false;
  stream.onopen = () => { opened = true; };
  stream.close();
  expect(signal?.aborted).toBe(true);
  complete(response(["data: stale\n\n"]));
  await Promise.resolve();
  expect(opened).toBe(false);
});
it("does not advance the replay cursor past a partially received event", async () => {
  vi.useFakeTimers();
  const headers: Headers[] = [];
  vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
    headers.push(new Headers(init.headers));
    return response(["id: 8\ndata: delivered\n\nid: 9\ndata: unfinished\n"]);
  });
  const stream = new AtlasEventStream("/events"); streams.push(stream);
  await vi.advanceTimersByTimeAsync(500);
  expect(headers[1].get("Last-Event-ID")).toBe("8");
});
it("parses CR line endings split independently of the following LF", async () => {
  vi.stubGlobal("fetch", async () => response(["id: 3\r", "\ndata: hello\r", "\n\r", "\nevent: stream_closed\ndata: {}\n\n"]));
  const stream = new AtlasEventStream("/events"); streams.push(stream);
  const received: string[] = [];
  stream.addEventListener("message", (event) => received.push(event.data));
  await vi.waitFor(() => expect(received).toEqual(["hello"]));
});
it("restarts a stream rejected by auth when the tab credential changes", async () => {
  vi.useFakeTimers();
  const values = new Map<string, string>();
  vi.stubGlobal("window", Object.assign(new EventTarget(), {
    sessionStorage: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) },
  }));
  const { setApiCredential } = await import("../auth");
  const requests: Headers[] = [];
  vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
    requests.push(new Headers(init.headers));
    return new Response('{"detail":"invalid API key"}', { status: 401 });
  });
  const stream = new AtlasEventStream("/events"); streams.push(stream);
  await vi.advanceTimersByTimeAsync(10000);
  expect(requests).toHaveLength(1);
  setApiCredential("new-key");
  await vi.advanceTimersByTimeAsync(0);
  expect(requests).toHaveLength(2);
  expect(requests[1].get("Authorization")).toBe("Bearer new-key");
});
it("dispatches complete frames terminated by CR even when the connection ends there", async () => {
  vi.stubGlobal("fetch", async () => response(["id: 4\rdata: cr-only\r\r"]));
  const stream = new AtlasEventStream("/events"); streams.push(stream);
  const received: string[] = [];
  stream.addEventListener("message", (event) => received.push(event.data));
  await vi.waitFor(() => expect(received).toEqual(["cr-only"]));
});
