import {test,expect} from '@playwright/test';
import {existsSync,readdirSync} from 'node:fs';
import {resolve} from 'node:path';

test.use({timezoneId:'UTC'});

test('compact sortable transfer results and direct workspace navigation',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const sip={id:'display-sip',profile:'1.3.0',sha256:'0'.repeat(64),units:[],files:{},metadata_path:'header/metadata.xml'};
  const transfers=Array.from({length:125},(_,index)=>({id:'display-transfer-'+index,title:'Transfer '+String(index).padStart(3,'0')+' · '+('longname'.repeat(20)),state:index===0?'RECEIVED':'INSPECTED',revision:index===0?2:1,sips:Array.from({length:index===0?0:30},(_,i)=>({...sip,id:sip.id+i,...(i===0?{original_filename:'Source Alpha.zip',metadata_path:'Root-Alpha/sub/header/metadata.xml'}:i===1?{original_filename:'other-original.zip'}:{})})),outputs:[],events:[{type:'validation',time:'2026-03-01T12:00:00Z'},{type:'creation',time:'2026-01-02T12:00:00Z'}],plan:null}));
  const cors={'access-control-allow-origin':'http://localhost:4200','access-control-allow-credentials':'true','access-control-allow-methods':'GET,POST,OPTIONS','access-control-allow-headers':'authorization,content-type'};
  if(process.env['ARCHIVE_UI_BUILT']){
    const assets=resolve('dist','workbench','browser'),files=new Set(readdirSync(assets));let nonce:string|null=null;
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin==='http://localhost:4200'){
        const name=url.pathname.slice(1)||'index.html',file=resolve(assets,name);
        if(files.has(name)&&existsSync(file)){const contentType=name.endsWith('.html')?'text/html':name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'image/svg+xml';await route.fulfill({path:file,contentType});return;}
      }
      if(url.origin==='http://localhost:8080'&&url.pathname.endsWith('/auth')){
        nonce=url.searchParams.get('nonce');const redirect=new URL(url.searchParams.get('redirect_uri')!);
        redirect.hash=new URLSearchParams({state:url.searchParams.get('state')!,code:'test-code'}).toString();
        await route.fulfill({contentType:'text/html',body:'<script>location.replace('+JSON.stringify(redirect.href)+')</script>'});return;
      }
      if(url.origin==='http://localhost:8080'&&url.pathname.endsWith('/token')){
        const jwt=(value:object)=>Buffer.from(JSON.stringify(value)).toString('base64url'),now=Math.floor(Date.now()/1000);
        const token=jwt({alg:'none'})+'.'+jwt({iat:now,exp:now+3600,preferred_username:'operator',sub:'test',nonce})+'.x';
        await route.fulfill({headers:cors,json:{access_token:token,id_token:token,refresh_token:token,token_type:'Bearer',expires_in:3600}});return;
      }
      await route.continue();
    });
  }
  await page.route('http://localhost:8000/api/transfers',async route=>{
    const method=route.request().method();
    if(method==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    if(method==='POST'){
      const title=route.request().postDataJSON().title as string;
      const created={id:'created-transfer',title,state:'RECEIVED',revision:0,sips:[],outputs:[],events:[] as {type:string;time:string}[],plan:null};
      transfers.unshift(created);await route.fulfill({headers:cors,json:created});return;
    }
    await route.fulfill({headers:cors,json:transfers});
  });
  let failPage=false;
  await page.route('**/api/transfer-pages?**',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    if(failPage){failPage=false;await route.fulfill({status:503,headers:cors,json:{detail:'Page unavailable'}});return;}
    const params=new URL(route.request().url()).searchParams,q=(params.get('q')||'').toLowerCase(),sort=params.get('sort'),direction=params.get('direction')==='ascending'?1:-1,offset=Number(params.get('offset')),limit=Number(params.get('limit'));
    if(q==='slow')await new Promise(resolve=>setTimeout(resolve,300));
    const filtered=transfers.filter(t=>t.title.toLowerCase().includes(q)||t.state.toLowerCase().includes(q));
    if(sort!=='created')filtered.sort((a,b)=>direction*(sort==='sips'?a.sips.length-b.sips.length:sort==='revision'?a.revision-b.revision:sort==='state'?a.state.localeCompare(b.state):a.title.localeCompare(b.title)));
    const items=filtered.slice(offset,offset+limit);
    await route.fulfill({headers:cors,json:{items,total:transfers.length,matched:filtered.length,next_offset:offset+items.length<filtered.length?offset+items.length:null}});
  });
  await page.goto('/');
  if(!process.env['ARCHIVE_UI_BUILT']){
    await page.getByRole('textbox',{name:'Username or email'}).fill('operator');
    await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');
    await page.getByRole('button',{name:'Sign In',exact:true}).click();
  }
  const overview=page.getByRole('region',{name:'Transfers'}),list=page.locator('.transfer-list'),detail=page.locator('.card.detail');
  await expect(list.locator('.transfer')).toHaveCount(30);
  await expect(page.locator('.overview-list-heading')).toContainText('30 of 125 results');
  const table=list.getByRole('table',{name:'Transfer results'});
  await expect(table.getByRole('columnheader')).toHaveText(['Title ↕','Status ↕','Created','SIPs ↕','Rev.↕']);
  await expect(table.locator('tbody tr').first().locator('time')).toHaveAttribute('datetime','2026-01-02T12:00:00Z');
  await expect(table.locator('tbody tr').first().locator('time')).toHaveText('1/2/26');
  await expect(table).not.toContainText('Source Alpha.zip');
  const firstResult=table.locator('tbody .transfer').first();
  await table.getByRole('button',{name:'Title',exact:true}).click();
  await expect(firstResult).toContainText('Transfer 000');
  await expect(table.getByRole('columnheader',{name:'Title'})).toHaveAttribute('aria-sort','ascending');
  await table.getByRole('button',{name:'Title',exact:true}).click();await expect(firstResult).toContainText('Transfer 124');
  await expect(table.getByRole('columnheader',{name:'Title'})).toHaveAttribute('aria-sort','descending');
  await table.getByRole('button',{name:'Status',exact:true}).click();await expect(table.locator('tbody tr').first().locator('td').nth(1)).toHaveText('INSPECTED');
  await table.getByRole('button',{name:'SIPs',exact:true}).click();await expect(firstResult).toContainText('Transfer 000');
  await table.getByRole('button',{name:'Rev.',exact:true}).click();await expect(table.locator('tbody tr').first().locator('td').nth(4)).toHaveText('1');
  await table.getByRole('button',{name:'Rev.',exact:true}).click();await expect(firstResult).toContainText('Transfer 000');
  const search=page.getByRole('searchbox',{name:'Search transfers'});
  await search.fill('received');await expect(list.locator('.transfer')).toHaveCount(1);await expect(list.locator('.transfer').first()).toContainText('Transfer 000');
  await search.fill('no matching transfer');await expect(list.getByText('No transfers match your search.')).toBeVisible();
  const slowRequest=page.waitForRequest(request=>new URL(request.url()).searchParams.get('q')==='slow');
  await search.fill('slow');await slowRequest;await search.fill('TRANSFER 012');await expect(list.locator('.transfer')).toHaveCount(1);await page.waitForTimeout(400);await expect(firstResult).toContainText('Transfer 012');
  await search.fill('');await expect(list.locator('.transfer')).toHaveCount(30);
  for(const [width,height] of [[1280,900],[1280,720],[900,720],[390,844]]){
    await page.setViewportSize({width,height});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    expect(await page.evaluate(()=>document.documentElement.scrollHeight)).toBeLessThanOrEqual(height);
    const bounds=await overview.boundingBox(),main=await page.locator('main').boundingBox();
    const padding=await page.locator('main').evaluate(e=>parseFloat(getComputedStyle(e).paddingLeft)+parseFloat(getComputedStyle(e).paddingRight));
    expect(Math.abs(bounds!.width-(main!.width-padding))).toBeLessThan(2);
    expect(await list.evaluate(e=>getComputedStyle(e).overflowY)).toBe('auto');
    expect(await list.evaluate(e=>e.scrollWidth<=e.clientWidth && e.scrollHeight>e.clientHeight)).toBe(true);
    const first=await table.locator('tbody tr').first().boundingBox(),second=await table.locator('tbody tr').nth(1).boundingBox(),tableBounds=await table.boundingBox();
    expect(first!.height).toBeLessThanOrEqual(50);expect(second!.y).toBeGreaterThanOrEqual(first!.y+first!.height-1);
    expect(Math.abs(first!.width-tableBounds!.width)).toBeLessThan(2);
    expect(await list.locator('.transfer').first().evaluate(e=>e.scrollWidth>e.clientWidth && getComputedStyle(e).whiteSpace==='nowrap')).toBe(true);
    for(const [index,name] of [[0,'Title'],[1,'Status'],[3,'SIPs'],[4,'Rev.']] as const){
      await table.getByRole('button',{name,exact:true}).click();
      expect(await table.getByRole('columnheader').nth(index).evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
    }
    await expect(list.locator('.transfer')).toHaveCount(30);
    await list.evaluate(e=>e.scrollTop=e.scrollHeight);
    await expect.poll(async()=>list.locator('.transfer').count()).toBeGreaterThan(30);
    const sticky=await table.getByRole('columnheader').first().boundingBox(),listBounds=await list.boundingBox();
    expect(Math.abs(sticky!.y-listBounds!.y)).toBeLessThan(2);
    expect(await table.getByRole('columnheader').first().evaluate(e=>getComputedStyle(e).backgroundColor)).toBe('rgb(255, 255, 255)');
  }
  for(const count of [90,120,125]){await list.evaluate(e=>e.scrollTop=e.scrollHeight);await expect.poll(async()=>list.locator('.transfer').count()).toBeGreaterThanOrEqual(count);}
  await expect(page.getByRole('button',{name:'Load more transfers'})).toHaveCount(0);
  failPage=true;await search.fill('TRANSFER 012');await expect(list.getByRole('alert')).toContainText('Page unavailable');await page.getByRole('button',{name:'Load more transfers'}).click();await expect(list.locator('.transfer')).toHaveCount(1);
  await table.locator('tbody tr').first().locator('td').nth(1).click();
  await expect(list).toHaveCount(0);await expect(detail.locator('.sip')).toHaveCount(30);
  await expect(detail.getByRole('heading',{name:/^Transfer 012/})).toBeVisible();
  await expect(detail.locator('.card-heading').first()).toContainText('Created: Jan 2, 2026');
  const firstSip=detail.locator('.sip').nth(0),secondSip=detail.locator('.sip').nth(1),olderSip=detail.locator('.sip').nth(2);
  await expect(firstSip.locator('.sip-source')).toContainText('Source Alpha.zip');await expect(firstSip.locator('.sip-source')).toContainText('Root-Alpha/sub');
  await expect(secondSip.locator('.sip-source')).toContainText('other-original.zip');await expect(secondSip.locator('.sip-source')).not.toContainText('SIP folder in ZIP');
  await expect(olderSip.locator('.sip-source')).toContainText('Not recorded');await expect(olderSip.locator('.sip-source')).not.toContainText('SIP folder in ZIP');
  for(const [width,height] of [[1280,900],[390,844]]){
    await page.setViewportSize({width,height});
    const bounds=await detail.boundingBox(),main=await page.locator('main').boundingBox();
    expect(bounds!.width).toBeGreaterThan(main!.width-(width>900?100:35));
    expect(bounds!.y+bounds!.height).toBeLessThanOrEqual(height);
    expect(await detail.evaluate(e=>getComputedStyle(e).overflowY==='auto'&&e.scrollHeight>e.clientHeight)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  await page.getByRole('button',{name:'Back to transfers',exact:true}).click();
  await expect(list.locator('.transfer')).toHaveCount(1);await expect(search).toHaveValue('TRANSFER 012');
  await search.fill('');await expect(list.locator('.transfer')).toHaveCount(30);
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('textbox',{name:'New transfer title'}).fill('New synthetic transfer');
  await page.getByRole('button',{name:'Create',exact:true}).click();
  await expect(detail.getByRole('heading',{name:'New synthetic transfer'})).toBeVisible();
  await expect(detail.locator('.card-heading').first()).toContainText('Created: Unavailable');
  await page.getByRole('button',{name:'Back to transfers',exact:true}).click();
  await expect(page.locator('aside .sidebar-stats').first().locator('dd')).toHaveText('126');
  await search.fill('New synthetic transfer');
  const created=list.getByRole('button',{name:'New synthetic transfer'});
  await expect(created.locator('xpath=../..')).toContainText('Unavailable');
  await created.focus();await page.keyboard.press('Enter');
  await expect(detail.getByRole('heading',{name:'New synthetic transfer'})).toBeVisible();
  await page.locator('aside').getByRole('button',{name:/Transfers/}).click();
  await expect(created).toBeVisible();
  expect(errors).toEqual([]);
});
