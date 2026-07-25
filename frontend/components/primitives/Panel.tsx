import React from "react";

interface PanelProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: string;
  action?: React.ReactNode;
}

export function Panel({ className = "", title, action, children, ...props }: PanelProps) {
  return (
    <div
      className={`bg-[var(--color-ink-900)] border border-[var(--color-line)] rounded-[var(--radius-md)] overflow-hidden shadow-sm ${className}`}
      {...props}
    >
      {(title || action) && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-line)] bg-[var(--color-ink-950)]">
          {title && <h3 className="text-sm font-medium text-[var(--color-paper-100)]">{title}</h3>}
          {action && <div>{action}</div>}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}
