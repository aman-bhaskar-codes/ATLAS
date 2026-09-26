"use client"; // Error boundaries must be Client Components

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { describeError } from "@/lib/api/errorMessage";

/**
 * Error boundary for the workbench segment. A render-time throw here (e.g. a
 * contract mismatch on a workspace response) would otherwise blank the whole
 * window; this keeps it recoverable. `unstable_retry` (Next 16.2+) re-renders the
 * segment so a backend that has come back recovers without a full reload.
 */
export default function DevError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error("[atlas] workbench error", error);
  }, [error]);

  return (
    <div className="wb-fullscreen-msg">
      <div>
        <AlertTriangle size={40} style={{ color: "var(--danger-400)", marginBottom: "1rem" }} />
        <h2 style={{ color: "var(--paper-100)", margin: "0 0 0.5rem" }}>The workbench stopped rendering</h2>
        <p style={{ color: "var(--paper-300)", maxWidth: "52ch", margin: "0 auto 0.5rem", lineHeight: 1.6 }}>
          {describeError(error)}
        </p>
        <p style={{ color: "var(--paper-500)", fontSize: "0.78rem", maxWidth: "52ch", margin: "0 auto 1.5rem" }}>
          The ATLAS runtime is unaffected by a UI error.
          {error.digest ? ` Server reference: ${error.digest}.` : ""}
        </p>
        <div style={{ display: "flex", gap: "0.6rem", justifyContent: "center" }}>
          <button className="primary" style={{ width: "auto" }} onClick={() => unstable_retry()}>
            Try again
          </button>
          <Link href="/" className="ghost-btn" style={{ display: "inline-flex", alignItems: "center", width: "auto" }}>
            Command Center
          </Link>
        </div>
      </div>
    </div>
  );
}
