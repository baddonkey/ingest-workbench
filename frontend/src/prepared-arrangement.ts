import {Component,OnChanges,OnDestroy,SimpleChanges,inject,input,output,signal} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {ArchiveApi} from './archive-api';
import {KEYCLOAK} from './identity';
import {EditorLanguage,editorText} from './metadata-editor.locale';
import {PreparationNote,PreparedSelection} from './metadata-editor.types';
import {arrangementText} from './prepared-arrangement.locale';

interface Unit {id:string;title:string;files:string[];metadata_xml:string}
interface Sip {id:string;profile:string;sha256:string;units:Unit[]}
interface Review {id:string;actor:string;created:string;reason:string;plan_sha256:string;evidence_sha256:string}
interface PreparedPlan {format:number;sha256:string;reason:string;targets:{label:string;units:{sip_id:string;unit_id:string}[]}[];inputs:{sip_id:string;input_sha256:string}[]}
interface Transfer {id:string;revision:number;state:string;plan:unknown;sips:Sip[]}
interface Binding {sip_id:string;prepared_id:string|null;input_sha256:string;receipt_checks:string[]}
interface Workspace {input_notes:{sip_id:string;prepared_id:string|null;notes:(PreparationNote & {object_title:string})[]}[];revision:number;state:string;plan:PreparedPlan|null;approval:{review_id?:string}|null;outputs:{id:string;label:string;version:number;sha256:string;profile:string}[];sips:Sip[];selected_inputs:{sip_id:string;prepared_id:string|null}[];bindings:Binding[];current_findings:Binding[];plan_current:boolean;stale_reason:string|null;prepared_review_id:string|null;reviews:Review[];choices:{sip_id:string;source_allowed:boolean;source_sha256:string;frozen:{id:string;operation_id:string;sha256:string;created:string;current:boolean}[]}[]}

@Component({selector:'archive-prepared-arrangement',standalone:true,imports:[CommonModule,FormsModule],templateUrl:'./prepared-arrangement.html',styleUrl:'./prepared-arrangement.css'})
export class PreparedArrangement implements OnChanges,OnDestroy {
  transfer=input.required<Transfer>();changed=output<void>();working=output<boolean>();openSip=output<string>();
  initialSelection=input<PreparedSelection|null>(null);selectionConsumed=output<void>();
  private api=inject(ArchiveApi);private identity=inject(KEYCLOAK);private generation=0;private activeId='';
  private retry:{signature:string;request:object}|null=null;
  private authorizationRetry:{signature:string;request:object}|null=null;
  private retainedTransfer:Transfer|null=null;
  document=signal<Workspace|null>(null);busy=signal(false);loading=signal(false);error=signal('');language=signal<EditorLanguage>('en');
  selected:Record<string,string>={};assignments:Record<string,string>={};reason='';reviewReason='';authorizationReason='';private touched=false;mappingDirty=signal(false);
  text(key:string){return arrangementText(key,this.language())||editorText(key,this.language());}
  canEdit(){return this.identity.hasRealmRole('archive-operator');}
  canReview(){return this.identity.hasRealmRole('archive-approver');}
  activeTransfer(){return this.retainedTransfer||this.transfer();}
  plan(){const value=this.activeTransfer().plan as PreparedPlan|null;return value?.format===2?value:null;}
  base(){return '/api/transfers/'+this.transfer().id;}
  ngOnChanges(changes:SimpleChanges){const t=this.transfer();if(this.activeId!==t.id){this.activeId=t.id;this.retainedTransfer=null;this.reviewReason='';void this.load();}else{if(t.revision>=(this.retainedTransfer?.revision||0))this.retainedTransfer=null;if(changes['transfer']&&!this.busy()&&t.revision!==this.document()?.revision)void this.load();}}
  ngOnDestroy(){this.generation++;}
  async load(){
    const generation=++this.generation;this.loading.set(true);this.error.set('');
    try{
      const doc=await this.api.request<Workspace>(this.base()+'/arrangement');if(generation!==this.generation)return;
      this.retainedTransfer={...this.activeTransfer(),revision:doc.revision,state:doc.state,plan:doc.plan};
      this.document.set(doc);this.selected=Object.fromEntries(doc.selected_inputs.map(s=>[s.sip_id,s.prepared_id||'']));
      const plan=this.activeTransfer().plan as PreparedPlan|null;this.reason=plan?.reason||'';this.assignments={};this.touched=!!plan;this.mappingDirty.set(false);
      for(const s of doc.sips)for(const u of s.units)this.assignments[s.id+':'+u.id]=u.title;
      for(const target of plan?.targets||[])for(const u of target.units)this.assignments[u.sip_id+':'+u.unit_id]=target.label;
      this.retry=null;
      this.authorizationRetry=null;
      const selection=this.initialSelection();
      if(selection){
        this.selectionConsumed.emit();
        const choice=doc.choices.find(c=>c.sip_id===selection.sip_id);
        if(!choice?.frozen.some(f=>f.id===selection.prepared_id && f.current))
          throw new Error('The selected snapshot is no longer current. Reopen Validation and freeze the latest working state.');
        this.loading.set(false);
        await this.choose(selection.sip_id,selection.prepared_id);
      }
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Arrangement could not be loaded');}
    finally{if(generation===this.generation)this.loading.set(false);}
  }
  async reload(){await this.load();this.changed.emit();}
  selections(){return (this.document()?.choices||[]).map(c=>({sip_id:c.sip_id,prepared_id:this.selected[c.sip_id]||null}));}
  validSelection(){return !!this.document()?.choices.length && this.document()!.choices.every(c=>this.selected[c.sip_id]?c.frozen.some(f=>f.id===this.selected[c.sip_id]&&f.current):c.source_allowed);}
  async choose(sipId:string,value:string){
    if(this.busy()||this.loading()||!this.canEdit())return;
    this.selected[sipId]=value;this.mappingDirty.set(true);if(!this.validSelection())return;
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{
      const result=await this.api.request<{revision:number;sips:Sip[];bindings:Binding[]}>(this.base()+'/arrangement/preview',{method:'POST',body:JSON.stringify({revision:this.document()!.revision,selected_inputs:this.selections()})});
      if(generation!==this.generation)return;
      this.document.update(doc=>doc?{...doc,...result}:doc);
      if(!this.touched)for(const s of result.sips)for(const u of s.units)this.assignments[s.id+':'+u.id]=u.title;
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Selection preview failed');}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  markAssignment(key:string,value:string){this.assignments[key]=value;this.touched=true;this.mappingDirty.set(true);}
  async save(){
    const doc=this.document();if(!doc||this.busy()||this.loading()||!this.canEdit()||!this.validSelection()||!this.reason.trim())return;
    const grouped=new Map<string,{sip_id:string;unit_id:string}[]>();
    for(const s of doc.sips)for(const u of s.units){const label=(this.assignments[s.id+':'+u.id]||'').trim();if(!label){this.error.set(this.text('assignAll'));return;}const items=grouped.get(label)||[];items.push({sip_id:s.id,unit_id:u.id});grouped.set(label,items);}
    await this.mutate('/plan',{revision:doc.revision,targets:[...grouped].map(([label,units])=>({label,units})),reason:this.reason,selected_inputs:this.selections()});
  }
  async review(){
    const doc=this.document(),plan=this.plan();if(!doc||!plan||this.busy()||this.loading()||this.mappingDirty()||!doc.plan_current||doc.prepared_review_id||!this.reviewReason.trim()||!this.canReview())return;
    const signature=JSON.stringify({plan:plan.sha256,reason:this.reviewReason});
    if(this.retry?.signature!==signature)this.retry={signature,request:{request_id:crypto.randomUUID(),revision:doc.revision,plan_sha256:plan.sha256,reason:this.reviewReason}};
    await this.mutate('/prepared-reviews',this.retry.request);
  }
  async authorize(){
    const doc=this.document(),plan=this.plan();
    if(!doc||!plan||this.busy()||this.loading()||this.mappingDirty()||!doc.plan_current||!doc.prepared_review_id||doc.state!=='PLANNED'||!this.canReview()||!this.authorizationReason.trim())return;
    const signature=JSON.stringify({plan:plan.sha256,review:doc.prepared_review_id,reason:this.authorizationReason});
    if(this.authorizationRetry?.signature!==signature)this.authorizationRetry={signature,request:{request_id:crypto.randomUUID(),revision:doc.revision,review_id:doc.prepared_review_id,plan_sha256:plan.sha256,reason:this.authorizationReason}};
    await this.mutate('/prepared-authorizations',this.authorizationRetry.request);
  }
  async build(){const doc=this.document();if(!doc||this.busy()||this.loading()||this.mappingDirty()||!this.canEdit()||!doc.plan_current||doc.state!=='APPROVED'||!doc.approval?.review_id)return;await this.mutate('/build',{revision:doc.revision});}
  async downloadCandidate(id:string){try{await this.api.download(this.base()+'/outputs/'+id,'AIP-candidate-'+id+'.zip');}catch(error){this.error.set(error instanceof Error?error.message:'Download failed');}}
  private async mutate(path:string,request:object){
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{const result=await this.api.request<Transfer|{transfer:Transfer}>(this.base()+path,{method:'POST',body:JSON.stringify(request)});if(generation!==this.generation)return;this.retainedTransfer='transfer'in result?result.transfer:result;this.changed.emit();await this.load();}
    catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Decision failed');}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  async download(review:Review){try{await this.api.download(this.base()+'/prepared-reviews/'+review.id+'/evidence','preparation-review-'+review.id+'.zip');}catch(error){this.error.set(error instanceof Error?error.message:'Download failed');}}
  async downloadInput(binding:Binding,kind:'zip'|'evidence'){
    if(!binding.prepared_id&&kind==='evidence')return;
    const path=this.base()+'/sips/'+binding.sip_id+(binding.prepared_id?'/prepared-inputs/'+binding.prepared_id+'/'+kind:'/original');
    try{await this.api.download(path,'selected-'+binding.sip_id+'-'+kind+'.zip');}catch(error){this.error.set(error instanceof Error?error.message:'Download failed');}
  }
}
