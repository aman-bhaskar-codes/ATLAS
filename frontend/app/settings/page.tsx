"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { atlasApi } from '@/lib/api/client';
import { ErrorState } from '@/components/primitives/ErrorState';

/**
 * Settings — read-only posture view.
 * Configuration lives in typed YAML/env (never editable through the UI for
 * safety); this page surfaces the effective runtime state per group.
 */
export default function SettingsPage() {
  const status = useQuery({ queryKey: ["runtime-status"], queryFn: atlasApi.runtimeStatus, refetchInterval: 15000 });
  const health = useQuery({ queryKey: ["runtime-health"], queryFn: atlasApi.runtimeHealth, refetchInterval: 30000 });

  const s = status.data;
  const err = status.error ?? health.error;

  const groups: { title: string; rows: [string, string][] }[] = [
    {
      title: 'General',
      rows: [
        ['Runtime state', s?.state ?? '—'],
        ['Version', s?.version ?? '—'],
        ['Environment', s?.environment ?? '—'],
      ],
    },
    {
      title: 'Safety',
      rows: [
        ['Kill switch', s?.kill_switch_active ? 'ACTIVE (all execution halted)' : 'inactive'],
        ['Pending approvals', String(s?.pending_approval_count ?? 0)],
      ],
    },
    {
      title: 'Runtime',
      rows: [
        ['Active tasks', String(s?.active_task_count ?? 0)],
        ['Overall health', health.data?.overall ?? '—'],
      ],
    },
  ];

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Settings</strong></div>

      {err ? (
        <ErrorState title="Failed to load settings" error={err} />
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
            {groups.map(g => (
              <section className="panel" key={g.title}>
                <div className="section-head"><h2>{g.title}</h2></div>
                <div style={{ padding: '0 1rem 1rem 1rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {g.rows.map(([label, value]) => (
                    <div key={label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem' }}>
                      <span style={{ color: 'var(--paper-500)' }}>{label}</span>
                      <span style={{ color: 'var(--paper-100)' }}>{value}</span>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>

          <section className="panel">
            <div className="section-head">
              <h2>Health Checks</h2>
              <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                Effective configuration is typed and validated at startup (config/*.yaml + env). Safety policy is never editable here.
              </span>
            </div>
            {health.isLoading ? (
              <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading health checks…</div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                    <th style={{ padding: '0.5rem 1rem' }}>Check</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Status</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {(health.data?.checks ?? []).map(c => (
                    <tr key={c.name} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-100)' }}>{c.name}</td>
                      <td style={{ padding: '0.5rem 1rem' }}>
                        <span className="badge" style={{
                          borderColor: c.status === 'pass' ? 'var(--jade-500)' : c.status === 'warn' ? 'var(--gold-500)' : 'var(--ember-500)',
                          color: c.status === 'pass' ? 'var(--jade-400)' : c.status === 'warn' ? 'var(--gold-400)' : 'var(--ember-400)',
                        }}>
                          {c.status}
                        </span>
                      </td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-500)' }}>{c.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </>
  );
}
