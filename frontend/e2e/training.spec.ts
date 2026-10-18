import { test, expect } from '@playwright/test';

test('IDE training prepares local data and never claims missing runtime trained weights', async ({ page }) => {
  await page.route('**/api/v1/ide/workspaces', route => route.fulfill({ json: { workspaces: [
    { workspace_id: 'synthetic-workspace', session_id: 'session', name: 'Synthetic training workspace', root_paths: ['/synthetic'] },
  ] } }));
  await page.route('**/api/v1/ide/training/readiness?**', route => route.fulfill({ json: {
    ready: false, reasons: ['mlx_lm is not installed in the API interpreter'], trainer: 'mlx_lm LoRA',
    installed: false, native_runtime: true, base_artifact_valid: false, training_completed: false,
  } }));
  await page.route('**/api/v1/ide/training/jobs?**', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ide/training/datasets?**', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ide/training/datasets/validate', route => route.fulfill({ json: {
    valid: true, examples: 5, groups: 5, train_count: 3, validation_count: 1, holdout_count: 1,
  } }));
  await page.route('**/api/v1/ide/training/datasets/prepare', async route => {
    expect(route.request().postDataJSON().approved).toBe(true);
    await route.fulfill({ json: { export_id: 'synthetic-export', path: '.atlas-training/exports/synthetic-export',
      digest: 'synthetic-hash', train_count: 3, validation_count: 1, holdout_count: 1, training_completed: false } });
  });
  await page.goto('/ide/training');
  await expect(page.getByRole('navigation', { name: 'IDE navigation' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Training workspace' }).selectOption('synthetic-workspace');
  await page.getByLabel('Workspace-relative dataset path').fill('datasets/synthetic.jsonl');
  await expect(page.getByRole('button', { name: 'Prepare local export' })).toBeDisabled();
  await page.getByRole('button', { name: 'Validate dataset' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Validated 5 traces' })).toBeVisible();
  await page.getByLabel('I reviewed the source for private/secret content and approve its local export.').check();
  await page.getByRole('button', { name: 'Prepare local export' }).click();
  await expect(page.getByText('Prepared; weight training not completed')).toBeVisible();
  await expect(page.getByText('Weight training unavailable', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start approved weight training' })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('training keeps failed validation visible and preserves the source path', async ({ page }) => {
  await page.route('**/api/v1/ide/workspaces', route => route.fulfill({ json: { workspaces: [
    { workspace_id: 'workspace', session_id: 's', name: 'Workspace', root_paths: ['/synthetic'] },
  ] } }));
  await page.route('**/api/v1/ide/training/readiness?**', route => route.fulfill({ status: 503, json: { detail: 'training service unavailable' } }));
  await page.route('**/api/v1/ide/training/jobs?**', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ide/training/datasets?**', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ide/training/datasets/validate', route => route.fulfill({ status: 422, json: { detail: 'line 1 contains sensitive data; remove it before export' } }));
  await page.goto('/ide/training?workspace_id=workspace');
  await page.getByLabel('Workspace-relative dataset path').fill('datasets/review.jsonl');
  await page.getByRole('button', { name: 'Validate dataset' }).click();
  await expect(page.getByText('line 1 contains sensitive data; remove it before export')).toBeVisible();
  await expect(page.getByLabel('Workspace-relative dataset path')).toHaveValue('datasets/review.jsonl');
  await expect(page.getByRole('button', { name: 'Start approved weight training' })).toBeDisabled();
});
