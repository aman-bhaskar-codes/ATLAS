"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { providersApi, opsApi } from '@/lib/api/client';
import { ErrorState } from '@/components/primitives/ErrorState';

const policyDescriptions: Record<string, { label: string; description: string; color: string }> = {
  zero_cost:      { label: 'ZERO COST',      description: 'All paid providers blocked. Only local models.', color: 'var(--jade-400)' },
  free_only:      { label: 'FREE ONLY',      description: 'Free-tier cloud allowed, paid blocked.',         color: '#60a5fa' },
  free_preferred: { label: 'FREE PREFERRED', description: 'Free-tier preferred, paid allowed with budget.', color: '#fbbf24' },
  balanced:       { label: 'BALANCED',        description: 'Cost-quality balanced. Budget enforced.',        color: '#c084fc' },
  unrestricted:   { label: 'UNRESTRICTED',    description: 'No cost restrictions. Best model selected.',     color: 'var(--ember-400)' },
};

export default function CostPage() {
  const profile = useQuery({ queryKey: ['profile'], queryFn: providersApi.profile, refetchInterval: 30000 });
  const models = useQuery({ queryKey: ['ops-models'], queryFn: () => opsApi.models(true) });

  // Both queries, not just the profile: every stat card and the whole pricing
  // table are derived from `models.data`, so a failed /ops/models rendered
  // "Total Models 0", "Free Models 0" and "No models configured." — four
  // fabricated facts about the fleet from a request that never came back.
  const err = profile.error ?? models.error;

  // Calculate cost stats from models
  const costBreakdown = React.useMemo(() => {
    if (!models.data) return { local: 0, free: 0, free_quota: 0, paid: 0 };
    const counts = { local: 0, free: 0, free_quota: 0, paid: 0 };
    models.data.forEach(m => {
      const cc = m.cost_class ?? (m.usd_per_1m_output === 0 ? 'free' : 'paid');
      if (cc in counts) counts[cc as keyof typeof counts]++;
    });
    return counts;
  }, [models.data]);

  const totalModels = models.data?.length ?? 0;
  const freeModels = costBreakdown.local + costBreakdown.free + costBreakdown.free_quota;

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Cost Controls</strong></div>

      {err ? (
        <ErrorState
          title="Failed to load cost data"
          error={err}
          onRetry={() => {
            void profile.refetch();
            void models.refetch();
          }}
        />
      ) : (
        <>
          {/* Cost Policy Banner */}
          {profile.data && (() => {
            const policy = policyDescriptions[profile.data.cost_policy] ?? policyDescriptions.unrestricted;
            return (
              <section className="panel" style={{ marginBottom: '1.5rem' }}>
                <div style={{ padding: '1.25rem', display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
                  <div style={{
                    width: '56px', height: '56px', borderRadius: '12px',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: `linear-gradient(135deg, ${policy.color}15, transparent)`,
                    border: `1px solid ${policy.color}30`,
                    fontSize: '1.5rem',
                  }}>
                    {profile.data.cost_policy === 'zero_cost' ? '🛡️' : profile.data.cost_policy === 'free_only' ? '🆓' : '💰'}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, fontSize: '1.1rem', color: policy.color, letterSpacing: '0.02em' }}>
                      {policy.label}
                    </div>
                    <div style={{ fontSize: '0.85rem', color: 'var(--paper-400)', marginTop: '4px' }}>
                      {policy.description}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '1.8rem', fontWeight: 700, color: 'var(--paper-100)', fontVariantNumeric: 'tabular-nums' }}>
                      ${profile.data.daily_usd.toFixed(2)}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      daily budget
                    </div>
                  </div>
                </div>
              </section>
            );
          })()}

          {/* Stats Cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem', marginBottom: '1.5rem' }}>
            {[
              { label: 'Total Models', value: totalModels, color: 'var(--paper-100)' },
              { label: 'Free Models', value: freeModels, color: 'var(--jade-400)' },
              { label: 'Local Models', value: costBreakdown.local, color: 'var(--jade-300)' },
              { label: 'Paid Models', value: costBreakdown.paid, color: 'var(--ember-400)' },
            ].map(stat => (
              <div key={stat.label} className="panel" style={{ padding: '1rem 1.25rem' }}>
                <div style={{ fontSize: '0.7rem', color: 'var(--paper-500)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.35rem' }}>
                  {stat.label}
                </div>
                <div style={{ fontSize: '1.5rem', fontWeight: 700, color: stat.color, fontVariantNumeric: 'tabular-nums' }}>
                  {stat.value}
                </div>
              </div>
            ))}
          </div>

          {/* Cost Class Distribution */}
          <section className="panel" style={{ marginBottom: '1.5rem' }}>
            <div className="section-head">
              <h2>Cost Class Distribution</h2>
            </div>
            <div style={{ padding: '1rem' }}>
              {totalModels > 0 && (
                <div style={{ display: 'flex', borderRadius: '6px', overflow: 'hidden', height: '32px', marginBottom: '1rem' }}>
                  {costBreakdown.local > 0 && (
                    <div style={{ flex: costBreakdown.local, background: 'var(--jade-600)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', color: 'white', fontWeight: 600 }}>
                      LOCAL ({costBreakdown.local})
                    </div>
                  )}
                  {costBreakdown.free > 0 && (
                    <div style={{ flex: costBreakdown.free, background: '#3b82f6', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', color: 'white', fontWeight: 600 }}>
                      FREE ({costBreakdown.free})
                    </div>
                  )}
                  {costBreakdown.free_quota > 0 && (
                    <div style={{ flex: costBreakdown.free_quota, background: '#d97706', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', color: 'white', fontWeight: 600 }}>
                      QUOTA ({costBreakdown.free_quota})
                    </div>
                  )}
                  {costBreakdown.paid > 0 && (
                    <div style={{ flex: costBreakdown.paid, background: 'var(--ember-600)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', color: 'white', fontWeight: 600 }}>
                      PAID ({costBreakdown.paid})
                    </div>
                  )}
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.75rem' }}>
                {[
                  { label: 'Local', count: costBreakdown.local, color: 'var(--jade-500)', desc: '$0 — runs on your hardware' },
                  { label: 'Free', count: costBreakdown.free, color: '#3b82f6', desc: '$0 — no limits' },
                  { label: 'Free Quota', count: costBreakdown.free_quota, color: '#d97706', desc: '$0 — daily limits apply' },
                  { label: 'Paid', count: costBreakdown.paid, color: 'var(--ember-500)', desc: 'Per-token pricing' },
                ].map(item => (
                  <div key={item.label} style={{ padding: '0.75rem', borderRadius: '6px', border: `1px solid ${item.color}25`, background: `${item.color}08` }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                      <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: item.color }} />
                      <span style={{ fontWeight: 600, color: 'var(--paper-200)', fontSize: '0.85rem' }}>{item.label}</span>
                    </div>
                    <div style={{ fontSize: '1.1rem', fontWeight: 700, color: item.color, marginBottom: '2px' }}>{item.count}</div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--paper-500)' }}>{item.desc}</div>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* Pricing Table */}
          <section className="panel">
            <div className="section-head">
              <h2>Model Pricing</h2>
              <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                Per 1M tokens · Sorted by output cost
              </span>
            </div>
            {models.data && models.data.length > 0 ? (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                    <th style={{ padding: '0.5rem 1rem' }}>Model</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Provider</th>
                    <th style={{ padding: '0.5rem 1rem', textAlign: 'right' }}>$/1M Input</th>
                    <th style={{ padding: '0.5rem 1rem', textAlign: 'right' }}>$/1M Output</th>
                    <th style={{ padding: '0.5rem 1rem', textAlign: 'center' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...models.data].sort((a, b) => a.usd_per_1m_output - b.usd_per_1m_output).map(m => (
                    <tr key={m.id} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-100)', fontWeight: 500 }}>{m.id}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>{m.provider}</td>
                      <td style={{ padding: '0.5rem 1rem', textAlign: 'right', color: m.usd_per_1m_input === 0 ? 'var(--jade-400)' : 'var(--paper-300)', fontVariantNumeric: 'tabular-nums' }}>
                        {m.usd_per_1m_input === 0 ? 'FREE' : `$${m.usd_per_1m_input.toFixed(2)}`}
                      </td>
                      <td style={{ padding: '0.5rem 1rem', textAlign: 'right', color: m.usd_per_1m_output === 0 ? 'var(--jade-400)' : 'var(--paper-300)', fontVariantNumeric: 'tabular-nums' }}>
                        {m.usd_per_1m_output === 0 ? 'FREE' : `$${m.usd_per_1m_output.toFixed(2)}`}
                      </td>
                      <td style={{ padding: '0.5rem 1rem', textAlign: 'center' }}>
                        <span className="badge" style={{
                          borderColor: m.enabled ? 'var(--jade-500)' : 'var(--line)',
                          color: m.enabled ? 'var(--jade-400)' : 'var(--paper-500)',
                        }}>
                          {m.enabled ? 'active' : 'off'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div style={{ padding: '1rem', color: 'var(--paper-500)', fontSize: '0.85rem' }}>No models configured.</div>
            )}
          </section>
        </>
      )}
    </>
  );
}
