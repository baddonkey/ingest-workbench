import {test,expect} from '@playwright/test';
import {createHash,randomUUID} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import type {MetadataSample,MetadataWorkspace} from '../src/metadata-editor.types';

test.use({actionTimeout:20_000});
test('seeded samples retain exact selections and per-object review evidence across later edits',async({page})=>{
  test.setTimeout(120_000);
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill('operator');await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await page.getByRole('textbox',{name:'New transfer title'}).fill('Retained sampling '+randomUUID());await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/preparation')&&response.ok());await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const response=await loaded,base=new URL(response.url()).pathname,document:MetadataWorkspace=await response.json();
  const editor=page.getByRole('region',{name:'Versioned metadata editor'}),tools=editor.locator('.tools-pane');
  await tools.getByRole('spinbutton',{name:'Size',exact:true}).fill('2');
  await tools.getByText('Criteria and seed',{exact:true}).click();await tools.getByRole('textbox',{name:'Seed (blank: random)',exact:true}).fill('browser-reproducible-seed');
  async function draw(){
    const drawn=page.waitForResponse(response=>response.url().includes(base+'/sample?')&&response.ok());
    await tools.getByRole('button',{name:'Draw',exact:true}).click();return (await (await drawn).json()) as MetadataSample;
  }
  const sample=await draw();expect(sample.items).toHaveLength(2);expect(await draw()).toEqual(sample);
  await tools.getByRole('button',{name:'Retain sample',exact:true}).click();
  const reason=editor.getByRole('dialog',{name:'Retain sample',exact:true});await reason.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Retain a reproducible metadata review sample.');
  await page.route('http://localhost:8000'+base+'/commands',async route=>{const response=await route.fetch({url:api+base+'/commands'});expect(response.ok()).toBeTruthy();await route.abort('failed');},{times:1});
  await reason.getByRole('button',{name:'Save',exact:true}).click();await expect(editor.getByRole('alert')).toBeVisible();
  const retry=page.waitForResponse(response=>response.url().endsWith(base+'/commands') && response.request().method()==='POST');
  await tools.getByRole('button',{name:'Retain sample',exact:true}).click();const replay=await (await retry).json();expect(replay.replayed).toBe(true);expect(replay.operation.changes.sample).toEqual(sample);
  await expect(tools.getByRole('combobox',{name:'Retained samples',exact:true})).toHaveValue(replay.operation.id);
  await tools.getByRole('button',{name:'Review sample',exact:true}).click();
  const review=editor.getByRole('dialog',{name:'Review sample',exact:true}),outcomes=review.getByRole('combobox',{name:'Review outcome',exact:true});
  await expect(review.getByRole('button',{name:'Retain review',exact:true})).toBeDisabled();
  await outcomes.nth(0).selectOption('checked');await outcomes.nth(1).selectOption('issue');
  await expect(review.getByRole('button',{name:'Retain review',exact:true})).toBeDisabled();
  await review.getByRole('textbox',{name:'Comment (required for issues)',exact:true}).nth(1).fill('Compare this description with the producer inventory.');
  if(process.env['ARCHIVE_UPDATE_DOCS']==='true'){
    const viewport=page.viewportSize()!;await page.setViewportSize({width:1600,height:1100});await page.screenshot({path:resolve('..','docs','images','sample-review.png'),fullPage:true});await page.setViewportSize(viewport);
  }
  const reviewed=page.waitForResponse(response=>response.url().endsWith(base+'/commands')&&response.request().method()==='POST'&&response.ok());
  await review.getByRole('button',{name:'Retain review',exact:true}).click();const record=(await (await reviewed).json()).operation;await expect(review).toBeHidden();
  await expect(tools.getByRole('table',{name:'Sample',exact:true})).toContainText('Checked');await expect(tools.getByRole('table',{name:'Sample',exact:true})).toContainText('Issue found');
  const evidence=page.waitForEvent('download');await tools.getByRole('button',{name:'Download sample evidence',exact:true}).click();
  expect(createHash('sha256').update(readFileSync((await (await evidence).path())!)).digest('hex')).toBe(record.evidence_sha256);
  await editor.locator('.menubar summary').filter({hasText:/^Package$/}).click();const reload=page.waitForResponse(response=>response.url().endsWith(base)&&response.ok());await editor.getByRole('button',{name:'Reload',exact:true}).click();
  const retained:MetadataWorkspace=await (await reload).json();expect(retained.history).toHaveLength(2);expect(retained.state.metadata_sha256).toBe(document.state.metadata_sha256);expect(retained.source_sha256).toBe(document.source_sha256);
  await expect(tools.getByRole('table',{name:'Sample',exact:true})).toContainText('Compare this description');
  const selected=sample.items[0];await tools.getByRole('button',{name:selected.name,exact:true}).click();await editor.getByRole('textbox',{name:'Title',exact:true}).fill('Successor title after sample review');
  const changed=page.waitForResponse(response=>response.url().endsWith(base+'/commands')&&response.request().method()==='POST'&&response.ok());await editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true}).click();await changed;
  await expect(tools.getByText('The retained sample metadata differs from the current working version. Selections and reviews remain bound to the displayed earlier version.',{exact:true})).toBeVisible();
  await expect(tools.getByRole('table',{name:'Sample',exact:true})).toContainText(selected.name);
  await expect(tools.getByRole('table',{name:'Sample',exact:true})).toContainText('Compare this description');
  await tools.getByRole('button',{name:'Review sample',exact:true}).click();
  await review.getByRole('textbox',{name:'Comment (required for issues)',exact:true}).nth(0).fill('Follow-up review on the retained earlier metadata.');
  const followup=page.waitForResponse(response=>response.url().endsWith(base+'/commands')&&response.request().method()==='POST'&&response.ok());
  await review.getByRole('button',{name:'Retain review',exact:true}).click();const followupRecord=(await (await followup).json()).operation;await expect(review).toBeHidden();
  await editor.locator('.reference-toolbar').getByRole('button',{name:/^Edit history ·/}).click();
  await editor.locator('tr[data-operation-id="'+followupRecord.id+'"] .history-event-link').click();
  const history=editor.getByRole('dialog',{name:'History event',exact:true});
  const previous=history.locator('dd').filter({hasText:'Compare this description with the producer inventory.'});
  await expect(previous).toHaveCount(2);
  await expect(history).toContainText(selected.name);
  await expect(history).not.toContainText('Successor title after sample review');
  await expect(history).toContainText('Follow-up review on the retained earlier metadata.');
  expect(errors).toEqual([]);
});
