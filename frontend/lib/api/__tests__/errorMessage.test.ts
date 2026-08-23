// frontend/lib/api/__tests__/errorMessage.test.ts
//
// The sentences the user actually reads. Two properties matter more than the
// wording: the message must distinguish the failures a user can act on from the
// ones they cannot, and it must never leak internals.

import { describe, expect, it } from "vitest";
import { AtlasApiError, AtlasContractError, AtlasTimeoutError } from "@/lib/api/client";
import { describeError } from "@/lib/api/errorMessage";

function apiError(status: number, code: string | null, detail = "detail", requestId = "rid-9") {
  return new AtlasApiError({ status, code, detail, requestId, path: "/runtime/status" });
}

describe("actionable distinctions", () => {
  it("names the kill switch for a halted 503", () => {
    expect(describeError(apiError(503, "halted"))).toContain("kill switch");
  });

  it("names the safety engine for a denial, and includes its reason", () => {
    const message = describeError(apiError(403, "denied", "tier 4 requires approval"));

    expect(message).toContain("safety engine");
    expect(message).toContain("tier 4 requires approval");
  });

  it("distinguishes a read-only key from a safety denial, though both are 403", () => {
    expect(describeError(apiError(403, "readonly_key"))).toContain("read-only");
    expect(describeError(apiError(403, "readonly_key"))).not.toContain("safety engine");
  });

  it("says the backend is unreachable for a network TypeError", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toContain("8730");
  });

  it("says the backend was slow for a timeout, not that it is missing", () => {
    const message = describeError(new AtlasTimeoutError("/runtime/status", 8000));

    expect(message).toContain("did not respond in time");
  });

  it("says a version mismatch for a contract error", () => {
    const message = describeError(new AtlasContractError("/runtime/status", "state: bad enum"));

    expect(message).toContain("versions may not match");
  });
});

describe("what must never appear", () => {
  it("does not surface the zod issues, which name internal fields", () => {
    const message = describeError(new AtlasContractError("/runtime/status", "kill_switch_active: expected boolean"));

    expect(message).not.toContain("kill_switch_active");
  });

  it("does not surface the URL for a 500", () => {
    // `error.message` contains the path; the user-facing sentence must not, or the
    // internal API shape ends up on screen.
    const message = describeError(apiError(500, "internal", "unexpected server error"));

    expect(message).not.toContain("/runtime/status");
  });

  it("keeps the request id for a 500, which is the one internal detail worth showing", () => {
    expect(describeError(apiError(500, "internal"))).toContain("rid-9");
  });

  it("omits the request id when there is none rather than printing null", () => {
    const message = describeError(
      new AtlasApiError({ status: 500, code: null, detail: "boom", requestId: null, path: "/x" }),
    );

    expect(message).not.toContain("null");
  });
});

describe("degenerate inputs", () => {
  it("returns an empty string for no error, so callers can render nothing", () => {
    expect(describeError(undefined)).toBe("");
    expect(describeError(null)).toBe("");
  });

  it("passes a plain string through — existing call sites pass literals", () => {
    expect(describeError("Failed to load tools")).toBe("Failed to load tools");
  });

  it("stringifies a thrown non-Error instead of rendering [object Object]", () => {
    expect(describeError(42)).toBe("42");
  });
});
