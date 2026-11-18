import {Component,OnChanges,OnDestroy,SimpleChanges,computed,inject,input,output,signal} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {ArchiveApi} from './archive-api';
import {KEYCLOAK} from './identity';
import {KostEntry,KostReport,kostOutcome} from './kost-report.types';
import {KostFileFindings} from './kost-file-findings';
import {PreparedInput} from './metadata-editor.types';

interface KostRun {
  id:string;state:string;result:'valid'|'invalid'|'error'|null;remote_status?:string;
  created:number;started:number|null;finished:number|null;actor:string;trigger:string;
  input_sha256:string;message:string|null;last_error:string|null;
  errors:{module:string;message:string}[];report_sha256:string|null;report_status:string;
}
interface KostTarget {target_id:string;prepared_id:string|null;input_sha256:string;label:string;latest:KostRun|null}
interface KostInventory {enabled:boolean;advisory:boolean;targets:KostTarget[]}
interface KostHistory {runs:KostRun[];next:string|null}

@Component({selector:'archive-kost-validation',standalone:true,imports:[CommonModule,FormsModule,KostFileFindings],
  templateUrl:'./kost-validation.html',styleUrl:'./kost-validation.css'})
export class KostValidation implements OnChanges,OnDestroy {
  transferId=input.required<string>();sipId=input.required<string>();revision=input.required<number>();
  sourceSha256=input('');
  targetLabels=input<Record<string,string>>({});
  preparedInputs=input<PreparedInput[]>([]);
  statuses=output<Record<string,string>>();
  serviceEnabled=output<boolean>();
  reportChanged=output<KostReport|null>();
  fileSelected=output<{path:string;input_sha256:string}>();
  private readonly api=inject(ArchiveApi);
  private readonly identity=inject(KEYCLOAK);
  readonly inventory=signal<KostInventory|null>(null);
  readonly chosen=signal('received');
  readonly selected=computed(()=>this.inventory()?.targets.find(target=>target.target_id===this.chosen())||null);
  readonly selectedSnapshot=computed(()=>this.preparedInputs().find(record=>record.id===this.selected()?.prepared_id)||null);
  readonly expanded=signal(false);readonly historyOpen=signal(false);
  readonly runs=signal<KostRun[]>([]);readonly next=signal<string|null>(null);
  readonly error=signal('');readonly working=signal(false);readonly historyBusy=signal(false);
  readonly downloading=signal(false);
  readonly report=signal<KostReport|null>(null);readonly reportBusy=signal(false);readonly reportError=signal('');
  readonly viewingRun=signal<KostRun|null>(null);readonly focusedPath=signal('');
  readonly outcomeFilter=signal('all');readonly severityFilter=signal('all');readonly search=signal('');
  readonly entryLimit=signal(100);readonly outcome=kostOutcome;
  readonly filteredEntries=computed(()=>{
    const query=this.search().trim().toLocaleLowerCase();
    return (this.report()?.entries||[]).filter(entry=>
      (this.outcomeFilter()==='all' || entry.outcome===this.outcomeFilter()) &&
      (this.severityFilter()==='all' || entry.findings.some(finding=>finding.severity===this.severityFilter())) &&
      (!query || [entry.path||entry.reported_path,entry.check,...entry.findings.map(finding=>finding.module+' '+finding.message)]
        .some(value=>value.toLocaleLowerCase().includes(query))));
  });
  private reportSequence=0;private reportKey='';
  private generation=0;private fetchSequence=0;private historySequence=0;
  private timer:ReturnType<typeof setTimeout>|undefined;
  private controller:AbortController|null=null;
  private attempt:{target:string;body:object}|null=null;
  canRun(){return this.identity.hasRealmRole('archive-operator');}
  targetLabel(target:KostTarget|null){return target?(this.targetLabels()[target.target_id]||target.label):'';}
  base(){return '/api/transfers/'+this.transferId()+'/sips/'+this.sipId()+'/kost-validations';}
  active(run:KostRun|null|undefined){return !!run && run.state!=='COMPLETE' && run.state!=='SUBMISSION_UNKNOWN';}
  status(run:KostRun|null|undefined):string {
    if(!run)return 'Not run';
    if(run.state==='SUBMISSION_UNKNOWN')return 'Submission uncertain';
    if(run.result)return ({valid:'Valid',invalid:'Invalid',error:'Service error'})[run.result];
    if(run.state==='SUBMITTING')return 'Submitting';
    if(run.remote_status==='running')return 'Running';
    return 'Queued';
  }
  ngOnChanges(changes:SimpleChanges){
    if(changes['transferId'] || changes['sipId']){
      this.generation++;this.controller?.abort();clearTimeout(this.timer);
      this.inventory.set(null);this.chosen.set('received');this.runs.set([]);this.next.set(null);
      this.attempt=null;this.error.set('');this.working.set(false);this.historyBusy.set(false);this.downloading.set(false);
      this.serviceEnabled.emit(false);
      this.resetReport();
    }
    if(this.sipId())void this.refresh();
  }
  ngOnDestroy(){this.generation++;this.controller?.abort();clearTimeout(this.timer);}
  async refresh(){
    clearTimeout(this.timer);
    const generation=this.generation,sequence=++this.fetchSequence;
    this.controller?.abort();const controller=new AbortController();this.controller=controller;
    try{
      const inventory=await this.api.request<KostInventory>(this.base(),{signal:controller.signal});
      if(generation!==this.generation || sequence!==this.fetchSequence)return;
      this.inventory.set(inventory);this.error.set('');
      this.serviceEnabled.emit(inventory.enabled);
      this.statuses.emit(Object.fromEntries(inventory.targets.map(target=>[target.target_id,this.status(target.latest)])));
      const latest=this.selected()?.latest;
      if(latest)this.runs.update(runs=>runs.map(run=>run.id===latest.id?latest:run));
      const reportRun=this.viewingRun()||latest;
      if(reportRun?.report_sha256)void this.loadReport(reportRun);
      else if(this.reportKey)this.resetReport();
    }catch(error){
      if(generation===this.generation && sequence===this.fetchSequence && !controller.signal.aborted)
        this.error.set(error instanceof Error?error.message:'Validation status unavailable');
    }finally{
      if(generation===this.generation && sequence===this.fetchSequence && !controller.signal.aborted &&
        (this.error() || this.inventory()?.targets.some(target=>this.active(target.latest))))
        this.timer=setTimeout(()=>void this.refresh(),5000);
    }
  }
  choose(id:string){this.chosen.set(id);this.attempt=null;this.runs.set([]);this.next.set(null);this.resetReport();const run=this.selected()?.latest;if(run?.report_sha256)void this.loadReport(run);if(this.historyOpen())void this.loadHistory();}
  show(id:string){this.expanded.set(true);this.choose(id);void this.refresh();}
  async run(){
    const target=this.selected();if(!target || !this.canRun() || !this.inventory()?.enabled || this.active(target.latest) || this.working())return;
    const uncertain=target.latest?.state==='SUBMISSION_UNKNOWN';
    if(uncertain && !window.confirm('The previous submission may still be running in KOST. Submit another job and retain the uncertain attempt?'))return;
    if(this.attempt?.target!==target.target_id)this.attempt={target:target.target_id,body:{
      request_id:crypto.randomUUID(),prepared_id:target.prepared_id,input_sha256:target.input_sha256,acknowledge_uncertain:uncertain}};
    const generation=this.generation;
    this.working.set(true);this.error.set('');
    try{
      await this.api.request<KostRun>(this.base(),{method:'POST',body:JSON.stringify(this.attempt.body)});
      if(generation!==this.generation)return;
      this.attempt=null;await this.refresh();if(this.historyOpen())await this.loadHistory();
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Unable to queue validation');}
    finally{if(generation===this.generation)this.working.set(false);}
  }
  async loadHistory(append=false){
    const target=this.selected();if(!target)return;
    const generation=this.generation,sequence=++this.historySequence;
    const query=new URLSearchParams();if(target.prepared_id)query.set('prepared_id',target.prepared_id);
    if(append && this.next())query.set('before',this.next()!);
    this.historyBusy.set(true);
    try{
      const history=await this.api.request<KostHistory>(this.base()+'/runs?'+query);
      if(generation!==this.generation || sequence!==this.historySequence || this.chosen()!==target.target_id)return;
      this.runs.set(append?[...this.runs(),...history.runs]:history.runs);this.next.set(history.next);
    }catch(error){if(generation===this.generation && this.chosen()===target.target_id)this.error.set(error instanceof Error?error.message:'Run history unavailable');}
    finally{if(generation===this.generation && sequence===this.historySequence)this.historyBusy.set(false);}
  }
  async download(run:KostRun){
    const generation=this.generation;this.downloading.set(true);
    try{await this.api.download(this.base()+'/runs/'+run.id+'/report','kost-'+run.id+'.xml');}
    catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Report download failed');}
    finally{if(generation===this.generation)this.downloading.set(false);}
  }
  private resetReport(){
    this.reportSequence++;this.reportKey='';this.report.set(null);this.reportChanged.emit(null);
    this.viewingRun.set(null);this.focusedPath.set('');this.reportError.set('');this.reportBusy.set(false);
    this.outcomeFilter.set('all');this.severityFilter.set('all');this.search.set('');this.entryLimit.set(100);
  }
  async loadReport(run:KostRun,force=false){
    const key=run.id+':'+run.report_sha256;
    if(!force && this.reportKey===key)return;
    this.reportKey=key;const generation=this.generation,sequence=++this.reportSequence;
    this.reportBusy.set(true);this.reportError.set('');
    try{
      const report=await this.api.request<KostReport>(this.base()+'/runs/'+run.id+'/findings');
      if(generation!==this.generation || sequence!==this.reportSequence)return;
      if(report.run_id!==run.id || report.input_sha256!==run.input_sha256 || report.report_sha256!==run.report_sha256)
        throw new Error('Validation report binding does not match the selected run');
      this.report.set(report);this.reportChanged.emit(report);
    }catch(error){if(generation===this.generation && sequence===this.reportSequence){this.reportKey='';this.reportError.set(error instanceof Error?error.message:'Structured findings unavailable');}}
    finally{if(generation===this.generation && sequence===this.reportSequence)this.reportBusy.set(false);}
  }
  inspectRun(run:KostRun){this.expanded.set(true);this.viewingRun.set(run);this.focusedPath.set('');void this.loadReport(run,true);}
  openFile(entry:KostEntry){
    if(!entry.path)return;
    this.focusedPath.set(entry.path);
    const report=this.report();
    if(report && !report.prepared_id && report.input_sha256===this.sourceSha256())
      this.fileSelected.emit({path:entry.path,input_sha256:report.input_sha256});
  }
}