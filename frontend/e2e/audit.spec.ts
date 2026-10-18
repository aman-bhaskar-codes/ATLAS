import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { test, expect } from '@playwright/test';

// Real audit writer, real SQLite test database, real API. Synthetic records use
// a unique actor and valid hash chain; raw payloads must never reach the export.
test.beforeAll(() => {
  const root = path.resolve(process.cwd(), '..');
  execFileSync(path.join(root, '.venv/bin/python'), ['-c', `
import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId
from atlas.infra.types import AuditRecord, Tier
from atlas.safety.audit import AuditLog

async def seed():
    db = Database(Path(os.environ['ATLAS_E2E_DATA_DIR']) / 'atlas.db')
    await db.start()
    try:
        log = AuditLog(db)
        now = datetime.now(UTC)
        existing = await db.conn.execute("SELECT COUNT(*) AS count FROM audit_events WHERE correlation_id='e2e-audit' AND action LIKE 'recent match %' AND julianday(ts) >= julianday(date('now'))")
        if (await existing.fetchone())['count'] >= 55:
            return
        for label, count, tier, ts in [('recent match', 55, Tier.BLOCK, now), ('old match', 3, Tier.BLOCK, now - timedelta(days=60)), ('nonmatch', 60, Tier.NOTIFY, now)]:
            for number in range(count):
                await log.record(AuditRecord(correlation_id=CorrelationId('e2e-audit'), ts=ts, actor='e2e-audit', action=f'{label} {number}', tier=tier, payload={'synthetic_secret': 'never-export-this'}))
    finally:
        await db.stop()
asyncio.run(seed())
`], {cwd: root});
});

test('audit filters search beyond the first page, paginate and download the filtered log', async ({page, request}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/audit');
  await expect(page.getByRole('heading', {name: 'System Audit Trail'})).toBeVisible();
  await page.getByRole('combobox', {name: 'Audit date'}).selectOption('today');
  await page.getByRole('combobox', {name: 'Audit tier'}).selectOption('4');
  const since = new URL(page.url()).searchParams.get('since');
  const reference = await request.get(`http://localhost:8730/api/v1/audit/export?tier=4&since=${encodeURIComponent(since!)}`);
  const matchingCount = (await reference.json()).items.length;
  expect(matchingCount).toBeGreaterThanOrEqual(55);
  await expect(page.locator('tbody tr')).toHaveCount(50);
  await expect(page.locator('tbody')).toContainText('recent match');
  await expect(page.locator('tbody')).not.toContainText('nonmatch');
  await expect(page.getByRole('button', {name: 'Older events'})).toBeEnabled();
  await page.getByRole('button', {name: 'Older events'}).click();
  await expect(page).toHaveURL(/cursor=/);
  await expect(page.locator('tbody tr')).toHaveCount(Math.min(50, matchingCount - 50));
  await expect(page.getByRole('button', {name: 'Older events'})).toBeDisabled();
  await page.reload();
  await expect(page.getByRole('combobox', {name: 'Audit tier'})).toHaveValue('4');
  await expect(page.locator('tbody tr')).toHaveCount(Math.min(50, matchingCount - 50));

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Export JSON'}).click();
  const download = await downloadPromise;
  const filename = await download.path();
  expect(filename).not.toBeNull();
  const content = await readFile(filename!, 'utf8');
  const exported = JSON.parse(content);
  expect(exported.items).toHaveLength(matchingCount);
  expect(exported.filters.tier).toBe(4);
  expect(exported.filters.since).toBeTruthy();
  expect(exported.truncated).toBe(false);
  expect(exported.max_events).toBe(1000);
  expect(content).not.toContain('never-export-this');
  expect(content).not.toContain('synthetic_secret');
  await expect(page.getByRole('status').filter({hasText: `Downloaded ${matchingCount} redacted events`})).toBeVisible();

  const textPromise = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Export Text'}).click();
  const textFile = await (await textPromise).path();
  const text = await readFile(textFile!, 'utf8');
  expect(text).toContain(`Exported events: ${matchingCount}`);
  expect(text).toContain('Truncated: no');
  expect(text).not.toContain('never-export-this');

  const bounded = await request.get('http://localhost:8730/api/v1/audit/export?tier=4&max_events=2');
  expect(bounded.ok()).toBe(true);
  const small = await bounded.json();
  expect(small.items).toHaveLength(2);
  expect(small.truncated).toBe(true);

  await page.getByRole('combobox', {name: 'Audit tier'}).selectOption('0');
  await expect(page).not.toHaveURL(/cursor=/);
  const tierZero = await request.get(`http://localhost:8730/api/v1/audit?tier=0&since=${encodeURIComponent(since!)}`);
  const zeroItems = (await tierZero.json()).items;
  if (zeroItems.length === 0) await expect(page.getByText('No events match these filters.')).toBeVisible();
  else await expect(page.locator('tbody tr')).toHaveCount(zeroItems.length);
});

test('composer context reflects real runtime counts and selected draft', async ({page, request}) => {
  const response = await request.get('http://localhost:8730/api/v1/memory/stats');
  const counts = await response.json();
  await page.goto('/');
  const context = page.getByLabel('Command context');
  await expect(context).toContainText(`Memory: ${counts.episode_count} episodes · ${counts.fact_count} facts · ${counts.document_count} documents`);
  await expect(context).not.toContainText('ATLAS Default');
  await expect(context).not.toContainText('Memory: Active');
  await expect(context).not.toContainText('MCP: Chrome, Files');
  await page.getByRole('textbox', {name: 'Task request'}).fill('Selected synthetic request');
  await expect(context).toContainText('Context: Request');
});
