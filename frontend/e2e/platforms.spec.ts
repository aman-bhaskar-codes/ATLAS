import { test, expect } from '@playwright/test';

test('command centre launches separately framed platforms with blocked-popup fallback', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('region', { name: 'Platforms' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Component pages' })).toBeVisible();
  await page.evaluate(() => { window.open = () => null; });
  await page.getByRole('button', { name: 'Launch Research' }).click();
  await expect(page).toHaveURL(/\/research$/);
  await expect(page.getByRole('navigation', { name: 'Research navigation' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toHaveCount(0);
  await expect(page.locator('header.topbar')).toHaveCount(0);
  await page.getByRole('link', { name: 'Return to command centre' }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.evaluate(() => { window.open = () => null; });
  await page.getByRole('button', { name: 'Launch IDE' }).click();
  await expect(page).toHaveURL(/\/workspaces$/);
  await expect(page.getByRole('navigation', { name: 'IDE navigation' })).toBeVisible();
  await expect(page.locator('header.topbar')).toHaveCount(0);
});

test('research corpus, history and status stay within the Research platform', async ({ page }) => {
  for (const path of ['/research/history', '/research/corpus', '/research/status', '/research/unknown-session']) {
    await page.goto(path);
    await expect(page.getByRole('navigation', { name: 'Research navigation' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Return to command centre' })).toBeVisible();
    await expect(page.locator('header.topbar')).toHaveCount(0);
  }
});

test('platform aliases and mobile navigation preserve boundaries', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/ide');
  await expect(page).toHaveURL(/\/workspaces$/);
  await expect(page.getByRole('navigation', { name: 'IDE navigation' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Mobile navigation' })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.goto('/research');
  await expect(page.getByRole('navigation', { name: 'Research navigation' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.goto('/');
  const mobile = page.getByRole('navigation', { name: 'Mobile navigation' });
  await mobile.locator('summary').click();
  await expect(mobile.getByRole('link', { name: 'Research' })).toBeVisible();
  await expect(mobile.getByRole('link', { name: 'IDE', exact: true })).toBeVisible();
});

test('MCP has a focused inspection page without command execution or secret fields', async ({ page }) => {
  await page.goto('/mcp');
  await expect(page.getByRole('heading', { name: 'MCP servers', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Refresh status' })).toBeVisible();
  await expect(page.locator('input[type="password"], textarea')).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toHaveCount(1);
});

test('Research exposes bounded embedding maintenance feedback', async ({ page }) => {
  await page.goto('/research/corpus');
  const maintenance = page.getByRole('region', { name: 'Embedding maintenance' });
  await maintenance.getByRole('button', { name: 'Refresh embedding batch' }).click();
  await expect(maintenance.getByRole('status')).toContainText(/Updated|unavailable|Configure/, { timeout: 35_000 });
});
