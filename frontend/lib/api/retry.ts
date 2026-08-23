// frontend/lib/api/retry.ts
//
// The retry predicate for every react-query fetch.
//
// THE BUG THIS FIXES: `providers.tsx` set no `retry`, so react-query used its
// default of 3 retries for EVERY failure. A 404 was requested four times, and so
// was a 403 from the safety engine and a schema mismatch — none of which can
// change between attempts. On top of the wasted requests, the user waited through
// the full exponential backoff (1s + 2s + 4s) before seeing an error state that
// was knowable immediately.
//
// The rules, and why each one:
//
//   4xx except 429   never retried — the request itself is wrong, so the same
//                    request will fail identically. 401/403 in particular would
//                    hammer the API with credentials that are not going to
//                    improve.
//   429              retried: the server explicitly said "later". This is the one
//                    4xx where the same request is expected to succeed on a
//                    retry. (Retry-After is not honoured here; react-query's
//                    backoff is the approximation.)
//   5xx              retried — a crashed request handler or a restarting backend
//                    is the classic transient.
//   timeout          retried — a slow first response is often a cold
//                    ChromaDB/Ollama warm-up.
//   contract error   never retried — a payload that does not match the schema
//                    will not match it on the third attempt either. Retrying
//                    hides a real backend/frontend version skew behind a delay.
//   anything else     retried — "Failed to fetch" (a TypeError from the network
//                    layer, not an AtlasError) is what a restarting backend looks
//                    like from the browser.

import { AtlasApiError, AtlasContractError } from "./client";

/** Retries allowed per query, on top of the initial attempt. */
export const MAX_RETRIES = 2;

/**
 * react-query's `retry` option.
 *
 * `failureCount` is the number of failures BEFORE this one is counted, so it is
 * 0 on the first failure — `failureCount < MAX_RETRIES` therefore permits
 * exactly MAX_RETRIES retries, matching the meaning of `retry: 2`.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) return false;
  if (error instanceof AtlasContractError) return false;
  if (error instanceof AtlasApiError) {
    if (error.status === 429) return true;
    return error.status >= 500;
  }
  return true;
}

/**
 * Whether a failure is worth offering a "retry" button for.
 *
 * Deliberately broader than `shouldRetry`: automatic retries are about not
 * wasting requests, but a person clicking retry has usually just fixed something
 * (started the backend, approved a task), so the only pointless case is a
 * contract mismatch — which needs a deploy, not a click.
 */
export function isRetryable(error: unknown): boolean {
  return !(error instanceof AtlasContractError);
}
