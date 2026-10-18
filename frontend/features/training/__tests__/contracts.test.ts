import { describe, expect, it } from 'vitest';
import { PreparedDatasetSchema, TrainingJobSchema, canStartTraining } from '../contracts';

describe('training truth', () => {
  it('refuses an export described as completed weight training', () => {
    expect(PreparedDatasetSchema.safeParse({ export_id: 'e', path: 'data', train_count: 3, validation_count: 1,
      holdout_count: 1, digest: 'hash', training_completed: true }).success).toBe(false);
  });
  it('requires a verified artifact for a completed job', () => {
    expect(TrainingJobSchema.safeParse({ job_id: 'j', workspace_id: 'w', export_id: 'e', status: 'trained',
      adapter_path: 'adapter', training_completed: true }).success).toBe(false);
  });
  it('never enables weight training for missing runtime, dataset, or compute approval', () => {
    expect(canStartTraining(false, 'export', true)).toBe(false);
    expect(canStartTraining(true, '', true)).toBe(false);
    expect(canStartTraining(true, 'export', false)).toBe(false);
    expect(canStartTraining(true, 'export', true)).toBe(true);
  });
});
