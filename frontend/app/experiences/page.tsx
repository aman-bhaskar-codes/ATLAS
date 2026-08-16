"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { trajectoryApi } from '@/lib/api/client';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';

const CATEGORY_COLORS: Record<string, string> = {
  tool_usage: 'var(--jade-400)',
  planning_pattern: 'var(--gold-400)',
  error_recovery: 'var(--ember-400)',
  user_preference: 'var(--paper-300)',
  domain_knowledge: 'var(--paper-100)',
  optimization: 'var(--jade-400)',
  constraint: 'var(--ember-400)',
};

export default function ExperiencesPage() {
  const { data: experiences, isLoading, isError, error } = useQuery({
    queryKey: ["experiences"],
    queryFn: () => trajectoryApi.experiences(50),
  });

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Experiences</strong></div>

      <section className="panel">
        <div className="section-head">
          <h2>Extracted Lessons</h2>
          <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
            Post-task lessons extracted from execution trajectories. Proven lessons promote into skills.
          </span>
        </div>

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading experiences…</div>
        ) : isError ? (
          <ErrorState title="Failed to load experiences" error={error} />
        ) : experiences && experiences.length > 0 ? (
          <div style={{ padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {experiences.map(exp => (
              <div key={exp.id} style={{ border: '1px solid var(--line)', background: 'var(--ink-850)', padding: '1rem', borderRadius: '4px' }}>
                <div style={{ display: 'flex', gap: '1rem', alignItems: 'baseline', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
                  <span className="badge" style={{ borderColor: CATEGORY_COLORS[exp.category] ?? 'var(--line)', color: CATEGORY_COLORS[exp.category] ?? 'var(--paper-300)' }}>
                    {exp.category}
                  </span>
                  <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>confidence {exp.confidence.toFixed(2)}</span>
                  <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>reused {exp.reuse_count}×</span>
                  {exp.reuse_count > 0 && (
                    <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                      success {(exp.success_rate * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
                <div style={{ color: 'var(--paper-100)', marginBottom: '0.35rem' }}>{exp.lesson_text}</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                  Applies when: {exp.applicability_context}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No experiences yet"
            description="Experiences are extracted after task completion. Complete a task to see lessons here."
          />
        )}
      </section>
    </>
  );
}
