"use client";

import React, { useState } from 'react';
import { useKnowledgeDocuments, useKnowledgeSearch, useIngestDocument, useDeleteDocument } from '@/features/knowledge/queries';
import type { SearchResult } from '@/features/knowledge/contracts';
import { Search, Database, BookOpen, Trash2, Plus, UploadCloud } from 'lucide-react';
import { ErrorRow } from '@/components/primitives/ErrorState';

export function KnowledgeDashboard() {
  const [activeTab, setActiveTab] = useState<'documents' | 'search' | 'ingest'>('documents');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', height: '100%' }}>
      <div className="crumb">
        ATLAS / <strong>Knowledge Fabric</strong>
      </div>

      <div className="panel" style={{ display: 'flex', gap: '1rem', padding: '1rem' }}>
        <button
          onClick={() => setActiveTab('documents')}
          style={{
            background: activeTab === 'documents' ? 'var(--gold-500)' : 'var(--ink-800)',
            color: activeTab === 'documents' ? 'var(--ink-950)' : 'var(--paper-100)',
            border: 'none', padding: '0.5rem 1rem', borderRadius: '4px', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600,
          }}
        >
          <Database size={16} />
          Documents
        </button>
        <button
          onClick={() => setActiveTab('search')}
          style={{
            background: activeTab === 'search' ? 'var(--gold-500)' : 'var(--ink-800)',
            color: activeTab === 'search' ? 'var(--ink-950)' : 'var(--paper-100)',
            border: 'none', padding: '0.5rem 1rem', borderRadius: '4px', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600,
          }}
        >
          <Search size={16} />
          Semantic Search
        </button>
        <button
          onClick={() => setActiveTab('ingest')}
          style={{
            background: activeTab === 'ingest' ? 'var(--gold-500)' : 'var(--ink-800)',
            color: activeTab === 'ingest' ? 'var(--ink-950)' : 'var(--paper-100)',
            border: 'none', padding: '0.5rem 1rem', borderRadius: '4px', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600,
          }}
        >
          <Plus size={16} />
          Ingest
        </button>
      </div>

      <div className="panel" style={{ padding: '1.5rem', flex: 1, overflowY: 'auto' }}>
        {activeTab === 'documents' && <DocumentsView />}
        {activeTab === 'search' && <SearchView />}
        {activeTab === 'ingest' && <IngestView />}
      </div>
    </div>
  );
}

function DocumentsView() {
  const { data, isLoading, isError, error, refetch } = useKnowledgeDocuments(100, 0);
  const deleteMutation = useDeleteDocument();

  if (isLoading) return <div>Loading documents...</div>;
  if (isError) return <ErrorRow error={error} onRetry={() => refetch()} />;

  const documents = data?.documents || [];

  if (documents.length === 0) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--paper-500)', fontStyle: 'italic' }}>
        No documents ingested yet. Go to the Ingest tab to add some.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {documents.map(doc => (
        <div key={doc.id} style={{
          border: '1px solid var(--line)', borderRadius: '4px',
          background: 'var(--ink-900)', padding: '1rem',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center'
        }}>
          <div>
            <div style={{ fontSize: '1rem', color: 'var(--paper-100)', fontWeight: 500, marginBottom: '0.35rem' }}>
              {doc.title}
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
              {doc.source_path} · {doc.source_type.toUpperCase()} · {doc.chunk_count} chunks
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--paper-600)', marginTop: '0.2rem' }}>
              Indexed: {new Date(doc.created_ts).toLocaleString()}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
             <span style={{
                fontSize: '0.75rem', padding: '0.25rem 0.5rem', borderRadius: '3px',
                background: doc.indexed ? 'rgba(34,197,94,0.15)' : 'rgba(250,204,21,0.15)',
                color: doc.indexed ? '#22c55e' : '#facc15',
                border: `1px solid ${doc.indexed ? '#22c55e40' : '#facc1540'}`,
              }}>
                {doc.indexed ? '✓ indexed' : '⟳ indexing'}
              </span>
            <button
              onClick={() => {
                if (confirm('Are you sure you want to delete this document?')) {
                  deleteMutation.mutate(doc.id);
                }
              }}
              style={{
                background: 'transparent', border: '1px solid var(--line)',
                color: 'var(--error)', padding: '0.4rem', borderRadius: '4px',
                cursor: 'pointer', display: 'flex', alignItems: 'center',
              }}
              title="Delete Document"
              disabled={deleteMutation.isPending}
            >
              <Trash2 size={16} />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function SearchView() {
  const [query, setQuery] = useState('');
  const { data, isLoading, isError, error, refetch } = useKnowledgeSearch(query);

  const isSearching = query.trim().length > 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ position: 'relative' }}>
        <Search size={18} style={{
          position: 'absolute', left: '1rem', top: '50%',
          transform: 'translateY(-50%)', color: 'var(--paper-500)',
        }} />
        <input
          type="text"
          placeholder="Semantic search across your knowledge base..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          style={{
            width: '100%', background: 'var(--ink-850)',
            border: '1px solid var(--line)', borderRadius: '4px',
            padding: '0.8rem 1rem 0.8rem 2.5rem',
            color: 'var(--paper-100)', outline: 'none', fontSize: '1rem',
          }}
        />
      </div>

      {!isSearching && (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--paper-500)', fontStyle: 'italic' }}>
          Type a query to search.
        </div>
      )}

      {isSearching && isLoading && <div>Searching...</div>}
      
      {isSearching && isError && <ErrorRow error={error} onRetry={() => refetch()} />}

      {isSearching && data?.results && data.results.length === 0 && (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--paper-500)', fontStyle: 'italic' }}>
          No semantic matches found.
        </div>
      )}

      {isSearching && data?.results && data.results.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {data.results.map((chunk: SearchResult) => (
            <div key={chunk.chunk_id} style={{
              border: '1px solid var(--line)', borderRadius: '4px',
              background: 'var(--ink-900)', padding: '1rem',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.85rem', color: 'var(--gold-400)', fontWeight: 600 }}>
                  <BookOpen size={14} style={{ display: 'inline', marginRight: '0.3rem', verticalAlign: 'text-bottom' }}/>
                  {chunk.document_title}
                </span>
                <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                  {(chunk.score * 100).toFixed(0)}% match
                </span>
              </div>
              <p style={{ margin: 0, fontSize: '0.9rem', color: 'var(--paper-200)', lineHeight: 1.6 }}>
                {chunk.content}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function IngestView() {
  const [path, setPath] = useState('');
  const [type, setType] = useState('txt');
  const ingestMutation = useIngestDocument();

  const handleIngest = (e: React.FormEvent) => {
    e.preventDefault();
    if (!path.trim()) return;
    ingestMutation.mutate({ source_path: path, source_type: type });
  };

  return (
    <div style={{ maxWidth: '600px' }}>
      <h3 style={{ marginTop: 0, color: 'var(--paper-100)', marginBottom: '0.5rem' }}>Ingest from Local Path</h3>
      <p style={{ color: 'var(--paper-400)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
        Add a local file or directory to the Knowledge Fabric.
      </p>

      <form onSubmit={handleIngest} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <label style={{ display: 'block', fontSize: '0.8rem', color: 'var(--paper-300)', marginBottom: '0.3rem' }}>
            Absolute Path
          </label>
          <input
            type="text"
            value={path}
            onChange={e => setPath(e.target.value)}
            placeholder="/Users/example/Documents/notes.md"
            style={{
              width: '100%', background: 'var(--ink-850)',
              border: '1px solid var(--line)', borderRadius: '4px',
              padding: '0.6rem 0.8rem', color: 'var(--paper-100)', outline: 'none',
            }}
            required
          />
        </div>
        
        <div>
          <label style={{ display: 'block', fontSize: '0.8rem', color: 'var(--paper-300)', marginBottom: '0.3rem' }}>
            Source Type
          </label>
          <select
            value={type}
            onChange={e => setType(e.target.value)}
            style={{
              width: '100%', background: 'var(--ink-850)',
              border: '1px solid var(--line)', borderRadius: '4px',
              padding: '0.6rem 0.8rem', color: 'var(--paper-100)', outline: 'none',
            }}
          >
            <option value="txt">Text (.txt)</option>
            <option value="markdown">Markdown (.md)</option>
            <option value="pdf">PDF (.pdf)</option>
            <option value="web">Web Page</option>
          </select>
        </div>

        <button
          type="submit"
          disabled={ingestMutation.isPending || !path.trim()}
          style={{
            background: 'var(--gold-500)', color: 'var(--ink-950)',
            border: 'none', padding: '0.75rem 1rem', borderRadius: '4px',
            cursor: ingestMutation.isPending || !path.trim() ? 'not-allowed' : 'pointer',
            fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem',
            marginTop: '0.5rem', opacity: ingestMutation.isPending || !path.trim() ? 0.7 : 1
          }}
        >
          <UploadCloud size={18} />
          {ingestMutation.isPending ? 'Ingesting...' : 'Ingest Document'}
        </button>

        {ingestMutation.isError && (
          <div style={{ color: 'var(--error)', fontSize: '0.9rem', marginTop: '0.5rem', padding: '0.5rem', background: 'rgba(239, 68, 68, 0.1)', borderRadius: '4px' }}>
            Failed to ingest: {ingestMutation.error?.message || 'Unknown error'}
          </div>
        )}
        
        {ingestMutation.isSuccess && (
          <div style={{ color: '#22c55e', fontSize: '0.9rem', marginTop: '0.5rem', padding: '0.5rem', background: 'rgba(34, 197, 94, 0.1)', borderRadius: '4px' }}>
            Successfully ingested document: {ingestMutation.data.title}
          </div>
        )}
      </form>
    </div>
  );
}
