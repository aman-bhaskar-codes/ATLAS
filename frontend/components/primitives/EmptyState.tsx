import React from "react";
import { CircleSlash } from "lucide-react";

interface EmptyStateProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="panel" style={{ textAlign: 'center', padding: '3rem 1rem', borderTop: 'none', border: '1px dashed var(--line)', borderRadius: '8px', background: 'var(--ink-950)' }}>
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '1rem' }}>
        <CircleSlash style={{ width: 32, height: 32, color: 'var(--paper-500)' }} />
      </div>
      <h3 style={{ fontSize: '1.1rem', color: 'var(--paper-100)', marginBottom: '0.5rem' }}>{title}</h3>
      {description && <p className="muted" style={{ maxWidth: '400px', margin: '0 auto 1.5rem', fontSize: '0.85rem' }}>{description}</p>}
      {action && <div>{action}</div>}
    </div>
  );
}
