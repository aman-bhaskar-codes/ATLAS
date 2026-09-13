"use client";

import React, { useState } from 'react';
import { useWorkspaces, useWorkspaceTree, useWorkspaceGitStatus, useOpenWorkspace } from '@/features/workspace/queries';
import type { Workspace } from '@/features/workspace/contracts';
import { Folder, GitBranch, Plus, FileCode, CheckCircle, AlertTriangle } from 'lucide-react';
import { ErrorRow } from '@/components/primitives/ErrorState';
import { AtlasApiError } from '@/lib/api/client';

function isSubsystemDisabled(error: unknown): boolean {
  return error instanceof AtlasApiError && error.status === 503;
}

export function WorkspacesDashboard() {
  const { data, isLoading, isError, error, refetch } = useWorkspaces();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  
  const workspaces = data?.workspaces || [];

  // If the IDE subsystem is disabled at the config level, show a clear message
  if (isError && isSubsystemDisabled(error)) {
    return (
      <div className="panel" style={{ padding: '3rem', textAlign: 'center' }}>
        <AlertTriangle size={48} style={{ color: 'var(--gold-500)', marginBottom: '1rem' }} />
        <h2 style={{ color: 'var(--paper-100)', margin: '0 0 0.5rem 0' }}>IDE Subsystem Disabled</h2>
        <p style={{ color: 'var(--paper-400)', maxWidth: '520px', margin: '0 auto 1.5rem auto', lineHeight: 1.6 }}>
          The ADE (Agentic Development Environment) is currently turned off. To enable Workspaces,
          set <code style={{ background: 'var(--ink-800)', padding: '0.15rem 0.4rem', borderRadius: '3px', color: 'var(--gold-400)' }}>ide.enabled: true</code> in{' '}
          <code style={{ background: 'var(--ink-800)', padding: '0.15rem 0.4rem', borderRadius: '3px', color: 'var(--paper-200)' }}>config/settings.yaml</code>{' '}
          and restart the ATLAS runtime.
        </p>
        <div style={{ background: 'var(--ink-900)', border: '1px solid var(--line)', borderRadius: '6px', padding: '1rem 1.5rem', display: 'inline-block', textAlign: 'left', fontFamily: 'var(--font-mono, monospace)', fontSize: '0.85rem', lineHeight: 1.8 }}>
          <span style={{ color: 'var(--paper-500)' }}># config/settings.yaml</span><br />
          <span style={{ color: 'var(--paper-200)' }}>ide:</span><br />
          <span style={{ color: 'var(--paper-200)' }}>{'  '}<span style={{ color: '#22c55e' }}>enabled: true</span></span>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', gap: '1.5rem', height: '100%' }}>
      {/* Left panel: List of workspaces */}
      <div className="panel" style={{ width: '350px', display: 'flex', flexDirection: 'column' }}>
        <div className="section-head" style={{ padding: '1rem', borderBottom: '1px solid var(--line)' }}>
          <h3 style={{ margin: 0, color: 'var(--paper-100)', fontSize: '1rem' }}>Workspaces</h3>
        </div>
        
        <div style={{ padding: '1rem', flex: 1, overflowY: 'auto' }}>
          {isLoading && <div style={{ color: 'var(--paper-500)', fontSize: '0.85rem' }}>Loading workspaces...</div>}
          {isError && <ErrorRow error={error} onRetry={() => refetch()} />}
          
          {!isLoading && workspaces.length === 0 && (
            <div style={{ color: 'var(--paper-500)', fontSize: '0.85rem', fontStyle: 'italic', textAlign: 'center', padding: '2rem 0' }}>
              No durable workspaces found.
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {workspaces.map((ws: Workspace) => (
              <div
                key={ws.workspace_id}
                onClick={() => setSelectedWorkspaceId(ws.workspace_id)}
                style={{
                  border: '1px solid',
                  borderColor: selectedWorkspaceId === ws.workspace_id ? 'var(--gold-500)' : 'var(--line)',
                  background: selectedWorkspaceId === ws.workspace_id ? 'var(--ink-850)' : 'var(--ink-900)',
                  padding: '0.75rem 1rem', borderRadius: '4px', cursor: 'pointer',
                  transition: 'all 0.15s ease'
                }}
              >
                <div style={{ fontWeight: 600, color: 'var(--paper-100)', fontSize: '0.9rem', marginBottom: '0.2rem' }}>
                  {ws.name}
                </div>
                <div style={{ color: 'var(--paper-500)', fontSize: '0.75rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {ws.root_paths[0]}
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: '1.5rem', paddingTop: '1rem', borderTop: '1px solid var(--line)' }}>
            <CreateWorkspaceForm />
          </div>
        </div>
      </div>

      {/* Right panel: Workspace details */}
      <div className="panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {selectedWorkspaceId ? (
          <WorkspaceDetails workspaceId={selectedWorkspaceId} workspaces={workspaces} />
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--paper-500)', fontStyle: 'italic' }}>
            Select a workspace to view details
          </div>
        )}
      </div>
    </div>
  );
}

function CreateWorkspaceForm() {
  const [name, setName] = useState('');
  const [rootPath, setRootPath] = useState('');
  const openMutation = useOpenWorkspace();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !rootPath.trim()) return;
    openMutation.mutate({ name, root_path: rootPath });
    setName('');
    setRootPath('');
  };

  return (
    <div>
      <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--paper-100)', marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
        <Plus size={14} /> Open Workspace
      </div>
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <input
          type="text"
          placeholder="Workspace Name"
          value={name}
          onChange={e => setName(e.target.value)}
          required
          style={{
            background: 'var(--ink-950)', border: '1px solid var(--line)', color: 'var(--paper-100)',
            padding: '0.5rem', borderRadius: '4px', fontSize: '0.8rem', outline: 'none'
          }}
        />
        <input
          type="text"
          placeholder="Absolute Path (/Users/...)"
          value={rootPath}
          onChange={e => setRootPath(e.target.value)}
          required
          style={{
            background: 'var(--ink-950)', border: '1px solid var(--line)', color: 'var(--paper-100)',
            padding: '0.5rem', borderRadius: '4px', fontSize: '0.8rem', outline: 'none'
          }}
        />
        <button
          type="submit"
          disabled={openMutation.isPending || !name.trim() || !rootPath.trim()}
          style={{
            background: 'var(--gold-500)', color: 'var(--ink-950)', fontWeight: 600,
            border: 'none', padding: '0.5rem', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem',
            opacity: openMutation.isPending ? 0.7 : 1
          }}
        >
          {openMutation.isPending ? 'Opening...' : 'Open'}
        </button>
      </form>
      {openMutation.isError && (
        <div style={{ color: 'var(--error)', fontSize: '0.75rem', marginTop: '0.5rem' }}>
          Error: {openMutation.error?.message}
        </div>
      )}
    </div>
  );
}

function WorkspaceDetails({ workspaceId, workspaces }: { workspaceId: string, workspaces: Workspace[] }) {
  const workspace = workspaces.find(w => w.workspace_id === workspaceId);
  const { data: tree, isLoading: treeLoading, isError: treeError } = useWorkspaceTree(workspaceId);
  const { data: git, isLoading: gitLoading } = useWorkspaceGitStatus(workspaceId);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="section-head" style={{ padding: '1.25rem', borderBottom: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ margin: 0, color: 'var(--paper-100)', fontSize: '1.25rem', marginBottom: '0.2rem' }}>
            {workspace?.name}
          </h2>
          <div className="mono" style={{ fontSize: '0.75rem', color: 'var(--paper-500)' }}>
            {workspace?.root_paths[0]}
          </div>
        </div>
        <div style={{ fontSize: '0.7rem', color: 'var(--paper-500)', background: 'var(--ink-850)', padding: '0.2rem 0.5rem', borderRadius: '4px', border: '1px solid var(--line)' }}>
          ID: {workspaceId.substring(0, 8)}...
        </div>
      </div>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        
        {/* File Tree */}
        <div style={{ flex: 1, borderRight: '1px solid var(--line)', padding: '1rem', overflowY: 'auto' }}>
          <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--paper-300)', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            <Folder size={14} /> Explorer
          </div>
          
          {treeLoading ? (
            <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>Loading tree...</div>
          ) : treeError ? (
            <div style={{ fontSize: '0.8rem', color: 'var(--error)' }}>Failed to load tree</div>
          ) : tree?.nodes && tree.nodes.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              {tree.nodes.map((node, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.2rem 0.5rem', fontSize: '0.85rem', color: node.is_dir ? 'var(--gold-400)' : 'var(--paper-200)' }}>
                  {node.is_dir ? <Folder size={14} /> : <FileCode size={14} style={{ color: 'var(--paper-500)' }} />}
                  <span style={{ cursor: 'default' }}>{node.path}</span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)', fontStyle: 'italic' }}>Tree is empty</div>
          )}
        </div>

        {/* Git Status */}
        <div style={{ width: '350px', padding: '1rem', overflowY: 'auto', background: 'var(--ink-950)' }}>
          <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--paper-300)', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            <GitBranch size={14} /> Source Control
          </div>

          {gitLoading ? (
            <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>Checking git status...</div>
          ) : !git?.is_git_repo ? (
            <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)', fontStyle: 'italic' }}>Not a git repository</div>
          ) : (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.75rem', background: 'var(--ink-900)', border: '1px solid var(--line)', borderRadius: '4px', marginBottom: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.9rem', color: 'var(--paper-100)' }}>
                  <GitBranch size={14} style={{ color: 'var(--gold-400)' }} />
                  {git.branch}
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', fontSize: '0.75rem' }}>
                  {git.ahead > 0 && <span style={{ color: '#22c55e' }}>↑{git.ahead}</span>}
                  {git.behind > 0 && <span style={{ color: 'var(--error)' }}>↓{git.behind}</span>}
                  {git.ahead === 0 && git.behind === 0 && <CheckCircle size={14} style={{ color: '#22c55e' }} />}
                </div>
              </div>

              {git.changes.length === 0 ? (
                <div style={{ fontSize: '0.8rem', color: 'var(--paper-500)', textAlign: 'center', padding: '1rem' }}>
                  Working tree clean
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                  {git.changes.map((change, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.3rem 0.5rem', fontSize: '0.8rem', borderLeft: change.staged ? '2px solid #22c55e' : '2px solid var(--line)' }}>
                      <span style={{ color: 'var(--paper-200)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {change.path}
                      </span>
                      <span style={{ 
                        color: change.state === 'M' || change.state === 'AM' ? '#eab308' : change.state === 'A' || change.state === '??' ? '#22c55e' : 'var(--error)',
                        fontWeight: 600, fontSize: '0.7rem' 
                      }}>
                        {change.state}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
