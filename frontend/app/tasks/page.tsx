"use client";

import React, { useState } from 'react';
import { useTasks } from '../../features/trust/queries';
import { useRouter } from 'next/navigation';
import { ErrorState } from '@/components/primitives/ErrorState';
import { EmptyState } from '@/components/primitives/EmptyState';

export default function TasksPage() {
  const { data, isLoading, isError, error } = useTasks();
  const router = useRouter();
  
  const [statusFilter, setStatusFilter] = useState('all');
  const [tierFilter, setTierFilter] = useState('all');

  const filteredTasks = data?.items.filter(task => {
    if (statusFilter !== 'all' && task.state !== statusFilter) return false;
    // Mock tier filtering since tier isn't on task schema yet
    return true; 
  }) || [];

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Tasks</strong>
      </div>

      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem', alignItems: 'center' }}>
        <select 
          className="ghost-btn" 
          value={statusFilter} 
          onChange={e => setStatusFilter(e.target.value)}
          style={{ padding: '0.4rem 0.8rem', background: 'var(--ink-900)', border: '1px solid var(--line)', color: 'var(--paper-300)' }}
        >
          <option value="all">All Statuses</option>
          <option value="completed">Completed</option>
          <option value="executing">Executing</option>
          <option value="failed">Failed</option>
        </select>
        
        <select 
          className="ghost-btn" 
          value={tierFilter} 
          onChange={e => setTierFilter(e.target.value)}
          style={{ padding: '0.4rem 0.8rem', background: 'var(--ink-900)', border: '1px solid var(--line)', color: 'var(--paper-300)' }}
        >
          <option value="all">All Tiers</option>
          <option value="auto">AUTO</option>
          <option value="confirm">CONFIRM</option>
          <option value="block">BLOCK</option>
        </select>
      </div>

      <section className="panel">
        <div className="section-head">
          <h2>Task History</h2>
        </div>

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading tasks...</div>
        ) : isError ? (
          <ErrorState title="Failed to load tasks" error={error} />
        ) : filteredTasks.length > 0 ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem', textAlign: 'left' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)' }}>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Request</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Status</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Tier</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Duration</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Cost</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Model</th>
                </tr>
              </thead>
              <tbody>
                {filteredTasks.map((task) => (
                  <tr 
                    key={task.id} 
                    onClick={() => router.push(`/tasks/${task.id}`)}
                    style={{ borderBottom: '1px solid var(--ink-850)', transition: 'background 0.15s ease', cursor: 'pointer' }} 
                    onMouseOver={e => e.currentTarget.style.background = 'var(--ink-850)'} 
                    onMouseOut={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <td style={{ padding: '0.75rem 1rem', maxWidth: '300px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--paper-100)' }}>
                      {task.request}
                    </td>
                    <td style={{ padding: '0.75rem 1rem' }}>
                      <span className="badge">{task.state}</span>
                    </td>
                    <td style={{ padding: '0.75rem 1rem', color: 'var(--paper-300)' }}>Tier 2</td>
                    <td style={{ padding: '0.75rem 1rem', color: 'var(--paper-300)' }} className="mono">14s</td>
                    <td style={{ padding: '0.75rem 1rem', color: 'var(--paper-300)' }} className="mono">$0.012</td>
                    <td style={{ padding: '0.75rem 1rem', color: 'var(--paper-300)' }}>GLM-5.2</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState 
            title="No tasks yet" 
            description="ATLAS is idle. Start one from the Command Center." 
            action={<button className="ghost-btn" onClick={() => router.push('/')}>Go to Command Center</button>}
          />
        )}
      </section>
    </>
  );
}
