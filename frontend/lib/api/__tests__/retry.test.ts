// frontend/lib/api/__tests__/retry.test.ts
//
// THE BUG THIS PINS: `providers.tsx` set no `retry`, so react-query's default of
// three retries applied to every failure — 404s, 403s from the safety engine, and
// schema mismatches included. Four identical requests, and the user waited out the
// full 1s+2s+4s backoff before seeing a state that was knowable immediately.
//
// The truth table below is the whole contract, so it is asserted exhaustively
// rather than by example.

import { describe, expect, it } from "vitest";
import { AtlasApiError, AtlasContractError, AtlasTimeoutError } from "@/lib/api/client";
import { MAX_RETRIES, isRetryable, shouldRetry } from "@/lib/api/retry";

function apiError(status: number, code: string | null = null): AtlasApiError {
  return new AtlasApiError({
    status,
    code,
    detail: "detail",
    requestId: "rid",
    path: "/runtime/status",
  });
}

// [status, retryable] — every status the backend can actually return.
const STATUS_TABLE: Array<[number, boolean]> = [
  [400, false], // malformed request: identical retry, identical failure
  [401, false], // no credential is going to appear between attempts
  [402, false], // budget exceeded: the cap does not lift on a retry
  [403, false], // safety denial or a read-only key
  [404, false],
  [409, false], // idempotency conflict: retrying is the thing that caused it
  [422, false], // validation
  [429, true], //  the one 4xx the server expects you to repeat
  [500, true],
  [502, true],
  [503, true], //  includes the kill switch — cleared by an operator, so worth a retry
  [504, true],
];

describe("shouldRetry: status table", () => {
  it.each(STATUS_TABLE)("status %i -> retry %s", (status, expected) => {
    expect(shouldRetry(0, apiError(status))).toBe(expected);
  });
});

describe("shouldRetry: error kinds", () => {
  it("retries a timeout — a cold Ollama or ChromaDB start is transient", () => {
    expect(shouldRetry(0, new AtlasTimeoutError("/runtime/status", 8000))).toBe(true);
  });

  it("never retries a contract error, at any failure count", () => {
    const error = new AtlasContractError("/runtime/status", "state: invalid enum value");

    expect(shouldRetry(0, error)).toBe(false);
    expect(shouldRetry(1, error)).toBe(false);
  });

  it("retries an unrecognised error — a bare TypeError is a dropped connection", () => {
    expect(shouldRetry(0, new TypeError("Failed to fetch"))).toBe(true);
  });

  it("retries a thrown non-Error value rather than swallowing it", () => {
    expect(shouldRetry(0, "something went wrong")).toBe(true);
    expect(shouldRetry(0, undefined)).toBe(true);
  });
});

describe("shouldRetry: the count", () => {
  it("permits exactly MAX_RETRIES retries", () => {
    // react-query calls the predicate BEFORE incrementing, so the first failure
    // arrives as failureCount 0. Getting this off by one either wastes a request
    // or silently drops a retry.
    const error = apiError(503);
    const verdicts = [0, 1, 2, 3].map((count) => shouldRetry(count, error));

    expect(verdicts).toEqual([true, true, false, false]);
    expect(MAX_RETRIES).toBe(2);
  });

  it("stops at the cap even for a 429", () => {
    expect(shouldRetry(MAX_RETRIES, apiError(429))).toBe(false);
  });
});

describe("isRetryable: what the retry button offers", () => {
  it("is broader than shouldRetry, because a person retries after fixing something", () => {
    // A 404 is not worth an automatic retry, but a human who just created the
    // missing thing should still be offered the button.
    expect(shouldRetry(0, apiError(404))).toBe(false);
    expect(isRetryable(apiError(404))).toBe(true);
  });

  it("excludes only the contract error, which needs a deploy and not a click", () => {
    expect(isRetryable(new AtlasContractError("/x", "boom"))).toBe(false);
  });

  it("offers a retry for a plain undefined error", () => {
    expect(isRetryable(undefined)).toBe(true);
  });
});
