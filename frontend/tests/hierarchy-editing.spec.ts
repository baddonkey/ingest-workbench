import {test,expect} from '@playwright/test';
import {randomUUID,createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

test.use({actionTimeout:20_000});
test('drag sibling rows to save order directly while preserving notes and history',async({page})=>{
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  let token='';const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',async r=>{if(r.url().endsWith('/protocol/openid-connect/token')&&r.ok())token=(await r.json()).access_token;});
  async function login(user:string){await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill(user);await page.getByRole('textbox',{name:'Password',exact:true}).fill(user+'-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();}
  async function get(path:string){const r=await fetch(api+path,{headers:{Authorization:'Bearer '+token}});expect(r.ok).toBeTruthy();return r.json();}
  await login('operator');const title='Hierarchy order '+randomUUID();await page.getByRole('textbox',{name:'New transfer title'}).fill(title);await page.getByRole('button',{name:'Create',exact:true}).click();await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const transfer=(await get('/api/transfers')).find((t:{title:string})=>t.title===title),sip=transfer.sips[0],base='/api/transfers/'+transfer.id,preparation=base+'/sips/'+sip.id+'/preparation';
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();const editor=page.getByRole('region',{name:'Versioned metadata editor'});const initial=await get(preparation),group=initial.reorder_groups.find((g:{child_kind:string;children:unknown[]})=>g.child_kind==='dossier'&&g.children.length===3),first=group.children[0];
  const classification=editor.locator('.classification-pane');
  await classification.locator('[data-object-id="'+first.id+'"]').click();await editor.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Preserve object and note identities while arranging sibling order.');await editor.getByRole('button',{name:'Notes · 0',exact:true}).click();await editor.locator('.reference-toolbar').getByRole('button',{name:'New note',exact:true}).click();await editor.getByRole('textbox',{name:'Note text',exact:true}).fill('This note must stay linked to this dossier.');await editor.getByRole('button',{name:'Save note',exact:true}).click();await expect(editor.locator('.preparation-note')).toHaveCount(1);
  await editor.locator('.reference-toolbar').getByRole('button',{name:'Validation',exact:true}).click();await editor.getByRole('button',{name:'Freeze & validate',exact:true}).click();await expect(editor.locator('.prepared-version')).toHaveCount(1,{timeout:30_000});await editor.locator('.classification-pane [data-object-id="'+first.id+'"]').click();const originalFreeze=(await get(base)).prepared_inputs[0];expect(originalFreeze.version).toBe(1);
  await editor.locator('#metadata-context').fill('');
  const row=(id:string)=>classification.locator('[data-object-id="'+id+'"]');
  const expected=[group.children[1],group.children[2],first];
  const orderedRows=classification.locator(group.children.map((child:{id:string})=>'[data-object-id="'+child.id+'"]').join(', '));
  const headings=(children:{title:string}[])=>children.map(child=>child.title);
  for(const child of group.children){
    const branch=row(child.id).locator('..').locator('.disclosure[aria-expanded="true"]');
    if(await branch.count())await branch.click();
  }
  await expect(row(first.id)).toHaveAttribute('draggable','true');
  await row(first.id).dragTo(row(group.parent_id),{targetPosition:{x:12,y:18}});
  await expect(orderedRows.locator('.node-label')).toHaveText(headings(group.children));
  expect((await get(preparation)).history).toHaveLength(1);
  await page.route('http://localhost:8000'+preparation+'/commands',async route=>{const response=await route.fetch({url:api+preparation+'/commands'});expect(response.ok()).toBeTruthy();await route.abort('failed');},{times:1});
  const dragAfterLast=async()=>{
    const target=row(group.children[2].id);
    await target.scrollIntoViewIfNeeded();
    await expect(row(first.id)).toBeInViewport();
    const from=(await row(first.id).boundingBox())!,to=(await target.boundingBox())!;
    await page.mouse.move(from.x+20,from.y+from.height/2);
    await page.mouse.down();
    await page.mouse.move(to.x+20,to.y+to.height*.75,{steps:10});
    await page.mouse.move(to.x+21,to.y+to.height*.75);
    await expect(target.locator('../..')).toHaveClass(/order-drop-after/);
    await page.mouse.up();
  };
  await dragAfterLast();await expect(editor.getByRole('alert')).toContainText('Failed to fetch');
  await dragAfterLast();await expect(orderedRows.locator('.node-label')).toHaveText(headings(expected));
  await expect(editor.locator('#metadata-context')).toHaveValue('');
  await expect(editor.getByRole('dialog')).toHaveCount(0);
  let doc=await get(preparation);expect(doc.history).toHaveLength(2);expect(doc.history[1].kind).toBe('structure-reorder');expect(doc.history[1].reason).toBeTruthy();expect(doc.history[1].changes.after_order).toEqual(expected.map((c:{id:string})=>c.id));expect(doc.nodes.map((n:{id:string})=>n.id).sort()).toEqual(initial.nodes.map((n:{id:string})=>n.id).sort());
  await editor.locator('.classification-pane [data-object-id="'+first.id+'"]').click();await expect(editor.locator('.classification-pane [data-object-id="'+first.id+'"]')).toHaveAttribute('aria-current','true');await editor.getByRole('button',{name:'Notes · 1',exact:true}).click();await expect(editor.locator('.preparation-note')).toContainText('This note must stay linked to this dossier.');
  await editor.locator('.reference-toolbar').getByRole('button',{name:'Validation',exact:true}).click();await editor.getByRole('textbox',{name:'Snapshot reason',exact:true}).fill('Freeze the saved sibling order.');await editor.getByRole('button',{name:'Freeze & validate',exact:true}).click();await expect(editor.locator('.prepared-version')).toHaveCount(2,{timeout:30_000});const frozen=(await get(base)).prepared_inputs.at(-1);expect(frozen.version).toBe(2);
  const download=page.waitForEvent('download');await editor.locator('[data-prepared-id="'+frozen.id+'"]').getByRole('button',{name:'Download METS/PREMIS evidence',exact:true}).click();expect(createHash('sha256').update(readFileSync((await (await download).path())!)).digest('hex')).toBe(frozen.evidence_sha256);
  await page.getByRole('button',{name:'m:n mapping',exact:true}).click();const mapping=page.getByRole('region',{name:'Frozen input arrangement and review'});await mapping.locator('[data-input-sip="'+sip.id+'"]').selectOption(frozen.id);await expect(mapping.locator('.mapping-row b').first()).toHaveText(expected[0].title);
  await page.getByRole('button',{name:'Ingest Packager',exact:true}).click();await classification.locator('[data-object-id="'+first.id+'"]').click();await editor.getByRole('textbox',{name:'Editing context / reason',exact:true}).fill('Verify undo and redo preserve note and order identity.');await editor.getByRole('button',{name:'Undo',exact:true}).click();await editor.getByRole('button',{name:'Redo',exact:true}).click();
  await editor.getByRole('button',{name:/^Edit history/}).first().click();await expect(editor.locator('.metadata-operation')).toHaveCount(4);
  const history=editor.locator('.metadata-operation');
  for(const [label,from,to] of [['Sibling order changed',1,3],['Undo',3,1],['Redo',1,3]] as const){
    const event=history.filter({has:page.getByRole('button',{name:label,exact:true})});
    await expect(event.locator('.history-order-summary')).toContainText(first.title+' moved from position '+from+' to '+to);
    await expect(event.locator('.history-old-value, .history-new-value')).toHaveCount(0);
  }
  const captureDocs=process.env['ARCHIVE_UPDATE_DOCS']==='true';
  if(captureDocs){
    await page.setViewportSize({width:2100,height:1400});
    await editor.locator('.history-table-scroll').screenshot({path:resolve('..','docs','images','sibling-reorder-history.png')});
  }
  await history.filter({has:page.getByRole('button',{name:'Undo',exact:true})}).getByRole('button').click();
  const historyDialog=editor.getByRole('dialog',{name:'History event',exact:true});
  const orderDetails=historyDialog.locator('.history-order-details');
  await expect(orderDetails.locator('ol').first()).toBeHidden();
  await orderDetails.getByText('Order details',{exact:true}).click();
  await expect(orderDetails.locator('ol').first().locator('li')).toHaveText(headings(expected));
  await expect(orderDetails.locator('ol').last().locator('li')).toHaveText(headings(group.children));
  if(captureDocs)await historyDialog.screenshot({path:resolve('..','docs','images','sibling-reorder-details.png')});
  await page.keyboard.press('Escape');
  const noteRow=editor.locator('.metadata-operation').filter({hasText:'Note added'});await expect(noteRow.locator('.history-old-value')).toHaveText('Not present');await expect(noteRow.locator('.history-new-value')).toContainText('This note must stay linked to this dossier.');
  await editor.locator('.metadata-operation').last().getByRole('button').click();await editor.getByRole('dialog',{name:'History event',exact:true}).getByRole('button',{name:'Compare with working version',exact:true}).click();await expect(editor.locator('.version-comparison')).toContainText('Object order');
  await editor.locator('.metadata-operation').last().getByRole('button').click();await editor.getByRole('dialog',{name:'History event',exact:true}).getByRole('button',{name:'Restore version',exact:true}).click();await expect(editor.locator('.metadata-operation')).toHaveCount(5);doc=await get(preparation);expect(doc.notes).toHaveLength(1);expect((await get(base)).sips[0]).toEqual(sip);
  await editor.locator('#metadata-context').fill('');
  await row(first.id).click({button:'right'});await expect(editor.getByRole('menuitem',{name:'Move up',exact:true})).toBeDisabled();await editor.getByRole('menuitem',{name:'Move down',exact:true}).click();
  await expect(orderedRows.locator('.node-label')).toHaveText(headings([group.children[1],first,group.children[2]]));
  await expect(history.first().locator('.history-order-summary')).toContainText(first.title+' and '+group.children[1].title+' swapped positions 1 and 2');
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await login('auditor');await page.locator('button.transfer').filter({hasText:title}).click();await page.getByRole('button',{name:'Ingest Packager',exact:true}).click();await expect(row(first.id)).toHaveAttribute('draggable','false');await row(first.id).click({button:'right'});await expect(editor.getByRole('menuitem',{name:'Move down',exact:true})).toBeDisabled();
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await page.screenshot({path:resolve('..','var','local-evidence','current','hierarchy-editing-mobile.png'),fullPage:true});expect(errors).toEqual([]);
});
