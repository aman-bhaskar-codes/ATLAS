"use client";

/**
 * Live Memory Dashboard — Phase 3, Task 3.7
 *
 * Four memory layers displayed as tabs, each with live WebSocket badge counts
 * and REST-backed data tables that auto-refresh.  The header row shows
 * aggregate stats and a pulsing "LIVE" indicator driven by the WS stream.
 */

import React, { useState, useEffect, useMemo } from 'react';
import { Search, Database, BookOpen, Brain, User, Activity, Zap, Clock } from 'lucide-react';

import { ErrorRow } from '@/components/primitives/ErrorState';
import { useMemoryLive, useLiveCounts } from '../../features/memory/useMemoryLive';
import {
  useMemoryStats, useEpisodes, useFacts,
  useKnowledgeDocs, useKnowledgeSearch, usePreferences,
} from '../../features/memory/queries';
import type { Episode, Fact, KnowledgeDoc, MemoryEvent } from '../../features/memory/contracts';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Layer = 'episodes' | 'facts' | 'knowledge' | 'preferences';

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function LiveBadge({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <span style={{
      marginLeft: '0.4rem',
      background: 'var(--gold-500)',
      color: 'var(--ink-950)',
      fontSize: '0.65rem',
      fontWeight: 700,
      borderRadius: '999px',
      padding: '0.1rem 0.4rem',
      verticalAlign: 'middle',
    }}>
      +{count}
    </span>
  );
}

function PulsingDot({ active }: { active: boolean }) {
  return (
    <span style={{
      display: 'inline-block',
      width: '0.5rem',
      height: '0.5rem',
      borderRadius: '50%',
      background: active ? '#22c55e' : 'var(--paper-500)',
      marginRight: '0.4rem',
      boxShadow: active ? '0 0 0 4px rgba(34,197,94,0.2)' : 'none',
      transition: 'all 0.3s ease',
    }} />
  );
}

function StatCard({
  icon: Icon, label, value, liveCount, active, onClick,
}: {
  icon: React.ElementType;
  label: string;
  value: number | string;
  liveCount?: number;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <div
      className="panel"
      onClick={onClick}
      style={{
        padding: '1.25rem',
        cursor: onClick ? 'pointer' : 'default',
        border: active ? '1px solid var(--gold-500)' : '1px solid var(--line)',
        background: active ? 'var(--ink-850)' : 'var(--ink-950)',
        transition: 'all 0.2s ease',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Subtle glow on active */}
      {active && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: '2px',
          background: 'linear-gradient(90deg, var(--gold-400), var(--gold-600))',
        }} />
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.06em',
            color: active ? 'var(--gold-400)' : 'var(--paper-500)', marginBottom: '0.5rem' }}>
            {label}
          </div>
          <div style={{ fontSize: '1.75rem', color: 'var(--paper-100)', lineHeight: 1 }}>
            {value}
            {liveCount !== undefined && liveCount > 0 && <LiveBadge count={liveCount} />}
          </div>
        </div>
        <Icon size={20} style={{ color: active ? 'var(--gold-400)' : 'var(--paper-600)', marginTop: '0.2rem' }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layer panels
// ---------------------------------------------------------------------------

function EpisodesPanel() {
  const { data: episodes = [], isLoading, isError, error, refetch } = useEpisodes({ limit: 100 });

  if (isLoading) return <PanelSkeleton />;
  // WHY this branch: without it a failed request fell through to "No episodes
  // recorded yet." — a claim about the store that the code has no basis for,
  // since the store was never successfully read.
  if (isError && episodes.length === 0) {
    return <ErrorRow error={error} onRetry={() => void refetch()} />;
  }
  if (episodes.length === 0) return <EmptyLayer label="No episodes recorded yet." />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {/* A failed poll with rows already on screen: keep them, but say so, because
          `refetchInterval` means the list silently stops advancing otherwise. */}
      {isError && <ErrorRow error={error} onRetry={() => void refetch()} />}
      {episodes.map((ep: Episode, i: number) => (
        <div key={ep.id ?? i} style={{
          border: '1px solid var(--line)', borderRadius: '4px',
          background: 'var(--ink-900)', padding: '0.75rem 1rem',
          display: 'grid', gridTemplateColumns: '1fr auto', gap: '0.5rem',
        }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
              <span className="mono" style={{
                fontSize: '0.7rem', textTransform: 'uppercase',
                background: 'var(--ink-800)', border: '1px solid var(--line)',
                padding: '0.1rem 0.4rem', borderRadius: '3px',
                color: ep.salience >= 0.7 ? 'var(--gold-400)' : 'var(--paper-500)',
              }}>
                {ep.kind}
              </span>
              {ep.tool && (
                <span style={{ fontSize: '0.7rem', color: 'var(--paper-500)' }}>
                  🔧 {ep.tool}
                </span>
              )}
            </div>
            <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--paper-200)',
              lineHeight: 1.5, wordBreak: 'break-word' }}>
              {ep.content.length > 180 ? ep.content.slice(0, 180) + '…' : ep.content}
            </p>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <div style={{ fontSize: '0.7rem', color: ep.salience >= 0.7 ? 'var(--gold-400)' : 'var(--paper-500)' }}>
              {(ep.salience * 100).toFixed(0)}%
            </div>
            <div style={{ fontSize: '0.65rem', color: 'var(--paper-600)', marginTop: '0.2rem' }}>
              {new Date(ep.ts).toLocaleTimeString()}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function FactsPanel() {
  const [minConf, setMinConf] = useState(0.5);
  const [kindFilter, setKindFilter] = useState('');
  const { data: facts = [], isLoading, isError, error, refetch } = useFacts({ min_confidence: minConf, kind: kindFilter || undefined, limit: 100 });

  const kinds = useMemo(() => {
    const set = new Set(facts.map((f: Fact) => f.kind));
    return Array.from(set).sort();
  }, [facts]);

  return (
    <div>
      {/* Filter row */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '0.75rem', color: 'var(--paper-500)' }}>Min confidence</span>
          <input type="range" min={0} max={1} step={0.05} value={minConf}
            onChange={e => setMinConf(Number(e.target.value))}
            style={{ width: '100px', accentColor: 'var(--gold-500)' }} />
          <span style={{ fontSize: '0.75rem', color: 'var(--paper-300)', width: '2.5rem' }}>
            {(minConf * 100).toFixed(0)}%
          </span>
        </div>
        <select value={kindFilter} onChange={e => setKindFilter(e.target.value)}
          style={{ background: 'var(--ink-850)', border: '1px solid var(--line)',
            color: 'var(--paper-200)', borderRadius: '4px', padding: '0.3rem 0.5rem',
            fontSize: '0.8rem' }}>
          <option value="">All kinds</option>
          {kinds.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
      </div>

      {isLoading ? <PanelSkeleton /> : isError && facts.length === 0 ? (
        // Deliberately not the empty state: "No facts match the current filters"
        // would blame the user's slider for a request that never completed.
        <ErrorRow error={error} onRetry={() => void refetch()} />
      ) : facts.length === 0 ? (
        <EmptyLayer label="No facts match the current filters." />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {isError && <ErrorRow error={error} onRetry={() => void refetch()} />}
          {facts.map((f: Fact) => (
            <div key={f.id} style={{
              border: '1px solid var(--line)', borderRadius: '4px',
              background: 'var(--ink-900)', padding: '0.75rem 1rem',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.35rem' }}>
                <span className="mono" style={{
                  fontSize: '0.7rem', textTransform: 'uppercase',
                  background: 'var(--ink-800)', border: '1px solid var(--line)',
                  padding: '0.1rem 0.4rem', borderRadius: '3px', color: 'var(--paper-400)',
                }}>
                  {f.kind}
                </span>
                <div style={{ display: 'flex', gap: '0.75rem', fontSize: '0.7rem' }}>
                  <span style={{ color: 'var(--gold-400)' }}>
                    {(f.confidence * 100).toFixed(0)}% conf
                  </span>
                  <span style={{ color: 'var(--paper-500)' }}>
                    v{f.version}
                  </span>
                </div>
              </div>
              <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--paper-200)', lineHeight: 1.5 }}>
                {f.text}
              </p>
              <div style={{ marginTop: '0.4rem', fontSize: '0.65rem', color: 'var(--paper-600)' }}>
                Updated {new Date(f.updated_ts).toLocaleDateString()}
                {f.superseded_by && (
                  <span style={{ marginLeft: '0.5rem', color: 'var(--paper-700)', fontStyle: 'italic' }}>
                    (superseded)
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function KnowledgePanel() {
  const [query, setQuery] = useState('');
  const docsQuery = useKnowledgeDocs(50);
  const searchQuery = useKnowledgeSearch(query);
  const { data: docs = [], isLoading: docsLoading } = docsQuery;
  const { data: searchResults = [], isLoading: searchLoading } = searchQuery;
  const isSearching = query.trim().length > 0;

  return (
    <div>
      {/* Search bar */}
      <div style={{ position: 'relative', marginBottom: '1.25rem' }}>
        <Search size={16} style={{
          position: 'absolute', left: '0.75rem', top: '50%',
          transform: 'translateY(-50%)', color: 'var(--paper-500)',
        }} />
        <input
          type="text"
          placeholder="Semantic search across all documents…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          style={{
            width: '100%', background: 'var(--ink-850)',
            border: '1px solid var(--line)', borderRadius: '4px',
            padding: '0.65rem 1rem 0.65rem 2.25rem',
            color: 'var(--paper-100)', outline: 'none', fontSize: '0.875rem',
          }}
        />
      </div>

      {isSearching ? (
        searchLoading ? <PanelSkeleton /> : searchQuery.isError ? (
          // "No matching chunks found." is a claim about the index. A failed search
          // is a claim about the request, and the two must not look identical.
          <ErrorRow error={searchQuery.error} onRetry={() => void searchQuery.refetch()} />
        ) : searchResults.length === 0 ? (
          <EmptyLayer label="No matching chunks found." />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {searchResults.map(chunk => (
              <div key={chunk.chunk_id} style={{
                border: '1px solid var(--line)', borderRadius: '4px',
                background: 'var(--ink-900)', padding: '0.75rem 1rem',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.35rem' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--gold-400)', fontWeight: 600 }}>
                    {chunk.document_title}
                  </span>
                  <span style={{ fontSize: '0.7rem', color: 'var(--paper-500)' }}>
                    {(chunk.score * 100).toFixed(0)}% match · chunk {chunk.chunk_index + 1}/{chunk.total_chunks}
                  </span>
                </div>
                <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--paper-200)', lineHeight: 1.5 }}>
                  {chunk.content.length > 300 ? chunk.content.slice(0, 300) + '…' : chunk.content}
                </p>
              </div>
            ))}
          </div>
        )
      ) : (
        docsLoading ? <PanelSkeleton /> : docsQuery.isError && docs.length === 0 ? (
          // Not the ingest hint: telling someone to run `atlas knowledge ingest`
          // when the listing simply failed sends them to fix the wrong thing.
          <ErrorRow error={docsQuery.error} onRetry={() => void docsQuery.refetch()} />
        ) : docs.length === 0 ? (
          <EmptyLayer label="No documents ingested yet. Use: atlas knowledge ingest <path>" />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {docsQuery.isError && (
              <ErrorRow error={docsQuery.error} onRetry={() => void docsQuery.refetch()} />
            )}
            {docs.map((doc: KnowledgeDoc) => (
              <div key={doc.id} style={{
                border: '1px solid var(--line)', borderRadius: '4px',
                background: 'var(--ink-900)', padding: '0.75rem 1rem',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <div>
                  <div style={{ fontSize: '0.875rem', color: 'var(--paper-100)', fontWeight: 500, marginBottom: '0.2rem' }}>
                    {doc.title}
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--paper-500)' }}>
                    {doc.source_type.toUpperCase()} · {doc.chunk_count} chunks
                    · {new Date(doc.created_ts).toLocaleDateString()}
                  </div>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <span style={{
                    fontSize: '0.65rem', padding: '0.2rem 0.5rem', borderRadius: '3px',
                    background: doc.indexed ? 'rgba(34,197,94,0.15)' : 'rgba(250,204,21,0.15)',
                    color: doc.indexed ? '#22c55e' : '#facc15',
                    border: `1px solid ${doc.indexed ? '#22c55e40' : '#facc1540'}`,
                  }}>
                    {doc.indexed ? '✓ indexed' : '⟳ indexing'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}

function PreferencesPanel() {
  const { data: prefs, isLoading, isError, error, refetch } = usePreferences();
  const entries = prefs ? Object.entries(prefs) : [];

  if (isLoading) return <PanelSkeleton />;
  // "No preferences learned yet. Interact with ATLAS…" would invite the user to do
  // work that changes nothing, when the real problem is the unread request.
  if (isError && entries.length === 0) {
    return <ErrorRow error={error} onRetry={() => void refetch()} />;
  }
  if (entries.length === 0) return (
    <EmptyLayer label="No preferences learned yet. Interact with ATLAS to build your profile." />
  );

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '0.5rem' }}>
      {isError && <ErrorRow error={error} onRetry={() => void refetch()} />}
      {entries.map(([key, value]) => (
        <div key={key} style={{
          border: '1px solid var(--line)', borderRadius: '4px',
          background: 'var(--ink-900)', padding: '0.75rem 1rem',
        }}>
          <div className="mono" style={{ fontSize: '0.7rem', color: 'var(--gold-400)', marginBottom: '0.25rem',
            textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            {key}
          </div>
          <div style={{ fontSize: '0.875rem', color: 'var(--paper-200)' }}>{String(value)}</div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Live event ticker
// ---------------------------------------------------------------------------

function LiveTicker({ events }: { events: MemoryEvent[] }) {
  if (events.length === 0) return null;
  const latest = events.slice(0, 5);

  return (
    <div style={{
      background: 'var(--ink-900)', border: '1px solid var(--line)',
      borderRadius: '4px', padding: '0.75rem 1rem',
      marginBottom: '1.5rem',
    }}>
      <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--paper-500)',
        letterSpacing: '0.06em', marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
        <Activity size={12} /> Live feed
      </div>
      {latest.map((ev, i) => (
        <div key={i} style={{
          display: 'flex', alignItems: 'baseline', gap: '0.75rem',
          padding: '0.2rem 0',
          borderTop: i > 0 ? '1px solid var(--line)' : 'none',
          opacity: 1 - i * 0.15,
        }}>
          <span className="mono" style={{ fontSize: '0.7rem', color: 'var(--gold-400)', flexShrink: 0 }}>
            {ev.kind}
          </span>
          <span style={{ fontSize: '0.75rem', color: 'var(--paper-400)', overflow: 'hidden',
            textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {ev.items?.[0] ?? ev.memory_type}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeletons / empties
// ---------------------------------------------------------------------------

function PanelSkeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {[1, 2, 3].map(i => (
        <div key={i} style={{
          height: '4.5rem', borderRadius: '4px',
          background: 'linear-gradient(90deg, var(--ink-900) 25%, var(--ink-850) 50%, var(--ink-900) 75%)',
          backgroundSize: '200% 100%',
          animation: 'shimmer 1.5s infinite',
        }} />
      ))}
    </div>
  );
}

function EmptyLayer({ label }: { label: string }) {
  return (
    <div style={{
      padding: '3rem 1rem', textAlign: 'center',
      color: 'var(--paper-500)', fontSize: '0.875rem', fontStyle: 'italic',
    }}>
      {label}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function MemoryPage() {
  const [activeLayer, setActiveLayer] = useState<Layer>('episodes');
  const { events, snapshot, status, updateCount, clearEvents } = useMemoryLive(200);
  const liveCounts = useLiveCounts(events);
  // No isError branch here on purpose: each card already falls back to the WS
  // snapshot and then to '—', which is the honest rendering of "unknown". The
  // failure itself is reported by the panel below and by the connection state
  // beside the breadcrumb, so a third copy would be noise, not information.
  const { data: stats } = useMemoryStats();

  const isLive = status === 'connected';

  return (
    <>
      {/* Breadcrumb */}
      <div className="crumb mb-6">
        ATLAS / <strong>Memory</strong>
        <span style={{ marginLeft: '0.75rem', fontSize: '0.75rem', display: 'inline-flex',
          alignItems: 'center', color: isLive ? '#22c55e' : 'var(--paper-500)' }}>
          <PulsingDot active={isLive} />
          {isLive ? 'LIVE' : status.toUpperCase()}
          {updateCount > 0 && (
            <span style={{ marginLeft: '0.5rem', color: 'var(--paper-500)' }}>
              · {updateCount} update{updateCount !== 1 ? 's' : ''}
            </span>
          )}
        </span>
      </div>

      {/* Stat cards (clicking switches layer) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem', marginBottom: '1.5rem' }}>
        <StatCard
          icon={Clock}
          label="Episodes"
          value={stats?.episode_count ?? snapshot?.episode_count ?? '—'}
          liveCount={liveCounts.episodic}
          active={activeLayer === 'episodes'}
          onClick={() => setActiveLayer('episodes')}
        />
        <StatCard
          icon={Brain}
          label="Semantic Facts"
          value={stats?.fact_count ?? snapshot?.fact_count ?? '—'}
          liveCount={liveCounts.semantic}
          active={activeLayer === 'facts'}
          onClick={() => setActiveLayer('facts')}
        />
        <StatCard
          icon={BookOpen}
          label="Knowledge Docs"
          value={stats?.document_count ?? snapshot?.document_count ?? '—'}
          liveCount={liveCounts.knowledge}
          active={activeLayer === 'knowledge'}
          onClick={() => setActiveLayer('knowledge')}
        />
        <StatCard
          icon={User}
          label="Preferences"
          value={stats?.preference_count ?? snapshot?.preference_count ?? '—'}
          liveCount={liveCounts.user_model}
          active={activeLayer === 'preferences'}
          onClick={() => setActiveLayer('preferences')}
        />
      </div>

      {/* Live feed ticker */}
      {events.length > 0 && <LiveTicker events={events} />}

      {/* Layer content panel */}
      <section className="panel">
        {/* Panel header with tab pills */}
        <div className="section-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: '0.25rem' }}>
            {(['episodes', 'facts', 'knowledge', 'preferences'] as Layer[]).map(layer => (
              <button key={layer} onClick={() => setActiveLayer(layer)} style={{
                padding: '0.3rem 0.75rem', borderRadius: '3px', fontSize: '0.8rem',
                border: '1px solid',
                borderColor: activeLayer === layer ? 'var(--gold-500)' : 'var(--line)',
                background: activeLayer === layer ? 'var(--ink-800)' : 'transparent',
                color: activeLayer === layer ? 'var(--gold-400)' : 'var(--paper-500)',
                cursor: 'pointer', transition: 'all 0.15s ease',
                textTransform: 'capitalize',
              }}>
                {layer}
                {layer === 'episodes' && liveCounts.episodic > 0 && <LiveBadge count={liveCounts.episodic} />}
                {layer === 'facts' && liveCounts.semantic > 0 && <LiveBadge count={liveCounts.semantic} />}
                {layer === 'knowledge' && liveCounts.knowledge > 0 && <LiveBadge count={liveCounts.knowledge} />}
                {layer === 'preferences' && liveCounts.user_model > 0 && <LiveBadge count={liveCounts.user_model} />}
              </button>
            ))}
          </div>
          {events.length > 0 && (
            <button onClick={clearEvents} style={{
              fontSize: '0.7rem', color: 'var(--paper-500)', background: 'none',
              border: '1px solid var(--line)', borderRadius: '3px', padding: '0.2rem 0.5rem',
              cursor: 'pointer',
            }}>
              Clear live counts
            </button>
          )}
        </div>

        <div style={{ padding: '1rem' }}>
          {activeLayer === 'episodes'    && <EpisodesPanel />}
          {activeLayer === 'facts'       && <FactsPanel />}
          {activeLayer === 'knowledge'   && <KnowledgePanel />}
          {activeLayer === 'preferences' && <PreferencesPanel />}
        </div>
      </section>

      {/* CSS shimmer */}
      <style>{`
        @keyframes shimmer {
          0%   { background-position: -200% 0; }
          100% { background-position:  200% 0; }
        }
      `}</style>
    </>
  );
}
