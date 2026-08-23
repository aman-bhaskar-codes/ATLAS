"use client";

import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { learningApi } from '@/lib/api/client';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorRow, ErrorState } from '@/components/primitives/ErrorState';

function statusColor(status: string): { borderColor: string; color: string } {
  if (status === 'active') return { borderColor: 'var(--jade-500)', color: 'var(--jade-400)' };
  if (status === 'disabled') return { borderColor: 'var(--ember-500)', color: 'var(--ember-400)' };
  return { borderColor: 'var(--gold-500)', color: 'var(--gold-400)' };
}

export default function SkillsPage() {
  const queryClient = useQueryClient();
  const { data: skills, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["skills"],
    queryFn: () => learningApi.skills(),
  });

  const disable = useMutation({
    mutationFn: (skillId: string) => learningApi.disableSkill(skillId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["skills"] }),
  });

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Skills</strong></div>

      <section className="panel">
        <div className="section-head">
          <h2>Learned Skills</h2>
          <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
            Reusable procedures promoted from proven experiences. Activation requires evidence; disabling is a human decision.
          </span>
        </div>

        {/* Disabling a skill is a governed decision, so a refusal has to be visible.
            Without this the button re-enabled itself and the skill stayed active with
            no explanation. */}
        {disable.isError && (
          <div style={{ padding: '0 1rem' }}>
            <ErrorRow error={disable.error} />
          </div>
        )}

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading skills…</div>
        ) : isError ? (
          <ErrorState title="Failed to load skills" error={error} onRetry={() => void refetch()} />
        ) : skills && skills.length > 0 ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1rem', padding: '1rem' }}>
            {skills.map(skill => (
              <div key={skill.id} style={{ border: '1px solid var(--line)', background: 'var(--ink-850)', padding: '1rem', borderRadius: '4px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.75rem' }}>
                  <h3 style={{ fontSize: '1rem', fontWeight: 500, color: 'var(--paper-100)', margin: 0 }}>{skill.name}</h3>
                  <span className="badge" style={statusColor(skill.status)}>{skill.status} v{skill.version}</span>
                </div>
                <div style={{ fontSize: '0.85rem', color: 'var(--paper-300)', marginBottom: '0.75rem' }}>{skill.description}</div>
                <div style={{ display: 'flex', gap: '1rem', fontSize: '0.8rem', color: 'var(--paper-500)', marginBottom: '0.75rem' }}>
                  <span>confidence {skill.confidence.toFixed(2)}</span>
                  <span>success {(skill.success_rate * 100).toFixed(0)}%</span>
                  <span>used {skill.usage_count}×</span>
                </div>
                {skill.known_failure_modes.length > 0 && (
                  <div style={{ fontSize: '0.8rem', color: 'var(--ember-400)', marginBottom: '0.75rem' }}>
                    Known failures: {skill.known_failure_modes.join(', ')}
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'flex-end', borderTop: '1px solid var(--line)', paddingTop: '0.75rem' }}>
                  {skill.status !== 'disabled' && (
                    <button
                      className="btn"
                      style={{ fontSize: '0.8rem', color: 'var(--ember-400)' }}
                      onClick={() => disable.mutate(skill.id)}
                      disabled={disable.isPending}
                    >
                      {disable.isPending ? 'Disabling…' : 'Disable'}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No skills yet"
            description="Skills are promoted from experiences that have been reused successfully at least twice. Run tasks to build evidence."
          />
        )}
      </section>
    </>
  );
}
