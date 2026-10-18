import { z } from 'zod';

export const TrainingReadinessSchema = z.object({
  ready: z.boolean(), reasons: z.array(z.string()), trainer: z.string(), installed: z.boolean(),
  native_runtime: z.boolean(), base_artifact_valid: z.boolean(), training_completed: z.literal(false),
}).refine(value => !value.ready || (value.installed && value.native_runtime && value.base_artifact_valid && value.reasons.length === 0));

export const DatasetValidationSchema = z.object({
  valid: z.literal(true), examples: z.number().int().positive(), groups: z.number().int().min(5),
  train_count: z.number().int().positive(), validation_count: z.number().int().positive(), holdout_count: z.number().int().positive(),
});
export const PreparedDatasetSchema = z.object({
  export_id: z.string(), path: z.string(), digest: z.string(), train_count: z.number().int().positive(),
  validation_count: z.number().int().positive(), holdout_count: z.number().int().positive(), training_completed: z.literal(false),
});
export const VerificationSchema = z.object({
  verification_id: z.string(), workspace_id: z.string(), ok: z.boolean(), passed: z.number().nullable(),
  failed: z.number().nullable(), path: z.string(), created_at: z.string(),
});
export const EvaluationSchema = z.object({
  source: z.enum(['prediction_files', 'local_runtime']).default('prediction_files'),
  evaluation_id: z.string(), export_id: z.string(), candidate_artifact_sha256: z.string().nullable(),
  gate: z.object({ cases: z.number().int().positive(), baseline_score: z.number(), candidate_score: z.number(),
    candidate_unsafe: z.number().int().nonnegative(), passed: z.boolean(), metric: z.string() }),
});
export const TrainingJobSchema = z.object({
  job_id: z.string(), workspace_id: z.string(), export_id: z.string(), adapter_path: z.string(),
  status: z.enum(['queued', 'running', 'trained', 'failed', 'denied', 'cancelled', 'interrupted']),
  training_completed: z.boolean(), artifact_sha256: z.string().length(64).optional(), error: z.string().optional(),
}).refine(value => value.status === 'trained'
  ? value.training_completed && !!value.artifact_sha256 : !value.training_completed);

export const PromotionSchema = z.object({ status: z.literal('candidate_approved'), active_model_registered: z.literal(false) });
export function canStartTraining(ready: boolean, exportId: string, approved: boolean) { return ready && !!exportId && approved; }
export type PreparedDataset = z.infer<typeof PreparedDatasetSchema>;
export type Evaluation = z.infer<typeof EvaluationSchema>;
