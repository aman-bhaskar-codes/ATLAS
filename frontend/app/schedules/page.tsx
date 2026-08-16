"use client";

import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { opsApi } from '@/lib/api/client';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';

export default function SchedulesPage() {
  const queryClient = useQueryClient();
  const { data: schedules, isLoading, isError, error } = useQuery({
    queryKey: ["ops-schedules"],
    queryFn: opsApi.schedules,
  });

  const toggle = useMutation({
    mutationFn: (id: string) => opsApi.toggleSchedule(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["ops-schedules"] }),
  });

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Schedules</strong></div>

      <section className="panel">
        <div className="section-head">
          <h2>Cron Schedules</h2>
          <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
            Persisted background jobs (memory consolidation, skill promotion, …).
          </span>
        </div>

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading schedules…</div>
        ) : isError ? (
          <ErrorState title="Failed to load schedules" error={error} />
        ) : schedules && schedules.length > 0 ? (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                <th style={{ padding: '0.5rem 1rem' }}>Name</th>
                <th style={{ padding: '0.5rem 1rem' }}>Cron</th>
                <th style={{ padding: '0.5rem 1rem' }}>Status</th>
                <th style={{ padding: '0.5rem 1rem' }}></th>
              </tr>
            </thead>
            <tbody>
              {schedules.map(s => (
                <tr key={s.id} style={{ borderBottom: '1px solid var(--line)' }}>
                  <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-100)' }}>{s.name}</td>
                  <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-300)', fontFamily: 'monospace' }}>{s.cron}</td>
                  <td style={{ padding: '0.5rem 1rem' }}>
                    <span className="badge" style={{
                      borderColor: s.enabled ? 'var(--jade-500)' : 'var(--line)',
                      color: s.enabled ? 'var(--jade-400)' : 'var(--paper-500)',
                    }}>
                      {s.enabled ? 'enabled' : 'paused'}
                    </span>
                  </td>
                  <td style={{ padding: '0.5rem 1rem', textAlign: 'right' }}>
                    <button
                      className="btn"
                      style={{ fontSize: '0.8rem' }}
                      onClick={() => toggle.mutate(s.id)}
                      disabled={toggle.isPending}
                    >
                      {toggle.isPending ? '…' : s.enabled ? 'Pause' : 'Resume'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState title="No schedules" description="Persisted schedules appear here once registered." />
        )}
      </section>
    </>
  );
}
