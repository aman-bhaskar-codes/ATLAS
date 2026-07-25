import React from "react";
import { AlertCircle } from "lucide-react";
import { Button } from "./Button";

interface ErrorStateProps {
  title?: string;
  error?: string | Error;
  onRetry?: () => void;
}

export function ErrorState({ title = "An error occurred", error, onRetry }: ErrorStateProps) {
  const errorMessage = error instanceof Error ? error.message : error;

  return (
    <div className="flex flex-col items-start p-4 bg-red-950/20 border border-red-900/50 rounded-[var(--radius-md)]">
      <div className="flex items-center text-[var(--color-danger-400)] mb-2">
        <AlertCircle className="w-5 h-5 mr-2" />
        <h3 className="font-medium">{title}</h3>
      </div>
      {errorMessage && <p className="text-sm text-red-200/70 mb-3">{errorMessage}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try Again
        </Button>
      )}
    </div>
  );
}
