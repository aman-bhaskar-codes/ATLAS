'use client';

import React from 'react';
import { TrustHeader } from '../../components/trust/TrustHeader';
import { useTasks } from '../../features/trust/queries';
import Link from 'next/link';

export default function TasksPage() {
  const { data, isLoading, error } = useTasks();

  return (
    <div className="max-w-5xl mx-auto py-8 px-6">
      <h1 className="text-3xl font-serif text-[var(--paper-100)] mb-6">Trust Center</h1>
      <TrustHeader active="tasks" />

      {isLoading ? (
        <div className="text-[var(--paper-500)] text-sm">Loading tasks...</div>
      ) : error ? (
        <div className="text-[var(--danger-400)] text-sm">Error loading tasks.</div>
      ) : (
        <div className="space-y-4">
          {data?.items.map((task) => (
            <Link 
              key={task.id} 
              href={`/tasks/${task.id}`}
              className="block glass-panel p-4 rounded-lg hover:border-[var(--gold-500)] transition-colors"
            >
              <div className="flex justify-between items-start mb-2">
                <h3 className="text-[var(--paper-100)] text-lg m-0">{task.request}</h3>
                <span className={`text-[0.7rem] uppercase tracking-wider px-2 py-1 rounded-full ${
                  task.state === 'completed' ? 'bg-[oklch(73%_0.13_162/0.1)] text-[var(--jade-400)]' :
                  task.state === 'failed' ? 'bg-[oklch(68%_0.18_22/0.1)] text-[var(--danger-400)]' :
                  'bg-[var(--ink-800)] text-[var(--paper-300)]'
                }`}>
                  {task.state}
                </span>
              </div>
              <div className="flex gap-4 text-[0.8rem] text-[var(--paper-500)]">
                <span>Created: {new Date(task.created_at).toLocaleString()}</span>
                <span>{task.steps_taken} steps</span>
                {task.approval_count > 0 && <span className="text-[var(--ember-400)]">{task.approval_count} approvals</span>}
              </div>
            </Link>
          ))}
          {data?.items.length === 0 && (
            <div className="text-[var(--paper-500)] text-sm text-center py-8">No tasks found.</div>
          )}
        </div>
      )}
    </div>
  );
}
