// Genuine local Keycloak/browser checks; no profile or native repository qualification.
import { test, expect } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

test.use({ actionTimeout: 20_000 });

test('Package Turbo workspace patterns integrate with authenticated preserved SIPs', async ({ page }) => {
  const api = process.env['ARCHIVE_BROWSER_API'] || 'http://localhost:8000';
  if (api !== 'http://localhost:8000') {
    expect(api).toBe('http://localhost:8001');
    await page.route('http://localhost:8000/api/**', route => route.continue({ url: route.request().url().replace('http://localhost:8000', api) }));
  }
  let token = '';
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', async response => {
    if (response.url().endsWith('/realms/ingest-workbench/protocol/openid-connect/token') && response.ok()) token = (await response.json()).access_token;
  });
  async function login(user: string) {
    await page.goto('/');
    await page.getByRole('textbox', { name: 'Username or email' }).fill(user);
    await page.getByRole('textbox', { name: 'Password', exact: true }).fill(user + '-local-only');
    await page.getByRole('button', { name: 'Sign In', exact: true }).click();
    await expect(page.locator('.sidebar-user')).toHaveText(user);
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
  }
  async function inspectOriginal() {
    const editor = page.getByRole('region', { name: 'Versioned metadata editor', exact: true });
    const packageMenu = editor.locator('.menubar details').first();
    await packageMenu.locator('summary').click();
    await packageMenu.getByRole('button', { name: 'Inspect original', exact: true }).click();
  }
  const title = 'Ingest Packager qualification ' + randomUUID();
  await login('operator');
  await page.getByRole('textbox', { name: 'New transfer title' }).fill(title);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..', 'fixtures', 'reference-1.3.zip'));
  await page.getByRole('button', { name: 'Inspect & add SIP' }).click();
  await expect(page.getByText('Internal checks passed', { exact: true })).toBeVisible();
  const transfers = await (await fetch(api + '/api/transfers', { headers: { Authorization: 'Bearer ' + token } })).json();
  const transfer = transfers.find((t: { title: string }) => t.title === title);
  const beforeRevision = transfer.revision, beforeEvents = transfer.events.length;
  const sourceDigest = createHash('sha256').update(readFileSync(resolve('..', 'fixtures', 'reference-1.3.zip'))).digest('hex');
  await page.getByRole('button', { name: 'Open Ingest Packager', exact: true }).click();
  const handler = page.getByRole('region', { name: 'Ingest Packager workspace' });
  await inspectOriginal();
  await expect(handler.getByText('Original · read only', { exact: true })).toBeVisible();
  await expect(handler.locator('.source-fixity')).toContainText(sourceDigest);
  await expect(handler.getByText('Receipt checks: PASS', { exact: true })).toBeVisible();
  await expect(handler.getByText('Archival acceptance: CLOSED', { exact: true })).toBeVisible();
  const search = handler.getByRole('searchbox', { name: 'Find title, ID or path' });
  await search.fill('ReadMe.txt');
  await expect(handler.locator('.search-hit')).toHaveCount(2);
  await handler.locator('.search-hit').filter({ hasText: 'physical' }).click();
  await expect(handler.locator('.object-heading h3')).toHaveText('ReadMe.txt');
  await expect(handler.locator('.text-preview')).toBeVisible();
  const detail = await (await fetch(api + '/api/transfers/' + transfer.id + '/sips/' + transfer.sips[0].id + '/workspace', { headers: { Authorization: 'Bearer ' + token } })).json();
  const readme = detail.nodes.find((n: { kind: string; title: string }) => n.kind === 'file' && n.title === 'ReadMe.txt');
  const metadata = detail.nodes.find((n: { kind: string; path: string }) => n.kind === 'file' && n.path === detail.metadata_path);
  for (const [label, digest] of [['Download original member', readme.sha256], ['Download original SIP', sourceDigest], ['Download source metadata', metadata.sha256]]) {
    const pending = page.waitForEvent('download');
    await handler.getByRole('button', { name: label, exact: true }).click();
    const downloaded = await pending;
    expect(createHash('sha256').update(readFileSync((await downloaded.path())!)).digest('hex')).toBe(digest);
  }
  // Synthetic response probes only exercise browser escaping and late-response fencing.
  const memberPattern = '**/nodes/' + readme.id;
  const hostileText = '<script>window.packagePreviewExecuted=true</script><img src=x onerror="window.packagePreviewExecuted=true">';
  await page.route(memberPattern, async route => {
    const response = await route.fetch({ url: route.request().url().replace('http://localhost:8000', api) });
    const body = await response.json();
    body.preview.text = hostileText;
    await route.fulfill({ response, json: body });
  });
  await handler.locator('.search-hit').filter({ hasText: 'physical' }).click();
  await expect(handler.locator('.text-preview')).toHaveText(hostileText);
  expect(await page.evaluate(() => (window as Window & { packagePreviewExecuted?: boolean }).packagePreviewExecuted)).toBeUndefined();
  await page.unroute(memberPattern);
  let releaseOldResponse!: () => void;
  const responseGate = new Promise<void>(resolve => { releaseOldResponse = resolve; });
  await page.route(memberPattern, async route => {
    const response = await route.fetch({ url: route.request().url().replace('http://localhost:8000', api) });
    await responseGate;
    await route.fulfill({ response });
  });
  const oldRequest = page.waitForRequest(request => request.url().endsWith('/nodes/' + readme.id));
  await handler.locator('.search-hit').filter({ hasText: 'physical' }).click();
  await oldRequest;
  await search.fill('Auftragsuebersicht.pdf');
  await handler.locator('.search-hit').filter({ hasText: 'physical' }).click();
  await expect(handler.getByText('Binary or active content is available as an attachment only.', { exact: true })).toBeVisible();
  const staleResponse = page.waitForResponse(response => response.url().endsWith('/nodes/' + readme.id));
  releaseOldResponse();
  await staleResponse;
  await expect(handler.locator('.object-heading h3')).toHaveText('Auftragsuebersicht.pdf');
  await expect(handler.locator('.text-preview')).toHaveCount(0);
  await page.unroute(memberPattern);
  await handler.getByRole('button', { name: 'Preservation history', exact: true }).click();
  await expect(handler.locator('.handler-event')).toHaveCount(2);
  await search.fill('IdD-1');
  await handler.locator('.search-hit').filter({ hasText: 'logical' }).first().click();
  await expect(handler.locator('.context-note').first()).toContainText('IdD-1');
  await page.screenshot({ path: resolve('..', 'var', 'browser', 'package-handler.png'), fullPage: true });
  await handler.getByRole('button', { name: 'Arrange dossiers', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Define archival boundaries', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Ingest Packager', exact: true }).click();
  const editor = page.getByRole('region', { name: 'Versioned metadata editor', exact: true });
  await editor.locator('.reference-toolbar').getByRole('button', { name: 'SIP-Validation', exact: true }).click();
  await expect(page.getByRole('region', { name: 'SIP-Validation', exact: true })).toBeVisible();
  const after = await (await fetch(api + '/api/transfers/' + transfer.id, { headers: { Authorization: 'Bearer ' + token } })).json();
  expect(after.revision).toBe(beforeRevision);
  expect(after.events.length).toBe(beforeEvents);
  expect(after.sips[0].sha256).toBe(sourceDigest);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Username or email' })).toBeVisible();
  await login('auditor');
  await page.locator('button.transfer').filter({ hasText: title }).click();
  await page.getByRole('button', { name: 'Ingest Packager', exact: true }).click();
  await inspectOriginal();
  await expect(page.locator('.source-fixity')).toContainText(sourceDigest);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Username or email' })).toBeVisible();
  expect(errors).toEqual([]);
});
