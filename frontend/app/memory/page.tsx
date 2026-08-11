"use client";

import React, { useState } from 'react';
import { useMemorySearch } from '../../features/trust/queries';

export default function MemoryPage() {
  const [query, setQuery] = useState('');
  const { data: facts, isLoading } = useMemorySearch(query);

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Memory</strong>
      </div>

      <section className="panel">
        <div className="section-head">
          <h2>Semantic Memory</h2>
        </div>

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
            <div className="text-[var(--paper-500)] text-sm py-4">Searching...</div>
          ) : facts && facts.length > 0 ? (
            facts.map((fact: import('../../features/trust/contracts').MemoryFactView) => (
              <div key={fact.id} className="border border-[var(--line)] bg-[var(--ink-850)] p-4 rounded-lg">
                <div className="flex justify-between items-start mb-2">
                  <span className="text-[0.75rem] text-[var(--paper-500)] uppercase tracking-wider font-mono">{fact.kind}</span>
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
            <div className="text-[var(--paper-500)] text-sm py-4">No facts found.</div>
          ) : (
            <div className="text-[var(--paper-500)] text-sm py-4">Enter a query to search ATLAS memory.</div>
          )}
        </div>
      </section>
    </>
  );
}
