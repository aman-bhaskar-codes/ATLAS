"use client";

import React, { useState } from 'react';
import { useMemorySearch } from '../../features/trust/queries';
import { Search } from 'lucide-react';
import { EmptyState } from '@/components/primitives/EmptyState';

type MemoryLayer = 'working' | 'episodic' | 'semantic' | 'user';

export default function MemoryPage() {
  const [activeLayer, setActiveLayer] = useState<MemoryLayer>('semantic');
  const [query, setQuery] = useState('');
  const { data: facts, isLoading } = useMemorySearch(query);

  const getCardStyle = (layer: MemoryLayer) => {
    const isActive = activeLayer === layer;
    return {
      padding: '1.25rem', 
      margin: 0, 
      cursor: 'pointer',
      border: isActive ? '1px solid var(--gold-500)' : '1px solid var(--line)',
      background: isActive ? 'var(--ink-850)' : 'var(--ink-950)',
      transition: 'all 0.2s ease'
    };
  };

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Memory</strong>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem', marginBottom: '2rem' }}>
        <div className="panel" style={getCardStyle('working')} onClick={() => setActiveLayer('working')}>
          <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: activeLayer === 'working' ? 'var(--gold-400)' : 'var(--paper-500)', letterSpacing: '0.05em' }}>Working Memory</div>
          <div style={{ fontSize: '1.5rem', color: 'var(--paper-100)', marginTop: '0.5rem' }}>Active</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)', marginTop: '0.25rem' }}>Current Task Context</div>
        </div>
        <div className="panel" style={getCardStyle('episodic')} onClick={() => setActiveLayer('episodic')}>
          <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: activeLayer === 'episodic' ? 'var(--gold-400)' : 'var(--paper-500)', letterSpacing: '0.05em' }}>Episodic Memory</div>
          <div style={{ fontSize: '1.5rem', color: 'var(--paper-100)', marginTop: '0.5rem' }}>1,204</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)', marginTop: '0.25rem' }}>Events Logged</div>
        </div>
        <div className="panel" style={getCardStyle('semantic')} onClick={() => setActiveLayer('semantic')}>
          <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: activeLayer === 'semantic' ? 'var(--gold-400)' : 'var(--paper-500)', letterSpacing: '0.05em' }}>Semantic Memory</div>
          <div style={{ fontSize: '1.5rem', color: 'var(--paper-100)', marginTop: '0.5rem' }}>842</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)', marginTop: '0.25rem' }}>Facts & Knowledge</div>
        </div>
        <div className="panel" style={getCardStyle('user')} onClick={() => setActiveLayer('user')}>
          <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: activeLayer === 'user' ? 'var(--gold-400)' : 'var(--paper-500)', letterSpacing: '0.05em' }}>User Model</div>
          <div style={{ fontSize: '1.5rem', color: 'var(--paper-100)', marginTop: '0.5rem' }}>14</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)', marginTop: '0.25rem' }}>Preferences</div>
        </div>
      </div>

      <div className="grid-cols-panel" style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '2rem' }}>
        {activeLayer === 'semantic' && (
          <section className="panel">
            <div className="section-head">
              <h2>Semantic Search (ChromaDB)</h2>
            </div>

            <div style={{ position: 'relative', marginBottom: '1.5rem', padding: '0 1rem' }}>
              <Search style={{ position: 'absolute', left: '1.75rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--paper-500)', width: '1.25rem', height: '1.25rem' }} />
              <input 
                type="text" 
                placeholder="Search facts and entities..." 
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                style={{ width: '100%', background: 'var(--ink-850)', border: '1px solid var(--line)', borderRadius: '4px', padding: '0.75rem 1rem 0.75rem 2.5rem', color: 'var(--paper-100)', outline: 'none' }}
              />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', padding: '0 1rem 1rem 1rem' }}>
              {isLoading ? (
                <div style={{ color: 'var(--paper-500)', fontSize: '0.9rem', fontStyle: 'italic' }}>Searching vector space...</div>
              ) : facts && facts.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                  {facts.map((fact: import('../../features/trust/contracts').MemoryFactView) => (
                    <div key={fact.id} style={{ border: '1px solid var(--line)', background: 'var(--ink-850)', padding: '1rem', borderRadius: '4px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
                        <span className="mono" style={{ fontSize: '0.75rem', color: 'var(--paper-500)', textTransform: 'uppercase' }}>{fact.kind}</span>
                        <span style={{ fontSize: '0.75rem', color: 'var(--gold-400)' }}>{(fact.confidence * 100).toFixed(0)}% Match</span>
                      </div>
                      <p style={{ color: 'var(--paper-100)', margin: 0, marginBottom: '0.75rem', fontSize: '0.95rem', lineHeight: 1.5 }}>{fact.text}</p>
                      <div style={{ display: 'flex', gap: '1rem', fontSize: '0.75rem', color: 'var(--paper-500)' }}>
                        <span>Updated: {new Date(fact.updated_at).toLocaleDateString()}</span>
                        <span>{fact.provenance_count} sources</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : query ? (
                <EmptyState title="No matches" description="No semantic knowledge matches your query." />
              ) : (
                <EmptyState title="Search Memory" description="Query semantic space to find entity facts, operational knowledge, or definitions." />
              )}
            </div>
          </section>
        )}

        {activeLayer === 'user' && (
          <section className="panel">
            <div className="section-head">
              <h2>User Model</h2>
            </div>
            <div style={{ padding: '0 1rem 1rem 1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <div style={{ border: '1px solid var(--line)', padding: '0.75rem', borderRadius: '4px', background: 'var(--ink-850)' }}>
                <div style={{ fontSize: '0.85rem', color: 'var(--paper-100)', marginBottom: '0.25rem' }}>Never delete source files</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)' }}>Constraint · Confidence 99%</div>
              </div>
              <div style={{ border: '1px solid var(--line)', padding: '0.75rem', borderRadius: '4px', background: 'var(--ink-850)' }}>
                <div style={{ fontSize: '0.85rem', color: 'var(--paper-100)', marginBottom: '0.25rem' }}>Prefers CLI over GUI</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)' }}>Preference · Confidence 85%</div>
              </div>
              <div style={{ border: '1px solid var(--line)', padding: '0.75rem', borderRadius: '4px', background: 'var(--ink-850)' }}>
                <div style={{ fontSize: '0.85rem', color: 'var(--paper-100)', marginBottom: '0.25rem' }}>Uses React for frontend</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)' }}>Stack · Confidence 95%</div>
              </div>
            </div>
          </section>
        )}

        {activeLayer === 'episodic' && (
          <section className="panel">
            <div className="section-head">
              <h2>Recent Episodic</h2>
            </div>
            <div style={{ padding: '0 1rem 1rem 1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <div style={{ fontSize: '0.85rem', color: 'var(--paper-100)' }}>
                Ran semantic search for &quot;python script to fetch top hackernews&quot;
                <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)', marginTop: '0.25rem' }}>10 mins ago</div>
              </div>
              <div style={{ borderTop: '1px solid var(--line)', paddingTop: '0.75rem', fontSize: '0.85rem', color: 'var(--paper-100)' }}>
                Updated dashboard architecture layout
                <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)', marginTop: '0.25rem' }}>1 hour ago</div>
              </div>
              <div style={{ borderTop: '1px solid var(--line)', paddingTop: '0.75rem', fontSize: '0.85rem', color: 'var(--paper-100)' }}>
                Denied calendar.delete_event action
                <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)', marginTop: '0.25rem' }}>1 day ago</div>
              </div>
            </div>
          </section>
        )}

        {activeLayer === 'working' && (
          <section className="panel">
            <div className="section-head">
              <h2>Working Memory</h2>
            </div>
            <div style={{ padding: '0 1rem 1rem 1rem' }}>
              <EmptyState title="No active task context" description="Working memory is volatile and only persists during active execution." />
            </div>
          </section>
        )}
      </div>
    </>
  );
}
