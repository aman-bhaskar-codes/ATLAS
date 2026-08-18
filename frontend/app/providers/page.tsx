"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { providersApi, opsApi } from '@/lib/api/client';
import { ErrorState } from '@/components/primitives/ErrorState';

const costClassColors: Record<string, { bg: string; text: string; border: string }> = {
  local:      { bg: 'rgba(52, 211, 153, 0.08)', text: 'var(--jade-400)',  border: 'var(--jade-600)' },
  free:       { bg: 'rgba(96, 165, 250, 0.08)', text: '#60a5fa',          border: '#3b82f6' },
  free_quota: { bg: 'rgba(251, 191, 36, 0.08)', text: '#fbbf24',          border: '#d97706' },
  paid:       { bg: 'rgba(248, 113, 113, 0.08)', text: 'var(--ember-400)', border: 'var(--ember-600)' },
};

function CostBadge({ costClass }: { costClass: string }) {
  const c = costClassColors[costClass] ?? costClassColors.paid;
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7rem',
      fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em',
      background: c.bg, color: c.text, border: `1px solid ${c.border}`,
    }}>
      {costClass.replace('_', ' ')}
    </span>
  );
}

function QuotaBar({ pct }: { pct: number }) {
  const color = pct > 60 ? 'var(--jade-500)' : pct > 20 ? '#fbbf24' : 'var(--ember-500)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: '120px' }}>
      <div style={{ flex: 1, height: '6px', borderRadius: '3px', background: 'var(--ink-800)', overflow: 'hidden' }}>
        <div style={{ width: `${Math.max(pct, 2)}%`, height: '100%', borderRadius: '3px', background: color, transition: 'width 0.3s ease' }} />
      </div>
      <span style={{ fontSize: '0.75rem', color: 'var(--paper-400)', minWidth: '35px', textAlign: 'right' }}>{pct.toFixed(0)}%</span>
    </div>
  );
}

export default function ProvidersPage() {
  const profile = useQuery({ queryKey: ['profile'], queryFn: providersApi.profile, refetchInterval: 30000 });
  const health = useQuery({ queryKey: ['providers-health'], queryFn: providersApi.health, refetchInterval: 10000 });
  const quota = useQuery({ queryKey: ['providers-quota'], queryFn: providersApi.quota, refetchInterval: 10000 });
  const models = useQuery({ queryKey: ['ops-models'], queryFn: () => opsApi.models(true) });

  const err = profile.error ?? health.error;

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Providers &amp; Quotas</strong></div>

      {err ? (
        <ErrorState title="Failed to load providers" error={err} />
      ) : (
        <>
          {/* Profile Banner */}
          {profile.data && (
            <section className="panel" style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1rem 1.25rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                  <div style={{
                    width: '40px', height: '40px', borderRadius: '8px', display: 'flex',
                    alignItems: 'center', justifyContent: 'center', fontSize: '1.2rem',
                    background: profile.data.cost_policy === 'zero_cost'
                      ? 'linear-gradient(135deg, rgba(52, 211, 153, 0.15), rgba(52, 211, 153, 0.05))'
                      : 'linear-gradient(135deg, rgba(96, 165, 250, 0.15), rgba(96, 165, 250, 0.05))',
                    border: `1px solid ${profile.data.cost_policy === 'zero_cost' ? 'var(--jade-600)' : '#3b82f6'}`,
                  }}>
                    {profile.data.cost_policy === 'zero_cost' ? '🛡️' : '⚡'}
                  </div>
                  <div>
                    <div style={{ fontWeight: 600, color: 'var(--paper-100)', fontSize: '0.95rem' }}>
                      Profile: {profile.data.profile.replace('_', ' ').toUpperCase()}
                    </div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)', marginTop: '2px' }}>
                      Cost: {profile.data.cost_policy} · Network: {profile.data.network_policy}
                      {profile.data.daily_usd === 0 && <span style={{ color: 'var(--jade-400)', marginLeft: '0.5rem' }}>$0 enforced</span>}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  {profile.data.allowed_cost_classes.map(cc => (
                    <CostBadge key={cc} costClass={cc} />
                  ))}
                </div>
              </div>
            </section>
          )}

          {/* Provider Health Grid */}
          <section className="panel" style={{ marginBottom: '1.5rem' }}>
            <div className="section-head">
              <h2>Provider Health</h2>
              <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                Live status · Auto-refreshes every 10s
              </span>
            </div>
            {health.isLoading ? (
              <div style={{ padding: '1rem', color: 'var(--paper-500)', fontSize: '0.85rem' }}>Loading providers…</div>
            ) : health.data && health.data.length > 0 ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '0.75rem', padding: '1rem' }}>
                {health.data.map(p => (
                  <div key={p.name} style={{
                    border: `1px solid ${p.healthy ? 'var(--jade-700)' : 'var(--ember-700)'}`,
                    background: p.healthy
                      ? 'linear-gradient(135deg, rgba(52, 211, 153, 0.04), transparent)'
                      : 'linear-gradient(135deg, rgba(248, 113, 113, 0.04), transparent)',
                    padding: '0.875rem 1rem', borderRadius: '6px',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                      <span style={{ fontWeight: 600, color: 'var(--paper-100)', fontSize: '0.9rem' }}>{p.name}</span>
                      <span style={{
                        width: '8px', height: '8px', borderRadius: '50%',
                        background: p.healthy ? 'var(--jade-400)' : 'var(--ember-400)',
                        boxShadow: p.healthy ? '0 0 6px var(--jade-500)' : '0 0 6px var(--ember-500)',
                      }} />
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--paper-400)' }}>
                      <span>{p.is_local ? 'Local' : 'Cloud'}</span>
                      <span>{p.avg_latency_ms}ms</span>
                    </div>
                    {p.quota_pct !== undefined && p.quota_pct < 100 && (
                      <div style={{ marginTop: '0.5rem' }}>
                        <QuotaBar pct={p.quota_pct} />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ padding: '1rem', color: 'var(--paper-500)', fontSize: '0.85rem' }}>
                No providers registered. Start ATLAS to see provider health.
              </div>
            )}
          </section>

          {/* Quota Snapshot */}
          {quota.data?.enabled && (
            <section className="panel" style={{ marginBottom: '1.5rem' }}>
              <div className="section-head">
                <h2>Free-Tier Quotas</h2>
                <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                  Daily limits · Resets at midnight UTC
                </span>
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                    <th style={{ padding: '0.5rem 1rem' }}>Provider</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Requests</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Tokens</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Remaining</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(quota.data.providers).map(([name, q]) => (
                    <tr key={name} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-100)', fontWeight: 500 }}>{name}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>
                        {q.requests_used.toLocaleString()} / {q.daily_requests_limit.toLocaleString()}
                      </td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>
                        {q.tokens_used.toLocaleString()} / {q.daily_tokens_limit.toLocaleString()}
                      </td>
                      <td style={{ padding: '0.5rem 1rem' }}>
                        <QuotaBar pct={q.pct_remaining} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {/* Model Registry with Cost Class */}
          <section className="panel">
            <div className="section-head">
              <h2>Model Registry</h2>
              <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                {models.data?.length ?? 0} models configured
              </span>
            </div>
            {models.isLoading ? (
              <div style={{ padding: '1rem', color: 'var(--paper-500)', fontSize: '0.85rem' }}>Loading models…</div>
            ) : models.data && models.data.length > 0 ? (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                    <th style={{ padding: '0.5rem 1rem' }}>Model</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Provider</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Cost Class</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Quality</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Context</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {models.data.map(m => (
                    <tr key={m.id} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-100)', fontWeight: 500 }}>{m.id}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>{m.provider}</td>
                      <td style={{ padding: '0.5rem 1rem' }}>
                        <CostBadge costClass={m.cost_class ?? (m.usd_per_1m_output === 0 ? 'free' : 'paid')} />
                      </td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>{m.quality_score.toFixed(2)}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>{(m.context_length / 1000).toFixed(0)}k</td>
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
              <div style={{ padding: '1rem', color: 'var(--paper-500)', fontSize: '0.85rem' }}>No models configured.</div>
            )}
          </section>
        </>
      )}
    </>
  );
}
