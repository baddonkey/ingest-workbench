import {test,expect} from '@playwright/test';
import {createHash,randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import type {MetadataWorkspace} from '../src/metadata-editor.types';

test.use({actionTimeout:20_000});
test('reviewed metadata imports retain exact sources, resolve conflicts and apply one retryable undo step',async({page})=>{
  test.setTimeout(150_000);
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  let token='';page.on('response',async response=>{if(response.url().endsWith('/protocol/openid-connect/token')&&response.ok())token=(await response.json()).access_token;});
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill('operator');await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();await page.getByRole('textbox',{name:'New transfer title'}).fill('Reviewed metadata import '+randomUUID());await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/preparation')&&response.ok());await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const response=await loaded,base=new URL(response.url()).pathname,doc:MetadataWorkspace=await response.json();
  async function getDocument():Promise<MetadataWorkspace>{const response=await fetch(api+base,{headers:{Authorization:'Bearer '+token}});expect(response.ok).toBeTruthy();return response.json();}
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  const provenance=doc.nodes.find(node=>node.kind==='provenienz')!,delivery=doc.nodes.find(node=>node.kind==='ablieferung')!,dossier=doc.nodes.find(node=>node.kind==='dossier')!;
  const field=provenance.fields.find(field=>field.tag==='systemBeschreibung')!;
  const source=Buffer.from(JSON.stringify([
    {object_id:provenance.id,field:field.tag,before:field.values?.[0]?.text||'',value:'Imported metadata <literal>'},
    {object_id:'producer-unmapped-object',field:'producer_title',value:'Explicitly mapped dossier'},
    {object_id:delivery.id,field:'ablieferungstyp',value:'Do not overwrite protected submission kind'},
    {object_id:delivery.id,field:'ablieferndeStelle',before:'Older external baseline',value:'Reviewed imported organisation'},
  ],null,2));
  async function openImports(){await editor.locator('.menubar summary').filter({hasText:'Tools'}).click();await editor.getByRole('button',{name:'Import metadata',exact:true}).click();}
  await openImports();
  const dialog=editor.getByRole('dialog',{name:'Import metadata',exact:true});
  for(const name of ['CSV','XLSX','JSON','metadata.xml'])await expect(dialog.getByRole('button',{name,exact:true})).toBeVisible();
  const templateDownloaded=page.waitForEvent('download');await dialog.getByRole('button',{name:'CSV',exact:true}).click();
  const template=await templateDownloaded;expect(template.suggestedFilename()).toBe('metadata-import-template.csv');
  expect((await readFile((await template.path())!)).toString()).toContain('value_json');
  await dialog.locator('input[type=file]').setInputFiles({name:'external-review.json',mimeType:'application/json',buffer:source});
  await dialog.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Retain the original source before resolving producer conflicts.');
  async function loseNextAcknowledgement(){await page.route('http://localhost:8000'+base+'/commands',async route=>{const response=await route.fetch({url:api+base+'/commands'});expect(response.ok()).toBeTruthy();await route.abort('failed');},{times:1});}
  await loseNextAcknowledgement();await dialog.getByRole('button',{name:'Retain import proposal',exact:true}).click();await expect(dialog.getByRole('alert')).toBeVisible();
  const retained=await getDocument();expect(retained.history).toHaveLength(1);expect(retained.state.metadata_sha256).toBe(doc.state.metadata_sha256);expect(retained.state.undo).toHaveLength(0);
  const acknowledged=page.waitForResponse(response=>response.url().endsWith(base+'/commands')&&response.request().method()==='POST'&&response.ok());
  await dialog.getByRole('button',{name:'Retain import proposal',exact:true}).click();expect((await (await acknowledged).json()).replayed).toBe(true);
  await expect(dialog.getByRole('combobox',{name:'Retained import proposals',exact:true})).toHaveValue(retained.history[0].id);
  expect(retained.history[0].changes.metadata_import_proposal?.source.sha256).toBe(createHash('sha256').update(source).digest('hex'));
  const rows=dialog.locator('[data-import-row]');await expect(rows).toHaveCount(4);
  await expect(rows.nth(1)).toContainText('Object identity unresolved');await expect(rows.nth(2)).toContainText('Protected field');await expect(rows.nth(3)).toContainText('External baseline differs');
  await expect(dialog.getByRole('button',{name:'Preview reviewed import',exact:true})).toBeDisabled();
  await rows.nth(0).getByRole('combobox',{name:'Review decision',exact:true}).selectOption('apply');
  await rows.nth(1).getByRole('combobox',{name:'Target object',exact:true}).fill(dossier.source_id||dossier.id);
  await rows.nth(1).getByRole('combobox',{name:'Metadata field',exact:true}).selectOption('titel');
  await rows.nth(1).getByRole('combobox',{name:'Review decision',exact:true}).selectOption('apply');
  await rows.nth(2).getByRole('combobox',{name:'Review decision',exact:true}).selectOption('exclude');
  await rows.nth(3).getByRole('combobox',{name:'Review decision',exact:true}).selectOption('apply');
  await expect(dialog.getByRole('button',{name:'Preview reviewed import',exact:true})).toBeDisabled();
  await rows.nth(2).getByRole('textbox',{name:'Reason for exclusion',exact:true}).fill('Protected submission kind must remain unchanged.');
  async function preview(){const response=page.waitForResponse(response=>response.url().endsWith(base+'/import-preview')&&response.ok());await dialog.getByRole('button',{name:'Preview reviewed import',exact:true}).click();return (await response).json();}
  expect((await preview()).edits).toHaveLength(3);expect((await getDocument()).history).toHaveLength(1);
  await rows.nth(2).getByRole('textbox',{name:'Reason for exclusion',exact:true}).fill('Reviewed protected field: preserve the received submission kind.');
  await expect(dialog.getByRole('button',{name:'Apply reviewed changes',exact:true})).toHaveCount(0);await preview();
  await dialog.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Resolve identity and baseline conflicts, explicitly exclude the protected field.');
  await dialog.getByRole('button',{name:'Apply reviewed changes',exact:true}).click();await expect(dialog.getByRole('alert')).toHaveText('Editing reason changed. Preview again before applying.');
  const finalPreview=await preview();
  if(process.env['ARCHIVE_UPDATE_DOCS']==='true'){
    const viewport=page.viewportSize()!;await page.setViewportSize({width:1600,height:1100});await dialog.evaluate(element=>element.scrollTop=0);await dialog.locator('.import-table-scroll').evaluate(element=>element.scrollTop=0);await page.screenshot({path:resolve('..','docs','images','metadata-import.png'),fullPage:true});await page.setViewportSize(viewport);
  }
  await loseNextAcknowledgement();await dialog.getByRole('button',{name:'Apply reviewed changes',exact:true}).click();await expect(dialog.getByRole('alert')).toBeVisible();
  const retried=page.waitForResponse(response=>response.url().endsWith(base+'/commands')&&response.request().method()==='POST'&&response.ok());
  await dialog.getByRole('button',{name:'Apply reviewed changes',exact:true}).click();expect((await (await retried).json()).replayed).toBe(true);
  await expect(dialog).toBeHidden();await expect(editor.locator('.workspace-status input')).toBeEnabled();
  const applied=await getDocument();expect(applied.history).toHaveLength(2);expect(applied.revision).toBe(doc.revision+2);expect(applied.state.metadata_sha256).toBe(finalPreview.metadata_sha256);expect(applied.state.undo).toHaveLength(1);
  expect(applied.nodes.find(node=>node.id===dossier.id)?.title).toBe('Explicitly mapped dossier');
  expect(applied.nodes.find(node=>node.id===delivery.id)?.fields.find(field=>field.tag==='ablieferungstyp')?.values).toEqual(delivery.fields.find(field=>field.tag==='ablieferungstyp')?.values);
  const undone=page.waitForResponse(response=>response.url().endsWith(base+'/commands')&&response.request().method()==='POST'&&response.ok());await editor.locator('.reference-toolbar').getByRole('button',{name:'Undo',exact:true}).click();await undone;
  await expect(editor.locator('.workspace-status input')).toBeEnabled();const restored=await getDocument();expect(restored.state.metadata_sha256).toBe(doc.state.metadata_sha256);
  await openImports();await expect(dialog.getByText('This proposal is read-only or no longer at the current head.',{exact:false})).toBeVisible();
  const sourceDownloaded=page.waitForEvent('download');await dialog.getByRole('button',{name:'Download original source',exact:true}).click();const original=await sourceDownloaded;
  expect(original.suggestedFilename()).toBe('external-review.json');expect(await readFile((await original.path())!)).toEqual(source);
  await dialog.getByRole('button',{name:'Create successor for current version',exact:true}).click();
  await dialog.getByRole('button',{name:'Retain import proposal',exact:true}).click();await expect(rows).toHaveCount(4);
  for(let index=0;index<4;index++){await rows.nth(index).getByRole('combobox',{name:'Review decision',exact:true}).selectOption('exclude');await rows.nth(index).getByRole('textbox',{name:'Reason for exclusion',exact:true}).fill('Reviewed and rejected without changing metadata.');}
  expect((await preview()).edits).toHaveLength(0);
  await dialog.getByRole('button',{name:'Retain review without metadata changes',exact:true}).click();await expect(dialog).toBeHidden();
  const rejected=await getDocument();expect(rejected.history).toHaveLength(5);expect(rejected.state.metadata_sha256).toBe(doc.state.metadata_sha256);expect(rejected.state.undo).toEqual(restored.state.undo);expect(rejected.state.redo).toEqual(restored.state.redo);
  expect(rejected.history.at(-1)?.changes.metadata_import?.decisions.every(choice=>choice.action==='exclude'&&choice.reason)).toBe(true);
  expect(errors).toEqual([]);
});
