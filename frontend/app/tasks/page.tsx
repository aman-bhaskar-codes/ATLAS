"use client";

import React from 'react';
import { useTasks } from '../../features/trust/queries';
import Link from 'next/link';

export default function TasksPage() {
  const { data, isLoading, error } = useTasks();

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Tasks</strong>
      </div>

      <section className="panel">
        <div className="section-head">
          <h2>Task History</h2>
        </div>

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4">Loading tasks...</div>
        ) : error ? (
          <div className="text-[var(--danger-400)] text-sm py-4">Error loading tasks.</div>
        ) : (
          <div className="space-y-4">
            {data?.items.map((task) => (
              <Link 
                key={task.id} 
                href={`/tasks/${task.id}`}
                className="block border border-[var(--line)] bg-[var(--ink-850)] p-4 rounded-lg hover:border-[var(--gold-500)] transition-colors"
                style={{ textDecoration: 'none' }}
              >
                <div className="flex justify-between items-start mb-2">
                  <h3 className="text-[var(--paper-100)] text-lg m-0 font-medium">{task.request}</h3>
                  <span className={`text-[0.7rem] uppercase tracking-wider px-2 py-1 rounded-full ${
                    task.state === 'completed' ? 'bg-[oklch(73%_0.13_162/0.1)] text-[var(--jade-400)]' :
                    task.state === 'failed' ? 'bg-[oklch(68%_0.18_22/0.1)] text-[var(--danger-400)]' :
                    'bg-[var(--ink-800)] text-[var(--paper-300)]'
                  }`}>
                    {task.state}
                  </span>
                </div>
                <div className="flex gap-4 text-[0.8rem] text-[var(--paper-500)] mt-2">
                  <span>Created: {new Date(task.created_at).toLocaleString()}</span>
                  <span>{task.steps_taken} steps</span>
                  {task.approval_count > 0 && <span className="text-[var(--ember-400)]">{task.approval_count} approvals</span>}
                </div>
              </Link>
            ))}
            {data?.items.length === 0 && (
              <div className="text-[var(--paper-500)] text-sm py-4">No tasks found.</div>
            )}
          </div>
        )}
      </section>
    </>
  );
}
