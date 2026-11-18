// Genuine local Keycloak and SQLite/PostgreSQL. Preparation review is not AIP approval.
import {test,expect} from '@playwright/test';
import {randomUUID,createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

test.use({actionTimeout:20_000});

test('select frozen metadata for arrangement, independently review exact bytes and retain stale review history',async({page})=>{
  test.skip(process.env['ARCHIVE_TEST_INDEPENDENT_REVIEW']!=='true','Requires server AIP_REQUIRE_INDEPENDENT_REVIEW=true.');
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  let token='';const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',async response=>{if(response.url().endsWith('/protocol/openid-connect/token')&&response.ok())token=(await response.json()).access_token;});
  async function login(user:string){
    await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill(user);
    await page.getByRole('textbox',{name:'Password',exact:true}).fill(user+'-local-only');
    await page.getByRole('button',{name:'Sign In',exact:true}).click();
    await expect(page.locator('.sidebar-user')).toHaveText(user);await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  }
  async function logout(){await page.getByRole('button',{name:'Sign out',exact:true}).click();await expect(page.getByRole('textbox',{name:'Username or email'})).toBeVisible();}
  async function get(path:string){const response=await fetch(api+path,{headers:{Authorization:'Bearer '+token}});expect(response.ok).toBeTruthy();return response.json();}
  async function downloadHash(button:ReturnType<typeof page.getByRole>){const download=page.waitForEvent('download');await button.click();return createHash('sha256').update(readFileSync((await (await download).path())!)).digest('hex');}
  await login('operator');const title='Frozen input review '+randomUUID();
  await page.getByRole('textbox',{name:'New transfer title'}).fill(title);await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));
  await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const transfer=(await get('/api/transfers')).find((t:{title:string})=>t.title===title);
  const sip=transfer.sips[0],base='/api/transfers/'+transfer.id;
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  const doc=await get(base+'/sips/'+sip.id+'/preparation');const dossier=doc.nodes.find((n:{kind:string})=>n.kind==='dossier');
  await editor.locator('.classification-pane [data-object-id="'+dossier.id+'"]').click();
  await editor.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Describe the exact metadata selected for independent preparation review.');
  await editor.locator('[data-field-tag="titel"]').getByRole('textbox',{name:'Title',exact:true}).fill('Selected dossier for frozen review');
  await editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true}).click();
  await editor.locator('.editor-type-tabs').getByRole('button',{name:'Validation',exact:true}).click();
  await editor.getByRole('button',{name:'Freeze & validate',exact:true}).click();
  await expect(editor.locator('.prepared-version')).toHaveCount(1);
  const prepared=(await get(base)).prepared_inputs[0];
  await page.getByRole('button',{name:'Create AIP candidate',exact:true}).click();
  const mapping=page.getByRole('region',{name:'Frozen input arrangement and review'});
  await mapping.locator('[data-input-sip="'+sip.id+'"]').selectOption(prepared.id);
  await expect(mapping.locator('.mapping-row b').filter({hasText:'Selected dossier for frozen review'})).toBeVisible();
  expect(await downloadHash(mapping.getByRole('button',{name:'Download selected SIP',exact:true}))).toBe(prepared.sha256);
  await mapping.getByRole('textbox',{name:'Reason for restructuring',exact:true}).fill('Arrange selected frozen metadata, retaining source and editing evidence.');
  await page.route('http://localhost:8000'+base+'/candidate-plan',async route=>{const response=await route.fetch({url:api+base+'/candidate-plan'});expect(response.ok()).toBeTruthy();await route.abort('failed');},{times:1});
  await mapping.getByRole('button',{name:'Save candidate inputs',exact:true}).click();
  await expect(mapping.getByRole('alert')).toContainText('Failed to fetch');
  await mapping.getByRole('button',{name:'Reload',exact:true}).click();
  await expect(mapping.getByRole('button',{name:'Record independent preparation review',exact:true})).toBeVisible();
  let body=await get(base);expect(body.plan.format).toBe(2);expect(body.prepared_plans).toHaveLength(1);expect(body.plan.inputs[0].input_sha256).toBe(prepared.sha256);expect(body.sips[0]).toEqual(sip);
  await logout();await login('reviewer');await page.locator('button.transfer').filter({hasText:title}).click();await page.getByRole('button',{name:'Create AIP candidate',exact:true}).click();
  await expect(mapping.locator('.mapping-row b').filter({hasText:'Selected dossier for frozen review'})).toBeVisible();
  await mapping.getByRole('textbox',{name:'Independent review reason',exact:true}).fill('Reviewed selected bytes, receipt checks, context and arrangement. AIP authorization remains pending.');
  const review=mapping.getByRole('button',{name:'Record independent preparation review',exact:true});
  await page.route('http://localhost:8000'+base+'/prepared-reviews',async route=>{const response=await route.fetch({url:api+base+'/prepared-reviews'});expect(response.ok()).toBeTruthy();await route.abort('failed');},{times:1});
  await review.click();await expect(mapping.getByRole('alert')).toContainText('Failed to fetch');
  const replay=page.waitForResponse(r=>r.url().endsWith(base+'/prepared-reviews')&&r.ok());
  await review.click();const result=await (await replay).json();expect(result.replayed).toBeTruthy();const recorded=result.review;
  await expect(mapping.getByText('Preparation review retained · AIP and archival acceptance remain held.',{exact:true})).toBeVisible();
  expect(await downloadHash(mapping.getByRole('button',{name:'Download review evidence',exact:true}))).toBe(recorded.evidence_sha256);
  body=await get(base);expect(body.prepared_reviews).toHaveLength(1);expect(body.approval).toBeUndefined();expect(body.state).toBe('PLANNED');expect(body.prepared_review_id).toBe(recorded.id);
  await mapping.getByRole('combobox',{name:'Language',exact:true}).selectOption('de');await expect(mapping.getByRole('heading',{name:'AIP-Kandidat erstellen',exact:true})).toBeVisible();
  await mapping.getByRole('combobox',{name:'Sprache',exact:true}).selectOption('fr');await expect(mapping.getByRole('heading',{name:'Créer un candidat AIP',exact:true})).toBeVisible();
  await mapping.getByRole('combobox',{name:'Langue',exact:true}).selectOption('it');await expect(mapping.getByRole('heading',{name:'Crea candidato AIP',exact:true})).toBeVisible();
  await mapping.getByRole('combobox',{name:'Lingua',exact:true}).selectOption('en');
  await logout();await login('operator');await page.locator('button.transfer').filter({hasText:title}).click();await page.getByRole('button',{name:'Ingest Packager',exact:true}).click();
  await editor.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Successor checkpoint requires a fresh selected-input review.');
  await editor.locator('.reference-toolbar').getByRole('button',{name:/^Edit history/}).click();
  await editor.getByRole('textbox',{name:'Checkpoint name',exact:true}).fill('Successor after preparation review');await editor.getByRole('button',{name:'Save checkpoint',exact:true}).click();
  await expect(editor.getByRole('textbox',{name:'Checkpoint name',exact:true})).toHaveValue('');
  await page.getByRole('button',{name:'Create AIP candidate',exact:true}).click();
  await expect(mapping.getByText(/Mapping must be saved and reviewed again/)).toBeVisible({timeout:30_000});
  await expect(mapping.getByText('Historical preparation review',{exact:true})).toBeVisible();
  expect(await downloadHash(mapping.getByRole('button',{name:'Download review evidence',exact:true}))).toBe(recorded.evidence_sha256);
  body=await get(base);expect(body.prepared_review_id).toBeUndefined();expect(body.sips[0]).toEqual(sip);expect(body.prepared_reviews).toHaveLength(1);
  await logout();await login('auditor');await page.locator('button.transfer').filter({hasText:title}).click();await page.getByRole('button',{name:'Create AIP candidate',exact:true}).click();
  await expect(mapping.getByRole('button',{name:'Save candidate inputs',exact:true})).toBeDisabled();
  await expect(mapping.getByRole('button',{name:'Record independent preparation review',exact:true})).toBeDisabled();
  expect(await downloadHash(mapping.getByRole('button',{name:'Download review evidence',exact:true}))).toBe(recorded.evidence_sha256);
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);expect(errors).toEqual([]);
});
