import { test, expect, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

class Composer {
  readonly region;
  constructor(readonly page: Page) { this.region = page.getByRole('region', {name: 'Command composer'}); }
  get start() { return this.region.getByRole('button', {name: 'Start task', exact: true}); }
  get input() { return this.region.getByRole('textbox', {name: 'Task request'}); }
  async attach(name: string, buffer: Buffer, mimeType = 'text/plain') {
    await this.region.getByLabel('Attachment files').setInputFiles({name, buffer, mimeType});
  }
  async remove(name: string) { await this.region.getByRole('button', {name: `Remove ${name}`}).click(); }
}

test('attachment-only composer uploads real content and opens its persisted task', async ({page, request}) => {
  await page.goto('/');
  const composer = new Composer(page);
  await expect(composer.start).toBeDisabled();
  await composer.attach('synthetic.md', Buffer.from('Synthetic upload deadline: Friday'), 'text/markdown');
  await expect(composer.start).toBeEnabled();
  await composer.start.click();
  await expect(page).toHaveURL(/\/tasks\/(?!live$)[^/]+$/);
  const id = new URL(page.url()).pathname.split('/').pop();
  const response = await request.get(`http://localhost:8730/api/v1/tasks/${id}`, { maxRetries: 2 });
  expect(response.ok()).toBe(true);
  expect((await response.json()).request).toContain('Synthetic upload deadline: Friday');
});

test('invalid image upload shows an error in idle composer and preserves the draft', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  const composer = new Composer(page);
  await composer.input.fill('Preserve this request');
  await composer.attach('synthetic.png', Buffer.from([137, 80, 78, 71]), 'image/png');
  await composer.start.click();
  await expect(composer.region.getByRole('status')).toContainText('Image text extraction failed');
  await expect(composer.input).toHaveValue('Preserve this request');
  await expect(composer.start).toBeEnabled();
  await composer.remove('synthetic.png');
  await composer.start.click();
  await expect(page).toHaveURL(/\/tasks\/(?!live$)[^/]+$/);
});

test('rejected task retries keep identity until the draft changes', async ({page}) => {
  await page.goto('/');
  const composer = new Composer(page);
  const submissions: {idempotency_key: string; attachments: {id: string}[]}[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/tasks') submissions.push(request.postDataJSON());
  });
  await composer.attach('long.txt', Buffer.from('a'.repeat(21_000)));
  await composer.start.click();
  await expect(composer.region.getByRole('status')).toContainText('20,000 characters');
  await composer.start.click();
  await expect.poll(() => submissions.length).toBe(2);
  await expect(composer.start).toBeEnabled();
  expect(submissions[1].idempotency_key).toBe(submissions[0].idempotency_key);
  expect(submissions[1].attachments).toEqual(submissions[0].attachments);
  await composer.remove('long.txt');
  await composer.input.fill('Short synthetic request');
  await composer.start.click();
  await expect(page).toHaveURL(/\/tasks\/(?!live$)[^/]+$/);
  expect(submissions[2].idempotency_key).not.toBe(submissions[0].idempotency_key);
});

test('local OCR image text reaches the persisted task', async ({page, request}) => {
  await page.goto('/');
  const composer = new Composer(page);
  const image = await readFile(path.resolve(process.cwd(), '../tests/knowledge/fixtures/orchid_scan.png'));
  await composer.attach('orchid.png', image, 'image/png');
  await composer.start.click();
  await expect(page).toHaveURL(/\/tasks\/(?!live$)[^/]+$/);
  const id = new URL(page.url()).pathname.split('/').pop();
  const response = await request.get(`http://localhost:8730/api/v1/tasks/${id}`, { maxRetries: 2 });
  expect(response.ok()).toBe(true);
  expect((await response.json()).request).toContain('275 megawatts');
});
