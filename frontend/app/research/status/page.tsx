"use client";
import { useRuntimeStatus, deriveAtlasState } from '@/features/runtime-console/useRuntimeStatus';
import { useResearchSessions } from '@/features/research/sessions';
import { ErrorRow } from '@/components/primitives/ErrorState';
export default function ResearchStatusPage() {
  const runtime = useRuntimeStatus();
  const research = useResearchSessions();
  const display = deriveAtlasState(runtime.data, runtime.isError);
  return <section className="panel"><div className="section-head"><h1>Research status</h1></div><div className="research-history">
    <h2>Shared runtime: {display.label}</h2><p>{display.detail}</p>
    {runtime.isError && <ErrorRow error={runtime.error} onRetry={() => void runtime.refetch()} />}
    {research.isLoading && <p role="status">Checking research availability…</p>}
    {research.isError && <ErrorRow error={research.error} onRetry={() => void research.refetch()} />}
    {research.data && <p>Research session service responded · {research.data.sessions.length} recent sessions loaded.</p>}
  </div></section>;
}
