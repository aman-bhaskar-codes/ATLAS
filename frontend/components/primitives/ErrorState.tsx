import React from "react";
import { AlertCircle } from "lucide-react";
import { describeError } from "@/lib/api/errorMessage";
import { isRetryable } from "@/lib/api/retry";

interface ErrorStateProps {
  title?: string;
  /**
   * Anything that was thrown. `unknown` rather than `string | Error` because the
   * useful distinctions (status, code, request id) live on AtlasApiError, and
   * `describeError` is what knows how to read them — a caller narrowing to
   * `Error` first would lose exactly that.
   */
  error?: unknown;
  onRetry?: () => void;
}

export function ErrorState({ title = "An error occurred", error, onRetry }: ErrorStateProps) {
  const errorMessage = describeError(error);
  // A contract mismatch will not fix itself, so offering "Try Again" for one is a
  // lie about what the button can do.
  const showRetry = onRetry !== undefined && isRetryable(error);

  return (
    <div style={{ padding: '1rem', background: 'oklch(30% .1 22 / .15)', border: '1px solid oklch(68% .18 22 / .38)', borderRadius: '8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', color: 'var(--danger-400)', marginBottom: '0.5rem' }}>
        <AlertCircle style={{ width: 20, height: 20, marginRight: '8px' }} />
        <h3 style={{ fontSize: '0.95rem', fontWeight: 500, margin: 0 }}>{title}</h3>
      </div>
      {errorMessage && <p style={{ fontSize: '0.85rem', color: 'var(--paper-300)', margin: '0 0 1rem 0' }}>{errorMessage}</p>}
      {showRetry && (
        <button className="ghost-btn kill" onClick={onRetry} style={{ height: '32px', fontSize: '0.8rem' }}>
          Try Again
        </button>
      )}
    </div>
  );
}

/**
 * A single-line variant for panels and table bodies, where the boxed ErrorState
 * would dominate a small card.
 */
export function ErrorRow({ error, onRetry }: { error?: unknown; onRetry?: () => void }) {
  return (
    <div
      role="status"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        padding: '0.6rem 0',
        fontSize: '0.8rem',
        color: 'var(--paper-500)',
      }}
    >
      <AlertCircle style={{ width: 15, height: 15, color: 'var(--danger-400)', flexShrink: 0 }} />
      <span>{describeError(error) || "Couldn't load."}</span>
      {onRetry !== undefined && isRetryable(error) && (
        <button
          className="ghost-btn"
          onClick={onRetry}
          style={{ height: '24px', fontSize: '0.72rem', padding: '0 0.5rem' }}
        >
          Retry
        </button>
      )}
    </div>
  );
}
