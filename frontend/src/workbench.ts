import { Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { KEYCLOAK } from './identity';
import { ArchiveApi } from './archive-api';
import { PackageHandler } from './package-handler';
import { PreparedArrangement } from './prepared-arrangement';
import { PreparedSelection } from './metadata-editor.types';
import {version} from '../package.json';
export { KEYCLOAK } from './identity';

interface Unit {id:string;title:string;files:string[];metadata_xml:string}
interface Sip {id:string;profile:string;sha256:string;metadata_path?:string;original_filename?:string|null;units:Unit[];files:Record<string,{path:string;sha256:string;size:number}>}
interface ArchiveEvent {id:string;time:string;type:string;actor:string;outcome:string;detail:unknown;hash:string}
interface Output {version?:number;conformance?:{structural_valid:boolean;preservation_ready:boolean;readiness_reasons:string[]};id:string;label:string;files:number;sha256:string;size:number;status:string}
interface Transfer {id:string;title:string;state:string;revision:number;sips:Sip[];events:ArchiveEvent[];outputs:Output[];plan:unknown;active_metadata_drafts?:Record<string,string>;aip_context?:{archive_identifier:string;archive_name:string;designated_community:string;knowledge_base:string;rights:unknown[];formats:unknown[]};representation_resources?:{id:string;title:string;sha256:string}[]}
type TransferSort = 'title'|'state'|'sips'|'revision';
interface TransferPage {items:Transfer[];total:number;matched:number;next_offset:number|null}

@Component({selector:'archive-app',standalone:true,imports:[CommonModule,FormsModule,PackageHandler,PreparedArrangement],template:`
<div class="shell" [class.sidebar-open]="sidebarOpen()">
  <aside><a class="brand" href="/" aria-label="Ingest Workbench home"><img class="cross" src="/archive-mark.svg" alt=""><span>Ingest Workbench</span></a>
    <div class="workspace">ARCHIVAL OPERATIONS</div><button class="nav active" (click)="backToTransfers()" [disabled]="busy() || handlerWorking || arrangementWorking">▦ &nbsp; Transfers <span>{{totalTransfers()}}</span></button>
    <dl class="sidebar-stats" aria-label="Workspace information">
      <div><dt>Transfers in workspace</dt><dd>{{totalTransfers()}}</dd></div>
    </dl>
    <div class="sidebar-bottom">
      <dl class="sidebar-stats" aria-label="Reference information">
        <div><dt>Supported SIP profiles</dt><dd>1.0 / 1.1 / 1.2 / 1.3</dd></div>
        <div><dt>Preservation evidence</dt><dd>METS + PREMIS</dd></div>
      </dl>
      <button class="about-link quiet" (click)="aboutDialog.showModal()">About</button>
      <div class="aside-note"><p>eCH-0160 · OAIS<br>Python API / Angular</p></div>
      <div class="sidebar-account"><span class="sidebar-user">{{userName}}</span><button class="quiet" (click)="logout()" [disabled]="arrangementWorking || handlerWorking">Sign out</button></div>
    </div>
    <button class="sidebar-close quiet" (click)="sidebarOpen.set(false)" aria-label="Close navigation">×</button>
  </aside>
  <main [class.package-focus]="tab==='handler'"><header><button class="mobile-nav-toggle quiet" (click)="sidebarOpen.set(!sidebarOpen())" [attr.aria-expanded]="sidebarOpen()" aria-label="Open navigation">☰</button></header>
    @if(error()){<div role="alert" class="error">{{error()}}</div>}
    @if(!workspaceOpen()){
      <section class="card transfers transfers-overview" aria-labelledby="transfers-heading">
        <div class="overview-heading"><div><div class="eyebrow">WORKSPACE</div><h1 id="transfers-heading">Transfers</h1><p>Find a transfer to continue preparing your archival packages.</p></div><button class="quiet" (click)="refresh()" [disabled]="busy()">Refresh</button></div>
        <div class="overview-toolbar">
          <label class="transfer-search" for="transfer-search">Search transfers<input id="transfer-search" type="search" maxlength="200" placeholder="Search by title or status" [ngModel]="search()" (ngModelChange)="setSearch($event)"></label>
          <form class="create-transfer" (ngSubmit)="create()"><label for="title">New transfer title</label><div class="create-controls"><input id="title" name="title" [(ngModel)]="title" placeholder="e.g. Building permits · 2024" required maxlength="200"><button [disabled]="busy() || !title.trim()"><span class="create-icon" aria-hidden="true">+</span> Create</button></div></form>
        </div>
        <div class="overview-list-heading"><h2>All transfers</h2><span aria-live="polite">{{transfers().length}} of {{matchedTransfers()}} results</span></div>
        <div #transferList class="transfer-list" (scroll)="loadMoreIfNeeded()"><table class="transfer-table" aria-label="Transfer results"><colgroup><col><col><col><col><col></colgroup><thead><tr><th scope="col" [attr.aria-sort]="sortKey()==='title'?sortDirection():null"><button type="button" (click)="sortBy('title')">Title <span aria-hidden="true">{{sortIcon('title')}}</span></button></th><th scope="col" [attr.aria-sort]="sortKey()==='state'?sortDirection():null"><button type="button" (click)="sortBy('state')">Status <span aria-hidden="true">{{sortIcon('state')}}</span></button></th><th scope="col">Created</th><th scope="col" [attr.aria-sort]="sortKey()==='sips'?sortDirection():null"><button type="button" (click)="sortBy('sips')">SIPs <span aria-hidden="true">{{sortIcon('sips')}}</span></button></th><th scope="col" [attr.aria-sort]="sortKey()==='revision'?sortDirection():null"><button type="button" (click)="sortBy('revision')"><abbr title="Revision">Rev.</abbr> <span aria-hidden="true">{{sortIcon('revision')}}</span></button></th></tr></thead><tbody>
          @for(t of transfers();track t.id){<tr (click)="openTransfer(t)"><td><button class="transfer" [title]="t.title">{{t.title}}</button></td><td><span class="badge" [title]="t.state">{{t.state}}</span></td><td>@if(createdAt(t);as created){<time [attr.datetime]="created" [title]="created | date:'medium'">{{created | date:'shortDate'}}</time>}@else{<span>Unavailable</span>}</td><td>{{t.sips.length}}</td><td>{{t.revision}}</td></tr>}@empty{ @if(!pageLoading() && !pageError()){<tr><td colspan="5"><p class="empty">@if(totalTransfers()){No transfers match your search.}@else{Create a transfer to begin. Original SIPs remain preserved as evidence.}</p></td></tr>} }
        </tbody></table>
        @if(pageError()){<p role="alert" class="page-error">{{pageError()}}</p>}
        @if(pageLoading()){<p class="page-status" role="status">Loading transfers…</p>}
        @if(nextOffset()!==null && !pageLoading()){<button class="quiet load-more" (click)="loadPage()">Load more transfers</button>}
        </div>
      </section>
    }@else{
    <div class="workspace-topbar"><button class="quiet" (click)="backToTransfers()" [disabled]="busy() || handlerWorking || arrangementWorking"><span aria-hidden="true">←</span> Back to transfers</button></div>
    <div class="content-grid" [class.handler-focus]="tab==='handler'"><section class="card detail">
    @if(current();as t){
      <div class="card-heading">@if(tab==='handler'){<button class="quiet handler-back" (click)="setTab('overview')" [disabled]="handlerWorking || arrangementWorking" title="Back to Packages"><span aria-hidden="true">←</span> Back</button>}<div><div class="eyebrow">TRANSFER DETAIL</div><h2>{{t.title}}</h2><small>Created: @if(createdAt(t);as created){<time [attr.datetime]="created">{{created | date:'medium'}}</time>}@else{Unavailable}</small></div><span class="badge">{{t.state}}</span></div>
      <div class="tabs"><button [disabled]="handlerWorking || arrangementWorking" [class.active]="tab==='overview'" (click)="setTab('overview')">Packages</button><button [disabled]="handlerWorking || arrangementWorking" [class.active]="tab==='handler'" (click)="handlerSipId='';setTab('handler')">Ingest Packager</button><button [disabled]="handlerWorking || arrangementWorking" [class.active]="tab==='mapping'" (click)="setTab('mapping')">Create AIP candidate</button><button [disabled]="handlerWorking || arrangementWorking" [class.active]="tab==='profile'" (click)="setTab('profile')">AIP profile</button><button [disabled]="handlerWorking || arrangementWorking" [class.active]="tab==='evidence'" (click)="setTab('evidence')">Evidence</button><button [disabled]="handlerWorking || arrangementWorking" [class.active]="tab==='outputs'" (click)="setTab('outputs')">AIP candidates</button></div>
      @if(tab==='overview'){
        <form class="upload" (ngSubmit)="upload()"><h3>Add an eCH-0160 SIP</h3><p>Validate against the selected trusted schema. No automatic profile conversion.</p><div class="inline"><label>Profile<select name="profile" [(ngModel)]="profile"><option>1.0</option><option>1.1</option><option>1.2.0</option><option>1.3.0</option></select></label><label class="file-label">SIP ZIP<input type="file" accept=".zip" (change)="chooseFile($event)"></label></div><button [disabled]="busy() || !file || !['RECEIVED','INSPECTED'].includes(t.state)">Inspect & add SIP</button><small>64 MiB compressed limit in this local reference implementation</small></form>
        @for(s of t.sips;track s.id){<article class="sip"><div class="card-heading"><h3>eCH-0160 {{s.profile}}</h3><span class="badge valid">Internal checks passed</span></div><dl class="sip-source"><div><dt>Original ZIP filename</dt><dd>{{s.original_filename || 'Not recorded'}}</dd></div>@if(sipRoot(s);as root){<div><dt>SIP folder in ZIP</dt><dd>{{root}}</dd></div>}</dl><p class="mono">{{s.sha256}}</p><p>{{fileCount(s)}} payload files</p><div class="actions"><button class="secondary" (click)="handlerSipId=s.id;setTab('handler')">Open Ingest Packager</button></div>@for(u of s.units;track u.id){<details><summary>{{u.title}} <span>{{u.files.length}} files</span></summary><pre>{{u.metadata_xml}}</pre></details>}</article>}
      }
      @if(tab==='handler'){<archive-package-handler [transferId]="t.id" [revision]="t.revision" [handoffBusy]="busy()" [sips]="t.sips" [events]="t.events" [openSipId]="handlerSipId" (arrange)="setTab('mapping')" (arrangePrepared)="arrangePrepared($event)" (closed)="setTab('overview')" (about)="aboutDialog.showModal()" (changed)="refresh()" (pendingChanges)="handlerPending=$event" (working)="handlerWorking=$event"/>}
      @if(tab==='mapping'){
        <archive-prepared-arrangement [transfer]="t" [initialSelection]="preparedSelection" (selectionConsumed)="preparedSelection=null" (changed)="refresh()" (working)="arrangementWorking=$event" (openSip)="handlerSipId=$event;setTab('handler')"/>
      }
      @if(tab==='profile'){
        <h3>Preservation context</h3><p>Describe the archive and intended users. Missing rights and format knowledge stays unassessed. Changes invalidate candidate authorization; independent review is required only when configured.</p>
        <label>Archive identifier<input [(ngModel)]="contextDraft.archive_identifier"></label><label>Archive name<input [(ngModel)]="contextDraft.archive_name"></label>
        <label>Designated community<textarea [(ngModel)]="contextDraft.designated_community" rows="2"></textarea></label><label>Knowledge base<textarea [(ngModel)]="contextDraft.knowledge_base" rows="2"></textarea></label>
        <p>{{contextDraft.rights.length}} rights assessments · {{contextDraft.formats.length}} format assessments</p>
        <label>Import preservation-context JSON<input type="file" accept=".json,application/json" (change)="importContext($event)"></label><p class="help">Use preservation-context.schema.json. Upload documentation first, then reference its resource IDs in assessments. Save applies server validation.</p>
        <button (click)="saveContext()" [disabled]="busy()">Save preservation context</button>
        <h3>Supporting documentation</h3><label>Resource title<input [(ngModel)]="resourceTitle"></label><label>Documentation file (8 MiB maximum)<input type="file" (change)="chooseResource($event)"></label><button class="secondary" (click)="uploadResource()" [disabled]="busy() || !resourceFile || !resourceTitle.trim()">Preserve documentation</button>
        @for(r of t.representation_resources || [];track r.id){<article class="sip"><b>{{r.title}}</b><p class="mono">Resource ID: {{r.id}}</p><small class="mono">SHA-256: {{r.sha256}}</small></article>}
        <button class="secondary" (click)="checkGate()" [disabled]="busy()">Check institutional AIP gate</button>@if(profileReport()){<pre>{{profileReport() | json}}</pre>}
      }
      @if(tab==='evidence'){
        <h3>Preservation event history</h3><p>Persisted event chain. Successes and inspection failures are retained.</p>
        @for(e of t.events;track e.id){<article class="event"><div class="event-mark" [class.failed]="e.outcome!=='success'"></div><div><div class="card-heading"><b>{{e.type}}</b><span>{{e.outcome}}</span></div><small>{{e.time | date:'medium'}} · {{e.actor}}</small><details><summary>Event details & fixity</summary><pre>{{e.detail | json}}</pre><p class="mono">{{e.hash}}</p></details></div></article>}
      }
      @if(tab==='outputs'){
        <h3>Archival package candidates</h3><p>Each ZIP includes source evidence references, selected payload, lineage, METS, PREMIS and a SHA-256 manifest.</p>
        @for(o of t.outputs;track o.id){<article class="sip"><div class="card-heading"><h3>{{o.label}}</h3><span class="badge">{{o.status}}</span></div><p>Version {{o.version || 1}} · {{o.files}} payload files · {{(o.size/1048576).toFixed(2)}} MiB</p><p class="mono">{{o.sha256}}</p><p>Structure: {{o.conformance?.structural_valid ? 'valid' : 'unverified'}} · Preservation context: {{o.conformance?.preservation_ready ? 'complete' : 'incomplete'}}</p><button class="secondary" (click)="verify(o)" [disabled]="busy()">Verify package</button><button class="secondary" (click)="download(o)" [disabled]="busy()">Download candidate</button></article>}@empty{<div class="empty">Open Create AIP candidate to combine this transfer's SIPs into one candidate.</div>}
      }
    }
    </section></div>
    }<footer>Originals retained. Decisions traceable. Archival acceptance explicit.</footer>
  </main>
</div>
<dialog #aboutDialog class="about-dialog" aria-labelledby="about-title">
  <h2 id="about-title">About Ingest Workbench</h2>
  <p>Version {{version}}</p>
  <p>A private, non-commercial educational project exploring ingest tools and agentic programming. This is not a production archival acceptance system.</p>
  <p>Copyright © 2026 baddonkey. MIT License; provided without warranty. Third-party components have their own licenses.</p>
  <button type="button" (click)="aboutDialog.close()">Close</button>
</dialog>`})
export class AppComponent {
  readonly version=version;
  private kc=inject(KEYCLOAK);
  private api=inject(ArchiveApi);
  private packageHandler=viewChild(PackageHandler);
  private transferList=viewChild<ElementRef<HTMLDivElement>>('transferList');
  private pageGeneration=0;
  handlerSipId='';handlerPending=0;handlerWorking=false;arrangementWorking=false;
  preparedSelection:PreparedSelection|null=null;
  async arrangePrepared(selection:PreparedSelection){
    const transfer=this.current();
    if(!transfer || this.busy() || !this.canLeaveHandler())return;
    await this.run(async()=>{
      const current=await this.request<Transfer>('/api/transfers/'+transfer.id);
      this.update(current);this.preparedSelection=selection;this.setTab('mapping');
    });
  }
  transfers=signal<Transfer[]>([]);current=signal<Transfer|null>(null);busy=signal(false);error=signal('');
  workspaceOpen=signal(false);search=signal('');
  totalTransfers=signal(0);matchedTransfers=signal(0);nextOffset=signal<number|null>(0);
  pageLoading=signal(false);pageError=signal('');
  sortKey=signal<TransferSort|null>(null);sortDirection=signal<'ascending'|'descending'>('ascending');
  sortIcon(key:TransferSort){return this.sortKey()===key?(this.sortDirection()==='ascending'?'↑':'↓'):'↕';}
  setSearch(value:string){this.search.set(value);void this.reloadTransfers();}
  sortBy(key:TransferSort){this.sortDirection.set(this.sortKey()===key && this.sortDirection()==='ascending'?'descending':'ascending');this.sortKey.set(key);void this.reloadTransfers();}
  async reloadTransfers(){this.pageGeneration++;this.transfers.set([]);this.nextOffset.set(0);this.pageLoading.set(false);this.pageError.set('');const list=this.transferList()?.nativeElement;if(list)list.scrollTop=0;await this.loadPage();}
  async loadPage(){
    const offset=this.nextOffset();if(offset===null || this.pageLoading())return;
    const generation=this.pageGeneration;this.pageLoading.set(true);this.pageError.set('');
    const params=new URLSearchParams({q:this.search().trim(),sort:this.sortKey() || 'created',direction:this.sortKey()?this.sortDirection():'descending',offset:String(offset),limit:'30'});
    try{
      const page=await this.request<TransferPage>('/api/transfer-pages?'+params);
      if(generation!==this.pageGeneration)return;
      this.transfers.update(existing=>{const seen=new Set(existing.map(t=>t.id));return [...existing,...page.items.filter(t=>!seen.has(t.id))];});
      this.totalTransfers.set(page.total);this.matchedTransfers.set(page.matched);this.nextOffset.set(page.next_offset);
    }catch(e){if(generation===this.pageGeneration)this.pageError.set(e instanceof Error?e.message:'Could not load transfers');}
    finally{if(generation===this.pageGeneration){this.pageLoading.set(false);if(!this.pageError())setTimeout(()=>this.loadMoreIfNeeded(),0);}}
  }
  loadMoreIfNeeded(){const list=this.transferList()?.nativeElement;if(list && !this.pageError() && list.scrollTop+list.clientHeight>=list.scrollHeight-120)void this.loadPage();}
  title='';profile='1.3.0';file:File|null=null;reason='';assignments:Record<string,string>={};tab='overview';
  contextDraft={archive_identifier:'',archive_name:'',designated_community:'',knowledge_base:'',rights:[] as unknown[],formats:[] as unknown[]};
  resourceTitle='';resourceFile:File|null=null;profileReport=signal<unknown>(null);
  hasDrafts(t:Transfer){return Object.keys(t.active_metadata_drafts || {}).length>0 || Object.keys((t as Transfer & {working_metadata?:Record<string,unknown>}).working_metadata || {}).length>0;}
  createdAt(t:Transfer){const value=t.events.find(e=>e.type==='creation')?.time;return value && !Number.isNaN(Date.parse(value))?value:null;}
  sipRoot(s:Sip){const suffix='/header/metadata.xml',path=s.metadata_path;return path?.endsWith(suffix)?path.slice(0,-suffix.length):null;}
  sidebarOpen=signal(false);
  get userName(){return this.kc.tokenParsed?.['preferred_username'] || 'Archivist';}
  constructor(){void this.refresh();}
  canLeaveHandler(){
    if(this.arrangementWorking){this.error.set('Wait for the preparation review decision to finish before leaving.');return false;}
    if(this.handlerWorking){this.error.set('Wait for the metadata decision to finish before leaving.');return false;}
    if(this.handlerPending && !window.confirm('Discard unsaved metadata proposals before leaving? Saved draft history remains retained.'))return false;
    this.packageHandler()?.discardPending(false);
    this.handlerPending=0;return true;
  }
  setTab(next:string){if(next!==this.tab && (this.tab==='handler'||this.arrangementWorking) && !this.canLeaveHandler())return false;this.tab=next;return true;}
  backToTransfers(){if(this.workspaceOpen() && (this.tab==='handler'||this.arrangementWorking) && !this.canLeaveHandler())return;const wasOpen=this.workspaceOpen();this.current.set(null);this.tab='overview';this.workspaceOpen.set(false);this.sidebarOpen.set(false);this.file=null;this.error.set('');if(wasOpen)void this.reloadTransfers();}
  openTransfer(t:Transfer){this.preparedSelection=null;this.select(t);this.tab='overview';this.workspaceOpen.set(true);this.sidebarOpen.set(false);}
  logout(){if(this.canLeaveHandler())void this.kc.logout({redirectUri:window.location.origin+'/'});}
  async request<T>(path:string,options:RequestInit={}):Promise<T>{
    return this.api.request<T>(path,options);
  }
  async run(action:()=>Promise<void>){this.busy.set(true);this.error.set('');try{await action();}catch(e){this.error.set(e instanceof Error?e.message:'Request failed');const current=this.current();if(current){try{this.update(await this.request<Transfer>('/api/transfers/'+current.id));}catch{/* Keep the original error visible. */}}}finally{this.busy.set(false);}}
  async refresh(){await this.run(async()=>{const current=this.current();if(current)this.current.set(await this.request<Transfer>('/api/transfers/'+current.id));await this.reloadTransfers();});}
  select(t:Transfer){if(this.arrangementWorking&&!this.canLeaveHandler())return;if(this.tab==='handler' && this.current()?.id!==t.id && !this.setTab('overview'))return;this.handlerSipId='';this.contextDraft=structuredClone(t.aip_context || {archive_identifier:'',archive_name:'',designated_community:'',knowledge_base:'',rights:[],formats:[]});this.profileReport.set(null);this.current.set(t);this.assignments={};this.reason='';
    for(const s of t.sips)for(const u of s.units)this.assignments[s.id+':'+u.id]=u.title;
    const plan=t.plan as {targets?:{label:string;units:{sip_id:string;unit_id:string}[]}[];reason?:string}|null;
    if(plan){this.reason=plan.reason||'';for(const target of plan.targets||[])for(const u of target.units)this.assignments[u.sip_id+':'+u.unit_id]=target.label;}}
  update(t:Transfer){this.current.set(t);this.transfers.update(all=>all.map(item=>item.id===t.id?t:item));}
  async create(){await this.run(async()=>{const t=await this.request<Transfer>('/api/transfers',{method:'POST',body:JSON.stringify({title:this.title})});this.totalTransfers.update(total=>total+1);this.openTransfer(t);this.search.set('');this.title='';});}
  chooseFile(event:Event){this.file=(event.target as HTMLInputElement).files?.[0]||null;}
  fileCount(s:Sip){return Object.keys(s.files).length;}
  async upload(){const t=this.current(),file=this.file;if(!t||!file)return;await this.run(async()=>{const form=new FormData();form.set('file',file);form.set('profile',this.profile);form.set('revision',String(t.revision));const result=await this.request<Transfer>('/api/transfers/'+t.id+'/sips',{method:'POST',body:form});this.update(result);this.select(result);});}
  async savePlan(){const t=this.current();if(!t)return;await this.run(async()=>{const grouped=new Map<string,{sip_id:string;unit_id:string}[]>();for(const s of t.sips)for(const u of s.units){const label=(this.assignments[s.id+':'+u.id]||'').trim();if(!label)throw new Error('Assign every dossier to a target AIP');const items=grouped.get(label)||[];items.push({sip_id:s.id,unit_id:u.id});grouped.set(label,items);}const targets=[...grouped].map(([label,units])=>({label,units}));this.update(await this.request<Transfer>('/api/transfers/'+t.id+'/plan',{method:'POST',body:JSON.stringify({revision:t.revision,targets,reason:this.reason})}));});}
  async act(action:string){const t=this.current();if(!t)return;await this.run(async()=>{this.update(await this.request<Transfer>('/api/transfers/'+t.id+'/'+action,{method:'POST',body:JSON.stringify({revision:t.revision})}));if(action==='build')this.tab='outputs';});}
  async importContext(event:Event){const file=(event.target as HTMLInputElement).files?.[0];if(!file)return;await this.run(async()=>{if(file.size>1024*1024)throw new Error('Context import limit: 1 MiB');const value=JSON.parse(await file.text());if(!value || typeof value!=='object' || !Array.isArray(value.rights) || !Array.isArray(value.formats))throw new Error('Expected context object with rights and formats arrays');this.contextDraft={...this.contextDraft,...value};});}
  async saveContext(){const t=this.current();if(!t)return;await this.run(async()=>{this.update(await this.request<Transfer>('/api/transfers/'+t.id+'/aip-context',{method:'POST',body:JSON.stringify({revision:t.revision,context:this.contextDraft})}));});}
  chooseResource(event:Event){this.resourceFile=(event.target as HTMLInputElement).files?.[0] || null;}
  async uploadResource(){const t=this.current(),file=this.resourceFile;if(!t||!file)return;await this.run(async()=>{const data=new FormData();data.set('revision',String(t.revision));data.set('title',this.resourceTitle);data.set('file',file);this.update(await this.request<Transfer>('/api/transfers/'+t.id+'/representation-resources',{method:'POST',body:data}));this.resourceTitle='';this.resourceFile=null;});}
  async checkGate(){const t=this.current();if(!t)return;await this.run(async()=>{this.profileReport.set(await this.request('/api/transfers/'+t.id+'/aip-gate'));});}
  async verify(o:Output){const t=this.current();if(!t)return;await this.run(async()=>{this.profileReport.set(await this.request('/api/transfers/'+t.id+'/outputs/'+o.id+'/conformance'));this.tab='profile';});}
  async download(o:Output){const t=this.current();if(t)await this.downloadSidecar('/api/transfers/'+t.id+'/outputs/'+o.id,'AIP-candidate-'+o.id+'.zip');}
  async downloadSidecar(path:string,name:string){await this.run(async()=>{await this.api.download(path,name);});}
}
