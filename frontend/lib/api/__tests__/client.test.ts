// frontend/lib/api/__tests__/client.test.ts
//
// THE BUG THESE PIN: every API failure used to be `new Error("ATLAS API 429: …")`.
// The status lived in a string, so no caller could branch on it. The retry
// predicate could not tell a 404 from a 503, and no query site could tell "the
// safety engine said no" from "the backend is not running".
//
// The tests below therefore assert on the TYPE and on the parsed fields, never on
// the message text — asserting on messages is what made the old code untestable.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AtlasApiError,
  AtlasContractError,
  AtlasTimeoutError,
  atlasApi,
  isAtlasError,
  parseContract,
} from "@/lib/api/client";
import { RuntimeStatusSchema } from "@/lib/api/contracts";
import { z } from "zod";

const VALID_STATUS = {
  schema_version: 1,
  state: "ready",
  version: "0.1.0",
  environment: "dev",
  kill_switch_active: false,
  active_task_count: 0,
  pending_approval_count: 0,
  last_audit_at: null,
};

function respond(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  const payload = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(payload, {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/**
 * Stub global fetch with a fixed response, or with an implementation that sees the
 * arguments. Returns the spy for call assertions.
 *
 * Typed with fetch's own signature rather than `() => Promise<Response>`, because
 * `spy.mock.calls` inherits whatever signature it is given — and `tsconfig.json`
 * includes `**\/*.ts`, so `next build` type-checks this file. A zero-arg spy makes
 * `calls[0]` the empty tuple and every argument assertion below a compile error.
 */
type FetchImpl = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

function stubFetch(response: Response | FetchImpl) {
  const impl: FetchImpl =
    typeof response === "function" ? response : () => Promise.resolve(response);
  const spy = vi.fn(impl);
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("non-2xx responses", () => {
  it("maps a status to AtlasApiError with the status intact", async () => {
    stubFetch(respond(404, { error: "not_found", detail: "Task not found", request_id: "rid-1" }));

    await expect(atlasApi.runtimeStatus()).rejects.toBeInstanceOf(AtlasApiError);
  });

  it("parses the ATLAS envelope into code, detail and requestId", async () => {
    stubFetch(respond(403, { error: "denied", detail: "blocked by policy", request_id: "rid-2" }));

    const error = await atlasApi.runtimeStatus().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AtlasApiError);
    const api = error as AtlasApiError;
    expect(api.status).toBe(403);
    expect(api.code).toBe("denied");
    expect(api.detail).toBe("blocked by policy");
    expect(api.requestId).toBe("rid-2");
  });

  it("tolerates FastAPI's bare {detail} shape, which the 401 path uses", async () => {
    // require_principal raises HTTPException, which does NOT produce the ATLAS
    // envelope. A parser that assumed `error` was present would throw here — while
    // handling a 401 — and surface as an unrelated crash.
    stubFetch(respond(401, { detail: "authentication required (Bearer API key)" }));

    const error = (await atlasApi.runtimeStatus().catch((e: unknown) => e)) as AtlasApiError;

    expect(error).toBeInstanceOf(AtlasApiError);
    expect(error.status).toBe(401);
    expect(error.code).toBeNull();
    expect(error.detail).toBe("authentication required (Bearer API key)");
  });

  it("flattens FastAPI's 422 validation array instead of showing [object Object]", async () => {
    stubFetch(
      respond(422, {
        detail: [
          { loc: ["body", "request"], msg: "Field required", type: "missing" },
          { loc: ["body", "idempotency_key"], msg: "String too short", type: "too_short" },
        ],
      }),
    );

    const error = (await atlasApi.runtimeStatus().catch((e: unknown) => e)) as AtlasApiError;

    expect(error.detail).toBe("Field required; String too short");
  });

  it("falls back to the X-Request-ID header when the body carries no request_id", async () => {
    stubFetch(respond(401, { detail: "invalid API key" }, { "X-Request-ID": "rid-header" }));

    const error = (await atlasApi.runtimeStatus().catch((e: unknown) => e)) as AtlasApiError;

    expect(error.requestId).toBe("rid-header");
  });

  it("survives a non-JSON error body", async () => {
    // What a reverse proxy returns when the backend is down: HTML, not JSON.
    stubFetch(new Response("<html>502 Bad Gateway</html>", { status: 502 }));

    const error = (await atlasApi.runtimeStatus().catch((e: unknown) => e)) as AtlasApiError;

    expect(error).toBeInstanceOf(AtlasApiError);
    expect(error.status).toBe(502);
    expect(error.detail).toContain("502 Bad Gateway");
  });

  it("survives an empty error body", async () => {
    stubFetch(new Response(null, { status: 500 }));

    const error = (await atlasApi.runtimeStatus().catch((e: unknown) => e)) as AtlasApiError;

    expect(error.status).toBe(500);
    expect(error.detail).toBe("no response body");
  });

  it("truncates a long detail so an error cannot flood the UI", async () => {
    stubFetch(respond(500, { error: "internal", detail: "x".repeat(5000) }));

    const error = (await atlasApi.runtimeStatus().catch((e: unknown) => e)) as AtlasApiError;

    expect(error.detail.length).toBe(500);
  });
});

describe("timeouts", () => {
  it("maps the abort to AtlasTimeoutError, not to a generic Error", async () => {
    // A fetch that never settles until the client's own AbortController fires.
    // The signal comes from the init the client passes, so the stub takes it as an
    // argument rather than reaching back into the spy's recorded calls.
    stubFetch(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        }),
    );
    vi.useFakeTimers();

    const pending = atlasApi.runtimeStatus();
    const assertion = expect(pending).rejects.toBeInstanceOf(AtlasTimeoutError);
    await vi.advanceTimersByTimeAsync(8000);

    await assertion;
  });

  it("rethrows a network failure as-is, because it is a different fact", async () => {
    // `fetch` rejects with TypeError when there is nothing listening. That is "the
    // backend is not there", not "the backend was slow", and the UI says so.
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));

    const error = await atlasApi.runtimeStatus().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(TypeError);
    expect(isAtlasError(error)).toBe(false);
  });
});

describe("contract violations", () => {
  it("wraps a schema mismatch in AtlasContractError", async () => {
    stubFetch(respond(200, { state: "ready" })); // missing every other field

    const error = await atlasApi.runtimeStatus().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AtlasContractError);
  });

  it("names the offending fields and the endpoint", async () => {
    stubFetch(respond(200, { ...VALID_STATUS, active_task_count: -1 }));

    const error = (await atlasApi.runtimeStatus().catch((e: unknown) => e)) as AtlasContractError;

    expect(error.path).toBe("/runtime/status");
    expect(error.issues).toContain("active_task_count");
  });

  it("wraps an unparseable 200 body too", async () => {
    stubFetch(new Response("not json at all", { status: 200 }));

    const error = (await atlasApi.runtimeStatus().catch((e: unknown) => e)) as AtlasContractError;

    expect(error).toBeInstanceOf(AtlasContractError);
    expect(error.issues).toContain("not valid JSON");
  });

  it("does not throw a raw ZodError from the list endpoint", async () => {
    // `tasks()` validates `data.items` itself, outside request(). A bare
    // `.parse()` there escaped as an untyped ZodError, which the retry predicate
    // then treated as retryable.
    stubFetch(respond(200, { items: [{ id: "not-a-task" }] }));

    const error = await atlasApi.tasks().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AtlasContractError);
    expect(error).not.toBeInstanceOf(z.ZodError);
  });

  it("does not throw when the list response has no items key", async () => {
    stubFetch(respond(200, {}));

    const error = await atlasApi.tasks().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AtlasContractError);
  });
});

describe("the happy path still works", () => {
  it("returns parsed, defaulted data", async () => {
    stubFetch(respond(200, VALID_STATUS));

    const status = await atlasApi.runtimeStatus();

    expect(status.state).toBe("ready");
    expect(status.version).toBe("0.1.0");
  });

  it("sends the JSON content type and hits the configured base URL", async () => {
    const spy = stubFetch(respond(200, VALID_STATUS));

    await atlasApi.runtimeStatus();

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("http://localhost:8730/api/v1/runtime/status");
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("requests trajectory experiences without the duplicated /api/v1 prefix", async () => {
    // The old path produced /api/v1/api/v1/trajectory/experiences — a guaranteed 404.
    const spy = stubFetch(respond(200, []));

    const { trajectoryApi } = await import("@/lib/api/client");
    await trajectoryApi.experiences(5);

    const [url] = spy.mock.calls[0];
    expect(url).toBe("http://localhost:8730/api/v1/trajectory/experiences?limit=5");
    expect(url).not.toContain("/api/v1/api/v1");
  });
});

describe("parseContract", () => {
  it("returns the parsed value on success", () => {
    expect(parseContract("/x", RuntimeStatusSchema, VALID_STATUS).state).toBe("ready");
  });

  it("reports the root path for a top-level type mismatch", () => {
    const error = (() => {
      try {
        parseContract("/x", z.array(z.string()), "not an array");
      } catch (e: unknown) {
        return e as AtlasContractError;
      }
    })();

    expect(error).toBeInstanceOf(AtlasContractError);
    expect(error?.issues).toContain("(root)");
  });
});
