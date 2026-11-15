// Genuine local Keycloak and retained SQLite/PostgreSQL. Freeze is not archival approval.
import {test,expect} from '@playwright/test';
import {randomUUID,createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

test.use({actionTimeout:20_000});

test('freeze exact working head, recover lost acknowledgement, retain successor history and reader downloads',async({page})=>{
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  let token='';const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',async response=>{if(response.url().endsWith('/protocol/openid-connect/token')&&response.ok())token=(await response.json()).access_token;});
  async function login(user:string){
    await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill(user);
    await page.getByRole('textbox',{name:'Password',exact:true}).fill(user+'-local-only');
    await page.getByRole('button',{name:'Sign In',exact:true}).click();
    await expect(page.locator('.identity')).toHaveText(user);await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  }
  async function get(path:string){const response=await fetch(api+path,{headers:{Authorization:'Bearer '+token}});expect(response.ok).toBeTruthy();return response.json();}
  await login('operator');const title='Frozen preparation '+randomUUID();
  await page.getByRole('textbox',{name:'New transfer title'}).fill(title);await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));
  await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const transfer=(await get('/api/transfers')).find((t:{title:string})=>t.title===title);
  const sip=transfer.sips[0],base='/api/transfers/'+transfer.id+'/sips/'+sip.id;
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  const freeze=editor.getByRole('button',{name:'Freeze working version',exact:true});
  await expect(freeze).toBeDisabled();
  await editor.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Freeze described submission for independent review.');
  const organisation=editor.getByRole('textbox',{name:'Submitting organisation',exact:true});
  await organisation.fill('Frozen submitting organisation');await expect(freeze).toBeDisabled();
  await editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true}).click();
  await expect(freeze).toBeEnabled();
  await page.route('http://localhost:8000'+base+'/prepared-inputs',async route=>{
    const response=await route.fetch({url:api+base+'/prepared-inputs'});expect(response.ok()).toBeTruthy();await route.abort('failed');
  },{times:1});
  await freeze.click();await expect(editor.getByRole('alert')).toContainText('Failed to fetch');
  const repeated=page.waitForResponse(r=>r.url().endsWith(base+'/prepared-inputs')&&r.ok());
  await freeze.click();const result=await (await repeated).json();expect(result.replayed).toBeTruthy();const first=result.prepared_input;
  await expect(freeze).toBeDisabled();
  const frozen=editor.getByRole('region',{name:'Frozen prepared versions'});
  const firstCard=frozen.locator('[data-prepared-id="'+first.id+'"]');
  await expect(firstCard).toContainText('Matches the applied working head');
  await expect(firstCard).toContainText('Receipt checks apply to this frozen input');
  for(const [name,digest] of [['Download prepared SIP',first.sha256],['Download METS/PREMIS evidence',first.evidence_sha256]]){
    const download=page.waitForEvent('download');await firstCard.getByRole('button',{name,exact:true}).click();
    expect(createHash('sha256').update(readFileSync((await (await download).path())!)).digest('hex')).toBe(digest);
  }
  await editor.getByRole('textbox',{name:'Checkpoint name',exact:true}).fill('Same XML, new review boundary');
  await editor.getByRole('button',{name:'Save checkpoint',exact:true}).click();
  await expect(freeze).toBeEnabled();await expect(firstCard).toContainText('Historical prepared version');
  await freeze.click();await expect(frozen.locator('.prepared-version')).toHaveCount(2);
  let doc=await get(base+'/preparation');const second=doc.prepared_inputs[1];
  expect(second.sha256).toBe(first.sha256);expect(second.preparation_sha256).not.toBe(first.preparation_sha256);expect(second.predecessor_id).toBe(first.id);
  await organisation.fill('Successor needs a fresh freeze');await expect(freeze).toBeDisabled();
  await editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true}).click();
  await expect(freeze).toBeEnabled();await expect(frozen.getByText('Historical prepared version',{exact:true})).toHaveCount(2);
  await editor.getByRole('combobox',{name:'Language',exact:true}).selectOption('de');
  await expect(editor.getByRole('button',{name:'Arbeitsversion einfrieren',exact:true})).toBeVisible();
  await editor.getByRole('combobox',{name:'Sprache',exact:true}).selectOption('fr');
  await expect(editor.getByRole('button',{name:'Figer la version de travail',exact:true})).toBeVisible();
  await editor.getByRole('combobox',{name:'Langue',exact:true}).selectOption('it');
  await expect(editor.getByRole('button',{name:'Congela versione di lavoro',exact:true})).toBeVisible();
  await editor.getByRole('combobox',{name:'Lingua',exact:true}).selectOption('en');
  const body=await get('/api/transfers/'+transfer.id);expect(body.sips[0]).toEqual(sip);expect(body.approval).toBeUndefined();
  await page.reload();await page.locator('button.transfer').filter({hasText:title}).click();await page.getByRole('button',{name:'Ingest Packager',exact:true}).click();
  await expect(frozen.locator('.prepared-version')).toHaveCount(2);await expect(organisation).toHaveValue('Successor needs a fresh freeze');
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await expect(page.getByRole('textbox',{name:'Username or email'})).toBeVisible();
  await login('auditor');await page.locator('button.transfer').filter({hasText:title}).click();await page.getByRole('button',{name:'Ingest Packager',exact:true}).click();
  await expect(freeze).toHaveCount(0);await expect(frozen.locator('.prepared-version')).toHaveCount(2);
  const download=page.waitForEvent('download');await firstCard.getByRole('button',{name:'Download prepared SIP',exact:true}).click();
  expect(createHash('sha256').update(readFileSync((await (await download).path())!)).digest('hex')).toBe(first.sha256);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  doc=await get(base+'/preparation');expect(doc.prepared_inputs).toHaveLength(2);expect(errors).toEqual([]);
});
