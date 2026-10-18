"use client";

import React, { useRef, useState, useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { atlasApi } from '@/lib/api/client';
import { getApiCredential, setApiCredential, subscribeCredentialChange } from '@/lib/api/auth';
import { Button } from '@/components/primitives/Button';
import { ErrorState } from '@/components/primitives/ErrorState';

/**
 * Settings — runtime posture and the current tab’s API credential.
 * Configuration lives in typed YAML/env (never editable through the UI for
 * safety); this page surfaces the effective runtime state per group.
 */
export default function SettingsPage() {
  const status = useQuery({ queryKey: ["runtime-status"], queryFn: atlasApi.runtimeStatus, refetchInterval: 15000 });
  const health = useQuery({ queryKey: ["runtime-health"], queryFn: atlasApi.runtimeHealth, refetchInterval: 30000 });

  const credentialInputRef = useRef<HTMLInputElement>(null);
  const [credential, setCredential] = useState("");
  const [credentialError, setCredentialError] = useState<string | null>(null);
  const [credentialNotice, setCredentialNotice] = useState<string | null>(null);
  const hasCredential = Boolean(useSyncExternalStore(subscribeCredentialChange, getApiCredential, () => ""));

  const saveCredential = (value: string) => {
    try {
      setApiCredential(value);
      setCredential("");
      setCredentialError(null);
      setCredentialNotice(value.trim() ? "API key saved for this tab." : "API key removed.");
    } catch (error) {
      setCredentialError(error instanceof Error ? error.message : "Could not save the API key. Check browser storage permissions.");
      setCredentialNotice(null);
      credentialInputRef.current?.focus();
    }
  };

  const s = status.data;
  const err = status.error ?? health.error;

  const groups: { title: string; rows: [string, string][] }[] = [
    {
      title: 'General',
      rows: [
        ['Runtime state', s?.state ?? '—'],
        ['Version', s?.version ?? '—'],
        ['Environment', s?.environment ?? '—'],
      ],
    },
    {
      title: 'Safety',
      rows: [
        ['Kill switch', s?.kill_switch_active ? 'ACTIVE (all execution halted)' : 'inactive'],
        // '—' rather than `?? 0`: before the first response lands there is no basis
        // for claiming zero pending approvals or zero active tasks, and those two
        // numbers are exactly the ones an operator would act on.
        ['Pending approvals', s === undefined ? '—' : String(s.pending_approval_count)],
      ],
    },
    {
      title: 'Runtime',
      rows: [
        ['Active tasks', s === undefined ? '—' : String(s.active_task_count)],
        ['Overall health', health.data?.overall ?? '—'],
      ],
    },
  ];

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Settings</strong></div>

      <section className="panel mb-6" aria-labelledby="api-access-title">
        <div className="section-head"><h2 id="api-access-title">API access</h2></div>
        <form noValidate className="p-4 flex flex-col gap-3" onSubmit={(event) => { event.preventDefault(); saveCredential(credential); }}>
          <p id="api-key-help" className="text-sm text-[var(--paper-500)]">
            Enter the API key configured for your ATLAS backend. It is saved for this tab’s session.
          </p>
          <label htmlFor="api-key" className="text-sm text-[var(--paper-300)]">API key</label>
          <input ref={credentialInputRef} id="api-key" type="password" value={credential} autoComplete="off" spellCheck={false}
            aria-describedby={credentialError ? "api-key-help api-key-error" : "api-key-help api-key-status"} aria-invalid={Boolean(credentialError)}
            className="ui-input"
            onChange={(event) => { setCredential(event.target.value); setCredentialError(null); }} />
          {credentialError && <p id="api-key-error" role="alert" className="text-sm text-[var(--ember-400)]">{credentialError}</p>}
          <p id="api-key-status" role="status" className="text-sm text-[var(--paper-500)]">
            {credentialNotice ?? (hasCredential ? "An API key is saved for this tab." : "No API key is saved. Local backends without authentication can connect directly.")}
          </p>
          <div className="flex flex-wrap gap-3">
            <Button type="submit" disabled={!credential.trim()}>Save API key</Button>
            <Button type="button" variant="secondary" disabled={!hasCredential} onClick={() => saveCredential("")}>Remove API key</Button>
          </div>
        </form>
      </section>

      {err ? (
        <ErrorState
          title="Failed to load settings"
          error={err}
          onRetry={() => {
            void status.refetch();
            void health.refetch();
          }}
        />
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
            {groups.map(g => (
              <section className="panel" key={g.title}>
                <div className="section-head"><h2>{g.title}</h2></div>
                <div style={{ padding: '0 1rem 1rem 1rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {g.rows.map(([label, value]) => (
                    <div key={label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem' }}>
                      <span style={{ color: 'var(--paper-500)' }}>{label}</span>
                      <span style={{ color: 'var(--paper-100)' }}>{value}</span>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>

          <section className="panel">
            <div className="section-head">
              <h2>Health Checks</h2>
              <span style={{ fontSize: '0.8rem', color: 'var(--paper-500)' }}>
                Effective configuration is typed and validated at startup (config/*.yaml + env). Safety policy is never editable here.
              </span>
            </div>
            {health.isLoading ? (
              <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading health checks…</div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)', textAlign: 'left' }}>
                    <th style={{ padding: '0.5rem 1rem' }}>Check</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Status</th>
                    <th style={{ padding: '0.5rem 1rem' }}>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {(health.data?.checks ?? []).map(c => (
                    <tr key={c.name} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-100)' }}>{c.name}</td>
                      <td style={{ padding: '0.5rem 1rem' }}>
                        <span className="badge" style={{
                          borderColor: c.status === 'pass' ? 'var(--jade-500)' : c.status === 'warn' ? 'var(--gold-500)' : 'var(--ember-500)',
                          color: c.status === 'pass' ? 'var(--jade-400)' : c.status === 'warn' ? 'var(--gold-400)' : 'var(--ember-400)',
                        }}>
                          {c.status}
                        </span>
                      </td>
                      <td style={{ padding: '0.5rem 1rem', color: 'var(--paper-500)' }}>{c.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </>
  );
}
