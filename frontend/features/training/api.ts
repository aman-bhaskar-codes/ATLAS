import { z } from 'zod';
import { requestContract } from '@/lib/api/client';
import { DatasetValidationSchema, EvaluationSchema, PreparedDatasetSchema, PromotionSchema,
  TrainingJobSchema, TrainingReadinessSchema, VerificationSchema } from './contracts';

const path = '/ide/training';
const post = (body: object): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });
export const trainingApi = {
  readiness: (workspace_id: string, base_path: string) => requestContract(`${path}/readiness?${new URLSearchParams({ workspace_id, base_path })}`, TrainingReadinessSchema),
  validate: (workspace_id: string, source: string) => requestContract(`${path}/datasets/validate`, DatasetValidationSchema, post({ workspace_id, path: source })),
  prepare: (workspace_id: string, source: string, approved: boolean) => requestContract(`${path}/datasets/prepare`, PreparedDatasetSchema, post({ workspace_id, path: source, approved })),
  verify: (workspace_id: string, command: string) => requestContract(`${path}/verification`, VerificationSchema, post({ workspace_id, command }), 650000),
  exportRuns: (workspace_id: string, run_ids: string[], verification_id: string, approved: boolean) => requestContract(`${path}/datasets/export-runs`, PreparedDatasetSchema, post({ workspace_id, run_ids, verification_id, approved })),
  evaluate: (workspace_id: string, export_id: string, baseline_path: string, candidate_path: string, adapter_path: string) => requestContract(`${path}/evaluations`, EvaluationSchema, post({ workspace_id, export_id, baseline_path, candidate_path, adapter_path })),
  evaluateRuntime: (workspace_id: string, export_id: string, baseline_model: string, candidate_model: string, adapter_path: string, approved: boolean) => requestContract(`${path}/evaluations/runtime`, EvaluationSchema, post({ workspace_id, export_id, baseline_model, candidate_model, adapter_path, approved }), 130000),
  promote: (workspace_id: string, evaluation_id: string, adapter_path: string, approved: boolean) => requestContract(`${path}/promotions`, PromotionSchema, post({ workspace_id, evaluation_id, adapter_path, approved })),
  start: (workspace_id: string, export_id: string, base_path: string, approved: boolean) => requestContract(`${path}/jobs`, TrainingJobSchema, post({ workspace_id, export_id, base_path, approved })),
  jobs: (workspace_id: string) => requestContract(`${path}/jobs?${new URLSearchParams({ workspace_id })}`, z.array(TrainingJobSchema)),
  exports: (workspace_id: string) => requestContract(`${path}/datasets?${new URLSearchParams({ workspace_id })}`, z.array(PreparedDatasetSchema)),
  cancel: (workspace_id: string, job_id: string) => requestContract(`${path}/jobs/${encodeURIComponent(job_id)}/cancel?${new URLSearchParams({ workspace_id })}`, TrainingJobSchema, post({})),
};
