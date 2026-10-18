import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { authHeaders, getApiCredential, setApiCredential, websocketProtocols } from "../auth";
import { atlasApi } from "../client";

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("window", Object.assign(new EventTarget(), {
    sessionStorage: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) },
  }));
});
afterEach(() => vi.unstubAllGlobals());

it("sends the session credential on HTTP calls without putting it in URLs", async () => {
  setApiCredential(" test-key ");
  let requestHeaders = new Headers();
  let requestUrl = "";
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    requestUrl = url;
    requestHeaders = new Headers(init.headers);
    return new Response(JSON.stringify([]));
  });
  await atlasApi.approvals();
  expect(requestHeaders.get("Authorization")).toBe("Bearer test-key");
  expect(requestUrl).not.toContain("test-key");
  expect(getApiCredential()).toBe("test-key");
});
it("clears the credential and leaves anonymous requests without Authorization", () => {
  setApiCredential("test-key");
  setApiCredential("");
  expect(new Headers(authHeaders()).has("Authorization")).toBe(false);
  expect(websocketProtocols()).toEqual(["atlas"]);
});
it("offers browser credentials only through a WebSocket subprotocol", () => {
  setApiCredential("test-key");
  expect(websocketProtocols()).toEqual(["atlas", "atlas.api-key.test-key"]);
});
it("rejects credentials that cannot be safely used as WebSocket protocol tokens", () => {
  expect(() => setApiCredential("bad key\nvalue")).toThrow();
  expect(getApiCredential()).toBe("");
});
it("does not access browser storage during server rendering", () => {
  vi.unstubAllGlobals();
  expect(getApiCredential()).toBe("");
});
