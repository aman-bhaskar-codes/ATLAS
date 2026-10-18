'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useWorkspaces } from '@/features/workspace/queries';
import { trainingApi } from '@/features/training/api';
import { canStartTraining, type Evaluation, type PreparedDataset } from '@/features/training/contracts';
import { Button } from '@/components/primitives/Button';
import { Panel } from '@/components/primitives/Panel';
import { ErrorRow } from '@/components/primitives/ErrorState';

const inputClass = 'w-full rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--surface-sunken)] p-2 text-sm text-[var(--text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]';

export function TrainingWorkspace({ initialWorkspace = '' }: { initialWorkspace?: string }) {
  const router = useRouter();
  const workspaces = useWorkspaces();
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [source, setSource] = useState('');
  const [base, setBase] = useState('');
  const [command, setCommand] = useState('pytest -q');
  const [runIds, setRunIds] = useState('');
  const [proof, setProof] = useState('');
  const [approvedExport, setApprovedExport] = useState(false);
  const [approvedCompute, setApprovedCompute] = useState(false);
  const [approvedPromotion, setApprovedPromotion] = useState(false);
  const [exported, setExported] = useState<PreparedDataset | null>(null);
  const [baseline, setBaseline] = useState('');
  const [candidate, setCandidate] = useState('');
  const [adapter, setAdapter] = useState('');
  const [baselineModel, setBaselineModel] = useState('');
  const [candidateModel, setCandidateModel] = useState('');
  const [approvedEvaluation, setApprovedEvaluation] = useState(false);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState<unknown>(null);
  const pending = useRef(false);
  const [checkedBase, setCheckedBase] = useState('');
  const readiness = useQuery({ queryKey: ['training', 'readiness', workspace, checkedBase],
    queryFn: () => trainingApi.readiness(workspace, checkedBase), enabled: !!workspace, retry: false });
  const jobs = useQuery({ queryKey: ['training', 'jobs', workspace], queryFn: () => trainingApi.jobs(workspace),
    enabled: !!workspace, retry: false, refetchInterval: 5000 });
  const exports = useQuery({ queryKey: ['training', 'exports', workspace], queryFn: () => trainingApi.exports(workspace),
    enabled: !!workspace, retry: false });

  async function action(label: string, work: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(label); setError(null); setFeedback('');
    try { await work(); } catch (caught) { setError(caught); }
    finally { pending.current = false; setBusy(''); }
  }
  function selectWorkspace(value: string) {
    setWorkspace(value); setExported(null); setEvaluation(null); setProof(''); setError(null); setFeedback('');
    setApprovedExport(false); setApprovedCompute(false); setApprovedPromotion(false); setApprovedEvaluation(false); setCheckedBase('');
    router.replace(`/ide/training${value ? `?workspace_id=${encodeURIComponent(value)}` : ''}`);
  }
  const disabled = !workspace || !!busy;
  const runtimeReady = !!readiness.data?.ready && checkedBase === base && !readiness.isFetching;
  return <div className="space-y-6">
    <h1>IDE model training</h1>
    <p className="platform-description">Prepare reviewed tool-call traces, reserve independent task groups, and compare held-out predictions. Weight training is a separate approved local job.</p>
    <label className="block max-w-xl">Workspace
      <select aria-label="Training workspace" className={`${inputClass} cursor-pointer`} value={workspace} onChange={event => selectWorkspace(event.target.value)} disabled={!!busy}>
        <option value="">Select a workspace</option>
        {(workspaces.data?.workspaces ?? []).map(item => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}
      </select>
    </label>
    {workspaces.isPending && <p role="status">Loading workspaces…</p>}
    {Boolean(workspaces.error) && <ErrorRow error={workspaces.error} onRetry={() => { void workspaces.refetch(); }} />}
    {!workspaces.isPending && !workspaces.error && workspaces.data?.workspaces.length === 0 && <p>No workspace is open. <Link href="/workspaces" className="text-[var(--accent)] underline">Open an IDE workspace</Link> first.</p>}
    <div className="min-h-10" aria-live="polite">{busy ? <p>{busy}</p> : feedback && <p role="status">{feedback}</p>}{Boolean(error) && <ErrorRow error={error} />}</div>
    <div className="grid items-start gap-6 lg:grid-cols-2">
      <Panel title="1. Validate and approve a local dataset">
        <div className="space-y-4">
          <p className="text-sm text-[var(--text-secondary)]">UTF-8 JSONL, up to 2 MiB / 2,000 complete traces. At least five independent task groups are required. No dataset is uploaded.</p>
          <label className="block">Previously approved local export<select className={`${inputClass} cursor-pointer`} value={exported?.export_id ?? ''} disabled={disabled} onChange={event => { setExported(exports.data?.find(item => item.export_id === event.target.value) ?? null); setEvaluation(null); setApprovedCompute(false); }}><option value="">Select a prepared export or prepare new data</option>{exports.data?.map(item => <option key={item.export_id} value={item.export_id}>{item.export_id} · {item.train_count} training traces</option>)}</select></label>
          {exports.error && <ErrorRow error={exports.error} onRetry={() => { void exports.refetch(); }} />}
          <label className="block">Workspace-relative dataset path<input className={inputClass} value={source} onChange={event => { setSource(event.target.value); setApprovedExport(false); }} placeholder="datasets/reviewed-traces.jsonl" disabled={!!busy} /></label>
          <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={approvedExport} onChange={event => setApprovedExport(event.target.checked)} disabled={!!busy} />I reviewed the source for private/secret content and approve its local export.</label>
          <div className="flex flex-wrap gap-3">
            <Button className="cursor-pointer" variant="secondary" disabled={disabled || !source} onClick={() => action('Validating dataset…', async () => { const result = await trainingApi.validate(workspace, source); setFeedback(`Validated ${result.examples} traces in ${result.groups} task groups. Split: ${result.train_count} train / ${result.validation_count} validation / ${result.holdout_count} untouched holdout.`); })}>Validate dataset</Button>
            <Button className="cursor-pointer" disabled={disabled || !source || !approvedExport} onClick={() => action('Preparing approved local export…', async () => { const result = await trainingApi.prepare(workspace, source, approvedExport); setExported(result); setEvaluation(null); setApprovedCompute(false); setFeedback('Dataset prepared locally. No weights have been trained.'); await exports.refetch(); })}>Prepare local export</Button>
          </div>
          {exported && <div className="overflow-wrap-anywhere text-sm"><p>Export <code className="break-all">{exported.export_id}</code></p><p>{exported.train_count} train · {exported.validation_count} validation · {exported.holdout_count} holdout</p><p className="break-all">{exported.path}</p><strong>Prepared; weight training not completed</strong></div>}
        </div>
      </Panel>
      <Panel title="2. Export verified agent runs">
        <div className="space-y-4">
          <p className="text-sm text-[var(--text-secondary)]">Use a real IDE test outcome and explicitly selected completed runs from this workspace. Recognized secrets and unsupported image traces are refused.</p>
          <label className="block">Governed verification command<input className={inputClass} value={command} onChange={event => setCommand(event.target.value)} disabled={!!busy} /></label>
          <Button className="cursor-pointer" variant="secondary" disabled={disabled || !command} onClick={() => action('Running governed verification; approval may be required…', async () => { const result = await trainingApi.verify(workspace, command); setProof(result.ok ? result.verification_id : ''); setFeedback(result.ok ? `Verified ${result.passed} passing tests. Proof ${result.verification_id}.` : 'Verification did not establish passing tests; export remains blocked.'); })}>Verify workspace</Button>
          <label className="block">Reviewed run IDs, one per line<textarea className={`${inputClass} resize-y min-h-24`} value={runIds} onChange={event => setRunIds(event.target.value)} disabled={!!busy} /></label>
          <p className="text-sm text-[var(--text-secondary)]">The local export consent in step 1 also applies to these selected runs.</p>
          <Button className="cursor-pointer" disabled={disabled || !proof || !approvedExport || runIds.trim().split(/\s+/).length < 5} onClick={() => action('Exporting reviewed verified runs…', async () => { const result = await trainingApi.exportRuns(workspace, runIds.trim().split(/\s+/), proof, approvedExport); setExported(result); setEvaluation(null); setApprovedCompute(false); setFeedback('Verified traces exported locally. No weight training started.'); await exports.refetch(); })}>Export selected runs</Button>
        </div>
      </Panel>
      <Panel title="3. Check compute readiness and train weights">
        <div className="space-y-4">
          <p className="text-sm text-[var(--text-secondary)]">Optional MLX LoRA on native Apple silicon: local 4-bit checkpoint, one job, batch 1, sequence 512, four layers, a 4 GiB MLX allocation guideline and a 15-minute default timeout. Ollama GGUF is not a training checkpoint.</p>
          <label className="block">Workspace-relative MLX base checkpoint<input className={inputClass} value={base} onChange={event => { setBase(event.target.value); setApprovedCompute(false); }} placeholder="models/local-mlx-base" disabled={!!busy} /></label>
          <Button className="cursor-pointer" variant="secondary" disabled={disabled || !base || readiness.isFetching} onClick={() => { if (checkedBase === base) { void readiness.refetch(); } else { setCheckedBase(base); } }}>Check trainer readiness</Button>
          {readiness.isPending && workspace && <p role="status">Checking local trainer…</p>}
          {readiness.error && <ErrorRow error={readiness.error} onRetry={() => { void readiness.refetch(); }} />}
          {readiness.data && <div role="status"><strong>{runtimeReady ? 'Local trainer ready' : 'Weight training unavailable'}</strong><ul className="mt-2 list-disc pl-5 text-sm">{readiness.data.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
          <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={approvedCompute} onChange={event => setApprovedCompute(event.target.checked)} disabled={!!busy} />I approve this prepared dataset and the bounded local compute job.</label>
          <Button className="cursor-pointer" disabled={disabled || !canStartTraining(runtimeReady, exported?.export_id ?? '', approvedCompute)} onClick={() => action('Submitting approved local weight-training job…', async () => { const job = await trainingApi.start(workspace, exported!.export_id, base, approvedCompute); setFeedback(`Job ${job.job_id} queued. Tool approval may still be required; queued is not trained.`); await jobs.refetch(); })}>Start approved weight training</Button>
        </div>
      </Panel>
      <Panel title="4. Evaluate a candidate on the untouched holdout">
        <div className="space-y-4">
          <p className="text-sm text-[var(--text-secondary)]">Score baseline and candidate prediction JSONL from the same held-out cases. This comparison does not generate predictions or train a model.</p>
          <label className="block">Baseline predictions path<input className={inputClass} value={baseline} onChange={event => { setBaseline(event.target.value); setEvaluation(null); setApprovedPromotion(false); }} disabled={!!busy} /></label>
          <label className="block">Candidate predictions path<input className={inputClass} value={candidate} onChange={event => { setCandidate(event.target.value); setEvaluation(null); setApprovedPromotion(false); }} disabled={!!busy} /></label>
          <label className="block">Candidate adapter directory (optional for scoring)<input className={inputClass} value={adapter} onChange={event => { setAdapter(event.target.value); setEvaluation(null); setApprovedPromotion(false); }} disabled={!!busy} /></label>
          <Button className="cursor-pointer" variant="secondary" disabled={disabled || !exported || !baseline || !candidate} onClick={() => action('Evaluating held-out predictions…', async () => { const report = await trainingApi.evaluate(workspace, exported!.export_id, baseline, candidate, adapter); setEvaluation(report); setApprovedPromotion(false); setFeedback('Held-out prediction comparison saved.'); })}>Compare held-out predictions</Button>
          <div className="border-t border-[var(--border-subtle)] pt-4 space-y-3">
            <p className="text-sm text-[var(--text-secondary)]">Generate fresh predictions with registered local models: at most 20 held-out cases, 16 turns per case, and 120 seconds total. Candidate serving must already be bound to this verified trained adapter. Tools are never executed during this benchmark.</p>
            <label className="block">Registered local baseline model ID<input className={inputClass} value={baselineModel} onChange={event => { setBaselineModel(event.target.value); setEvaluation(null); setApprovedEvaluation(false); }} disabled={!!busy} /></label>
            <label className="block">Registered local candidate model ID<input className={inputClass} value={candidateModel} onChange={event => { setCandidateModel(event.target.value); setEvaluation(null); setApprovedEvaluation(false); }} disabled={!!busy} /></label>
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={approvedEvaluation} onChange={event => setApprovedEvaluation(event.target.checked)} disabled={!!busy} />I approve bounded local inference on this holdout and the verified candidate adapter.</label>
            <Button className="cursor-pointer" variant="secondary" disabled={disabled || !exported || !baselineModel || !candidateModel || !adapter || !approvedEvaluation} onClick={() => action('Generating and comparing local held-out predictions…', async () => { const report = await trainingApi.evaluateRuntime(workspace, exported!.export_id, baselineModel, candidateModel, adapter, approvedEvaluation); setEvaluation(report); setApprovedPromotion(false); setFeedback('Actual local runtime evaluation saved.'); })}>Evaluate registered local models</Button>
          </div>
          {evaluation && <div role="status"><strong>{evaluation.gate.passed ? 'Prediction gate passed' : 'Prediction gate refused'}</strong><p>{evaluation.gate.cases} cases · baseline {(evaluation.gate.baseline_score * 100).toFixed(1)}% · candidate {(evaluation.gate.candidate_score * 100).toFixed(1)}% · {evaluation.gate.candidate_unsafe} unapproved calls</p></div>}
          <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={approvedPromotion} onChange={event => setApprovedPromotion(event.target.checked)} disabled={!!busy} />I reviewed the held-out evidence and approve this verified adapter candidate.</label>
          <Button className="cursor-pointer" disabled={disabled || !evaluation?.gate.passed || evaluation.source !== 'local_runtime' || !evaluation.candidate_artifact_sha256 || !adapter || !approvedPromotion} onClick={() => action('Checking artifact and promotion evidence…', async () => { await trainingApi.promote(workspace, evaluation!.evaluation_id, adapter, approvedPromotion); setFeedback('Adapter candidate approved. It has not been activated as a serving model.'); })}>Approve verified adapter candidate</Button>
        </div>
      </Panel>
    </div>
    <Panel title="Local training jobs">
      {jobs.error && <ErrorRow error={jobs.error} onRetry={() => { void jobs.refetch(); }} />}
      {jobs.isPending && workspace && <p role="status">Loading jobs…</p>}
      {!workspace && <p>Select a workspace to inspect its training jobs.</p>}
      {jobs.data?.length === 0 && <p>No local weight-training jobs have been submitted.</p>}
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><caption className="sr-only">Latest 50 local training jobs</caption><thead><tr><th className="p-2">Job</th><th className="p-2">Observed state</th><th className="p-2">Action</th></tr></thead><tbody>{jobs.data?.map(job => <tr key={job.job_id}><td className="p-2 font-mono break-all">{job.job_id}</td><td className="p-2">{job.status === 'trained' ? 'Verified weight artifact' : job.status}</td><td className="p-2">{['queued', 'running'].includes(job.status) && <Button className="cursor-pointer" variant="secondary" size="sm" disabled={!!busy} onClick={() => action('Cancelling local compute…', async () => { await trainingApi.cancel(workspace, job.job_id); await jobs.refetch(); setFeedback('Cancellation recorded.'); })}>Cancel job</Button>}</td></tr>)}</tbody></table></div>
    </Panel>
  </div>;
}
