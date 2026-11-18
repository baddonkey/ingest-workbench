import {test,expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {resolve} from 'node:path';
import type {MetadataWorkspace} from '../src/metadata-editor.types';

test.use({actionTimeout:20_000});
test('bulk metadata changes preview every object and retain one atomic retryable undo step',async({page})=>{
  test.setTimeout(120_000);
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  let token='';page.on('response',async response=>{if(response.url().endsWith('/protocol/openid-connect/token')&&response.ok())token=(await response.json()).access_token;});
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill('operator');await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();await page.getByRole('textbox',{name:'New transfer title'}).fill('Reviewed bulk changes '+randomUUID());await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/preparation')&&response.ok());await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const response=await loaded,base=new URL(response.url()).pathname,doc:MetadataWorkspace=await response.json();
  async function getDocument():Promise<MetadataWorkspace>{const response=await fetch(api+base,{headers:{Authorization:'Bearer '+token}});expect(response.ok).toBeTruthy();return response.json();}
  const editor=page.getByRole('region',{name:'Versioned metadata editor'}),tools=editor.locator('.tools-pane');
  await tools.locator('.tool-tabs').getByRole('button',{name:'Search metadata',exact:true}).click();
  await expect(tools.getByRole('button',{name:'Bulk change…',exact:true})).toBeDisabled();
  await tools.locator('.metadata-search-filters summary').click();await tools.getByRole('combobox',{name:'Object type',exact:true}).selectOption('dossier');
  const dossiers=doc.nodes.filter(node=>node.kind==='dossier');expect(dossiers.length).toBeGreaterThan(1);
  await tools.getByRole('button',{name:'Bulk change…',exact:true}).click();
  const dialog=editor.getByRole('dialog',{name:'Bulk change…',exact:true});
  await dialog.getByRole('combobox',{name:'Metadata field',exact:true}).selectOption('titel');
  await dialog.getByRole('textbox',{name:'New value for all matching objects',exact:true}).fill('Reviewed bulk title <literal>');
  await dialog.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Review every affected dossier title before one atomic change.');
  async function preview(){const response=page.waitForResponse(response=>response.url().endsWith(base+'/edit-preview')&&response.ok());await dialog.getByRole('button',{name:'Preview bulk change',exact:true}).click();return (await response).json();}
  const proposed=await preview();expect(proposed.edits).toHaveLength(dossiers.length);
  await expect(dialog.locator('tbody tr')).toHaveCount(dossiers.length);
  expect((await getDocument()).history).toHaveLength(0);
  await dialog.getByRole('textbox',{name:'New value for all matching objects',exact:true}).fill('Final reviewed title');
  await expect(dialog.getByRole('button',{name:'Apply reviewed changes',exact:true})).toHaveCount(0);await preview();
  await dialog.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Changed reason requires a new preview.');
  await dialog.getByRole('button',{name:'Apply reviewed changes',exact:true}).click();await expect(dialog.getByRole('alert')).toHaveText('Editing reason changed. Preview again before applying.');
  expect((await getDocument()).history).toHaveLength(0);const finalPreview=await preview();
  if(process.env['ARCHIVE_UPDATE_DOCS']==='true'){
    const viewport=page.viewportSize()!;await page.setViewportSize({width:1600,height:1100});await page.screenshot({path:resolve('..','docs','images','bulk-metadata.png'),fullPage:true});await page.setViewportSize(viewport);
  }
  await page.route('http://localhost:8000'+base+'/commands',async route=>{const response=await route.fetch({url:api+base+'/commands'});expect(response.ok()).toBeTruthy();await route.abort('failed');},{times:1});
  await dialog.getByRole('button',{name:'Apply reviewed changes',exact:true}).click();await expect(dialog.getByRole('alert')).toBeVisible();
  const retried=page.waitForResponse(response=>response.url().endsWith(base+'/commands') && response.request().method()==='POST');
  await dialog.getByRole('button',{name:'Apply reviewed changes',exact:true}).click();const replay=await (await retried).json();expect(replay.replayed).toBe(true);
  await expect(dialog).toBeHidden();await expect(editor.locator('.workspace-status input')).toBeEnabled();
  const applied=await getDocument();expect(applied.history).toHaveLength(1);expect(applied.revision).toBe(doc.revision+1);expect(applied.state.metadata_sha256).toBe(finalPreview.metadata_sha256);
  expect(applied.nodes.filter(node=>node.kind==='dossier').map(node=>node.title)).toEqual(dossiers.map(()=>'Final reviewed title'));
  expect(applied.source_sha256).toBe(doc.source_sha256);expect(applied.history[0].changes.edits).toHaveLength(dossiers.length);
  const undone=page.waitForResponse(response=>response.url().endsWith(base+'/commands')&&response.request().method()==='POST'&&response.ok());await editor.locator('.reference-toolbar').getByRole('button',{name:'Undo',exact:true}).click();await undone;
  await expect(editor.locator('.workspace-status input')).toBeEnabled();const restored=await getDocument();
  expect(restored.nodes.filter(node=>node.kind==='dossier').map(node=>node.title)).toEqual(dossiers.map(node=>node.title));expect(restored.history).toHaveLength(2);
  expect(errors).toEqual([]);
});
