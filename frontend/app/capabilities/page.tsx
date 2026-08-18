"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { atlasApi, providersApi } from '@/lib/api/client';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';

export default function CapabilitiesPage() {
  const { data: capabilities, isLoading, isError, error } = useQuery({
    queryKey: ["capabilities"],
    queryFn: atlasApi.capabilities,
  });
  const matrix = useQuery({ queryKey: ['capability-matrix'], queryFn: providersApi.capabilityMatrix });

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Capabilities</strong>
      </div>

      {/* Capability × Cost Class Matrix */}
      {matrix.data && Object.keys(matrix.data.matrix).length > 0 && (
        <section className="panel" style={{ marginBottom: '1.5rem' }}>
          <div className="section-head">
            <h2>Capability Coverage Matrix</h2>
            <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
              {matrix.data.total_models} models · Shows which capabilities are available at each cost tier
            </span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                  <th style={{ padding: '0.5rem 1rem', minWidth: '150px' }}>Capability</th>
                  <th style={{ padding: '0.5rem 1rem' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--jade-500)' }} />
                      Local ($0)
                    </span>
                  </th>
                  <th style={{ padding: '0.5rem 1rem' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#d97706' }} />
                      Free Quota
                    </span>
                  </th>
                  <th style={{ padding: '0.5rem 1rem' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--ember-500)' }} />
                      Paid
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(matrix.data.matrix)
                  .sort(([, a], [, b]) => (b.local.length + b.free_quota.length) - (a.local.length + a.free_quota.length))
                  .map(([cap, tiers]) => {
                    const hasLocal = tiers.local.length > 0;
                    const hasFreeQuota = tiers.free_quota.length > 0;
                    const hasPaid = tiers.paid.length > 0;
                    const freeAvailable = hasLocal || hasFreeQuota;
                    return (
                      <tr key={cap} style={{ borderBottom: '1px solid var(--line)' }}>
                        <td style={{ padding: '0.5rem 1rem', fontWeight: 500, color: freeAvailable ? 'var(--paper-100)' : 'var(--paper-400)' }}>
                          {cap}
                          {freeAvailable && <span style={{ color: 'var(--jade-400)', marginLeft: '0.35rem', fontSize: '0.7rem' }}>FREE</span>}
                        </td>
                        <td style={{ padding: '0.5rem 1rem' }}>
                          {hasLocal ? (
                            <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
                              {tiers.local.slice(0, 3).map(m => (
                                <span key={m} style={{ fontSize: '0.72rem', padding: '1px 6px', borderRadius: '3px', background: 'rgba(52, 211, 153, 0.1)', border: '1px solid var(--jade-700)', color: 'var(--jade-400)' }}>
                                  {m}
                                </span>
                              ))}
                              {tiers.local.length > 3 && <span style={{ fontSize: '0.7rem', color: 'var(--paper-500)' }}>+{tiers.local.length - 3}</span>}
                            </div>
                          ) : (
                            <span style={{ color: 'var(--paper-600)', fontSize: '0.8rem' }}>—</span>
                          )}
                        </td>
                        <td style={{ padding: '0.5rem 1rem' }}>
                          {hasFreeQuota ? (
                            <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
                              {tiers.free_quota.slice(0, 3).map(m => (
                                <span key={m} style={{ fontSize: '0.72rem', padding: '1px 6px', borderRadius: '3px', background: 'rgba(217, 119, 6, 0.1)', border: '1px solid #92400e', color: '#fbbf24' }}>
                                  {m}
                                </span>
                              ))}
                              {tiers.free_quota.length > 3 && <span style={{ fontSize: '0.7rem', color: 'var(--paper-500)' }}>+{tiers.free_quota.length - 3}</span>}
                            </div>
                          ) : (
                            <span style={{ color: 'var(--paper-600)', fontSize: '0.8rem' }}>—</span>
                          )}
                        </td>
                        <td style={{ padding: '0.5rem 1rem' }}>
                          {hasPaid ? (
                            <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
                              {tiers.paid.slice(0, 3).map(m => (
                                <span key={m} style={{ fontSize: '0.72rem', padding: '1px 6px', borderRadius: '3px', background: 'rgba(248, 113, 113, 0.1)', border: '1px solid var(--ember-700)', color: 'var(--ember-400)' }}>
                                  {m}
                                </span>
                              ))}
                              {tiers.paid.length > 3 && <span style={{ fontSize: '0.7rem', color: 'var(--paper-500)' }}>+{tiers.paid.length - 3}</span>}
                            </div>
                          ) : (
                            <span style={{ color: 'var(--paper-600)', fontSize: '0.8rem' }}>—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="panel">
        <div className="section-head">
          <h2>Registered Capabilities</h2>
        </div>

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading capabilities...</div>
        ) : isError ? (
          <ErrorState title="Failed to load capabilities" error={error} />
        ) : capabilities && capabilities.length > 0 ? (
          <div className="grid-cols-panel" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '1rem', padding: '1rem' }}>
            {capabilities.map(cap => (
              <div key={cap.name} className="panel" style={{ margin: 0, padding: '1rem', background: 'var(--ink-850)', border: '1px solid var(--line)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
                  <h3 style={{ fontSize: '1.1rem', fontWeight: 500, color: 'var(--paper-100)', margin: 0 }}>{cap.name}</h3>
                  <span className="badge" style={{ 
                    borderColor: cap.state === 'ready' ? 'var(--jade-500)' : 'var(--ember-500)', 
                    color: cap.state === 'ready' ? 'var(--jade-400)' : 'var(--ember-400)',
                    background: cap.state === 'ready' ? 'oklch(73% 0.13 162 / 0.1)' : 'oklch(68% 0.18 22 / 0.1)'
                  }}>
                    {cap.state}
                  </span>
                </div>
                
                <div style={{ fontSize: '0.85rem', color: 'var(--paper-300)', marginBottom: '1rem' }}>
                  {cap.operations.length} operations · {cap.providers} providers
                </div>

                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
                  {cap.operations.slice(0, 5).map(op => (
                    <span key={op} style={{ fontSize: '0.75rem', padding: '0.2rem 0.4rem', background: 'var(--ink-900)', border: '1px solid var(--line)', borderRadius: '4px', color: 'var(--paper-500)' }}>
                      {op}
                    </span>
                  ))}
                  {cap.operations.length > 5 && (
                    <span style={{ fontSize: '0.75rem', padding: '0.2rem 0.4rem', color: 'var(--paper-500)' }}>
                      +{cap.operations.length - 5} more
                    </span>
                  )}
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--line)', paddingTop: '1rem' }}>
                  <div style={{ fontSize: '0.75rem', color: cap.requires_auth ? 'var(--gold-400)' : 'var(--paper-500)' }}>
                    {cap.requires_auth ? 'Authentication required' : 'Public access'}
                  </div>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                    <span style={{ fontSize: '0.75rem', color: 'var(--paper-500)' }}>Enabled</span>
                    <input type="checkbox" defaultChecked={cap.state === 'ready'} style={{ accentColor: 'var(--jade-400)' }} />
                  </label>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState 
            title="No capabilities found" 
            description="The ATLAS agent does not have any registered MCP tool endpoints or plugins." 
          />
        )}
      </section>
    </>
  );
}
