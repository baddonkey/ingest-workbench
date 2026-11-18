import {test,expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {resolve} from 'node:path';

test.use({actionTimeout:15_000});

test('KOST progress, reruns and frozen targets preserve unsaved metadata',async({page})=>{
  test.setTimeout(120_000);
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const now=Date.now()/1000;
  const digest='a'.repeat(64),frozenDigest='b'.repeat(64);
  const initialId=randomUUID();
  let latest={id:initialId,state:'TRACKING',result:null as string|null,remote_status:'queued',created:now,
    started:now,finished:null as number|null,actor:'automatic',trigger:'upload',input_sha256:digest,
    errors:[] as {module:string;message:string}[],message:null,last_error:null,report_sha256:null as string|null,report_status:'pending'};
  let frozen:typeof latest|null=null;
  const previous:typeof latest[]=[];
  let submitted:Record<string,unknown>|null=null;
  await page.route('**/kost-validations**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname.endsWith('/findings')){
      const id=url.pathname.split('/').at(-2);
      const run=[latest,...previous,...(frozen?[frozen]:[])].find(item=>item.id===id)!;
      await route.fulfill({json:{run_id:run.id,input_sha256:run.input_sha256,report_sha256:run.report_sha256,
        prepared_id:run===frozen?'frozen-test':null,reported_result:run.result,filename:'test.zip',available:false,reason:'Test report has no structured findings.'}});return;
    }
    if(url.pathname.endsWith('/report')){
      await route.fulfill({status:200,contentType:'application/xml',headers:{'content-disposition':'attachment; filename="kost.xml"'},body:'<report/>'});return;
    }
    if(url.pathname.endsWith('/runs')){
      await route.fulfill({json:{runs:url.searchParams.has('prepared_id')?(frozen?[frozen]:[]):[latest,...previous],next:null}});return;
    }
    if(request.method()==='POST'){
      submitted=request.postDataJSON();
      const next={...latest,id:randomUUID(),state:'QUEUED',result:null,remote_status:'queued',errors:[],report_sha256:null,report_status:'pending'};
      if(submitted?.['prepared_id'])frozen={...next,input_sha256:frozenDigest};
      else{previous.unshift({...latest});latest=next;}
      await route.fulfill({status:202,json:next});return;
    }
    await route.fulfill({json:{enabled:true,advisory:true,targets:[
      {target_id:'received',prepared_id:null,label:'Received SIP',input_sha256:digest,latest},
      {target_id:'frozen-test',prepared_id:'frozen-test',label:'Frozen test version',input_sha256:frozenDigest,latest:frozen},
    ]}});
  });
  await page.goto('/');
  await page.getByRole('textbox',{name:'Username or email'}).fill('operator');
  await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'About',exact:true}).click();
  const about=page.getByRole('dialog',{name:'About Ingest Workbench'});
  await expect(about).toContainText('Version 0.0.1');
  await about.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('textbox',{name:'New transfer title'}).fill('KOST browser '+randomUUID());
  await page.getByRole('button',{name:'Create',exact:true}).click();
  await expect(page.getByRole('button',{name:'Storage & AIS',exact:true})).toHaveCount(0);
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));
  await page.getByRole('button',{name:'Inspect & add SIP'}).click();
  await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  await page.clock.install();
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  await editor.locator('.menubar').getByText('Help',{exact:true}).click();
  await editor.getByRole('button',{name:'About Ingest Workbench',exact:true}).click();
  await expect(about).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(about).toBeHidden();
  const panel=page.getByRole('region',{name:'SIP-Validation',exact:true});
  await expect(editor.locator('.tool-tabs').getByRole('button',{name:'Validation help',exact:true})).toHaveCount(0);
  expect((await page.request.get('/package-turbo/scan-search.svg')).headers()['content-type']).toContain('image/svg+xml');
  await expect(panel).toBeHidden();
  await editor.locator('.reference-toolbar').getByRole('button',{name:'SIP-Validation',exact:true}).click();
  await expect(panel).toBeVisible();
  await expect(panel.locator('details').first()).toHaveAttribute('open','');
  await expect(panel.locator('.kost-status')).toHaveText('Queued');
  const toolBody=editor.locator('.tool-body');
  const kostHost=toolBody.locator('archive-kost-validation');
  const toolBodyBox=await toolBody.boundingBox(),kostHostBox=await kostHost.boundingBox();
  expect(toolBodyBox&&kostHostBox).not.toBeNull();
  expect(Math.abs(kostHostBox!.y-toolBodyBox!.y)).toBeLessThan(2);
  expect(Math.abs(kostHostBox!.y+kostHostBox!.height-toolBodyBox!.y-toolBodyBox!.height)).toBeLessThan(2);
  const splitter=editor.getByRole('separator',{name:'Resize editor and tools',exact:true});
  await expect(splitter).toBeVisible();
  const initialPercent=Number(await splitter.getAttribute('aria-valuenow'));
  const splitterBox=await splitter.boundingBox();
  const classification=editor.locator('.classification-pane'),content=editor.locator('.content-pane');
  const classificationBefore=await classification.boundingBox(),contentBefore=await content.boundingBox();
  const objectBefore=await editor.locator('.object-editor').boundingBox(),toolsBefore=await editor.locator('.tools-pane').boundingBox();
  expect(splitterBox).not.toBeNull();
  expect(classificationBefore&&contentBefore&&objectBefore&&toolsBefore).not.toBeNull();
  await page.mouse.move(splitterBox!.x+splitterBox!.width/2,splitterBox!.y+splitterBox!.height/2);
  await page.mouse.down();
  await page.mouse.move(splitterBox!.x+splitterBox!.width/2,splitterBox!.y+splitterBox!.height/2-60);
  await page.mouse.up();
  const draggedPercent=Number(await splitter.getAttribute('aria-valuenow'));
  expect(draggedPercent).toBeLessThan(initialPercent);
  const classificationAfter=await classification.boundingBox(),contentAfter=await content.boundingBox();
  const objectAfter=await editor.locator('.object-editor').boundingBox(),toolsAfter=await editor.locator('.tools-pane').boundingBox();
  expect(classificationAfter&&contentAfter&&objectAfter&&toolsAfter).not.toBeNull();
  for(const [before,after] of [[classificationBefore,classificationAfter],[contentBefore,contentAfter]]){
    expect(Math.abs(before!.y-after!.y)).toBeLessThan(1);
    expect(Math.abs(before!.height-after!.height)).toBeLessThan(1);
  }
  expect(objectAfter!.height).toBeLessThan(objectBefore!.height-40);
  expect(toolsAfter!.height).toBeGreaterThan(toolsBefore!.height+40);
  await splitter.focus();
  await page.keyboard.press('ArrowDown');
  await expect(splitter).toHaveAttribute('aria-valuenow',String(draggedPercent+1));
  const classificationAfterKey=await classification.boundingBox(),contentAfterKey=await content.boundingBox();
  expect(classificationAfterKey&&contentAfterKey).not.toBeNull();
  expect(Math.abs(classificationBefore!.height-classificationAfterKey!.height)).toBeLessThan(1);
  expect(Math.abs(contentBefore!.y-contentAfterKey!.y)).toBeLessThan(1);
  await page.setViewportSize({width:500,height:900});
  await expect(classification).toBeVisible();
  await expect(content).toBeVisible();
  await expect(splitter).toBeHidden();
  expect((await classification.boundingBox())!.height).toBeGreaterThanOrEqual(140);
  expect((await content.boundingBox())!.height).toBeGreaterThanOrEqual(180);
  await page.setViewportSize({width:1280,height:720});
  const topStatus=editor.locator('.kost-top-status');
  await expect(topStatus).toContainText('Advisory');
  await expect(topStatus).toContainText('Received SIP');
  await expect(topStatus).toContainText('Queued');
  const statusBox=await topStatus.boundingBox(),languageBox=await editor.locator('.workspace-language').boundingBox(),searchBox=await editor.locator('.quick-search').boundingBox();
  for(const item of [languageBox,searchBox])expect(statusBox&&item&&Math.abs(statusBox.y+statusBox.height/2-item.y-item.height/2)).toBeLessThan(2);
  const organisation=editor.getByRole('textbox',{name:'Submitting organisation',exact:true});
  await organisation.fill('Unsaved while SIP validation runs');
  latest={...latest,remote_status:'running'};
  await page.clock.fastForward(5100);
  await expect(panel.locator('.kost-status')).toHaveText('Running');
  await expect(organisation).toHaveValue('Unsaved while SIP validation runs');
  await expect(panel.getByRole('button',{name:'Re-run SIP validation',exact:true})).toBeDisabled();
  latest={...latest,state:'COMPLETE',result:'invalid',finished:now+10,report_sha256:'c'.repeat(64),report_status:'retained',
    errors:[{module:'SIP',message:'<img src=x onerror="window.kostExecuted=true"> '+ 'Detailed failure '.repeat(30)}]};
  await page.clock.fastForward(5100);
  await expect(panel.locator('.kost-status')).toHaveText('Invalid');
  await expect(panel.locator('.kost-findings').first()).toContainText('<img src=x');
  expect(await page.evaluate(()=>(window as Window & {kostExecuted?:boolean}).kostExecuted)).toBeUndefined();
  const download=page.waitForEvent('download');
  await panel.getByRole('button',{name:'Download XML report',exact:true}).click();
  expect((await download).suggestedFilename()).toMatch(/kost-/);
  await panel.getByRole('button',{name:'Re-run SIP validation',exact:true}).click();
  await expect(panel.locator('.kost-status')).toHaveText('Queued');
  await panel.getByText('Previous runs and reports',{exact:true}).click();
  await expect(panel.locator('.kost-history article')).toHaveCount(2);
  await expect(panel.locator('.kost-history article').last()).toContainText('Invalid');
  await panel.getByRole('combobox',{name:'Validated artifact'}).selectOption('frozen-test');
  await expect(panel.locator('.kost-status')).toHaveText('Not run');
  await panel.getByRole('button',{name:'Run SIP validation',exact:true}).click();
  await expect(panel.locator('.kost-status')).toHaveText('Queued');
  expect(submitted?.['prepared_id']).toBe('frozen-test');
  expect(submitted?.['input_sha256']).toBe(frozenDigest);
  await expect(organisation).toHaveValue('Unsaved while SIP validation runs');
  await page.screenshot({path:resolve('..','var','browser','kost-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:resolve('..','var','browser','kost-mobile.png'),fullPage:true});
  expect(errors).toEqual([]);
});

test('structured KOST table links exact files and isolates frozen versions',async({page})=>{
  test.setTimeout(120_000);
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  let token='',sip:any;
  page.on('response',async response=>{if(response.url().endsWith('/protocol/openid-connect/token')&&response.ok())token=(await response.json()).access_token;});
  const receivedId=randomUUID(),frozenId=randomUUID(),reportDigest='d'.repeat(64);
  let frozenDigest='f'.repeat(64);
  const hostile='<img src=x onerror="window.reportExecuted=true">';
  const latest=(frozen=false)=>({id:frozen?frozenId:receivedId,state:'COMPLETE',result:'invalid',created:Date.now()/1000,
    started:Date.now()/1000,finished:Date.now()/1000,actor:'operator',trigger:'manual',input_sha256:frozen?frozenDigest:sip.sha256,
    errors:[],message:null,last_error:null,report_sha256:reportDigest,report_status:'retained'});
  let readmePath='',pdfPath='';
  await page.route('**/kost-validations**',async route=>{
    const url=new URL(route.request().url()),frozen=url.pathname.includes(frozenId)||url.searchParams.has('prepared_id');
    const run=latest(frozen);
    if(url.pathname.endsWith('/report')){await route.fulfill({contentType:'application/xml',body:'<KOSTValLog/>'});return;}
    if(url.pathname.endsWith('/findings')){
      await route.fulfill({json:{run_id:run.id,input_sha256:run.input_sha256,report_sha256:reportDigest,
        prepared_id:frozen?'frozen-report':null,reported_result:'invalid',filename:'reference-1.3.zip',available:true,
        checked_at:Date.now()/1000,report_path:url.pathname.replace('/findings','/report'),
        parser_version:1,validator_error:true,file_counts:{valid:1,invalid:0,recognized:1,not_assessed:0},
        finding_counts:{error:0,warning:1,info:0,validator_error:1},entries:[
          {id:'package',scope:'package',path:null,reported_path:'C:\\Downloads\\reference-1.3.zip',mapping:'package',
            check:'Validation: SIP',format_version:'',outcome:'invalid',findings:[{severity:'validator_error',module:'3C) format validation',message:'Missing resource key '+hostile}]},
          {id:'pdf',scope:'file',path:pdfPath,reported_path:'C:\\temp\\'+pdfPath,mapping:'matched',
            check:'Validation: PDF/A',format_version:'1B',outcome:'valid',findings:[{severity:'warning',module:'Warning',message:'Example warning'}]},
          {id:'readme',scope:'file',path:readmePath,reported_path:'C:\\temp\\'+readmePath,mapping:'matched',
            check:'Recognition: TXT',format_version:'',outcome:'recognized',findings:[]},
        ]}});return;
    }
    if(url.pathname.endsWith('/runs')){await route.fulfill({json:{runs:[run],next:null}});return;}
    await route.fulfill({json:{enabled:true,advisory:true,targets:[
      {target_id:'received',prepared_id:null,label:'Received SIP',input_sha256:sip.sha256,latest:latest()},
      {target_id:'frozen-report',prepared_id:'frozen-report',label:'Frozen version',input_sha256:frozenDigest,latest:latest(true)},
    ]}});
  });
  await page.goto('/');
  await page.getByRole('textbox',{name:'Username or email'}).fill('operator');
  await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await page.getByRole('textbox',{name:'New transfer title'}).fill('Structured KOST '+randomUUID());
  await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));
  const uploaded=page.waitForResponse(response=>response.url().endsWith('/sips')&&response.request().method()==='POST'&&response.ok());
  await page.getByRole('button',{name:'Inspect & add SIP'}).click();
  const transfer=await (await uploaded).json();sip=transfer.sips[0];frozenDigest=sip.sha256;
  const files=Object.values(sip.files) as {path:string}[];
  readmePath=files.find(file=>file.path.endsWith('ReadMe.txt'))!.path;
  pdfPath=files.find(file=>file.path.endsWith('.pdf'))!.path;
  const preparation=page.waitForResponse(response=>response.url().endsWith('/preparation')&&response.ok());
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const prepared=await (await preparation).json();
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  await editor.locator('.reference-toolbar').getByRole('button',{name:'SIP-Validation',exact:true}).click();
  const panel=page.getByRole('region',{name:'SIP-Validation',exact:true});
  const table=panel.getByRole('table');
  await expect(table.getByRole('row')).toHaveCount(4);
  await expect(panel.getByText('Validator error present',{exact:true})).toBeVisible();
  await expect(table).toContainText('Valid with warning');
  await expect(table).toContainText('Recognized, not validated');
  await table.getByRole('row').filter({hasText:'Validation: SIP'}).locator('summary').click();
  await expect(table).toContainText(hostile);
  expect(await page.evaluate(()=>(window as Window & {reportExecuted?:boolean}).reportExecuted)).toBeUndefined();
  await panel.getByRole('combobox',{name:'Severity',exact:true}).selectOption('warning');
  await expect(table.getByRole('row')).toHaveCount(2);
  await panel.getByRole('combobox',{name:'Severity',exact:true}).selectOption('all');
  await editor.locator('.editor-type-tabs').getByRole('button',{name:'Statistics',exact:true}).click();
  const statistics=editor.getByRole('region',{name:'Statistics',exact:true});
  await expect(statistics.getByText('Format assessment entries for this input',{exact:true})).toBeVisible();
  await statistics.getByRole('row').filter({has:page.getByRole('rowheader',{name:'Recognized, not validated',exact:true})}).getByRole('button').click();
  const formatDetails=editor.getByRole('dialog',{name:'Statistics · Recognized, not validated',exact:true});
  await expect(formatDetails).toContainText('ReadMe.txt');await expect(formatDetails).toContainText(reportDigest);
  await formatDetails.getByRole('button',{name:'Close',exact:true}).click();
  await statistics.getByRole('row').filter({has:page.getByRole('rowheader',{name:'Validator error',exact:true})}).getByRole('button').click();
  const errorDetails=editor.getByRole('dialog',{name:'Statistics · Validator error',exact:true});
  await expect(errorDetails).toContainText(hostile);
  expect(await page.evaluate(()=>(window as Window & {reportExecuted?:boolean}).reportExecuted)).toBeUndefined();
  await errorDetails.getByRole('button',{name:'Close',exact:true}).click();
  await editor.locator('.tool-tabs').getByRole('button',{name:'Search metadata',exact:true}).click();
  const searchTools=editor.locator('.tools-pane'),searchFilters=searchTools.locator('.metadata-search-filters');
  await searchFilters.locator('summary').click();
  const findingFilter=searchFilters.getByRole('combobox',{name:'SIP validation finding',exact:true});
  await expect(findingFilter).toBeEnabled();await findingFilter.selectOption('warning');
  const searchRows=searchTools.locator('.metadata-search-table tbody tr');
  await expect(searchRows.first()).toContainText(pdfPath.split('/').at(-1)!);
  expect(await searchRows.locator('.metadata-search-link').allTextContents()).not.toContain('ReadMe.txt');
  await editor.locator('.tool-tabs').getByRole('button',{name:'SIP-Validation',exact:true}).click();
  await panel.getByRole('combobox',{name:'Validated artifact'}).selectOption('frozen-report');
  await expect(statistics.getByText('Format assessment entries for this input',{exact:true})).toHaveCount(0);
  await editor.locator('.tool-tabs').getByRole('button',{name:'Search metadata',exact:true}).click();
  await searchFilters.locator('summary').click();await expect(findingFilter).toBeDisabled();await expect(searchRows).toContainText('No matches.');
  await editor.locator('.tool-tabs').getByRole('button',{name:'SIP-Validation',exact:true}).click();
  await panel.getByRole('combobox',{name:'Validated artifact'}).selectOption('received');
  await editor.getByRole('button',{name:'Package content',exact:true}).click();
  const sourceId=Object.entries(sip.files).find(([,file])=>(file as {path:string}).path===readmePath)![0];
  const fileNode=prepared.nodes.find((node:{source_id:string;kind:string})=>node.source_id===sourceId&&node.kind==='datei');
  await editor.locator('[data-object-id="'+fileNode.id+'"]').first().click();
  const fileFindings=editor.getByRole('region',{name:'File validation results'});
  await expect(fileFindings).toContainText('Recognized, not validated');
  await expect(fileFindings).toContainText('Checked');
  const fileReport=page.waitForEvent('download');
  await editor.getByRole('button',{name:'Download run XML report',exact:true}).click();
  expect((await fileReport).suggestedFilename()).toBe('kost-'+receivedId+'.xml');
  await panel.getByRole('combobox',{name:'Validated artifact'}).selectOption('frozen-report');
  await expect(fileFindings).toContainText('another SIP version');
  await table.getByRole('button',{name:pdfPath,exact:true}).click();
  await expect(table.getByRole('row').filter({hasText:pdfPath})).toHaveClass(/selected/);
  await panel.getByRole('combobox',{name:'Validated artifact'}).selectOption('received');
  await expect(fileFindings).toContainText('Recognized, not validated');
  await editor.locator('.editor-type-tabs').getByRole('button',{name:'Overview',exact:true}).click();
  const organisation=editor.getByRole('textbox',{name:'Submitting organisation',exact:true});
  const original=await organisation.inputValue();
  await organisation.fill('Unsaved alongside report navigation');
  await table.getByRole('button',{name:readmePath,exact:true}).click();
  await expect(organisation).toHaveValue('Unsaved alongside report navigation');
  await organisation.fill(original);
  await expect(editor.locator('.reference-toolbar').getByRole('button',{name:'Save',exact:true})).toBeDisabled();
  await table.getByRole('button',{name:readmePath,exact:true}).click();
  const handler=page.getByRole('region',{name:'Ingest Packager workspace'});
  await expect(handler.locator('.object-heading h3')).toHaveText('ReadMe.txt');
  await expect(handler.locator('.package-editor').getByRole('region',{name:'File validation results'})).toContainText('Recognized, not validated');
  await page.screenshot({path:resolve('..','var','browser','kost-report-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:resolve('..','var','browser','kost-report-mobile.png'),fullPage:true});
  const after=await page.request.get('http://localhost:8000/api/transfers/'+transfer.id,{headers:{Authorization:'Bearer '+token}});
  expect((await after.json()).revision).toBe(transfer.revision);
  expect(errors).toEqual([]);
});

test('live KOST service validates a received SIP and retains rerun history',async({page})=>{
  test.skip(process.env['ARCHIVE_KOST_LIVE']!=='true','Opt-in local KOST service qualification');
  test.setTimeout(240_000);
  let token='';
  page.on('response',async response=>{
    if(response.url().endsWith('/protocol/openid-connect/token') && response.ok())token=(await response.json()).access_token;
  });
  await page.goto('/');
  await page.getByRole('textbox',{name:'Username or email'}).fill('operator');
  await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await page.getByRole('textbox',{name:'New transfer title'}).fill('Live KOST '+randomUUID());
  await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));
  const uploaded=page.waitForResponse(response=>response.url().endsWith('/sips') && response.request().method()==='POST' && response.ok());
  await page.getByRole('button',{name:'Inspect & add SIP'}).click();
  const transfer=await (await uploaded).json();
  expect(transfer.sips[0].original_filename).toBe('reference-1.3.zip');
  const base='http://localhost:8000/api/transfers/'+transfer.id+'/sips/'+transfer.sips[0].id+'/kost-validations';
  const headers={Authorization:'Bearer '+token};
  async function latest(){const response=await page.request.get(base,{headers});expect(response.ok()).toBeTruthy();return (await response.json()).targets[0].latest;}
  await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const editor=page.getByRole('region',{name:'Versioned metadata editor'});
  await editor.locator('.reference-toolbar').getByRole('button',{name:'SIP-Validation',exact:true}).click();
  const panel=page.getByRole('region',{name:'SIP-Validation',exact:true});
  await expect.poll(async()=>(await latest())?.state,{timeout:150_000,intervals:[1000,3000,5000]}).toBe('COMPLETE');
  const first=await latest();
  expect(['valid','invalid']).toContain(first.result);
  expect(first.input_sha256).toBe(transfer.sips[0].sha256);
  expect(first.filename).toBe(transfer.sips[0].original_filename);
  expect(first.report_sha256).toMatch(/^[a-f0-9]{64}$/);
  const report=await page.request.get(base+'/runs/'+first.id+'/report',{headers});
  expect(report.ok()).toBeTruthy();expect(report.headers()['content-disposition']).toContain('attachment');
  await panel.getByRole('button',{name:'Refresh SIP Validator status',exact:true}).click();
  const queued=page.waitForResponse(response=>response.url()===base && response.request().method()==='POST');
  await panel.getByRole('button',{name:'Re-run SIP validation',exact:true}).click();
  const second=await (await queued).json();expect(second.id).not.toBe(first.id);
  expect(second.filename).toBe(transfer.sips[0].original_filename);
  await expect.poll(async()=>(await latest())?.state,{timeout:60_000,intervals:[1000,3000,5000]}).toBe('COMPLETE');
  expect((await latest()).remote_id).not.toBe(first.remote_id);
  const history=await page.request.get(base+'/runs',{headers});expect((await history.json()).runs).toHaveLength(2);
  const after=await page.request.get('http://localhost:8000/api/transfers/'+transfer.id,{headers});
  expect((await after.json()).revision).toBe(transfer.revision);
  await page.screenshot({path:resolve('..','var','browser','kost-live.png'),fullPage:true});
});