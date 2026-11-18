import {test,expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {resolve} from 'node:path';
import type {MetadataWorkspace,MetadataRow} from '../src/metadata-editor.types';

test.use({actionTimeout:20_000});
test('structured metadata search combines fields, identities and dates on the applied version',async({page})=>{
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  let token='';page.on('response',async response=>{if(response.url().endsWith('/protocol/openid-connect/token')&&response.ok())token=(await response.json()).access_token;});
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill('operator');await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await page.getByRole('textbox',{name:'New transfer title'}).fill('Structured search '+randomUUID());await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/preparation')&&response.ok());
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const response=await loaded,base=response.url(),doc:MetadataWorkspace=await response.json();
  const dossier=doc.nodes.find(node=>node.kind==='dossier' && node.fields.some(field=>field.tag==='entstehungszeitraum' && field.values?.length))!;
  const period=dossier.fields.find(field=>field.tag==='entstehungszeitraum')!,dates=structuredClone(period.values!);
  const dateRow=(rows:MetadataRow[],text:string)=>{
    for(const row of rows){
      if(row.children['datum']){row.children['datum'][0].text=text;return;}
      dateRow(Object.values(row.children).flat(),text);
    }
  };
  dateRow(dates[0].children['von'],'2001');dateRow(dates[0].children['bis'],'2003-12-31');
  const saved=await fetch(base+'/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({
    request_id:randomUUID(),revision:doc.revision,source_sha256:doc.source_sha256,base_operation_id:doc.state.head,kind:'edit',reason:'Set an explicit search test period',
    node_id:dossier.id,field_id:period.id,before:period.values,values:dates,
  })});
  expect(saved.ok,await saved.text()).toBeTruthy();
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  await editor.locator('.menubar summary').filter({hasText:/^Package$/}).click();await editor.getByRole('button',{name:'Reload',exact:true}).click();
  await editor.locator('.tool-tabs').getByRole('button',{name:'Search metadata',exact:true}).click();
  const tools=editor.locator('.tools-pane'),filters=tools.locator('.metadata-search-filters');
  await filters.locator('summary').click();await expect(filters.getByRole('combobox',{name:'SIP validation finding',exact:true})).toBeDisabled();
  await filters.getByRole('combobox',{name:'Object type',exact:true}).selectOption('dossier');
  await filters.getByRole('combobox',{name:'Metadata field',exact:true}).selectOption('titel');
  await filters.getByRole('searchbox',{name:'Name, identifier or path',exact:true}).fill(dossier.source_id!);
  await filters.getByLabel('Period from',{exact:true}).fill('2002-01-01');await filters.getByLabel('Period until',{exact:true}).fill('2002-12-31');
  const results=tools.locator('.metadata-search-table tbody tr');
  await expect(results).toHaveCount(1);await expect(results).toContainText(dossier.title);
  await expect(tools.locator('.search-version')).toContainText('Applied working version; excludes unsaved proposals');
  if(process.env['ARCHIVE_UPDATE_DOCS']==='true'){
    const viewport=page.viewportSize()!;await page.setViewportSize({width:1600,height:1100});await editor.getByRole('separator',{name:'Resize editor and tools',exact:true}).focus();await page.keyboard.press('Home');await editor.locator('.tool-body').evaluate(element=>{element.scrollTop=0;});await page.screenshot({path:resolve('..','docs','images','metadata-search.png'),fullPage:true});await page.setViewportSize(viewport);
  }
  await filters.getByLabel('Period from',{exact:true}).fill('2003-12-31');await filters.getByLabel('Period until',{exact:true}).fill('2003-12-31');await expect(results).toContainText(dossier.title);
  await filters.getByLabel('Period from',{exact:true}).fill('2004-01-01');await filters.getByLabel('Period until',{exact:true}).fill('2004-12-31');await expect(results).toContainText('No matches.');
  await filters.getByLabel('Period from',{exact:true}).fill('2005-01-01');await expect(tools.getByRole('alert')).toHaveText('The start date must be on or before the end date.');
  await filters.getByRole('button',{name:'Reset filters',exact:true}).click();await expect(tools.getByRole('alert')).toHaveCount(0);
  const query=tools.getByRole('searchbox',{name:'Search metadata',exact:true});
  await query.fill(dossier.source_id!);await expect(tools.getByRole('button',{name:dossier.title,exact:true})).toBeVisible();
  await tools.getByRole('button',{name:dossier.title,exact:true}).click();await expect(editor.locator('.record-heading').filter({hasText:dossier.title})).toBeVisible();
  await expect(tools.locator('.metadata-search-table')).toBeVisible();await expect(editor.locator('.classification-pane')).toBeVisible();
  await editor.getByRole('textbox',{name:'Title',exact:true}).fill('Unsaved search-only proposal');
  await query.fill('Unsaved search-only proposal');await expect(results).toContainText('No matches.');
  await query.fill(dossier.source_id!);await expect(results).toContainText(dossier.title);
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
