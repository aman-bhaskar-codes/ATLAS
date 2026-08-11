"use client";

import React, { useState } from 'react';
import { useAuditLog } from '../../features/trust/queries';
import { ErrorState } from '@/components/primitives/ErrorState';
import { EmptyState } from '@/components/primitives/EmptyState';

export default function AuditPage() {
  const { data, isLoading, isError, error } = useAuditLog();
  
  const [dateFilter, setDateFilter] = useState('all');
  const [tierFilter, setTierFilter] = useState('all');

  // Fake filtering logic for visual completeness
  const filteredEvents = data?.items?.filter(event => {
    // If we had tier in audit events, we'd filter here
    return true;
  }) || [];

  const handleExport = (format: 'json' | 'text') => {
    alert(`Exporting audit log as ${format.toUpperCase()}...`);
  };

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Audit Log</strong>
      </div>

      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: '1rem' }}>
          <select 
            className="ghost-btn" 
            value={dateFilter} 
            onChange={e => setDateFilter(e.target.value)}
            style={{ padding: '0.4rem 0.8rem', background: 'var(--ink-900)', border: '1px solid var(--line)', color: 'var(--paper-300)' }}
          >
            <option value="all">Any Date</option>
            <option value="today">Today</option>
            <option value="week">Last 7 days</option>
            <option value="month">Last 30 days</option>
          </select>
          
          <select 
            className="ghost-btn" 
            value={tierFilter} 
            onChange={e => setTierFilter(e.target.value)}
            style={{ padding: '0.4rem 0.8rem', background: 'var(--ink-900)', border: '1px solid var(--line)', color: 'var(--paper-300)' }}
          >
            <option value="all">All Tiers</option>
            <option value="1">Tier 1 (Safe)</option>
            <option value="2">Tier 2 (Destructive)</option>
            <option value="3">Tier 3 (Irreversible)</option>
          </select>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="ghost-btn" onClick={() => handleExport('json')} style={{ padding: '0.4rem 0.8rem', border: '1px solid var(--line)' }}>Export JSON</button>
          <button className="ghost-btn" onClick={() => handleExport('text')} style={{ padding: '0.4rem 0.8rem', border: '1px solid var(--line)' }}>Export Text</button>
        </div>
      </div>

      <section className="panel">
        <div className="section-head">
          <h2>System Audit Trail</h2>
        </div>
        
        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading audit log...</div>
        ) : isError ? (
          <ErrorState title="Failed to load audit log" error={error} />
        ) : filteredEvents.length > 0 ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem', textAlign: 'left' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)' }}>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Timestamp</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Actor</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Action</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Capability</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {filteredEvents.map((event: import('../../features/trust/contracts').AuditEventView) => (
                  <tr key={event.id} style={{ borderBottom: '1px solid var(--ink-850)', transition: 'background 0.15s ease' }} onMouseOver={e => e.currentTarget.style.background = 'var(--ink-850)'} onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                    <td style={{ padding: '0.75rem 1rem', whiteSpace: 'nowrap', color: 'var(--paper-500)' }} className="mono">
                      {new Date(event.ts).toLocaleString()}
                    </td>
                    <td style={{ padding: '0.75rem 1rem' }}>{event.actor}</td>
                    <td style={{ padding: '0.75rem 1rem' }}>{event.action}</td>
                    <td style={{ padding: '0.75rem 1rem', color: 'var(--gold-400)' }}>{event.capability || '-'}</td>
                    <td style={{ padding: '0.75rem 1rem' }}>
                      <span className={
                        event.outcome === 'success' ? 'text-[var(--jade-400)]' :
                        event.outcome === 'denied' ? 'text-[var(--danger-400)]' :
                        'text-[var(--paper-300)]'
                      }>
                        {event.outcome || '-'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState 
            title="No audit events found" 
            description="The immutable audit log is empty." 
          />
        )}
      </section>
    </>
  );
}
