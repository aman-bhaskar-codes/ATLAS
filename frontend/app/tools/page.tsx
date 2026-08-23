"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { opsApi } from '@/lib/api/client';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';

function healthColor(h: number): string {
  if (h >= 0.8) return 'var(--jade-400)';
  if (h >= 0.5) return 'var(--gold-400)';
  return 'var(--ember-400)';
}

export default function ToolsPage() {
  const { data: tools, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["ops-tools"],
    queryFn: opsApi.tools,
  });

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Tools</strong></div>

      <section className="panel">
        <div className="section-head">
          <h2>Tool Runtime</h2>
          <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
            Registered tools with metadata and live health. All execution flows through the Safety Engine.
          </span>
        </div>

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading tools…</div>
        ) : isError ? (
          <ErrorState title="Failed to load tools" error={error} onRetry={() => void refetch()} />
        ) : tools && tools.length > 0 ? (
          <div style={{ padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {tools.map(tool => (
              <div key={tool.name} style={{ border: '1px solid var(--line)', background: 'var(--ink-850)', padding: '1rem', borderRadius: '4px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.5rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                  <div>
                    <span style={{ fontWeight: 500, color: 'var(--paper-100)' }}>{tool.name}</span>
                    <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)', marginLeft: '0.75rem' }}>{tool.description}</span>
                  </div>
                  <div style={{ display: 'flex', gap: '1rem', fontSize: '0.8rem' }}>
                    <span style={{ color: healthColor(tool.health) }}>health {(tool.health * 100).toFixed(0)}%</span>
                    <span style={{ color: 'var(--paper-500)' }}>~{Math.round(tool.latency_ewma_ms)}ms</span>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
                  {tool.operations.map(op => (
                    <span key={op} style={{ fontSize: '0.75rem', padding: '0.2rem 0.4rem', background: 'var(--ink-900)', border: '1px solid var(--line)', borderRadius: '4px', color: 'var(--paper-500)' }}>
                      {op}
                    </span>
                  ))}
                  {tool.idempotent != null && (
                    <span className="badge" style={{ borderColor: tool.idempotent ? 'var(--jade-500)' : 'var(--ember-500)', color: tool.idempotent ? 'var(--jade-400)' : 'var(--ember-400)' }}>
                      {tool.idempotent ? 'idempotent' : 'non-idempotent'}
                    </span>
                  )}
                  {tool.side_effects && (
                    <span className="badge" style={{ borderColor: 'var(--gold-500)', color: 'var(--gold-400)' }}>side effects</span>
                  )}
                  {tool.supports_rollback && (
                    <span className="badge" style={{ borderColor: 'var(--jade-500)', color: 'var(--jade-400)' }}>rollback</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="No tools registered" description="Tools appear here once registered in the composition root." />
        )}
      </section>
    </>
  );
}
