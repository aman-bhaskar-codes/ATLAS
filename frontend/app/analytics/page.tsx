"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { learningApi } from '@/lib/api/client';
import { ErrorState } from '@/components/primitives/ErrorState';

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div style={{ border: '1px solid var(--line)', background: 'var(--ink-850)', padding: '1.25rem', borderRadius: '4px' }}>
      <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--paper-500)', marginBottom: '0.5rem' }}>{label}</div>
      <div style={{ fontSize: '1.75rem', fontWeight: 500, color: 'var(--paper-100)' }}>{value}</div>
      {hint && <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)', marginTop: '0.35rem' }}>{hint}</div>}
    </div>
  );
}

function pct(x: number | null | undefined): string {
  return x == null ? '—' : `${(x * 100).toFixed(0)}%`;
}

export default function AnalyticsPage() {
  const analytics = useQuery({ queryKey: ["learning-analytics"], queryFn: learningApi.analytics });
  const evaluations = useQuery({ queryKey: ["evaluations"], queryFn: () => learningApi.evaluations(20) });

  const a = analytics.data;
  const err = analytics.error ?? evaluations.error;

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Analytics</strong></div>

      {err ? (
        <ErrorState title="Failed to load analytics" error={err} />
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
            <Stat label="Task success rate" value={pct(a?.trajectory_success_rate)} hint={`${a?.total_trajectories ?? 0} trajectories`} />
            <Stat label="Verification (7d)" value={pct(a?.recent_verification_pass_rate)} hint="answers passing the verifier" />
            <Stat label="Experiences" value={String(a?.total_experiences ?? 0)} hint="active lessons" />
            <Stat label="Skills" value={`${a?.active_skills ?? 0} active`} hint={`${a?.candidate_skills ?? 0} candidates`} />
            <Stat label="Strategies" value={String(a?.active_strategies ?? 0)} hint="governed, eval-gated" />
          </div>

          <section className="panel">
            <div className="section-head">
              <h2>Recent Evaluation Runs</h2>
              <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                Golden-task results from the regression gate.
              </span>
            </div>
            {evaluations.isLoading ? (
              <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading evaluations…</div>
            ) : evaluations.data && evaluations.data.length > 0 ? (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                    <th style={{ padding: '0.5rem 1rem' }}>Task</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Run</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Evaluator</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Result</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Score</th>
                    <th style={{ padding: '0.5rem 1rem' }}>When</th>
                  </tr>
                </thead>
                <tbody>
                  {evaluations.data.map(e => (
                    <tr key={`${e.run_id}-${e.golden_id}`} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-100)' }}>{e.golden_id}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-500)', fontFamily: 'monospace' }}>{e.run_id.slice(0, 8)}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>{e.evaluator}</td>
                      <td style={{ padding: '0.5rem 1rem' }}>
                        <span className="badge" style={{
                          borderColor: e.passed ? 'var(--jade-500)' : 'var(--ember-500)',
                          color: e.passed ? 'var(--jade-400)' : 'var(--ember-400)',
                        }}>
                          {e.passed ? 'PASS' : 'FAIL'}
                        </span>
                      </td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)' }}>{e.score.toFixed(2)}</td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-500)' }}>{new Date(e.created_ts).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-[var(--paper-500)] text-sm py-4 px-4">
                No evaluation runs recorded yet. Run <code>scripts/eval_gate.py</code> or the nightly gate.
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
