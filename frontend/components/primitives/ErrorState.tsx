import React from "react";
import { AlertCircle } from "lucide-react";

interface ErrorStateProps {
  title?: string;
  error?: string | Error;
  onRetry?: () => void;
}

export function ErrorState({ title = "An error occurred", error, onRetry }: ErrorStateProps) {
  const errorMessage = error instanceof Error ? error.message : error;

  return (
    <div style={{ padding: '1rem', background: 'oklch(30% .1 22 / .15)', border: '1px solid oklch(68% .18 22 / .38)', borderRadius: '8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', color: 'var(--danger-400)', marginBottom: '0.5rem' }}>
        <AlertCircle style={{ width: 20, height: 20, marginRight: '8px' }} />
        <h3 style={{ fontSize: '0.95rem', fontWeight: 500, margin: 0 }}>{title}</h3>
      </div>
      {errorMessage && <p style={{ fontSize: '0.85rem', color: 'var(--paper-300)', margin: '0 0 1rem 0' }}>{errorMessage}</p>}
      {onRetry && (
        <button className="ghost-btn kill" onClick={onRetry} style={{ height: '32px', fontSize: '0.8rem' }}>
          Try Again
        </button>
      )}
    </div>
  );
}
