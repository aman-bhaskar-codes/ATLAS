// frontend/lib/api/errorMessage.ts
//
// One place that turns a thrown value into a sentence a person can act on.
//
// WHY this is not inlined at each call site: the useful information differs per
// failure (a 403 has a safety reason, a 500 has a request id, a contract error has
// neither and needs a deploy), and thirty call sites each guessing at that
// produced either `error.message` — which carried the raw URL and status code — or
// a flat "Something went wrong". Formatting lives here; the primitives just render
// what this returns.
//
// A rule that holds for every branch below: never surface a stack trace, a raw
// URL, or a header value. The backend already truncates `detail` and keeps
// tracebacks server-side; this must not undo that.

import { AtlasApiError, AtlasContractError, AtlasTimeoutError } from "./client";

function describeApiError(error: AtlasApiError): string {
  const { status, code, detail, requestId } = error;

  if (status === 401) {
    return "The backend requires an API key. This UI does not send one — start ATLAS without ATLAS_API_KEYS for local use.";
  }
  if (status === 403 && code === "readonly_key") {
    return "This API key is read-only, so the action was refused.";
  }
  if (status === 403) {
    return `The safety engine refused this action: ${detail}`;
  }
  if (status === 402) {
    return `Budget limit reached: ${detail}`;
  }
  if (status === 404) {
    return "Not found — it may have been deleted, or never existed.";
  }
  if (status === 429) {
    return "Too many requests. ATLAS is throttling this client; it will retry shortly.";
  }
  if (status === 503 && code === "halted") {
    return "The kill switch is active — new actions are blocked until it is cleared.";
  }
  if (status >= 500) {
    // The request id is the only thing that connects this screen to a server log
    // line, which is exactly what someone debugging needs.
    return requestId
      ? `The backend failed to handle this request (request ${requestId}).`
      : "The backend failed to handle this request.";
  }
  return detail;
}

/** A short, user-facing description of any thrown value. */
export function describeError(error: unknown): string {
  if (error === null || error === undefined) return "";
  if (typeof error === "string") return error;

  if (error instanceof AtlasTimeoutError) {
    return "The backend did not respond in time. It may still be starting up.";
  }
  if (error instanceof AtlasContractError) {
    return "The backend sent data this build does not recognise. The frontend and backend versions may not match.";
  }
  if (error instanceof AtlasApiError) {
    return describeApiError(error);
  }
  if (error instanceof TypeError) {
    // What `fetch` rejects with when there is nothing listening, or when a
    // cross-origin response arrives without CORS headers.
    return "Could not reach the ATLAS backend. Check that it is running on port 8730.";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
