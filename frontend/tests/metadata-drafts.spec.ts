// Genuine local OIDC and persistence; no external-validator or institutional qualification.
import { test, expect } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type {MetadataWorkspace} from '../src/metadata-editor.types';

test.use({ actionTimeout: 20_000 });

for(const group of [
  {label:'additional metadata',kind:'ordnungssystem',tag:'zusatzDaten',edit:'Edit additional metadata...',add:'+ Characteristic',empty:'No characteristics'},
  {label:'file properties',kind:'datei',tag:'eigenschaft',edit:'Edit file properties...',add:'+ Property',empty:'No properties'},
]){
test(group.label+' dialog stages, cancels and persists ordered characteristics',async({page})=>{
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  let token='';page.on('response',async response=>{if(response.url().endsWith('/protocol/openid-connect/token')&&response.ok())token=(await response.json()).access_token;});
  await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill('operator');await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  const title='Additional metadata '+randomUUID();await page.getByRole('textbox',{name:'New transfer title'}).fill(title);await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/preparation')&&response.ok());
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const document:MetadataWorkspace=await (await loaded).json(),system=document.nodes.find(node=>node.kind===group.kind && document.navigation.classification.some(row=>row.object_id===node.id))!;
  const transfers=await fetch(api+'/api/transfers',{headers:{Authorization:'Bearer '+token}}),transfer=(await transfers.json()).find((item:{title:string})=>item.title===title);
  const preparation='/api/transfers/'+transfer.id+'/sips/'+document.sip_id+'/preparation';
  async function getPreparation():Promise<MetadataWorkspace>{const response=await fetch(api+preparation,{headers:{Authorization:'Bearer '+token}});expect(response.ok).toBeTruthy();return response.json();}
  const savedField=(doc:MetadataWorkspace)=>doc.nodes.find(node=>node.id===system.id)!.fields.find(field=>field.tag===group.tag)!;
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  await editor.locator('.classification-pane [data-object-id="'+system.id+'"]').click();
  await expect(editor.locator('.record-heading').filter({hasText:system.title})).toBeVisible();
  if(group.kind==='datei'){
    await expect.poll(()=>editor.locator('.working-field:visible').evaluateAll(fields=>fields.map(field=>field.getAttribute('data-field-tag')))).toEqual(['name','id']);
    await expect(editor.getByRole('textbox',{name:'ID',exact:true})).toHaveValue(system.source_id!);await expect(editor.getByRole('textbox',{name:'ID',exact:true})).toHaveAttribute('readonly','');
    await expect(editor.locator('[data-field-tag="name"] textarea')).toBeDisabled();
  }
  const field=editor.locator('.working-field[data-field-tag="'+group.tag+'"]'),edit=field.getByRole('button',{name:group.edit,exact:true}),summary=field.getByRole('textbox');
  await expect(field).toBeHidden();await editor.locator('.additional-toggle').click();await expect(field).toBeVisible();
  if(group.kind==='datei'){
    await expect.poll(()=>editor.locator('.working-field:visible').evaluateAll(fields=>fields.map(field=>field.getAttribute('data-field-tag')))).toEqual(['name','id','eigenschaft','originalName','pruefalgorithmus','pruefsumme']);
    await expect(editor.getByRole('combobox',{name:'Checksum algorithm',exact:true})).toBeDisabled();await expect(editor.getByRole('textbox',{name:'Checksum',exact:true})).toBeDisabled();
    if(process.env['ARCHIVE_UPDATE_DOCS']==='true'){
      await page.setViewportSize({width:1600,height:1100});await page.screenshot({path:resolve('..','docs','images','file-metadata.png'),fullPage:true});
    }
  }
  expect(await field.evaluate(element=>!!element.previousElementSibling?.classList.contains('additional-toggle'))).toBe(true);
  await expect(summary).toHaveAttribute('readonly','');
  const originalSummary=await summary.inputValue(),dialog=page.getByRole('dialog',{name:group.edit,exact:true});
  async function clearProperties(){
    const remove=dialog.getByRole('button',{name:'Remove entry',exact:true});let count=await remove.count();
    while(count){await remove.first().click();await expect(remove).toHaveCount(--count);}
  }
  await edit.click();
  await clearProperties();
  await dialog.getByRole('button',{name:group.add,exact:true}).click();
  await dialog.getByRole('textbox',{name:'Value',exact:true}).fill('A & B <literal>');
  const nameBox=await dialog.getByRole('textbox',{name:'Name',exact:true}).boundingBox(),valueBox=await dialog.getByRole('textbox',{name:'Value',exact:true}).boundingBox(),addBox=await dialog.getByRole('button',{name:group.add,exact:true}).boundingBox(),dialogBox=await dialog.boundingBox();
  expect(dialogBox!.width).toBeLessThanOrEqual(520);expect(Math.abs(nameBox!.width-valueBox!.width)).toBeLessThanOrEqual(1);expect(nameBox!.y).toBe(valueBox!.y);expect(nameBox!.height).toBe(valueBox!.height);expect(addBox!.width).toBeGreaterThan(nameBox!.width+valueBox!.width);
  await dialog.getByRole('button',{name:'Save',exact:true}).click();await expect(dialog).toBeVisible();
  await dialog.getByRole('textbox',{name:'Name',exact:true}).fill('Collection');
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await expect(summary).toHaveValue(originalSummary);
  await expect(editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true})).toBeDisabled();
  await edit.click();
  await clearProperties();
  await dialog.getByRole('button',{name:group.add,exact:true}).click();await dialog.getByRole('textbox',{name:'Name',exact:true}).fill('Collection');await dialog.getByRole('textbox',{name:'Value',exact:true}).fill('A & B <literal>');
  await dialog.getByRole('button',{name:group.add,exact:true}).click();await dialog.getByRole('textbox',{name:'Name',exact:true}).nth(1).fill('Empty value');
  const up=dialog.getByRole('button',{name:'Move up',exact:true}),down=dialog.getByRole('button',{name:'Move down',exact:true});
  await expect(up.first()).toBeDisabled();await expect(down.last()).toBeDisabled();
  await down.first().click();
  await expect(dialog.getByRole('textbox',{name:'Name',exact:true}).first()).toHaveValue('Empty value');
  await down.first().click();
  await expect(dialog.getByRole('textbox',{name:'Name',exact:true}).first()).toHaveValue('Collection');
  await up.last().click();
  if(process.env['ARCHIVE_UPDATE_DOCS']==='true' && group.kind==='ordnungssystem'){
    await page.setViewportSize({width:1600,height:1100});
    await dialog.screenshot({path:resolve('..','docs','images','additional-metadata.png')});
  }
  const expectedSummary='Empty value: \nCollection: A & B <literal>';
  await dialog.getByRole('button',{name:'Save',exact:true}).click();await expect(dialog).toBeHidden();await expect(summary).toHaveValue(expectedSummary);
  expect((await getPreparation()).history).toHaveLength(document.history.length);
  await edit.click();await down.first().click();await page.keyboard.press('Escape');
  await expect(summary).toHaveValue(expectedSummary);
  async function save(reason:string){
    await editor.locator('.workspace-status input').fill('');
    await editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true}).click();
    const reasonDialog=editor.getByRole('dialog',{name:'Save metadata changes',exact:true});await reasonDialog.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill(reason);
    const saved=page.waitForResponse(response=>response.url().endsWith(preparation+'/commands')&&response.request().method()==='POST');
    await reasonDialog.getByRole('button',{name:'Save',exact:true}).click();const response=await saved;expect(response.ok(),await response.text()).toBeTruthy();
    await expect(reasonDialog).toBeHidden();await expect(editor.locator('.workspace-status input')).toBeEnabled();await expect(editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true})).toBeDisabled();
  }
  await save('Record additional characteristics from source review.');
  const persisted=await getPreparation(),values=savedField(persisted).values!,properties=group.tag==='eigenschaft'?values:values[0].children['merkmal'];
  expect(properties.map(row=>({name:row.attributes['name'],value:row.text}))).toEqual([{name:'Empty value',value:''},{name:'Collection',value:'A & B <literal>'}]);
  expect(persisted.source_sha256).toBe(document.source_sha256);
  if(group.kind==='datei'){
    const savedNode=persisted.nodes.find(node=>node.id===system.id)!;expect(savedNode.source_id).toBe(system.source_id);
    for(const tag of ['name','pruefalgorithmus','pruefsumme'])expect(savedNode.fields.find(field=>field.tag===tag)!.values).toEqual(system.fields.find(field=>field.tag===tag)!.values);
  }
  await editor.locator('.menubar summary').filter({hasText:/^Package$/}).click();
  const reloaded=page.waitForResponse(response=>response.url().endsWith(preparation)&&response.ok());await editor.getByRole('button',{name:'Reload',exact:true}).click();await reloaded;await expect(summary).toHaveValue(expectedSummary);
  await edit.click();await expect(dialog.getByRole('textbox',{name:'Name',exact:true})).toHaveCount(2);
  await clearProperties();
  await dialog.getByRole('button',{name:'Save',exact:true}).click();await expect(dialog).toBeHidden();await expect(summary).toHaveValue(group.empty);
  await save('Remove additional characteristics after review.');expect(savedField(await getPreparation()).values).toEqual([]);
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.getByRole('textbox',{name:'Username or email'}).fill('auditor');await page.getByRole('textbox',{name:'Password',exact:true}).fill('auditor-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();await page.locator('button.transfer').filter({hasText:title}).click();await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  await editor.locator('.classification-pane [data-object-id="'+system.id+'"]').click();await expect(editor.locator('.record-heading').filter({hasText:system.title})).toBeVisible();await editor.locator('.additional-toggle').click();await expect(edit).toBeDisabled();
  expect(errors).toEqual([]);
});
}

test('adds and saves an absent optional metadata group',async({page})=>{
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  await page.goto('/');
  await page.getByRole('textbox',{name:'Username or email'}).fill('operator');
  await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await page.getByRole('textbox',{name:'New transfer title'}).fill('Optional metadata group '+randomUUID());
  await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.getByRole('combobox',{name:'Profile',exact:true}).selectOption({label:'1.0'});
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','corpus','SIP_20120628_eCH1.0F_BfB.zip'));
  await page.getByRole('button',{name:'Inspect & add SIP'}).click();
  await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/preparation')&&response.ok());
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const response=await loaded, document:MetadataWorkspace=await response.json();
  const submission=document.nodes.find(node=>node.kind==='ablieferung')!;
  expect(submission.fields.find(field=>field.tag==='entstehungszeitraum')!.values).toEqual([]);
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  await editor.locator('.record-heading[data-object-id="'+submission.id+'"]').click();
  const field=editor.locator('.working-field[data-field-tag="entstehungszeitraum"]');
  if(!await field.isVisible())await editor.locator('.additional-toggle').click();
  const add=field.getByRole('button',{name:'Add value',exact:true}), dates=field.getByRole('textbox',{name:'Date',exact:true});
  await add.click();
  await expect(add).toHaveCount(0);
  await dates.nth(0).fill('2000');await dates.nth(1).fill('2001-12-31');
  await editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true}).click();
  const dialog=editor.getByRole('dialog',{name:'Save metadata changes',exact:true});
  await dialog.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Record the reviewed creation date range.');
  const saved=page.waitForResponse(result=>result.url()===response.url()+'/commands'&&result.request().method()==='POST');
  await dialog.getByRole('button',{name:'Save',exact:true}).click();
  const result=await saved;expect(result.ok(),await result.text()).toBeTruthy();
  await expect(dialog).toBeHidden();
  await editor.locator('.menubar summary').filter({hasText:/^Package$/}).click();
  const reloaded=page.waitForResponse(result=>result.url()===response.url()&&result.ok());
  await editor.getByRole('button',{name:'Reload',exact:true}).click();await reloaded;
  await expect(dates.nth(0)).toHaveValue('2000');await expect(dates.nth(1)).toHaveValue('2001-12-31');
});

test('metadata proposals save, survive reload, reject stale writes and retain discarded evidence', async ({ page }) => {
  const api = process.env['ARCHIVE_BROWSER_API'] || 'http://localhost:8000';
  if (api !== 'http://localhost:8000') {
    expect(api).toBe('http://localhost:8001');
    await page.route('http://localhost:8000/api/**', route => route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  }
  let token = '';
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', async response => {
    if (response.url().endsWith('/protocol/openid-connect/token') && response.ok()) token=(await response.json()).access_token;
  });
  async function login(user: string) {
    await page.goto('/');
    await page.getByRole('textbox',{name:'Username or email'}).fill(user);
    await page.getByRole('textbox',{name:'Password',exact:true}).fill(user+'-local-only');
    await page.getByRole('button',{name:'Sign In',exact:true}).click();
    await expect(page.locator('.identity')).toHaveText(user);
    await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  }
  async function call(path: string, method='GET', body?: unknown) {
    const response=await fetch(api+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    expect(response.ok, response.ok ? '' : await response.text()).toBeTruthy();
    return response;
  }
  async function get(path: string) {
    const response=await fetch(api+path,{headers:{Authorization:'Bearer '+token}});
    expect(response.ok).toBeTruthy();return response.json();
  }
  const title='Metadata draft browser '+randomUUID();
  await login('operator');
  await page.getByRole('textbox',{name:'New transfer title'}).fill(title);
  await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));
  await page.getByRole('button',{name:'Inspect & add SIP'}).click();
  await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const transfer=(await get('/api/transfers')).find((t:{title:string})=>t.title===title);
  const base='/api/transfers/'+transfer.id+'/sips/'+transfer.sips[0].id;
  const originalDigest=transfer.sips[0].sha256;
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const handler=page.getByRole('region',{name:'Ingest Packager workspace'});
  await handler.getByRole('button',{name:'Inspect original',exact:true}).click();
  const search=handler.getByRole('searchbox',{name:'Find title, ID or path'});
  await search.fill('IdD-1');
  await handler.locator('.search-hit').first().click();
  const draftTitle=handler.getByRole('textbox',{name:'Draft titel',exact:true});
  const originalTitle=await draftTitle.inputValue();
  await draftTitle.fill('Reviewed title from browser');
  await expect(handler.locator('.draft-diff').first()).toContainText(originalTitle);
  await expect(handler.locator('.draft-diff').first()).toContainText('Reviewed title from browser');
  page.once('dialog',dialog=>dialog.dismiss());
  await page.getByRole('button',{name:'Packages',exact:true}).click();
  await expect(draftTitle).toHaveValue('Reviewed title from browser');
  const proposals=page.waitForEvent('download');
  await handler.getByRole('button',{name:'Download unsaved proposals',exact:true}).click();
  const proposalJson=JSON.parse(readFileSync((await (await proposals).path())!,'utf8'));
  expect(proposalJson.status).toBe('UNSAVED_BROWSER_PROPOSALS');
  expect(proposalJson.source_sha256).toBe(originalDigest);
  await handler.getByRole('textbox',{name:'Metadata change / discard reason'}).fill('Browser review: correct source description as an explicit draft.');
  const saved=page.waitForResponse(r=>r.url().endsWith(base+'/draft') && r.request().method()==='POST');
  await handler.getByRole('button',{name:'Save metadata draft',exact:true}).click();
  const savedResponse=await saved;expect(savedResponse.ok()).toBeTruthy();
  const first=(await savedResponse.json()).draft;
  await expect(handler.locator('.draft-revision')).toHaveCount(1);
  await expect(draftTitle).toHaveValue('Reviewed title from browser');
  expect((await get('/api/transfers/'+transfer.id)).sips[0].sha256).toBe(originalDigest);
  await handler.getByRole('textbox',{name:'Derived SIP export reason'}).fill('Export exact saved proposal; retain source and approval hold.');
  const beforePlan=await get('/api/transfers/'+transfer.id);
  await call('/api/transfers/'+transfer.id+'/plan','POST',{revision:beforePlan.revision,reason:'Concurrent arrangement before export',targets:[{label:'Retained dossier group',units:transfer.sips[0].units.map((u:{id:string})=>({sip_id:transfer.sips[0].id,unit_id:u.id}))}]});
  await handler.getByRole('button',{name:'Export derived SIP',exact:true}).click();
  await expect(handler.getByRole('alert')).toContainText('Stale transfer revision');
  await handler.getByRole('button',{name:'Reload package',exact:true}).click();
  await handler.getByRole('button',{name:/^Changes/}).click();
  // Commit an export while dropping its acknowledgement, then retry the exact request.
  await page.route('http://localhost:8000'+base+'/exports',async route=>{
    const response=await route.fetch({url:api+base+'/exports'});
    expect(response.ok()).toBeTruthy();
    await route.abort('failed');
  },{times:1});
  await handler.getByRole('button',{name:'Export derived SIP',exact:true}).click();
  await expect(handler.getByRole('alert')).toContainText('Failed to fetch');
  const exported=page.waitForResponse(r=>r.url().endsWith(base+'/exports') && r.request().method()==='POST');
  await handler.getByRole('button',{name:'Export derived SIP',exact:true}).click();
  const exportResponse=await exported;expect(exportResponse.ok()).toBeTruthy();
  const exportResult=await exportResponse.json();expect(exportResult.replayed).toBeTruthy();
  const sipExport=exportResult.export;
  await expect(handler.locator('.sip-export')).toHaveCount(1);
  await expect(handler.locator('.sip-export')).toContainText('Receipt checks are recorded separately');
  await expect(handler.getByRole('button',{name:'Export derived SIP',exact:true})).toBeDisabled();
  const zipDownload=page.waitForEvent('download');
  await handler.getByRole('button',{name:'Download derived SIP',exact:true}).click();
  const zipBytes=readFileSync((await (await zipDownload).path())!);
  expect(createHash('sha256').update(zipBytes).digest('hex')).toBe(sipExport.sha256);
  const lineageDownload=page.waitForEvent('download');
  await handler.getByRole('button',{name:'Download export lineage',exact:true}).click();
  const lineageBytes=readFileSync((await (await lineageDownload).path())!);
  expect(createHash('sha256').update(lineageBytes).digest('hex')).toBe(sipExport.evidence_sha256);
  const lineage=JSON.parse(lineageBytes.toString('utf8'));
  expect(lineage.export.draft_id).toBe(first.id);
  expect(lineage.export.source_sha256).toBe(originalDigest);
  expect(lineage.export.metadata_sha256).toBe(first.metadata_sha256);
  expect(lineage.members.filter((m:{replaced_metadata:boolean})=>m.replaced_metadata)).toHaveLength(1);
  expect(lineage.members.filter((m:{replaced_metadata:boolean})=>!m.replaced_metadata).every((m:{sha256:string;source_sha256:string})=>m.sha256===m.source_sha256)).toBeTruthy();
  expect((await get('/api/transfers/'+transfer.id)).active_metadata_drafts[transfer.sips[0].id]).toBe(first.id);
  await page.reload();
  await expect(page.locator('.identity')).toHaveText('operator');
  await page.locator('button.transfer').filter({hasText:title}).click();
  await page.getByRole('button',{name:'Ingest Packager',exact:true}).click();
  await handler.getByRole('button',{name:'Inspect original',exact:true}).click();
  await search.fill('IdD-1');await handler.locator('.search-hit').first().click();
  await expect(draftTitle).toHaveValue('Reviewed title from browser');
  await handler.getByRole('button',{name:/^Changes/}).click();
  const downloaded=page.waitForEvent('download');
  await handler.getByRole('button',{name:'Download draft metadata',exact:true}).click();
  expect(createHash('sha256').update(readFileSync((await (await downloaded).path())!)).digest('hex')).toBe(first.metadata_sha256);
  await draftTitle.fill('Pending browser correction');
  const doc=await get(base+'/workspace');
  const node=await get(base+'/nodes/'+proposalJson.changes[0].node_id);
  const field=node.edit_fields.find((f:{label:string})=>f.label==='titel');
  await call(base+'/draft','POST',{revision:doc.transfer_revision,source_sha256:originalDigest,base_draft_id:first.id,reason:'Concurrent editor revision',changes:[{node_id:field.node_id,field_id:field.field_id,before:field.saved_value,value:'Concurrent saved correction'}]});
  await handler.getByRole('textbox',{name:'Metadata change / discard reason'}).fill('Pending local proposal must not overwrite a newer revision.');
  await handler.getByRole('button',{name:'Save metadata draft',exact:true}).click();
  await expect(handler.getByRole('alert')).toContainText('Stale transfer revision');
  await expect(draftTitle).toHaveValue('Pending browser correction');
  page.once('dialog',dialog=>dialog.accept());
  await handler.getByRole('button',{name:'Reload package',exact:true}).click();
  await expect(draftTitle).toHaveValue('Concurrent saved correction');
  await handler.getByRole('button',{name:/^Changes/}).click();
  await expect(handler.locator('.draft-revision:not(.sip-export)')).toHaveCount(2);
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Username or email'})).toBeVisible();
  await login('auditor');
  await page.locator('button.transfer').filter({hasText:title}).click();
  await page.getByRole('button',{name:'Ingest Packager',exact:true}).click();
  await handler.getByRole('button',{name:'Inspect original',exact:true}).click();
  await handler.getByRole('button',{name:/^Changes/}).click();
  await expect(handler.locator('.draft-revision:not(.sip-export)')).toHaveCount(2);
  await expect(handler.getByRole('button',{name:'Save metadata draft',exact:true})).toHaveCount(0);
  await expect(handler.getByRole('button',{name:'Discard saved draft',exact:true})).toHaveCount(0);
  await expect(handler.getByRole('button',{name:'Export derived SIP',exact:true})).toHaveCount(0);
  await expect(handler.getByRole('button',{name:'Download derived SIP',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Username or email'})).toBeVisible();
  await login('operator');
  await page.locator('button.transfer').filter({hasText:title}).click();
  await page.getByRole('button',{name:'Ingest Packager',exact:true}).click();
  await handler.getByRole('button',{name:'Inspect original',exact:true}).click();
  await handler.getByRole('button',{name:/^Changes/}).click();
  await handler.getByRole('textbox',{name:'Metadata change / discard reason'}).fill('Discard proposal after review; retain both revisions and originals.');
  await handler.getByRole('button',{name:'Discard saved draft',exact:true}).click();
  await expect(handler.getByText('No active saved differences.',{exact:true})).toBeVisible();
  await expect(handler.locator('.draft-revision:not(.sip-export)')).toHaveCount(2);
  const after=await get('/api/transfers/'+transfer.id);
  expect(after.active_metadata_drafts).toEqual({});
  expect(after.sip_exports).toEqual([sipExport]);
  expect(after.sips[0]).toEqual(transfer.sips[0]);
  expect(after.events.filter((e:{type:string})=>e.type==='metadata-draft-saved')).toHaveLength(2);
  expect(after.events.at(-1).type).toBe('metadata-draft-discarded');
  expect((await get(base+'/workspace')).validation.internal).toBe('PASS');
  await page.screenshot({path:resolve('..','var','browser','metadata-drafts.png'),fullPage:true});
  await search.fill('IdD-1');await handler.locator('.search-hit').first().click();
  await draftTitle.fill('Unsaved proposal explicitly discarded at sign out');
  const dialogs: string[]=[];
  page.on('dialog',dialog=>{dialogs.push(dialog.type());void dialog.accept();});
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Username or email'})).toBeVisible();
  expect(dialogs).toEqual(['confirm']);
  expect(errors).toEqual([]);
});
