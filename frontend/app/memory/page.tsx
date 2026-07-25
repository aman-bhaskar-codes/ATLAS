'use client';

import React, { useState } from 'react';
import { TrustHeader } from '../../components/trust/TrustHeader';
import { useMemorySearch } from '../../features/trust/queries';

export default function MemoryPage() {
  const [query, setQuery] = useState('');
  const { data: facts, isLoading } = useMemorySearch(query);

  return (
    <div className="max-w-5xl mx-auto py-8 px-6">
      <h1 className="text-3xl font-serif text-[var(--paper-100)] mb-6">Trust Center</h1>
      <TrustHeader active="memory" />

      <div className="mb-6">
        <input 
          type="text" 
          placeholder="Search semantic memory..." 
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full bg-[var(--ink-850)] border border-[var(--line)] rounded-lg px-4 py-3 text-[var(--paper-100)] outline-none focus:border-[var(--gold-500)] transition-colors"
        />
      </div>

      <div className="space-y-4">
        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm">Searching...</div>
        ) : facts && facts.length > 0 ? (
          facts.map((fact: import('../../features/trust/contracts').MemoryFactView) => (
            <div key={fact.id} className="glass-panel p-4 rounded-lg">
              <div className="flex justify-between items-start mb-2">
                <span className="text-[0.75rem] text-[var(--paper-500)] uppercase tracking-wider">{fact.kind}</span>
                <span className="text-[0.75rem] text-[var(--gold-400)]">Conf: {(fact.confidence * 100).toFixed(0)}%</span>
              </div>
              <p className="text-[var(--paper-100)] m-0 mb-3">{fact.text}</p>
              <div className="flex gap-4 text-[0.75rem] text-[var(--paper-500)]">
                <span>Updated: {new Date(fact.updated_at).toLocaleDateString()}</span>
                <span>{fact.provenance_count} sources</span>
                <span className={fact.status === 'active' ? 'text-[var(--jade-400)]' : 'text-[var(--ember-400)]'}>
                  {fact.status}
                </span>
              </div>
            </div>
          ))
        ) : query ? (
          <div className="text-[var(--paper-500)] text-sm">No facts found.</div>
        ) : (
          <div className="text-[var(--paper-500)] text-sm">Enter a query to search ATLAS memory.</div>
        )}
      </div>
    </div>
  );
}
