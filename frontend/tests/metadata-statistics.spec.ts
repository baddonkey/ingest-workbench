import {test,expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {resolve} from 'node:path';
import type {MetadataWorkspace} from '../src/metadata-editor.types';

test.use({actionTimeout:20_000});
test('preparation statistics bind applied metadata and drill down without including pending changes',async({page})=>{
  test.setTimeout(120_000);
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill('operator');await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();await page.getByRole('textbox',{name:'New transfer title'}).fill('Version-bound statistics '+randomUUID());await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/preparation')&&response.ok());await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const response=await loaded,base=new URL(response.url()).pathname,doc:MetadataWorkspace=await response.json();
  const editor=page.getByRole('region',{name:'Versioned metadata editor'}),statistics=editor.getByRole('region',{name:'Statistics',exact:true});
  const row=(name:string)=>statistics.getByRole('row').filter({has:page.getByRole('rowheader',{name,exact:true})});
  const openStatistics=()=>editor.locator('.editor-type-tabs').getByRole('button',{name:'Statistics',exact:true}).click();
  await openStatistics();await expect(statistics).toContainText(doc.statistics.binding.metadata_sha256);
  await expect(row('Content files').getByRole('button')).toHaveText(String(doc.statistics.payload_files.length));
  await expect(row('Broken file references').getByRole('button')).toHaveText('0');
  await expect(statistics.getByText('Format assessment entries for this input',{exact:true})).toHaveCount(0);
  await row('Dossier').getByRole('button').click();
  const dialog=editor.getByRole('dialog',{name:'Statistics · Dossier',exact:true});
  await expect(dialog.locator('tbody tr')).toHaveCount(doc.statistics.objects['dossier'].length);
  const dossier=doc.statistics.objects['dossier'][0];
  await dialog.getByRole('button',{name:dossier.title,exact:true}).click();await expect(dialog).toBeHidden();
  await editor.getByRole('textbox',{name:'Title',exact:true}).fill('Pending statistics title <literal>');
  await openStatistics();await row('Dossier').getByRole('button').click();
  await expect(dialog).toContainText(dossier.title);await expect(dialog).not.toContainText('Pending statistics title');
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await editor.locator('.workspace-status input').fill('Review exact-version statistics after one applied title change.');
  const reloaded=page.waitForResponse(response=>response.url().endsWith(base)&&response.ok());
  await editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true}).click();
  const applied:MetadataWorkspace=await (await reloaded).json();
  expect(applied.statistics.binding.metadata_sha256).not.toBe(doc.statistics.binding.metadata_sha256);
  expect(applied.statistics.binding.operation_id).toBe(applied.history[0].id);
  expect(applied.statistics.payload_bytes).toBe(doc.statistics.payload_bytes);
  await openStatistics();await expect(statistics).toContainText(applied.statistics.binding.metadata_sha256);
  await row('Dossier').getByRole('button').click();await expect(dialog).toContainText('Pending statistics title <literal>');
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await row('Empty scalar schema fields').getByRole('button').click();
  const empty=editor.getByRole('dialog',{name:'Statistics · Empty scalar schema fields',exact:true});
  const count=applied.statistics.empty_scalar_fields.length;
  await expect(empty.locator('tbody tr')).toHaveCount(Math.min(100,count));
  if(count>100){
    await empty.getByRole('button',{name:'Next',exact:true}).click();
    await expect(empty.locator('tbody tr')).toHaveCount(Math.min(100,count-100));
    await empty.getByRole('button',{name:'Previous',exact:true}).click();
  }
  const target=applied.statistics.empty_scalar_fields[0];
  await empty.locator('tbody tr').first().getByRole('button').click();await expect(empty).toBeHidden();
  const node=applied.nodes.find(node=>node.id===target.node_id)!,field=node.fields.find(field=>field.id===target.field_id)!;
  await expect(editor.locator('[data-node-id="'+node.id+'"] [data-field-tag="'+field.tag+'"]')).toBeInViewport();
  if(process.env['ARCHIVE_UPDATE_DOCS']==='true'){
    const viewport=page.viewportSize()!;await page.setViewportSize({width:1600,height:1300});await editor.getByRole('separator').press('End');await openStatistics();
    await page.screenshot({path:resolve('..','docs','images','preparation-statistics.png'),fullPage:true});await page.setViewportSize(viewport);
  }
  expect(errors).toEqual([]);
});
