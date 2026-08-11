"use client";

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { atlasApi } from '@/lib/api/client';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';

export default function CapabilitiesPage() {
  const { data: capabilities, isLoading, isError, error } = useQuery({
    queryKey: ["capabilities"],
    queryFn: atlasApi.capabilities,
  });

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Capabilities</strong>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem', marginBottom: '2rem' }}>
        <section className="panel">
          <div className="section-head">
            <h2>Cost Governance</h2>
          </div>
          <div style={{ padding: '0 1rem 1rem 1rem', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.85rem' }}>
                <span style={{ color: 'var(--paper-100)' }}>Daily Budget</span>
                <span style={{ color: 'var(--paper-300)' }}>$0.18 / $1.00</span>
              </div>
              <div style={{ width: '100%', height: '6px', background: 'var(--ink-950)', borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{ width: '18%', height: '100%', background: 'var(--gold-400)' }}></div>
              </div>
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.85rem' }}>
                <span style={{ color: 'var(--paper-100)' }}>Weekly Limit</span>
                <span style={{ color: 'var(--paper-300)' }}>$4.20 / $5.00</span>
              </div>
              <div style={{ width: '100%', height: '6px', background: 'var(--ink-950)', borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{ width: '84%', height: '100%', background: 'var(--ember-400)' }}></div>
              </div>
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.85rem' }}>
                <span style={{ color: 'var(--paper-100)' }}>Per-Task Limit</span>
                <span style={{ color: 'var(--paper-300)' }}>$0.00 / $0.10</span>
              </div>
              <div style={{ width: '100%', height: '6px', background: 'var(--ink-950)', borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{ width: '2%', height: '100%', background: 'var(--jade-400)' }}></div>
              </div>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="section-head">
            <h2>Fallback Chains</h2>
          </div>
          <div style={{ padding: '0 1rem 1rem 1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ border: '1px solid var(--line)', background: 'var(--ink-850)', padding: '1rem', borderRadius: '4px' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)', textTransform: 'uppercase', marginBottom: '0.5rem' }}>Primary Reasoning</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span className="badge" style={{ borderColor: 'var(--jade-500)', color: 'var(--jade-400)' }}>GLM-5.2</span>
                <span style={{ color: 'var(--paper-500)' }}>→</span>
                <span className="badge" style={{ borderColor: 'var(--line)', color: 'var(--paper-300)' }}>Claude 3.5 Sonnet</span>
                <span style={{ color: 'var(--paper-500)' }}>→</span>
                <span className="badge" style={{ borderColor: 'var(--line)', color: 'var(--paper-300)' }}>GPT-4o</span>
              </div>
            </div>
            <div style={{ border: '1px solid var(--line)', background: 'var(--ink-850)', padding: '1rem', borderRadius: '4px' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--paper-500)', textTransform: 'uppercase', marginBottom: '0.5rem' }}>Fast Execution</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span className="badge" style={{ borderColor: 'var(--jade-500)', color: 'var(--jade-400)' }}>Qwen2.5:3B</span>
                <span style={{ color: 'var(--paper-500)' }}>→</span>
                <span className="badge" style={{ borderColor: 'var(--line)', color: 'var(--paper-300)' }}>GLM-4-Flash</span>
              </div>
            </div>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="section-head">
          <h2>Registered Capabilities</h2>
        </div>

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading capabilities...</div>
        ) : isError ? (
          <ErrorState title="Failed to load capabilities" error={error} />
        ) : capabilities && capabilities.length > 0 ? (
          <div className="grid-cols-panel" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '1rem', padding: '1rem' }}>
            {capabilities.map(cap => (
              <div key={cap.name} className="panel" style={{ margin: 0, padding: '1rem', background: 'var(--ink-850)', border: '1px solid var(--line)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
                  <h3 style={{ fontSize: '1.1rem', fontWeight: 500, color: 'var(--paper-100)', margin: 0 }}>{cap.name}</h3>
                  <span className="badge" style={{ 
                    borderColor: cap.state === 'ready' ? 'var(--jade-500)' : 'var(--ember-500)', 
                    color: cap.state === 'ready' ? 'var(--jade-400)' : 'var(--ember-400)',
                    background: cap.state === 'ready' ? 'oklch(73% 0.13 162 / 0.1)' : 'oklch(68% 0.18 22 / 0.1)'
                  }}>
                    {cap.state}
                  </span>
                </div>
                
                <div style={{ fontSize: '0.85rem', color: 'var(--paper-300)', marginBottom: '1rem' }}>
                  {cap.operations.length} operations · {cap.providers} providers
                </div>

                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
                  {cap.operations.slice(0, 5).map(op => (
                    <span key={op} style={{ fontSize: '0.75rem', padding: '0.2rem 0.4rem', background: 'var(--ink-900)', border: '1px solid var(--line)', borderRadius: '4px', color: 'var(--paper-500)' }}>
                      {op}
                    </span>
                  ))}
                  {cap.operations.length > 5 && (
                    <span style={{ fontSize: '0.75rem', padding: '0.2rem 0.4rem', color: 'var(--paper-500)' }}>
                      +{cap.operations.length - 5} more
                    </span>
                  )}
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--line)', paddingTop: '1rem' }}>
                  <div style={{ fontSize: '0.75rem', color: cap.requires_auth ? 'var(--gold-400)' : 'var(--paper-500)' }}>
                    {cap.requires_auth ? 'Authentication required' : 'Public access'}
                  </div>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                    <span style={{ fontSize: '0.75rem', color: 'var(--paper-500)' }}>Enabled</span>
                    <input type="checkbox" defaultChecked={cap.state === 'ready'} style={{ accentColor: 'var(--jade-400)' }} />
                  </label>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState 
            title="No capabilities found" 
            description="The ATLAS agent does not have any registered MCP tool endpoints or plugins." 
          />
        )}
      </section>
    </>
  );
}
