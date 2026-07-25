'use client';

import React from 'react';
import { TrustHeader } from '../../components/trust/TrustHeader';
import { useAuditLog } from '../../features/trust/queries';

export default function AuditPage() {
  const { data, isLoading, error } = useAuditLog();

  return (
    <div className="max-w-5xl mx-auto py-8 px-6">
      <h1 className="text-3xl font-serif text-[var(--paper-100)] mb-6">Trust Center</h1>
      <TrustHeader active="audit" />

      <div className="glass-panel p-6 rounded-lg">
        <h2 className="text-lg font-medium mb-4">System Audit Trail</h2>
        
        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm">Loading audit log...</div>
        ) : error ? (
          <div className="text-[var(--danger-400)] text-sm">Error loading audit log.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--line)] text-[var(--paper-500)]">
                  <th className="font-normal py-2 px-2">Timestamp</th>
                  <th className="font-normal py-2 px-2">Actor</th>
                  <th className="font-normal py-2 px-2">Action</th>
                  <th className="font-normal py-2 px-2">Capability</th>
                  <th className="font-normal py-2 px-2">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((event) => (
                  <tr key={event.id} className="border-b border-[var(--line)] last:border-0 hover:bg-[var(--ink-850)] transition-colors">
                    <td className="py-3 px-2 text-[var(--paper-500)] font-mono text-xs whitespace-nowrap">
                      {new Date(event.ts).toLocaleString()}
                    </td>
                    <td className="py-3 px-2">{event.actor}</td>
                    <td className="py-3 px-2">{event.action}</td>
                    <td className="py-3 px-2 text-[var(--gold-400)]">{event.capability || '-'}</td>
                    <td className="py-3 px-2">
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
            {data?.items.length === 0 && (
              <div className="text-center text-[var(--paper-500)] py-8">No audit events found.</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
