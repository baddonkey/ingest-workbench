// Actual local Keycloak/browser checks. No production realm or simulated token.
import { test, expect, Page } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const issuer = 'http://localhost:8080/realms/ingest-workbench';
const api = process.env['ARCHIVE_BROWSER_API'] || 'http://localhost:8000';
const prefix = 'qualification-' + randomUUID();
const password = randomUUID();
let adminToken: string;
const createdUsers: string[] = [];
let otherUser: string;

async function admin(path: string, method = 'GET', body?: unknown) {
  const response = await fetch('http://127.0.0.1:8080/admin/realms/ingest-workbench' + path, {
    method, headers: { Authorization: 'Bearer ' + adminToken, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(response.ok, `Local realm administration failed: ${response.status}`).toBeTruthy();
  return response;
}

async function login(page: Page, user: string, secret: string) {
  if (api !== 'http://localhost:8000') {
    expect(api).toBe('http://localhost:8001');
    await page.route('http://localhost:8000/api/**', route => route.continue({ url: route.request().url().replace('http://localhost:8000',api) }));
  }
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in to your account' })).toBeVisible();
  const auth = new URL(page.url());
  expect(auth.searchParams.get('code_challenge_method')).toBe('S256');
  expect(auth.searchParams.get('response_type')).toBe('code');
  await page.getByRole('textbox', { name: 'Username or email' }).fill(user);
  await page.getByRole('textbox', { name: 'Password', exact: true }).fill(secret);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await expect(page.locator('.sidebar-user')).toHaveText(user);
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
}

async function logout(page: Page) {
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to your account' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Username or email' })).toHaveValue('');
}

async function authenticateAdmin() {
  // Known local fixture administrator only. No installation credentials are logged.
  const response = await fetch('http://127.0.0.1:8080/realms/master/protocol/openid-connect/token', {
    method: 'POST', body: new URLSearchParams({ grant_type: 'password', client_id: 'admin-cli',
      username: process.env['KC_BOOTSTRAP_ADMIN_USERNAME'] || 'local-admin',
      password: process.env['KC_BOOTSTRAP_ADMIN_PASSWORD'] || 'local-admin-change-me' }),
  });
  expect(response.ok).toBeTruthy();
  adminToken = (await response.json()).access_token;
}

test.beforeAll(async () => {
  await authenticateAdmin();
  const profile = await (await admin('/users/profile')).json();
  const scope = profile.attributes.find((a: { name: string }) => a.name === 'archive_id');
  expect(scope?.permissions.edit, 'Run scripts/configure_local_identity.py for the development realm').toEqual(['admin']);
  const roleNames = ['archive-reader', 'archive-operator', 'archive-approver'];
  const roles = await Promise.all(roleNames.map(async name => (await admin('/roles/' + name)).json()));
  for (const [suffix, tenant, selected] of [
    ['other', prefix + '-other', roles.slice(0, 2)],
    ['both', 'archive-demo', roles],
  ] as const) {
    const username = prefix + '-' + suffix;
    const created = await admin('/users', 'POST', { username, enabled: true,
      firstName: 'Qualification', lastName: suffix, email: username+'@example.invalid',emailVerified: true,
      attributes: { archive_id: [tenant] }, credentials: [{ type: 'password', value: password, temporary: false }] });
    const id = created.headers.get('location')!.split('/').pop()!;
    createdUsers.push(id);
    expect((await (await admin('/users/'+id)).json()).attributes.archive_id).toEqual([tenant]);
    await admin('/users/' + id + '/role-mappings/realm', 'POST', selected);
    if (suffix === 'other') otherUser = username;
  }
});

test.afterAll(async () => {
  await authenticateAdmin();
  for (const id of createdUsers) {
    // Delete only exact IDs created by this run; do not reset users/realm/volumes.
    const user = await (await admin('/users/' + id)).json();
    expect(user.username.startsWith(prefix + '-')).toBeTruthy();
    await admin('/users/' + id, 'DELETE');
  }
});

test('shipped realm imports the administrator-controlled tenant attribute', async () => {
  const realm = prefix + '-realm';
  const fixture = JSON.parse(readFileSync(resolve('..', 'config', 'realm.json'), 'utf8'));
  fixture.realm = realm;
  delete fixture.users;
  const headers = { Authorization: 'Bearer ' + adminToken, 'Content-Type': 'application/json' };
  const created = await fetch('http://127.0.0.1:8080/admin/realms', { method: 'POST', headers, body: JSON.stringify(fixture) });
  expect(created.status).toBe(201);
  try {
    const response = await fetch('http://127.0.0.1:8080/admin/realms/' + realm + '/users/profile', { headers });
    expect(response.ok).toBeTruthy();
    const profile = await response.json();
    const scope = profile.attributes.find((a: { name: string }) => a.name === 'archive_id');
    expect(scope.permissions.edit).toEqual(['admin']);
    expect(scope.multivalued).toBe(false);
  } finally {
    const removed = await fetch('http://127.0.0.1:8080/admin/realms/' + realm, { method: 'DELETE', headers });
    expect(removed.status).toBe(204);
  }
});

test('roles, independent review, candidate download and browser tenant isolation', async ({ page }) => {
  let token = '';
  const observeToken = async (response: import('@playwright/test').Response) => {
    if (response.url() === issuer + '/protocol/openid-connect/token' && response.ok()) {
      token = (await response.json()).access_token;
    }
  };
  page.on('response', observeToken);
  const apiCall = (path: string, method = 'GET', body?: unknown) => page.evaluate(async ({ url, bearer, method, body }) => {
    const r = await fetch(url, { method, headers: { Authorization: 'Bearer ' + bearer, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: r.status, body: await r.json() };
  }, { url: api + path, bearer: token, method, body });
  const title = prefix + ' browser transfer';
  await login(page, 'operator', 'operator-local-only');
  if (api === 'http://localhost:8001') {
    // Node's request context bypasses the browser's staging route override.
    const source = await page.request.get('http://localhost:8000/api/transfers', { headers: { Authorization: 'Bearer '+token } });
    expect(source.ok()).toBeTruthy();
    const originals = await source.json();
    expect(originals.length).toBeGreaterThan(0);
    let checkedPackages = 0;
    for (const original of originals) {
      const copied = await page.request.get(api+'/api/transfers/'+original.id, { headers: { Authorization: 'Bearer '+token } });
      expect(copied.ok()).toBeTruthy();
      expect(await copied.json()).toEqual(original);
      for (const output of original.outputs) {
        const copiedBytes = await page.request.get(api+'/api/transfers/'+original.id+'/outputs/'+output.id, { headers: { Authorization: 'Bearer '+token } });
        expect(copiedBytes.ok()).toBeTruthy();
        expect(createHash('sha256').update(await copiedBytes.body()).digest('hex')).toBe(output.sha256);
        checkedPackages++;
      }
    }
    console.log(`Development clone verified: ${originals.length} exact transfer projections, ${checkedPackages} sealed candidate digests.`);
  }
  await page.getByRole('textbox', { name: 'New transfer title' }).fill(title);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  const listing = await apiCall('/api/transfers');
  const transfer = listing.body.find((t: { title: string }) => t.title === title);
  await page.locator('input[type=file]').first().setInputFiles(resolve('..', 'fixtures', 'reference-1.3.zip'));
  await page.getByRole('button', { name: 'Inspect & add SIP' }).click();
  await expect(page.getByText('Internal checks passed', { exact: true })).toBeVisible();
  const uploaded = (await apiCall('/api/transfers/' + transfer.id)).body;
  const sourceTargets = uploaded.sips[0].units.map((unit: {id:string}, index:number) => ({
    label: 'Legacy target ' + index, units: [{sip_id: uploaded.sips[0].id, unit_id: unit.id}]
  }));
  const legacyPlan = await apiCall('/api/transfers/' + transfer.id + '/plan', 'POST',
    {revision:uploaded.revision,targets:sourceTargets,reason:'Verify historical mapping authorization'});
  expect(legacyPlan.status).toBe(200);
  expect((await apiCall('/api/transfers/' + transfer.id + '/approve', 'POST', {revision:legacyPlan.body.revision})).status).toBe(403);
  await logout(page);

  // A principal with both roles still cannot approve its own plan.
  await login(page, prefix + '-both', password);
  const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString());
  expect(claims.archive_id).toBe('archive-demo');
  expect(claims.azp).toBe('archive-workbench');
  expect(claims.typ).toBe('Bearer');
  const currentResponse = await apiCall('/api/transfers/' + transfer.id);
  expect(currentResponse.status,JSON.stringify(currentResponse.body)).toBe(200);
  const current = currentResponse.body;
  const targets = current.plan.targets.map((t: { label: string; units: unknown[] }) => ({ label: t.label, units: t.units }));
  const planned = await apiCall('/api/transfers/' + transfer.id + '/plan', 'POST', { revision: current.revision,
    targets, reason: 'Same-subject independent-review negative test' });
  expect(planned.status).toBe(200);
  const selfApproval = await apiCall('/api/transfers/' + transfer.id + '/approve', 'POST', { revision: planned.body.revision });
  expect(selfApproval.status).toBe(409);
  await logout(page);

  await login(page, 'reviewer', 'reviewer-local-only');
  const approved = await apiCall('/api/transfers/' + transfer.id + '/approve', 'POST', {revision:planned.body.revision});
  expect(approved.status).toBe(200);
  expect((await apiCall('/api/transfers/' + transfer.id + '/build', 'POST', {revision:approved.body.revision})).status).toBe(403);
  await logout(page);

  await login(page, 'operator', 'operator-local-only');
  const built = await apiCall('/api/transfers/' + transfer.id + '/build', 'POST', {revision:approved.body.revision});
  expect(built.status).toBe(200);
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await page.locator('button.transfer').filter({ hasText: title }).click();
  await page.getByRole('button', { name: 'AIP candidates', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Download candidate', exact: true })).toHaveCount(3);
  const digest = await page.locator('.detail article.sip p.mono').first().innerText();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download candidate', exact: true }).first().click();
  const result = await download;
  expect(createHash('sha256').update(readFileSync((await result.path())!)).digest('hex')).toBe(digest);
  const output = (await apiCall('/api/transfers/' + transfer.id)).body.outputs[0].id;
  await logout(page);

  await login(page, 'auditor', 'auditor-local-only');
  await page.getByRole('textbox', { name: 'New transfer title' }).fill(prefix + ' forbidden');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('archive-operator');
  expect((await apiCall('/api/transfers/' + transfer.id)).status).toBe(200);
  await logout(page);

  await login(page, otherUser, password);
  await expect(page.locator('button.transfer').filter({ hasText: title })).toHaveCount(0);
  expect((await apiCall('/api/transfers/' + transfer.id)).status).toBe(404);
  expect((await apiCall('/api/transfers/' + transfer.id + '/outputs/' + output)).status).toBe(404);
  expect((await apiCall('/api/transfers/' + transfer.id + '/approve', 'POST', { revision: 0 })).status).toBe(403);
  await logout(page);
});

test('actual expired access token is rejected and the SPA refreshes its token', async ({ page }) => {
  let token = '';
  let issues = 0;
  page.on('response', async response => {
    if (response.url() === issuer + '/protocol/openid-connect/token' && response.ok()) {
      token = (await response.json()).access_token;
      issues++;
    }
  });
  await login(page, 'operator', 'operator-local-only');
  const original = token;
  const expiration = JSON.parse(Buffer.from(original.split('.')[1], 'base64url').toString()).exp;
  // Reading exp schedules a negative test; the API remains responsible for signature verification.
  const delay = expiration * 1000 + 12_000 - Date.now();
  expect(delay).toBeLessThan(330_000);
  await page.waitForTimeout(Math.max(0, delay));
  const expired = await page.evaluate(async ({ url, bearer }) =>
    (await fetch(url, { headers: { Authorization: 'Bearer ' + bearer } })).status,
    { url: api + '/api/transfers', bearer: original });
  expect(expired).toBe(401);
  const renewed = page.waitForResponse(r => r.url() === issuer + '/protocol/openid-connect/token' && r.ok());
  const reloaded = page.waitForResponse(r => r.url() === api + '/api/transfers' && r.ok());
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await renewed;
  await reloaded;
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect.poll(() => issues).toBeGreaterThan(1);
  expect(token).not.toBe(original);
  await logout(page);
});
