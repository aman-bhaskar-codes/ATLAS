import { afterEach, beforeEach, expect, it, vi } from "vitest";
// A minimal hook host lets the real transport callbacks run without a DOM.
const host = vi.hoisted(() => ({ states: [] as unknown[], effects: [] as (() => void | (() => void))[], cleanups: [] as (() => void)[] }));
vi.mock("react", () => ({
  useState: (initial: unknown) => { const slot = host.states.length; host.states.push(initial); return [initial, (value: unknown) => { host.states[slot] = value; }]; },
  useRef: (current: unknown) => ({ current }),
  useCallback: (callback: unknown) => callback,
  useEffect: (effect: () => void | (() => void)) => host.effects.push(effect),
}));
import { useWebSocket } from "../useWebSocket";
import { setApiCredential } from "../../api/auth";
class Socket {
  static OPEN = 1;
  static CONNECTING = 0;
  static all: Socket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  sent: string[] = [];
  constructor(readonly url: string, readonly protocols?: string[]) { Socket.all.push(this); }
  send(message: string) { this.sent.push(message); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  finish(code = 1006) { this.onclose?.({ code, reason: "" }); }
}
function Mount() {
  const hook = useWebSocket("ws://localhost:8730/ws/memory");
  host.effects.forEach((effect) => { const cleanup = effect(); if (cleanup) host.cleanups.push(cleanup); });
  return hook;
}
beforeEach(() => {
  vi.useFakeTimers(); Socket.all = []; host.states = []; host.effects = []; host.cleanups = [];
  const values = new Map<string, string>();
  vi.stubGlobal("window", Object.assign(new EventTarget(), { sessionStorage: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  } }));
  vi.stubGlobal("WebSocket", Socket);
});
afterEach(() => { host.cleanups.forEach((cleanup) => cleanup()); vi.unstubAllGlobals(); vi.useRealTimers(); });
it("authenticates a socket through protocols, keeping the key out of its URL", () => {
  setApiCredential("tab-key"); Mount();
  expect(Socket.all[0].protocols).toEqual(["atlas", "atlas.api-key.tab-key"]);
  expect(Socket.all[0].url).not.toContain("tab-key");
});
it("keeps a replacement socket alive when the old socket closes late", () => {
  const hook = Mount(); const old = Socket.all[0]; old.open();
  hook.reconnect();
  expect(Socket.all).toHaveLength(2);
  const next = Socket.all[1]; next.open(); old.finish(); hook.send("current");
  expect(next.sent).toEqual(["current"]);
  expect(host.states[1]).toBe("connected");
});
it("ignores messages from a disposed socket and cancels its retry timer", async () => {
  Mount(); const old = Socket.all[0]; old.finish();
  host.cleanups.forEach((cleanup) => cleanup());
  old.onmessage?.({ data: '{"type":"stale"}' });
  await vi.advanceTimersByTimeAsync(60000);
  expect(host.states[0]).toBeNull();
  expect(Socket.all).toHaveLength(1);
});
it("replaces the socket immediately when the tab credential changes", () => {
  Mount(); const old = Socket.all[0]; old.open(); setApiCredential("rotated-key");
  expect(old.readyState).toBe(3);
  expect(Socket.all).toHaveLength(2);
  expect(Socket.all[1].protocols).toEqual(["atlas", "atlas.api-key.rotated-key"]);
});
it("waits for a corrected key after a policy close instead of hammering auth", async () => {
  Mount(); Socket.all[0].finish(1008);
  await vi.advanceTimersByTimeAsync(60000);
  expect(Socket.all).toHaveLength(1);
  setApiCredential("corrected");
  expect(Socket.all).toHaveLength(2);
});
it("bounds failed handshakes and resets that budget after a successful open", async () => {
  Mount();
  Socket.all[0].finish();
  await vi.advanceTimersByTimeAsync(1000);
  Socket.all[1].finish();
  await vi.advanceTimersByTimeAsync(2000);
  Socket.all[2].finish();
  await vi.advanceTimersByTimeAsync(60000);
  expect(Socket.all).toHaveLength(3);
  setApiCredential("fixed");
  expect(Socket.all).toHaveLength(4);
  Socket.all[3].open(); Socket.all[3].finish();
  await vi.advanceTimersByTimeAsync(1000);
  expect(Socket.all).toHaveLength(5);
});
it("keeps a deliberately closed socket stopped when credentials change", () => {
  const hook = Mount(); hook.close(); setApiCredential("rotated");
  expect(Socket.all).toHaveLength(1);
  hook.reconnect();
  expect(Socket.all).toHaveLength(2);
});
