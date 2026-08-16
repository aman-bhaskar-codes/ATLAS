"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { opsApi } from '@/lib/api/client';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';

export default function ModelsPage() {
  const models = useQuery({ queryKey: ["ops-models"], queryFn: () => opsApi.models(true) });
  const providers = useQuery({ queryKey: ["ops-providers"], queryFn: opsApi.providers });

  const err = models.error ?? providers.error;

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Models &amp; Providers</strong></div>

      {err ? (
        <ErrorState title="Failed to load models" error={err} />
      ) : (
        <>
          <section className="panel" style={{ marginBottom: '2rem' }}>
            <div className="section-head">
              <h2>Providers</h2>
            </div>
            {providers.isLoading ? (
              <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading providers…</div>
            ) : providers.data && providers.data.length > 0 ? (
              <div style={{ display: 'flex', gap: '1rem', padding: '1rem', flexWrap: 'wrap' }}>
                {providers.data.map(p => (
                  <div key={p.name} style={{ border: '1px solid var(--line)', background: 'var(--ink-850)', padding: '0.75rem 1rem', borderRadius: '4px', minWidth: '180px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
                      <span style={{ fontWeight: 500, color: 'var(--paper-100)' }}>{p.name}</span>
                      <span className="badge" style={{
                        borderColor: p.available ? 'var(--jade-500)' : 'var(--ember-500)',
                        color: p.available ? 'var(--jade-400)' : 'var(--ember-400)',
                      }}>
                        {p.available ? 'available' : 'unreachable'}
                      </span>
                    </div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                      {p.is_local ? 'local (free)' : 'cloud'}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title="No providers" description="Providers register at startup from configuration." />
            )}
          </section>

          <section className="panel">
            <div className="section-head">
              <h2>Models</h2>
              <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                From config/models.yaml. Selection is capability-based, health-aware, budget-governed.
              </span>
            </div>
            {models.isLoading ? (
              <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading models…</div>
            ) : models.data && models.data.length > 0 ? (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                    <th style={{ padding: '0.5rem 1rem' }}>Model</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Provider</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Context</th>
                    <th style={{ padding: '0.5rem 1rem' }}>$/1M out</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Latency</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Capabilities</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {models.data.map(m => (
                    <tr key={m.id} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-100)' }}>{m.id}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>{m.provider}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>{(m.context_length / 1000).toFixed(0)}k</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>${m.usd_per_1m_output.toFixed(2)}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>~{(m.latency_estimate_ms / 1000).toFixed(1)}s</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-500)', fontSize: '0.75rem' }}>
                        {m.capabilities.join(', ')}
                        {m.supports_tool_calling && <span style={{ color: 'var(--jade-400)', marginLeft: '0.35rem' }}>+tools</span>}
                      </td>
                      <td style={{ padding: '0.5rem 1rem' }}>
                        <span className="badge" style={{
                          borderColor: m.enabled ? 'var(--jade-500)' : 'var(--line)',
                          color: m.enabled ? 'var(--jade-400)' : 'var(--paper-500)',
                        }}>
                          {m.enabled ? 'enabled' : 'disabled'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState title="No models configured" description="Add models to config/models.yaml." />
            )}
          </section>
        </>
      )}
    </>
  );
}
