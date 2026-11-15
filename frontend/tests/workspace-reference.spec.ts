// Visual reference only. Every API response is synthetic and intercepted in this
// browser context; no request changes the user's separate Package Turbo service.
import {test,expect} from '@playwright/test';
import {resolve} from 'node:path';

test('capture Package Turbo layout with an explicitly simulated package',async({page})=>{
  const url=process.env['ARCHIVE_REFERENCE_URL'];test.skip(!url,'Set ARCHIVE_REFERENCE_URL to the local reference UI for a visual comparison.');
  const sip={id:'f044321a-b150-4a3d-9b40-af2f156ea558',name:'Reference layout · simulated data',read_only:false,delivery_type:'GEVER',schema_version:'5.1',revision:'visual-reference'};
  const entity=(id:string,kind:string,name:string,has_children=false)=>({id,kind,name,label:kind,has_children,appearance:'',reference:'',timespan:''});
  const root=entity('0','paket',sip.name,true),toc=entity('0.0','inhaltsverzeichnis','Paketinhalt',true),submission=entity('0.1','ablieferung','Testamt',true),provenance=entity('0.1.0','provenienz','Testamt'),system=entity('0.1.1','ordnungssystem','Testplan',true),position=entity('0.1.1.0','ordnungssystemposition','1 Allgemeines',true),dossier=entity('0.1.1.0.0','dossier','Sitzungsakten'),content=entity('0.0.0','ordner','content');
  const nodes=[root,toc,submission,provenance,system,position,dossier,content];
  const children:Record<string,typeof nodes>={'0':[toc,submission],'0.0':[content],'0.1':[provenance,system],'0.1.1':[position],'0.1.1.0':[dossier]};
  const fields:Record<string,object[]>={'0.1':[{tag:'ablieferndeStelle',label:'Abliefernde Stelle',kind:'text',values:['Testamt'],editable:true,primary:true,min_items:0,max_items:1,choices:[]}],'0.1.0':[{tag:'aktenbildnerName',label:'Aktenbildner',kind:'text',values:['Testarchiv'],editable:true,primary:true,min_items:0,max_items:1,choices:[]}]};
  const unexpected:string[]=[];
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;let result:unknown;
    if(route.request().method()!=='GET'){unexpected.push(route.request().method()+' '+path);await route.abort();return;}
    if(path==='/api/v1/auth/session')result={display_name:'Simulated visual reference',subject:'visual-reference'};
    else if(path==='/api/v1/packages')result=[sip];
    else if(path==='/api/v1/packages/'+sip.id)result=sip;
    else if(path.endsWith('/dossiers'))result={items:[dossier],next_offset:null,revision:sip.revision};
    else if(path.includes('/nodes/')){const parts=path.split('/nodes/')[1].split('/');const id=decodeURIComponent(parts[0]);result=parts[1]==='children'?{items:children[id]||[],next_offset:null,revision:sip.revision}:{node:nodes.find(n=>n.id===id),parent_id:id==='0'?null:id.slice(0,id.lastIndexOf('.')),fields:fields[id]||[],references:[],allowed_children:[],can_upload_file:false,can_add_note:false,file_name:null,revision:sip.revision};}
    else{unexpected.push(path);await route.abort();return;}
    await route.fulfill({json:result});
  });
  await page.setViewportSize({width:1280,height:720});await page.goto(url!);await page.getByRole('button',{name:'Öffnen',exact:true}).click();await page.getByRole('button',{name:/Reference layout · simulated data/}).click();
  await expect(page.getByRole('heading',{name:'Ordnungssystem-Navigator',exact:true})).toBeVisible();await expect(page.getByText('Testamt',{exact:false}).first()).toBeVisible();
  await page.screenshot({path:resolve('..','var','local-evidence','current','workspace-ux-reference-simulated.png'),fullPage:true});expect(unexpected).toEqual([]);
});
