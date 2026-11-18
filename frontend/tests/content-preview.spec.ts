import {test,expect} from '@playwright/test';
import {createHash,randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {crc32,inflateRawSync} from 'node:zlib';
import type {Route} from '@playwright/test';
import type {PackageWorkspace} from '../src/package-handler.types';

test.use({actionTimeout:20_000});
test('passive previews decode native images and video without executing submitted PDF actions',async({page})=>{
    test.setTimeout(120_000);
    const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
    if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
    const unexpected:string[]=[];page.on('pageerror',error=>unexpected.push(error.message));
    page.on('request',request=>{if(request.url().includes('untrusted.invalid'))unexpected.push(request.url());});
    page.on('dialog',async dialog=>{unexpected.push(dialog.message());await dialog.dismiss();});
    await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill('operator');await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
    await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
    const generated=await page.evaluate(async()=>{
      const canvas=document.createElement('canvas');canvas.width=96;canvas.height=64;
      const context=canvas.getContext('2d')!;context.fillStyle='#175c8a';context.fillRect(0,0,96,64);
      const png=await new Promise<Blob>(resolve=>canvas.toBlob(blob=>resolve(blob!),'image/png'));
      const stream=canvas.captureStream(10),recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'}),chunks:Blob[]=[];
      recorder.ondataavailable=event=>chunks.push(event.data);
      const finished=new Promise<void>(resolve=>recorder.onstop=()=>resolve());
      recorder.start();
      for(let frame=0;frame<8;frame++){context.fillStyle=frame%2?'#175c8a':'#aa3926';context.fillRect(0,0,96,64);await new Promise(resolve=>setTimeout(resolve,100));}
      recorder.stop();await finished;stream.getTracks().forEach(track=>track.stop());
      return {png:Array.from(new Uint8Array(await png.arrayBuffer())),video:Array.from(new Uint8Array(await new Blob(chunks,{type:'video/webm'}).arrayBuffer()))};
    });
    const objects=[
      '<< /Type /Catalog /Pages 2 0 R /OpenAction 8 0 R >>',
      '<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R /Annots [9 0 R] >>',
      pdfStream('BT /F1 20 Tf 20 120 Td (Passive preview sample) Tj ET'),
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
      pdfStream('BT /F1 20 Tf 20 120 Td (Second sample page) Tj ET'),
      '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
      '<< /Type /Action /S /JavaScript /JS (app.alert\\("Submitted action executed"\\);) >>',
      '<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] /A << /S /URI /URI (https://untrusted.invalid/from-pdf) >> >>',
    ];
    let pdf='%PDF-1.7\n';const offsets=[0];
    for(const [index,object] of objects.entries()){offsets.push(Buffer.byteLength(pdf));pdf+=`${index+1} 0 obj\n${object}\nendobj\n`;}
    const xref=Buffer.byteLength(pdf);pdf+=`xref\n0 ${offsets.length}\n0000000000 65535 f \n`+offsets.slice(1).map(offset=>`${String(offset).padStart(10,'0')} 00000 n \n`).join('')+`trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    const files=unzip(await readFile(resolve('..','fixtures','reference-1.3.zip')));
    const replacements=[
      {before:'Auftragsuebersicht.pdf',after:'passive-probe.pdf',data:Buffer.from(pdf)},
      {before:'Auftragsuebersicht.tiff',after:'passive-image.png',data:Buffer.from(generated.png)},
      {before:'WAVE-Testdatei.wav',after:'passive-video.webm',data:Buffer.from(generated.video)},
    ];
    const metadata=[...files.keys()].find(path=>path.endsWith('/header/metadata.xml'))!;
    const incoming=replacements.map(item=>({...item,data:undefined,hashes:Object.fromEntries(['md5','sha1','sha256','sha512'].map(algorithm=>[algorithm,createHash(algorithm).update(item.data).digest('hex')]))}));
    const changed=await page.evaluate(({source,changes})=>{
      const document=new DOMParser().parseFromString(source,'application/xml');
      for(const change of changes){
        const element=Array.from(document.getElementsByTagNameNS('*','datei')).find(element=>Array.from(element.children).some(child=>child.localName==='name'&&child.textContent===change.before));
        if(!element)throw new Error('Sample file metadata missing');
        const children=Array.from(element.children),name=children.find(child=>child.localName==='name')!,algorithm=children.find(child=>child.localName==='pruefalgorithmus')!.textContent!.toLowerCase().replaceAll('-',''),checksum=children.find(child=>child.localName==='pruefsumme')!;
        name.textContent=change.after;checksum.textContent=change.hashes[algorithm];
      }
      return new XMLSerializer().serializeToString(document);
    },{source:files.get(metadata)!.toString(),changes:incoming});
    files.set(metadata,Buffer.from(changed));
    for(const replacement of replacements){const path=[...files.keys()].find(path=>path.endsWith('/'+replacement.before))!;files.delete(path);files.set(path.slice(0,-replacement.before.length)+replacement.after,replacement.data);}
    await page.getByRole('textbox',{name:'New transfer title'}).fill('Native passive preview '+randomUUID());await page.getByRole('button',{name:'Create',exact:true}).click();
    await page.locator('input[type=file]').first().setInputFiles({name:'passive-preview.zip',mimeType:'application/zip',buffer:zip(files)});
    await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
    const editor=page.getByRole('region',{name:'Versioned metadata editor'}),content=editor.locator('.content-pane'),dialog=page.getByRole('dialog',{name:'Preview original content',exact:true});
    await content.locator('.navigator-tabs').getByRole('button',{name:'Package content',exact:true}).click();
    for(const name of ['passive-probe.pdf','passive-image.png','passive-video.webm']){
      await content.locator('input[type=search]').fill(name);await content.locator('.metadata-object').filter({hasText:name}).first().click();
      await editor.locator('.reference-toolbar').getByRole('button',{name:'Preview original content',exact:true}).click();
      await dialog.getByRole('button',{name:'Load preview',exact:true}).click();
      if(name.endsWith('.webm')){
        const video=dialog.locator('video');await expect(video).toBeVisible();await expect.poll(()=>video.evaluate(element=>element instanceof HTMLVideoElement&&Number.isFinite(element.duration)&&element.videoWidth===96)).toBe(true);
        expect(await video.evaluate(element=>element instanceof HTMLVideoElement&&element.paused)).toBe(true);
        await video.evaluate(element=>element instanceof HTMLVideoElement?element.play():Promise.reject(new Error('Expected video')));
        await expect.poll(()=>video.evaluate(element=>element instanceof HTMLVideoElement&&!element.paused)).toBe(true);
      }else{await expect(dialog.locator('canvas')).toBeVisible();await expect(dialog.locator('iframe,object,embed,a')).toHaveCount(0);}
      await dialog.getByRole('button',{name:'Close',exact:true}).click();await expect(dialog).toBeHidden();await expect(page.locator('audio,video')).toHaveCount(0);
    }
    expect(unexpected).toEqual([]);
  });

  function pdfStream(content:string){return `<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}\nendstream`;}

  function unzip(data:Buffer){
    const end=data.lastIndexOf(Buffer.from([0x50,0x4b,0x05,0x06])),files=new Map<string,Buffer>();
    if(end<0)throw new Error('Sample ZIP directory missing');
    let position=data.readUInt32LE(end+16);
    for(let index=0;index<data.readUInt16LE(end+10);index++){
      const size=data.readUInt32LE(position+20),nameSize=data.readUInt16LE(position+28),extraSize=data.readUInt16LE(position+30),commentSize=data.readUInt16LE(position+32),local=data.readUInt32LE(position+42);
      const name=data.subarray(position+46,position+46+nameSize).toString(),start=local+30+data.readUInt16LE(local+26)+data.readUInt16LE(local+28),compressed=data.subarray(start,start+size),method=data.readUInt16LE(position+10);
      if(method!==0&&method!==8)throw new Error('Unsupported sample compression');
      files.set(name,method===8?inflateRawSync(compressed):Buffer.from(compressed));position+=46+nameSize+extraSize+commentSize;
    }
    return files;
  }

  function zip(files:Map<string,Buffer>){
    const local:Buffer[]=[],central:Buffer[]=[];let offset=0;
    for(const [path,data] of files){
      const name=Buffer.from(path),header=Buffer.alloc(30),directory=Buffer.alloc(46),checksum=crc32(data);
      header.writeUInt32LE(0x04034b50);header.writeUInt16LE(20,4);header.writeUInt16LE(0x800,6);header.writeUInt16LE(33,12);header.writeUInt32LE(checksum,14);header.writeUInt32LE(data.length,18);header.writeUInt32LE(data.length,22);header.writeUInt16LE(name.length,26);
      directory.writeUInt32LE(0x02014b50);directory.writeUInt16LE(20,4);directory.writeUInt16LE(20,6);directory.writeUInt16LE(0x800,8);directory.writeUInt16LE(33,14);directory.writeUInt32LE(checksum,16);directory.writeUInt32LE(data.length,20);directory.writeUInt32LE(data.length,24);directory.writeUInt16LE(name.length,28);directory.writeUInt32LE(offset,42);
      local.push(header,name,data);central.push(directory,name);offset+=header.length+name.length+data.length;
    }
    const index=Buffer.concat(central),end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50);end.writeUInt16LE(files.size,8);end.writeUInt16LE(files.size,10);end.writeUInt32LE(index.length,12);end.writeUInt32LE(offset,16);
    return Buffer.concat([...local,index,end]);
  }
test('passive source previews render PDF TIFF JP2 and audio, check fixity and release resources',async({page})=>{
  test.setTimeout(150_000);
  const api=process.env['ARCHIVE_BROWSER_API']||'http://localhost:8000';
  if(api!=='http://localhost:8000')await page.route('http://localhost:8000/api/**',route=>route.continue({url:route.request().url().replace('http://localhost:8000',api)}));
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  let token='';page.on('response',async response=>{if(response.url().endsWith('/protocol/openid-connect/token')&&response.ok())token=(await response.json()).access_token;});
  await page.addInitScript(()=>{
    const active=new Set<string>(),create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
    URL.createObjectURL=blob=>{const url=create(blob);active.add(url);return url;};
    URL.revokeObjectURL=url=>{active.delete(url);revoke(url);};
    Object.defineProperty(window,'previewBlobCount',{get:()=>active.size});
  });
  await page.goto('/');await page.getByRole('textbox',{name:'Username or email'}).fill('operator');await page.getByRole('textbox',{name:'Password',exact:true}).fill('operator-local-only');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();await page.getByRole('textbox',{name:'New transfer title'}).fill('Passive content preview '+randomUUID());await page.getByRole('button',{name:'Create',exact:true}).click();
  await page.locator('input[type=file]').first().setInputFiles(resolve('..','fixtures','reference-1.3.zip'));await page.getByRole('button',{name:'Inspect & add SIP'}).click();await expect(page.getByText('Internal checks passed',{exact:true})).toBeVisible();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/workspace')&&response.ok());await page.getByRole('button',{name:'Open Ingest Packager',exact:true}).click();
  const response=await loaded,workspace:PackageWorkspace=await response.json(),base=new URL(response.url()).pathname.replace(/\/workspace$/,'');
  const before=await fetch(api+base+'/preparation',{headers:{Authorization:'Bearer '+token}}).then(response=>response.json());
  const editor=page.getByRole('region',{name:'Versioned metadata editor'}),content=editor.locator('.content-pane');
  await content.locator('.navigator-tabs').getByRole('button',{name:'Package content',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Preview original content',exact:true});
  async function open(name:string){
    await content.locator('input[type=search]').fill(name);
    await content.locator('.metadata-object').filter({hasText:name}).first().click();
    await editor.locator('.reference-toolbar').getByRole('button',{name:'Preview original content',exact:true}).click();
    await expect(dialog.getByRole('heading',{name,exact:true})).toBeVisible();
  }
  async function close(){await dialog.getByRole('button',{name:'Close',exact:true}).click();await expect(dialog).toBeHidden();await expect.poll(()=>page.evaluate(()=>Reflect.get(window,'previewBlobCount'))).toBe(0);}
  async function image(name:string){
    await open(name);await dialog.getByRole('button',{name:'Load preview',exact:true}).click();
    const canvas=dialog.locator('canvas');await expect(canvas).toBeVisible({timeout:25_000});
    const rendered=await canvas.evaluate(element=>{
      if(!(element instanceof HTMLCanvasElement))throw new Error('Expected canvas');
      const pixels=element.getContext('2d')!.getImageData(0,0,element.width,element.height).data;
      let minimum=255,alpha=0;
      for(let index=0;index<pixels.length;index+=4){minimum=Math.min(minimum,pixels[index]);alpha=Math.max(alpha,pixels[index+3]);}
      return {minimum,alpha,width:element.width,height:element.height};
    });
    expect(rendered.alpha,JSON.stringify(rendered)).toBeGreaterThan(0);
    expect(rendered.minimum,JSON.stringify(rendered)).toBeLessThan(240);
    await expect(dialog.locator('iframe,object,embed')).toHaveCount(0);return canvas;
  }
  await image('gpl2.pdf');
  await expect(dialog.getByRole('button',{name:'Next page',exact:true})).toBeEnabled();await dialog.getByRole('button',{name:'Next page',exact:true}).click();
  await expect(dialog.locator('canvas')).toBeVisible();await expect(dialog.getByRole('navigation',{name:'PDF pages'})).toContainText('2 /');
  await close();await image('Auftragsuebersicht.tiff');
  if(process.env['ARCHIVE_UPDATE_DOCS']==='true'){
    const viewport=page.viewportSize()!;await page.setViewportSize({width:1600,height:1100});await page.screenshot({path:resolve('..','docs','images','content-preview.png'),fullPage:true});await page.setViewportSize(viewport);
  }
  await close();await image('00000004.jp2');await close();
  await open('WAVE-Testdatei.wav');await dialog.getByRole('button',{name:'Load preview',exact:true}).click();
  const audio=dialog.locator('audio');await expect(audio).toBeVisible();await expect.poll(()=>audio.evaluate(element=>element instanceof HTMLAudioElement&&Number.isFinite(element.duration)&&element.duration>0)).toBe(true);
  expect(await audio.evaluate(element=>element instanceof HTMLAudioElement&&element.paused)).toBe(true);
  await audio.evaluate(async element=>{if(!(element instanceof HTMLAudioElement))throw new Error('Expected audio');element.muted=true;await element.play();});
  await expect.poll(()=>audio.evaluate(element=>element instanceof HTMLAudioElement&&element.currentTime>0)).toBe(true);
  const playing=await audio.elementHandle();expect(playing).not.toBeNull();await close();
  expect(await playing!.evaluate(element=>element instanceof HTMLAudioElement&&element.paused&&!element.hasAttribute('src'))).toBe(true);await playing!.dispose();
  await open('ReadMe.txt');await expect(dialog.getByText('This format, including HTML/SVG, remains attachment-only.',{exact:true})).toBeVisible();await expect(dialog.getByRole('button',{name:'Load preview',exact:true})).toHaveCount(0);await close();
  const pdf=workspace.nodes.find(node=>node.kind==='file'&&node.title==='gpl2.pdf')!,url='http://localhost:8000'+base+'/nodes/'+pdf.id+'/content';
  await page.route(url,async route=>{const response=await route.fetch({url:api+base+'/nodes/'+pdf.id+'/content'});const body=await response.body();body[10]^=1;await route.fulfill({response,body});},{times:1});
  await open('gpl2.pdf');await dialog.getByRole('button',{name:'Load preview',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('SHA-256 does not match the retained original.');await expect(dialog.locator('canvas')).toBeHidden();await close();
  await page.clock.install();
  let held:Route|null=null;const intercepted=page.waitForRequest(url);
  await page.route(url,route=>{held=route;},{times:1});
  await open('gpl2.pdf');await dialog.getByRole('button',{name:'Load preview',exact:true}).click();await intercepted;
  await page.clock.fastForward(20_001);await expect(dialog.getByRole('alert')).toContainText('Preview timed out.');await held!.abort();await close();
  const after=await fetch(api+base+'/preparation',{headers:{Authorization:'Bearer '+token}}).then(response=>response.json());
  expect(after.history).toEqual(before.history);expect(after.revision).toBe(before.revision);expect(after.state.metadata_sha256).toBe(before.state.metadata_sha256);
  expect(errors).toEqual([]);
});
