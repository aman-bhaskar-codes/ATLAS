import React from "react";
import { CircleSlash } from "lucide-react";

interface EmptyStateProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center p-8 text-center bg-[var(--color-ink-950)] border border-dashed border-[var(--color-line)] rounded-[var(--radius-md)]">
      <div className="flex items-center justify-center w-12 h-12 mb-4 rounded-full bg-[var(--color-ink-850)] text-[var(--color-paper-300)]">
        <CircleSlash className="w-6 h-6" />
      </div>
      <h3 className="text-base font-medium text-[var(--color-paper-100)] mb-1">{title}</h3>
      {description && <p className="text-sm text-[var(--color-paper-300)] max-w-sm mb-4">{description}</p>}
      {action && <div>{action}</div>}
    </div>
  );
}
