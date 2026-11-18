import {Component,ElementRef,Injector,OnChanges,OnDestroy,SimpleChanges,afterNextRender,computed,inject,input,output,signal,viewChild} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {ArchiveApi} from './archive-api';
import {KEYCLOAK} from './identity';
import {MetadataFieldEditor} from './metadata-field';
import {ContentChange,MetadataField,MetadataFieldChange,MetadataObject,MetadataRow,MetadataWorkspace,MetadataOperation,PreparedInput,PreparedSelection,PreparationNote,ReorderGroup,CreationOption,MetadataNavigator,MetadataTreeObject,MetadataSample,MetadataStatisticItem,PreparationStatistics,MetadataImportSource,MetadataImportDecision} from './metadata-editor.types';
import {EditorLanguage,editorText} from './metadata-editor.locale';
import {ContextMenu,ContextMenuState,ContextCommand} from './context-menu';
import {KostValidation} from './kost-validation';
import {KostFileFindings} from './kost-file-findings';
import {KostEntry,KostReport} from './kost-report.types';

@Component({selector:'archive-metadata-editor',standalone:true,imports:[CommonModule,FormsModule,MetadataFieldEditor,ContextMenu,KostValidation,KostFileFindings],templateUrl:'./metadata-editor.html',styleUrl:'./metadata-editor.css'})
export class MetadataEditor implements OnChanges,OnDestroy {
  transferId=input.required<string>();sipId=input.required<string>();revision=input.required<number>();
  changed=output<void>();working=output<boolean>();pending=output<number>();
  kostEnabled=input(false);kostEnabledChange=output<boolean>();
  kostStatuses=input<Record<string,string>>({});kostStatusesChange=output<Record<string,string>>();
  kostReport=input<KostReport|null>(null);kostPaths=input<Record<string,string>>({});
  kostReportChange=output<KostReport|null>();kostFileSelected=output<{path:string;input_sha256:string}>();
  sourceSha256=input('');
  inspectOriginal=output<void>();arrange=output<void>();close=output<void>();about=output<void>();sourceDownload=output<'original'|'metadata'>();
  arrangePrepared=output<PreparedSelection>();
  previewFile=output<{id:string;language:EditorLanguage}>();
  packageName=input('');originalFilename=input('');statistics=input<{payload_files:number;payload_bytes:number}|null>(null);
  contextMenu=signal<ContextMenuState|null>(null);
  kostPanel=viewChild(KostValidation);
  editorLayout=viewChild<ElementRef<HTMLElement>>('editorLayout');
  private injector=inject(Injector);
  upperPanePercent=signal(60);
  private splitterDrag:{pointerId:number;startY:number;startPercent:number;height:number}|null=null;
  editorView=signal<'overview'|'metadata'|'statistics'|'validation'>('overview');
  overviewNodes=computed(()=>this.document()?.nodes.filter(n=>n.kind==='ablieferung'||n.kind==='provenienz')||[]);
  formNodes=computed(()=>this.editorView()==='overview'?this.overviewNodes():this.selectedObject()?[this.selectedObject()!]:[]);
  editorKinds=['ordnungssystem','ordnungssystemposition','dossier','dokument','vorgang','aktivitaet','mappe','datei','ordner'];
  badge(kind:string){return ({ordnungssystem:'OS',ordnungssystemposition:'OSP',dossier:'D',subdossier:'SD',dokument:'Dk',mappe:'M',vorgang:'V',aktivitaet:'A',paket:'P',ordner:'O',datei:'F'} as Record<string,string>)[kind]||'';}
  fieldText(node:MetadataObject|undefined,tag:string){return this.describe(node?.fields.find(f=>f.tag===tag)?.values).replace(/\n/g,' · ');}
  summary(kind:string,tag:string){return this.fieldText(this.document()?.nodes.find(n=>n.kind===kind),tag);}
  chooseEditor(view:'overview'|'metadata'|'statistics'|'validation',kind?:string){
    if(kind){const node=this.document()?.nodes.find(n=>n.kind===kind);if(node)this.select(node.id);}
    this.editorView.set(view);
  }
  validationView(){
    this.chooseEditor('validation');
    this.openKostValidation(this.latestFrozen()?.id || 'received');
  }
  statisticsDialog=viewChild<ElementRef<HTMLDialogElement>>('statisticsDialog');
  statisticsSelection=signal<{title:string;items:MetadataStatisticItem[];binding:PreparationStatistics['binding'];report?:KostReport}|null>(null);
  statisticsPage=signal(0);
  statisticsPageItems=computed(()=>this.statisticsSelection()?.items.slice(this.statisticsPage()*100,(this.statisticsPage()+1)*100)||[]);
  statisticsGroups=computed(()=>Object.entries(this.document()?.statistics.objects||{}));
  statisticsOutcomes=['valid','invalid','recognized','not_assessed'] as const;
  statisticsSeverities=['error','warning','info','validator_error'] as const;
  showStatistics(title:string,items:MetadataStatisticItem[],report?:KostReport){
    const binding=this.document()?.statistics.binding;if(!binding)return;
    this.statisticsSelection.set({title,items,binding,report});this.statisticsPage.set(0);this.statisticsDialog()?.nativeElement.showModal();
  }
  private statisticReportItem(entry:KostEntry,detail:string):MetadataStatisticItem{
    const path=entry.path;
    const node=path?this.document()?.nodes.find(node=>node.kind==='datei' && this.kostPaths()[node.source_id||'']===path):undefined;
    return {node_id:node?.id||null,title:node?.title||path||entry.reported_path||this.text('paket'),detail};
  }
  showFormatStatistics(outcome:typeof this.statisticsOutcomes[number]){
    const items=(this.searchReport()?.entries||[]).filter(entry=>entry.scope==='file' && entry.outcome===outcome)
      .map(entry=>this.statisticReportItem(entry,[entry.reported_path,entry.check,entry.format_version].filter(Boolean).join(' · ')));
    this.showStatistics(this.text('stat_'+outcome),items,this.searchReport()||undefined);
  }
  showFindingStatistics(severity:typeof this.statisticsSeverities[number]){
    const items=(this.searchReport()?.entries||[]).flatMap(entry=>entry.findings.filter(finding=>finding.severity===severity)
      .map(finding=>this.statisticReportItem(entry,[entry.reported_path,finding.module,finding.message].filter(Boolean).join(' · '))));
    this.showStatistics(this.text('finding_'+severity),items,this.searchReport()||undefined);
  }
  openStatistic(item:MetadataStatisticItem){
    if(!item.node_id)return;
    const node=this.document()?.nodes.find(node=>node.id===item.node_id);if(!node)return;
    this.statisticsDialog()?.nativeElement.close();this.select(node.id);
    const field=node.fields.find(field=>field.id===item.field_id);
    if(field){
      this.secondary.set(true);
      afterNextRender(()=>this.editorLayout()?.nativeElement.querySelector<HTMLElement>('[data-node-id="'+CSS.escape(node.id)+'"] [data-field-tag="'+CSS.escape(field.tag)+'"]')?.scrollIntoView({block:'nearest'}),{injector:this.injector});
    }
  }
  openKostValidation(target?:string){this.setTool('kost');this.kostPanel()?.show(target ?? this.kostPanel()?.chosen() ?? 'received');}
  startSplitterDrag(event:PointerEvent){
    if(event.button!==0)return;
    const height=this.editorLayout()?.nativeElement.getBoundingClientRect().height||0;
    if(height<=0)return;
    this.splitterDrag={pointerId:event.pointerId,startY:event.clientY,startPercent:this.upperPanePercent(),height};
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    event.preventDefault();
  }
  moveSplitter(event:PointerEvent){
    const drag=this.splitterDrag;
    if(!drag || drag.pointerId!==event.pointerId)return;
    this.upperPanePercent.set(Math.max(35,Math.min(80,drag.startPercent+(event.clientY-drag.startY)/drag.height*100)));
  }
  endSplitterDrag(event:PointerEvent){
    if(this.splitterDrag?.pointerId!==event.pointerId)return;
    this.splitterDrag=null;
    const handle=event.currentTarget as HTMLElement;
    if(handle.hasPointerCapture(event.pointerId))handle.releasePointerCapture(event.pointerId);
  }
  splitterKeydown(event:KeyboardEvent){
    const step=event.shiftKey?5:1;
    if(event.key==='ArrowUp')this.upperPanePercent.update(value=>Math.max(35,value-step));
    else if(event.key==='ArrowDown')this.upperPanePercent.update(value=>Math.min(80,value+step));
    else if(event.key==='Home')this.upperPanePercent.set(35);
    else if(event.key==='End')this.upperPanePercent.set(80);
    else return;
    event.preventDefault();
  }
  newObjectAction(){this.setTool('hierarchy');const options=this.creationOptions();if(options.length===1)this.startCreation(options[0]);}
  saveReasonDialog=viewChild<ElementRef<HTMLDialogElement>>('saveReasonDialog');
  noteDialog=viewChild<ElementRef<HTMLDialogElement>>('noteDialog');
  saveReasonTitle=signal('saveReasonTitle');
  private resumeWithReason:(()=>Promise<void>)|null=null;
  private requireReason(resume:()=>Promise<void>,title='saveReasonTitle'){
    if(this.reason.trim())return true;
    this.resumeWithReason=resume;this.saveReasonTitle.set(title);const dialog=this.saveReasonDialog()?.nativeElement;
    if(dialog && !dialog.open){dialog.showModal();dialog.querySelector<HTMLInputElement>('input')?.focus();}
    return false;
  }
  continueWithReason(){if(this.busy() || !this.canEdit() || !this.reason.trim())return;const resume=this.resumeWithReason;this.resumeWithReason=null;this.saveReasonDialog()?.nativeElement.close();void resume?.();}
  historyDialog=viewChild<ElementRef<HTMLDialogElement>>('historyDialog');
  historyOperation=signal<MetadataOperation|null>(null);
  historyOrder(operation:MetadataOperation){
    const reverse=operation.kind==='undo';
    const source=reverse || operation.kind==='redo'
      ?this.document()?.history.find(item=>item.id===operation.changes.related_operation_id)
      :operation;
    const changes=source?.changes;
    if(source?.kind!=='structure-reorder' || !changes?.before_order || !changes.after_order)return null;
    const [before,after]=reverse?[changes.after_order,changes.before_order]:[changes.before_order,changes.after_order];
    const titles=new Map(changes.objects?.map(object=>[object.id,object.title]));
    const title=(id:string)=>titles.get(id)||id;
    const changed=before.flatMap((id,index)=>id===after[index]?[]:[index]);
    const first=changed[0],last=changed.at(-1)!;
    const format=(key:string,...args:(string|number)[])=>this.text(key).replace(/\{(\d+)\}/g,(_,index)=>String(args[Number(index)]));
    let summary=format('orderPositionsChanged',changed.length);
    if(changed.length===2 && before[first]===after[last] && before[last]===after[first]){
      summary=format('orderSwapped',title(before[first]),title(before[last]),first+1,last+1);
    }else if(changed.length>2){
      // A single move must take an object from one end of the changed range to the other.
      const moves=[[first,last],[last,first]].filter(([from,to])=>{
        const reordered=[...before];
        reordered.splice(to,0,...reordered.splice(from,1));
        return reordered.every((id,index)=>id===after[index]);
      });
      if(moves.length===1){
        const [from,to]=moves[0];
        summary=format('orderMoved',title(before[from]),from+1,to+1);
      }
    }
    const scope=[changes.parent_id?this.objectTitle(changes.parent_id):'',changes.child_kind?this.text(changes.child_kind):''].filter(Boolean).join(' · ');
    return {scope,summary,before:before.map(title),after:after.map(title)};
  }
  historyValue(operation:MetadataOperation,side:'before'|'after',depth=0):string{
    const changes=operation.changes,rows=changes[side];
    if(changes.content){const change=changes.content;return side==='before'
      ?change.before_path+' · '+change.before_size+' B · '+change.before_sha256
      :change.after_path+' · '+change.after_size+' B · '+change.after_sha256;}
    const edits=changes.edits||changes.metadata_import?.edits;
    if(edits)return edits.map(edit=>this.objectTitle(edit.node_id)+' · '+this.text(edit.tag)+': '+(this.describe(edit[side])||this.text('empty'))).join('\n');
    if(changes.metadata_import_proposal)return side==='before'?this.text('empty'):changes.metadata_import_proposal.source.filename+' · '+changes.metadata_import_proposal.source.sha256+' · '+changes.metadata_import_proposal.rows.length+' '+this.text('importRows');
    if(changes.sample)return side==='before'?this.text('empty'):changes.sample.items.map(item=>item.reference+' · '+item.name).join('\n');
    if(changes.sample_review){
      const history=this.document()?.history||[],sampleId=changes.sample_review.sample_id;
      if(side==='before'){
        const index=history.findIndex(item=>item.id===operation.id);
        const previous=index<0?undefined:history.slice(0,index).filter(item=>item.changes.sample_review?.sample_id===sampleId).at(-1);
        return previous?this.historyValue(previous,'after'):this.text('empty');
      }
      const sample=history.find(item=>item.id===sampleId)?.changes.sample;
      return changes.sample_review.reviews.map(item=>(sample?.items.find(selected=>selected.node_id===item.node_id)?.name||item.node_id)+' · '+this.text('sample_'+item.outcome)+' · '+item.comment).join('\n');
    }
    if(rows!==undefined)return rows.length?this.describe(rows)||this.text('historyEmptyValue'):this.text('empty');
    if(changes.note_id){const note=side==='before'?changes.note_before:changes.note_after;return note?note.text+' · '+this.text(note.purpose):this.text('empty');}
    if(operation.kind==='structure-create')return side==='before'?this.text('empty'):(changes.objects||[]).map(object=>object.title+' · '+object.source_id).join('\n')||this.text('empty');
    const history=this.document()?.history||[];
    if((operation.kind==='undo'||operation.kind==='redo') && depth<history.length){
      const related=history.find(item=>item.id===changes.related_operation_id);
      if(related)return this.historyValue(related,operation.kind==='undo'?(side==='before'?'after':'before'):side,depth+1);
    }
    if(operation.before_metadata_sha256!==operation.after_metadata_sha256)return this.text('historyMetadataVersion')+'\n'+(side==='before'?operation.before_metadata_sha256:operation.after_metadata_sha256);
    return '—';
  }
  actorLabel(actor:string){
    const claims=this.identity.tokenParsed;
    if(!claims || actor!==claims['sub'])return actor;
    const name=typeof claims['name']==='string'?claims['name'].trim():'';
    const username=typeof claims['preferred_username']==='string'?claims['preferred_username'].trim():'';
    return name && username && name!==username?name+' ('+username+')':name||username||actor;
  }
  historyLabel(operation:MetadataOperation){return [this.text(operation.kind),operation.changes.tag?this.text(operation.changes.tag):'',operation.changes.name||'',operation.kind==='structure-create'?operation.changes.objects?.map(object=>object.title).join(' · '):''].filter(Boolean).join(' · ');}
  openHistory(operation:MetadataOperation){this.historyOperation.set(operation);const dialog=this.historyDialog()?.nativeElement;if(dialog && !dialog.open){dialog.showModal();dialog.querySelector<HTMLButtonElement>('.close-history')?.focus();}}
  canSave(){return !this.busy() && !this.structurePending() && !this.noteDraft() && !this.sampleReviewDraft() && !this.bulkDraft() && !this.importDraft()?.dirty && this.canEdit() && this.proposed().length>0;}
  async saveChanges(){
    if(!this.canSave())return;
    const dialog=this.saveReasonDialog()?.nativeElement;
    if(!this.requireReason(()=>this.saveChanges()))return;
    dialog?.close();
    const edits=this.proposed().map(({node,field})=>({node_id:node.id,field_id:field.id,before:field.values,values:this.fieldRows(field)}));
    await this.mutate('edit-batch',{edits});
  }
  async downloadWorking(){const history=this.document()?.history||[];const latest=history[history.length-1];if(latest)await this.download(latest,'metadata');else this.sourceDownload.emit('metadata');}
  async downloadWorkingSip(){const latest=this.document()?.history.at(-1);if(latest)await this.download(latest,'archive');else this.sourceDownload.emit('original');}
  async copyValue(value:string){try{await navigator.clipboard.writeText(value);this.message.set(this.text('copied'));}catch{this.error.set(this.text('copyFailed'));}}
  menu(event:MouseEvent|KeyboardEvent,label:string,commands:ContextCommand[]){
    event.preventDefault();event.stopPropagation();
    const invoker=event.currentTarget as HTMLElement,bounds=invoker.getBoundingClientRect();
    this.contextMenu.set({x:event instanceof MouseEvent?event.clientX:bounds.left,y:event instanceof MouseEvent?event.clientY:bounds.bottom,label,commands,invoker});
  }
  contextKey(event:KeyboardEvent,open:()=>void){if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10')open();}
  nodeContextKey(event:KeyboardEvent,node:MetadataObject){this.contextKey(event,()=>this.nodeContext(event,node));}
  fieldContextKey(event:KeyboardEvent,node:MetadataObject,field:MetadataField){this.contextKey(event,()=>this.fieldContext(event,node,field));}
  historyContextKey(event:KeyboardEvent,operation:MetadataOperation){this.contextKey(event,()=>this.historyContext(event,operation));}
  noteContextKey(event:KeyboardEvent,note:PreparationNote){this.contextKey(event,()=>this.noteContext(event,note));}
  countKind(kind:string){return this.document()?.nodes.filter(n=>n.kind===kind).length||0;}
  hasKind(kind:string){return !!this.countKind(kind);}
  nodeContext(event:MouseEvent|KeyboardEvent,node:MetadataObject){
    const blocked=this.busy()||!!this.pendingCount()||!this.canEdit();
    const commands:ContextCommand[]=[{label:this.text('showInEditor'),run:()=>this.select(node.id)},{label:this.text('copyName'),icon:'copy',run:()=>void this.copyValue(node.title)},{label:this.text('copyIdentifier'),icon:'copy',run:()=>void this.copyValue(node.source_id||node.id)}];
    for(const option of this.document()?.creation_options.filter(o=>o.parent_id===node.id)||[])commands.push({label:this.text('newObject')+' · '+this.text(option.child_kind),icon:'file-plus',disabled:blocked,run:()=>{this.select(node.id);this.startCreation(option);}});
    const title=node.fields.find(f=>f.tag==='titel'||f.tag==='name');
    const group=this.reorderGroups().get(node.id),index=group?.children.findIndex(c=>c.id===node.id)??-1;
    const payload=!!this.document()?.content_files?.[node.id];
    if(payload)commands.push({label:this.text('contentReplace'),disabled:blocked,run:()=>this.startContent(node,'replace')});
    commands.push({label:this.text('rename'),disabled:blocked||(!title?.editable&&!payload),run:()=>{if(payload){this.startContent(node,'rename');return;}this.select(node.id);this.chooseEditor('metadata');setTimeout(()=>document.querySelector<HTMLElement>('[data-field-tag="'+title?.tag+'"] textarea')?.focus());}},
      {label:this.text('up'),disabled:blocked||index<=0,run:()=>void this.moveSibling(node.id,-1)},
      {label:this.text('down'),disabled:blocked||!group||index<0||index>=group.children.length-1,run:()=>void this.moveSibling(node.id,1)},
      {label:this.text('newNote'),icon:'sticky-note',disabled:blocked,run:()=>{this.select(node.id);this.startNote();}},
      {label:this.text('history'),icon:'clock',run:()=>this.showHistory()});
    this.menu(event,node.title,commands);
  }
  fieldContext(event:MouseEvent|KeyboardEvent,node:MetadataObject,field:MetadataField){
    this.menu(event,this.text(field.tag,field.label),[
      {label:this.text('copyValue'),icon:'copy',run:()=>void this.copyValue(this.describe(this.fieldRows(field)))},
      {label:this.text('discardField'),icon:'x',disabled:this.busy()||!this.drafts().has(field.id!),run:()=>{this.drafts.update(all=>{const next=new Map(all);next.delete(field.id!);return next;});this.emitPending();}},
      {label:this.text('clearOptionalField'),icon:'x',disabled:this.busy()||!this.canEdit()||!field.editable||field.minimum>0||!this.fieldRows(field).length,run:()=>this.stage(field,[])},
      {label:this.text('history'),icon:'clock',run:()=>this.showHistory()}]);
  }
  historyContext(event:MouseEvent|KeyboardEvent,operation:MetadataOperation){this.menu(event,this.text('history'),[
    {label:this.text('compare'),run:()=>void this.compare(operation)},
    {label:this.text('restore'),disabled:this.busy()||!this.canEdit()||!this.reason.trim(),run:()=>void this.action('restore',operation.id)},
    {label:this.text('downloadMetadata'),icon:'file-text',run:()=>void this.download(operation,'metadata')},
    {label:this.text('evidence'),icon:'download',run:()=>void this.download(operation,'evidence')}]);}
  noteContext(event:MouseEvent|KeyboardEvent,note:PreparationNote){
    const row=event.currentTarget as HTMLTableRowElement,table=row.closest('table');
    const rowText=(item:HTMLTableRowElement)=>Array.from(item.cells).map(cell=>cell.innerText.trim().replace(/[\t\r\n]+/g,' ')).join('\t');
    this.menu(event,this.text('notes'),[
    {label:this.text('showInEditor'),run:()=>this.select(note.node_id)},
    {label:this.text('copyValue'),icon:'copy',run:()=>void this.copyValue(note.text)},
    {label:this.text('copyRow'),icon:'copy',run:()=>void this.copyValue(rowText(row))},
    {label:this.text('copyTable'),icon:'copy',run:()=>void this.copyValue(table?Array.from(table.rows).map(rowText).join('\n'):'')},
    {label:this.text('editNote'),disabled:this.busy()||!this.canEdit()||!!this.pendingCount(),run:()=>this.startNote(note)},
    {label:this.text('notesForObject'),disabled:this.busy(),run:()=>{this.notesOwner.set(note.node_id);this.tool.set('notes');}},
    {label:this.text('deleteNote'),icon:'x',danger:true,disabled:this.busy()||!this.canEdit()||!!this.pendingCount(),run:()=>void this.deleteNote(note)}]);}
  workspaceContext(event:MouseEvent){if((event.target as Element).closest('input,textarea,select,a,button,summary'))return;this.menu(event,this.text('viewMenu'),[
    {label:this.text('overview'),run:()=>this.chooseEditor('overview')},
    {label:this.text('statistics'),run:()=>this.chooseEditor('statistics')},
    {label:this.text('validation'),icon:'shield-check',run:()=>this.validationView()},
    {label:this.text('changes'),icon:'file-text',run:()=>this.setTool('changes')},
    {label:this.text('history'),icon:'clock',run:()=>this.showHistory()}]);}
  preparedContext(event:MouseEvent,record:PreparedInput){this.menu(event,this.text('frozenVersions'),[
    {label:this.text('downloadPrepared'),icon:'download',run:()=>void this.downloadPrepared(record,'zip')},
    {label:this.text('evidence'),icon:'download',run:()=>void this.downloadPrepared(record,'evidence')}]);}
  private api=inject(ArchiveApi);private identity=inject(KEYCLOAK);
  document=signal<MetadataWorkspace|null>(null);selected=signal('');language=signal<EditorLanguage>('en');
  query=signal('');busy=signal(false);error=signal('');message=signal('');
  navigator=signal<'hierarchy'|'inventory'>('hierarchy');
  tool=signal<'history'|'changes'|'search'|'notes'|'hierarchy'|'sample'|'kost'>('sample');
  sampleResult=signal<MetadataSample|null>(null);sampleMode=signal<'absolute'|'percent'>('absolute');sampleSize=signal(10);sampleLoading=signal(false);
  sampleNameFilter=signal('');sampleReferenceFilter=signal('');sampleSeed=signal('');sampleSelection=signal('');
  sampleRecords=computed(()=>this.document()?.history.filter(record=>record.kind==='sample-record')||[]);
  retainedSample=computed(()=>this.sampleRecords().find(record=>record.id===this.sampleSelection()));
  displayedSample=computed(()=>this.retainedSample()?.changes.sample||this.sampleResult());
  sampleReview=computed(()=>this.document()?.history.filter(record=>record.kind==='sample-review' && record.changes.sample_review?.sample_id===this.retainedSample()?.id).at(-1));
  sampleReviewDialog=viewChild<ElementRef<HTMLDialogElement>>('sampleReviewDialog');
  sampleReviewDraft=signal<Record<string,{outcome:''|'checked'|'issue';comment:string}>|null>(null);
  sampleParametersChanged(){this.sampleResult.set(null);this.sampleSelection.set('');}
  chooseSample(id:string){if(this.busy() || this.sampleLoading() || this.sampleReviewDraft())return;this.sampleSelection.set(id);}
  async retainSample(){
    const sample=this.sampleResult(),doc=this.document();
    if(!sample || !doc || this.busy() || this.sampleLoading() || !this.canEdit() || this.pendingCount() || !sample.items.length || sample.revision!==doc.revision)return;
    if(!this.requireReason(()=>this.retainSample(),'sampleRetainTitle'))return;
    await this.mutate('sample-record',{sample_mode:sample.mode,sample_size:sample.size,sample_seed:sample.seed,sample_name:sample.name_filter,sample_reference:sample.reference_filter});
  }
  startSampleReview(){
    const sample=this.retainedSample()?.changes.sample;
    if(!sample || this.busy() || this.sampleLoading() || !this.canEdit() || this.pendingCount())return;
    const previous=this.sampleReview()?.changes.sample_review?.reviews||[];
    this.sampleReviewDraft.set(Object.fromEntries(sample.items.map(item=>{const old=previous.find(row=>row.node_id===item.node_id);return [item.node_id,{outcome:old?.outcome||'',comment:old?.comment||''}];})));
    this.emitPending();this.sampleReviewDialog()?.nativeElement.showModal();
  }
  updateSampleReview(id:string,key:'outcome'|'comment',value:string){
    if(key==='outcome' && value!=='' && value!=='checked' && value!=='issue')return;
    this.sampleReviewDraft.update(draft=>draft && draft[id]?{...draft,[id]:{...draft[id],[key]:value}}:draft);
  }
  canSaveSampleReview(){
    const draft=this.sampleReviewDraft();
    return !!draft && this.canEdit() && !this.busy() && !!this.reason.trim() && Object.values(draft).every(row=>!!row.outcome && (row.outcome!=='issue' || !!row.comment.trim()));
  }
  async saveSampleReview(){
    const sample=this.retainedSample()?.changes.sample,draft=this.sampleReviewDraft();
    if(!sample || !draft || !this.canSaveSampleReview())return;
    await this.mutate('sample-review',{sample_id:this.sampleSelection(),reviews:sample.items.map(item=>({node_id:item.node_id,...draft[item.node_id]}))});
  }
  closeSampleReview(event?:Event){event?.preventDefault();if(this.busy())return;this.sampleReviewDraft.set(null);this.sampleReviewDialog()?.nativeElement.close();this.emitPending();}
  sampleItemReview(id:string){return this.sampleReview()?.changes.sample_review?.reviews.find(row=>row.node_id===id);}
  sampleObjectAvailable(id:string){return !!this.document()?.nodes.some(node=>node.id===id);}
  creationDraft=signal<{option:CreationOption;count:number;values:Record<string,MetadataRow[]>;request:Record<string,unknown>|null;preview:{objects:{id:string;source_id:string;title:string}[]}|null}|null>(null);
  creationOptions=computed(()=>this.document()?.creation_options.filter(o=>o.parent_id===this.selected())||[]);
  structurePending=computed(()=>!!this.creationDraft() || !!this.contentDraft());
  creationSecondary=signal(false);
  startCreation(option:CreationOption){
    if(!this.canEdit() || this.busy() || this.pendingCount())return;
    const row=(field:MetadataField):MetadataRow=>({id:'new-'+crypto.randomUUID(),text:'',attributes:Object.fromEntries(field.attributes.map(a=>[a.name,''])),children:Object.fromEntries(field.children.map(c=>[c.tag,Array.from({length:c.minimum},()=>row(c))]))});
    const values=Object.fromEntries(option.fields.map(f=>[f.tag,Array.from({length:Math.max(f.minimum,f.tag==='titel'?1:0)},()=>row(f))]));
    this.creationDraft.set({option,count:1,values,request:null,preview:null});this.creationSecondary.set(false);this.tool.set('hierarchy');this.emitPending();
  }
  creationValue(tag:string,rows:MetadataRow[]){this.creationDraft.update(d=>d?{...d,values:{...d.values,[tag]:rows},request:null,preview:null}:d);}
  creationCount(count:number){this.creationDraft.update(d=>d?{...d,count,request:null,preview:null}:d);}
  cancelCreation(){if(this.busy() || !window.confirm(this.text('discardCreation')))return;this.creationDraft.set(null);this.emitPending();}
  async previewCreation(){
    const draft=this.creationDraft(),doc=this.document();if(!draft || !doc || this.busy() || !this.canEdit() || !this.reason.trim())return;
    const request={request_id:crypto.randomUUID(),revision:doc.revision,source_sha256:doc.source_sha256,base_operation_id:doc.state.head,kind:'structure-create',reason:this.reason,parent_id:draft.option.parent_id,child_kind:draft.option.child_kind,count:draft.count,object_values:draft.values};
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{const preview=await this.api.request<{objects:{id:string;source_id:string;title:string}[]}>(this.base()+'/creation-preview',{method:'POST',body:JSON.stringify(request)});if(generation===this.generation)this.creationDraft.set({...draft,request,preview});}
    catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Creation preview failed');}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  async applyCreation(){const draft=this.creationDraft();if(draft?.preview && draft.request){if(draft.request['reason']!==this.reason){this.error.set(this.text('refreshCreation'));return;}await this.mutate('structure-create',draft.request);}}
  private reorderGroups=computed(()=>new Map(
    (this.document()?.reorder_groups||[]).flatMap(group=>group.children.map(child=>[child.id,group] as const))));
  private reorderDrag:{node:MetadataTreeObject;view:MetadataNavigator;revision:number}|null=null;
  reorderDrop=signal<{key:string;after:boolean}|null>(null);
  canDragOrder(id:string){return this.canEdit() && !this.busy() && !this.pendingCount() && this.reorderGroups().has(id);}
  startOrderDrag(event:DragEvent,node:MetadataTreeObject,view:MetadataNavigator){
    const doc=this.document();
    if(!doc || !event.dataTransfer || !this.canDragOrder(node.id)){event.preventDefault();return;}
    event.dataTransfer.effectAllowed='move';
    event.dataTransfer.setData('text/plain',node.id);
    this.reorderDrag={node,view,revision:doc.revision};
  }
  endOrderDrag(){this.reorderDrag=null;this.reorderDrop.set(null);}
  orderDragLeave(event:DragEvent,node:MetadataTreeObject){
    if(this.reorderDrop()?.key===node.navigation_key && !(event.relatedTarget instanceof Node && event.currentTarget instanceof Element && event.currentTarget.contains(event.relatedTarget)))this.reorderDrop.set(null);
  }
  private dropOrder(event:DragEvent,node:MetadataTreeObject,view:MetadataNavigator){
    const drag=this.reorderDrag;
    if(!drag || drag.revision!==this.document()?.revision || drag.view!==view || drag.node.parent_key!==node.parent_key || drag.node.id===node.id
        || !this.canDragOrder(drag.node.id) || this.reorderGroups().get(drag.node.id)!==this.reorderGroups().get(node.id)
        || !(event.currentTarget instanceof HTMLElement))return null;
    const bounds=event.currentTarget.getBoundingClientRect();
    const after=event.clientY>=bounds.top+bounds.height/2;
    const group=this.reorderGroups().get(node.id)!;
    const from=group.children.findIndex(child=>child.id===drag.node.id);
    const target=group.children.findIndex(child=>child.id===node.id);
    const to=target+Number(after)-Number(from<target);
    return {group,from,to,after};
  }
  orderDragOver(event:DragEvent,node:MetadataTreeObject,view:MetadataNavigator){
    const order=this.dropOrder(event,node,view);
    if(!order || order.from===order.to){this.reorderDrop.set(null);return;}
    event.preventDefault();
    if(event.dataTransfer)event.dataTransfer.dropEffect='move';
    this.reorderDrop.set({key:node.navigation_key,after:order.after});
  }
  async finishOrderDrop(event:DragEvent,node:MetadataTreeObject,view:MetadataNavigator){
    const order=this.dropOrder(event,node,view);
    this.endOrderDrag();
    if(!order || order.from===order.to)return;
    event.preventDefault();
    await this.saveOrder(order.group,order.from,order.to);
  }
  private async moveSibling(id:string,delta:number){
    if(!this.canDragOrder(id))return;
    const group=this.reorderGroups().get(id)!;
    const from=group.children.findIndex(child=>child.id===id);
    if(from+delta>=0 && from+delta<group.children.length)await this.saveOrder(group,from,from+delta);
  }
  private async saveOrder(group:ReorderGroup,from:number,to:number){
    const before_order=group.children.map(child=>child.id),after_order=[...before_order];
    after_order.splice(from,1);after_order.splice(to,0,before_order[from]);
    await this.mutate('structure-reorder',{parent_id:group.parent_id,child_kind:group.child_kind,before_order,after_order});
  }
  orderText(objects:{id:string;title:string}[]|undefined,order:string[]|undefined){return (order||[]).map(id=>objects?.find(c=>c.id===id)?.title||id).join('\n');}
  noteDraft=signal<{node_id:string;before:PreparationNote|null;text:string;purpose:PreparationNote['purpose']}|null>(null);
  notesOwner=signal<string|null>(null);
  visibleNotes=computed(()=>this.document()?.notes.filter(note=>!this.notesOwner() || note.node_id===this.notesOwner())||[]);
  noteKind(note:PreparationNote){const node=this.document()?.nodes.find(node=>node.id===note.node_id);return node?this.text(node.kind,node.label):note.node_id;}
  typeNote(text:string){
    if(!this.canEdit() || this.busy() || this.structurePending() || this.drafts().size || this.noteDraft()?.before)return;
    const draft=this.noteDraft();this.noteDraft.set(text?{node_id:draft?.node_id||this.selected(),before:null,text,purpose:'internal'}:null);this.emitPending();
  }
  canSaveNote(){const draft=this.noteDraft();return this.canEdit() && !this.busy() && !this.structurePending() && !this.drafts().size && !!draft?.text.trim();}
  closeNoteDialog(event?:Event){event?.preventDefault();if(this.busy())return;this.noteDraft.set(null);this.emitPending();this.noteDialog()?.nativeElement.close();}
  pendingCount=computed(()=>this.drafts().size+(this.noteDraft()?1:0)+(this.creationDraft()?1:0)+(this.sampleReviewDraft()?1:0)+(this.bulkDraft()?1:0)+(this.importDraft()?.dirty?1:0)+(this.contentDraft()?1:0));
  emitPending(){this.pending.emit(this.pendingCount());}
  objectTitle(id:string){return this.document()?.nodes.find(n=>n.id===id)?.title||id;}
  startNote(note:PreparationNote|null=null){
    if(!this.canEdit() || this.busy() || this.structurePending())return;
    if(this.noteDraft() && !window.confirm(this.text('discardNote')))return;
    this.tool.set('notes');this.notesOwner.set(null);
    this.noteDraft.set(note?{node_id:note.node_id,before:note,text:note.text,purpose:note.purpose}:null);this.emitPending();
    if(note){const dialog=this.noteDialog()?.nativeElement;dialog?.showModal();setTimeout(()=>dialog?.open && dialog.querySelector<HTMLTextAreaElement>('textarea')?.focus());}
    else setTimeout(()=>document.getElementById('new-preparation-note')?.focus());
  }
  updateNote(key:'text'|'purpose',value:string){this.noteDraft.update(d=>d?{...d,[key]:value}:d);this.emitPending();}
  async saveNote(){const draft=this.noteDraft();if(!draft || !this.canSaveNote() || !this.requireReason(()=>this.saveNote(),'saveNote'))return;await this.mutate(draft.before?'note-edit':'note-add',{node_id:draft.node_id,note_id:draft.before?.id||null,note_before:draft.before,note_text:draft.text,note_purpose:draft.purpose});if(!this.noteDraft())this.noteDialog()?.nativeElement.close();}
  private async removeNote(note:PreparationNote){if(this.requireReason(()=>this.removeNote(note),'deleteNote'))await this.mutate('note-delete',{node_id:note.node_id,note_id:note.id,note_before:note});}
  async deleteNote(note:PreparationNote){if(this.busy() || !this.canEdit() || this.pendingCount())return;if(window.confirm(this.text('confirmDeleteNote')))await this.removeNote(note);}
  search=signal('');secondary=signal(false);
  collapsed=signal(new Set<string>());
  parents=computed(()=>{const result=new Map<string,string[]>(),stack:MetadataObject[]=[];for(const node of this.document()?.nodes||[]){while(stack.length && stack[stack.length-1].depth>=node.depth)stack.pop();result.set(node.id,stack.map(n=>n.id));stack.push(node);}return result;});
  classificationKinds=new Set(['paket','ablieferung','provenienz','ordnungssystem','ordnungssystemposition']);
  navigationRows=computed(()=>{
    const doc=this.document(),nodes=new Map(doc?.nodes.map(node=>[node.id,node])||[]);
    const result:Record<MetadataNavigator,MetadataTreeObject[]>={classification:[],hierarchy:[],inventory:[]};
    for(const view of ['classification','hierarchy','inventory'] as const){
      for(const row of doc?.navigation?.[view]||[]){
        const node=nodes.get(row.object_id);
        if(node)result[view].push({...node,navigation_key:row.key,parent_key:row.parent_key,navigation_depth:row.depth});
      }
    }
    return result;
  });
  navigationParents=computed(()=>new Map(Object.values(this.navigationRows()).flat().map(node=>[node.navigation_key,node.parent_key])));
  classification=computed(()=>this.navigationRows().classification);
  objectIds=computed(()=>new Set(this.objects().map(n=>n.id)));
  visibleClassification=computed(()=>this.visibleRows(this.classification()));
  visibleObjects=computed(()=>this.query().trim()?this.objects():this.visibleRows(this.objects()));
  branches=computed(()=>new Set(Object.values(this.navigationRows()).flat().map(node=>node.parent_key).filter((key):key is string=>!!key)));
  searchKind=signal('');searchField=signal('');searchName=signal('');searchFrom=signal('');searchUntil=signal('');
  searchFinding=signal('');
  searchKinds=computed(()=>[...new Set(this.document()?.nodes.map(node=>node.kind)||[])].sort());
  searchFields=computed(()=>[...new Map((this.document()?.nodes||[]).filter(node=>!this.searchKind() || node.kind===this.searchKind()).flatMap(node=>node.fields.map(field=>[field.tag,field] as const))).values()]);
  searchActive=computed(()=>!!(this.search().trim() || this.searchKind() || this.searchField() || this.searchName().trim() || this.searchFrom() || this.searchUntil() || this.searchFinding()));
  searchDateError=computed(()=>!!(this.searchFrom() && this.searchUntil() && this.searchFrom()>this.searchUntil()));
  searchReport=computed(()=>{
    const report=this.kostReport();
    return report?.available && report.input_sha256===this.assessmentSha256() && report.prepared_id===(this.frozenCurrent()?.id??null)?report:null;
  });
  searchResults=computed(()=>{
    if(!this.searchActive() || this.searchDateError())return [];
    const q=this.search().trim().toLocaleLowerCase(),name=this.searchName().trim().toLocaleLowerCase();
    return (this.document()?.nodes||[]).flatMap(node=>{
      if(this.searchKind() && node.kind!==this.searchKind())return [];
      const identity=[node.title,node.source_id||'',node.id,node.path,this.document()?.content_files?.[node.id]?.path||''].join('\n').toLocaleLowerCase();
      if(name && !identity.includes(name))return [];
      if((this.searchFrom() || this.searchUntil()) && !this.matchesDates(node))return [];
      if(this.searchFinding()){
        const path=node.source_id?this.kostPaths()[node.source_id]:undefined;
        if(!path || !this.searchReport()?.entries?.some(entry=>entry.scope==='file' && entry.path===path && entry.mapping==='matched' && entry.findings.some(finding=>finding.severity===this.searchFinding())))return [];
      }
      const candidates=node.fields.filter(field=>(!this.searchField() || field.tag===this.searchField()) && !!field.values?.length);
      const matches=candidates.flatMap(field=>{const value=this.describe(field.values);return !q || value.toLocaleLowerCase().includes(q)?[{node,field,value}]:[];});
      if(matches.length || !q || !identity.includes(q))return matches;
      const field=candidates.find(field=>field.tag==='titel'||field.tag==='name')||candidates[0];
      return field?[{node,field,value:this.describe(field.values)}]:[];
    });
  });
  bulkDialog=viewChild<ElementRef<HTMLDialogElement>>('bulkDialog');
  bulkDraft=signal<{nodeIds:string[];fieldTag:string;value:string;request:Record<string,unknown>|null;preview:{edits:MetadataFieldChange[];metadata_sha256:string}|null}|null>(null);
  searchObjects=computed(()=>[...new Map(this.searchResults().map(hit=>[hit.node.id,hit.node])).values()]);
  bulkTargets=computed(()=>this.document()?.nodes.filter(node=>this.bulkDraft()?.nodeIds.includes(node.id))||[]);
  bulkFields=computed(()=>this.commonBulkFields(this.bulkTargets()));
  bulkField=computed(()=>this.bulkFields().find(field=>field.tag===this.bulkDraft()?.fieldTag));
  private commonBulkFields(nodes:MetadataObject[]){
    const eligible=(field:MetadataField)=>field.editable && !field.children.length && !field.attributes.length && field.maximum===1;
    return nodes[0]?.fields.filter(field=>eligible(field) && nodes.every(node=>node.fields.some(other=>other.tag===field.tag && eligible(other))))||[];
  }
  canStartBulk(){return this.canEdit() && !this.busy() && !this.pendingCount() && this.searchObjects().length>0 && this.searchObjects().length<=200 && this.commonBulkFields(this.searchObjects()).length>0;}
  startBulk(){
    if(!this.canStartBulk())return;
    const targets=this.searchObjects(),field=this.commonBulkFields(targets)[0];
    this.bulkDraft.set({nodeIds:targets.map(node=>node.id),fieldTag:field.tag,value:'',request:null,preview:null});
    this.error.set('');this.emitPending();this.bulkDialog()?.nativeElement.showModal();
  }
  updateBulk(key:'fieldTag'|'value',value:string){this.bulkDraft.update(draft=>draft?{...draft,[key]:value,request:null,preview:null}:draft);}
  closeBulk(event?:Event){event?.preventDefault();if(this.busy())return;this.bulkDraft.set(null);this.bulkDialog()?.nativeElement.close();this.emitPending();}
  async previewBulk(){
    const draft=this.bulkDraft(),doc=this.document(),field=this.bulkField();
    if(!draft || !doc || !field || this.busy() || !this.canEdit() || !this.reason.trim())return;
    const edits=this.bulkTargets().flatMap(node=>{
      const current=node.fields.find(item=>item.tag===field.tag)!;
      if(current.values?.length===1 && current.values[0].text===draft.value)return [];
      const values=structuredClone(current.values||[]);
      if(values.length)values[0].text=draft.value;
      else values.push({id:'new-'+crypto.randomUUID(),text:draft.value,attributes:{},children:{}});
      return [{node_id:node.id,field_id:current.id,before:current.values,values}];
    });
    if(!edits.length){this.error.set(this.text('bulkNoChanges'));return;}
    const request={request_id:crypto.randomUUID(),revision:doc.revision,source_sha256:doc.source_sha256,base_operation_id:doc.state.head,kind:'edit-batch',reason:this.reason,edits};
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{
      const preview=await this.api.request<{edits:MetadataFieldChange[];metadata_sha256:string}>(this.base()+'/edit-preview',{method:'POST',body:JSON.stringify(request)});
      if(generation===this.generation)this.bulkDraft.set({...draft,request,preview});
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Bulk preview failed');}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  async applyBulk(){
    const draft=this.bulkDraft();if(!draft?.request || !draft.preview)return;
    if(draft.request['reason']!==this.reason){this.error.set(this.text('refreshCreation'));return;}
    await this.mutate('edit-batch',draft.request);
  }
  contentDialog=viewChild<ElementRef<HTMLDialogElement>>('contentDialog');
  contentDraft=signal<{node:MetadataObject;action:'rename'|'replace';name:string;source:{filename:string;data_base64:string}|null;request:Record<string,unknown>|null;preview:{content:ContentChange}|null}|null>(null);
  startContent(node:MetadataObject,action:'rename'|'replace'){
    if(this.busy()||this.pendingCount()||!this.canEdit()||!this.document()?.content_files?.[node.id])return;
    this.select(node.id);this.contentDraft.set({node,action,name:node.title,source:null,request:null,preview:null});
    this.error.set('');this.emitPending();this.contentDialog()?.nativeElement.showModal();
  }
  invalidateContent(){this.contentDraft.update(draft=>draft?{...draft,request:null,preview:null}:draft);}
  contentName(name:string){this.contentDraft.update(draft=>draft?{...draft,name,request:null,preview:null}:draft);}
  private encodedFile(file:File){
    return new Promise<string>((resolve,reject)=>{
      const reader=new FileReader();reader.onerror=()=>reject(reader.error||new Error(this.text('importReadFailed')));
      reader.onabort=()=>reject(new DOMException('File reading aborted','AbortError'));
      reader.onload=()=>{
        const result=reader.result,index=typeof result==='string'?result.indexOf(','):-1;
        if(typeof result!=='string'||index<0||!result.slice(0,index).endsWith(';base64')){reject(new Error(this.text('importReadFailed')));return;}
        resolve(result.slice(index+1));
      };
      reader.readAsDataURL(file);
    });
  }
  async chooseContentFile(event:Event){
    const input=event.target;
    if(!(input instanceof HTMLInputElement))return;
    const file=input.files?.[0];input.value='';if(!file||this.busy()||!this.canEdit())return;
    this.contentDraft.update(draft=>draft?{...draft,source:null,request:null,preview:null}:draft);
    if(file.size>256*1024*1024){this.error.set(this.text('contentLimit'));return;}
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{
      const data_base64=await this.encodedFile(file);
      if(generation===this.generation)this.contentDraft.update(draft=>draft?{...draft,source:{filename:file.name,data_base64},request:null,preview:null}:draft);
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:this.text('importReadFailed'));}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  closeContent(event?:Event){
    event?.preventDefault();if(this.busy()||this.contentDraft()&&!window.confirm(this.text('contentDiscard')))return;
    this.contentDraft.set(null);this.contentDialog()?.nativeElement.close();this.emitPending();
  }
  async previewContent(){
    const draft=this.contentDraft(),doc=this.document();
    if(!draft||!doc||this.busy()||!this.canEdit()||!this.reason.trim()||draft.action==='replace'&&!draft.source)return;
    const request={request_id:crypto.randomUUID(),revision:doc.revision,source_sha256:doc.source_sha256,
      base_operation_id:doc.state.head,kind:'content-edit',reason:this.reason,node_id:draft.node.id,content_action:draft.action,
      content_name:draft.action==='rename'?draft.name:'',content_source:draft.action==='replace'?draft.source:null};
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    this.invalidateContent();
    try{
      const preview=await this.api.request<{content:ContentChange}>(this.base()+'/content-preview',{method:'POST',body:JSON.stringify(request)});
      if(generation===this.generation)this.contentDraft.set({...draft,request,preview});
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:this.text('contentFailed'));}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  async applyContent(){
    const draft=this.contentDraft();
    if(!draft?.preview||!draft.request)return;
    if(draft.request['reason']!==this.reason){this.error.set(this.text('refreshCreation'));return;}
    await this.mutate('content-edit',draft.request);
  }
  importDialog=viewChild<ElementRef<HTMLDialogElement>>('importDialog');
  importDraft=signal<{source:MetadataImportSource|null;recordId:string;choices:Record<string,Omit<MetadataImportDecision,'action'>&{action:''|'apply'|'exclude'}>;dirty:boolean;request:Record<string,unknown>|null;preview:{edits:MetadataFieldChange[];metadata_sha256:string}|null}|null>(null);
  importRecords=computed(()=>this.document()?.history.filter(record=>record.kind==='import-record')||[]);
  importRecord=computed(()=>this.importRecords().find(record=>record.id===this.importDraft()?.recordId));
  importProposal=computed(()=>this.importRecord()?.changes.metadata_import_proposal);
  importEditable=computed(()=>this.canEdit() && this.importRecord()?.id===this.document()?.state.head);
  importTargets=computed(()=>this.document()?.nodes.filter(node=>node.fields.some(field=>this.importFieldEligible(field)))||[]);
  private importFieldEligible(field:MetadataField){return field.editable && !field.children.length && !field.attributes.length && field.maximum===1;}
  startImports(id?:string){
    if(this.busy() || this.pendingCount())return;
    this.selectImport(id||this.importRecords().at(-1)?.id||'');this.error.set('');this.importDialog()?.nativeElement.showModal();
  }
  selectImport(recordId:string){
    const record=this.importRecords().find(record=>record.id===recordId);
    this.importDraft.set({source:null,recordId,choices:Object.fromEntries((record?.changes.metadata_import_proposal?.rows||[])
      .map(row=>[row.id,{row_id:row.id,action:'',node_id:row.node_id,field:row.field,reason:''}])),dirty:false,request:null,preview:null});
    this.emitPending();
  }
  async chooseImportFile(event:Event){
    const input=event.target as HTMLInputElement,file=input.files?.[0];input.value='';if(!file || this.busy() || !this.canEdit())return;
    const extension=file.name.split('.').at(-1)?.toLowerCase();
    if(extension!=='csv' && extension!=='xlsx' && extension!=='xml' && extension!=='json'){this.error.set(this.text('importFileType'));return;}
    if(!file.size || file.size>256*1024){this.error.set(this.text('importFileLimit'));return;}
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{
      const data_base64=await this.encodedFile(file);
      if(generation===this.generation){this.importDraft.set({source:{filename:file.name,format:extension,data_base64},recordId:'',choices:{},dirty:true,request:null,preview:null});this.emitPending();}
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:this.text('importReadFailed'));}
    finally{this.busy.set(false);this.working.emit(false);input.value='';}
  }
  successorImport(){
    const source=this.importProposal()?.source;if(!source || !this.canEdit() || this.busy())return;
    this.importDraft.set({source:{filename:source.filename,format:source.format,data_base64:source.data_base64},recordId:'',choices:{},dirty:true,request:null,preview:null});this.emitPending();
  }
  async retainImport(){
    const source=this.importDraft()?.source;if(!source || this.busy() || !this.canEdit())return;
    if(!this.requireReason(()=>this.retainImport(),'importRetain'))return;
    await this.mutate('import-record',{import_source:source});
  }
  updateImport(rowId:string,key:'action'|'node_id'|'field'|'reason',value:string){
    if(key==='action' && value!=='' && value!=='apply' && value!=='exclude')return;
    if(key==='node_id'){
      const matches=this.document()?.nodes.filter(node=>node.id===value || node.source_id===value)||[];
      if(matches.length===1)value=matches[0].id;
    }
    this.importDraft.update(draft=>{
      if(!draft)return draft;
      const choice=draft.choices[rowId];if(!choice)return draft;
      const next={...choice};
      if(key==='action'){
        if(value!=='' && value!=='apply' && value!=='exclude')return draft;
        next.action=value;
      }else next[key]=value;
      return {...draft,choices:{...draft.choices,[rowId]:next},dirty:true,request:null,preview:null};
    });this.emitPending();
  }
  importTargetFields(rowId:string){const choice=this.importDraft()?.choices[rowId];return this.document()?.nodes.find(node=>node.id===choice?.node_id)?.fields.filter(field=>this.importFieldEligible(field))||[];}
  importTargetField(rowId:string){const choice=this.importDraft()?.choices[rowId];return this.importTargetFields(rowId).find(field=>field.tag===choice?.field);}
  importTargetValue(rowId:string){const field=this.importTargetField(rowId);return field?this.describe(field.values)||this.text('empty'):this.text('importUnresolved');}
  importDecisions():MetadataImportDecision[]{return Object.values(this.importDraft()?.choices||{}).flatMap(choice=>choice.action?[{...choice,action:choice.action}]:[]);}
  canPreviewImport(){
    const rows=this.importProposal()?.rows||[],decisions=this.importDecisions();
    return this.importEditable() && !this.busy() && !!this.reason.trim() && rows.length>0 && decisions.length===rows.length
      && decisions.every(choice=>choice.action==='exclude'?!!choice.reason.trim():!rows.find(row=>row.id===choice.row_id)?.blocked && !!this.importTargetField(choice.row_id));
  }
  async previewImport(){
    const draft=this.importDraft(),doc=this.document();if(!draft || !doc || !this.canPreviewImport())return;
    const request={request_id:crypto.randomUUID(),revision:doc.revision,source_sha256:doc.source_sha256,base_operation_id:doc.state.head,kind:'import-apply',reason:this.reason,import_id:draft.recordId,import_decisions:this.importDecisions()};
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{
      const preview=await this.api.request<{edits:MetadataFieldChange[];metadata_sha256:string}>(this.base()+'/import-preview',{method:'POST',body:JSON.stringify(request)});
      if(generation===this.generation)this.importDraft.set({...draft,request,preview});
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:this.text('importPreviewFailed'));}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  async applyImport(){
    const draft=this.importDraft();if(!draft?.request || !draft.preview || this.busy())return;
    if(draft.request['reason']!==this.reason){this.error.set(this.text('refreshCreation'));return;}
    await this.mutate('import-apply',draft.request);
  }
  closeImports(event?:Event){
    event?.preventDefault();if(this.busy())return;
    if(this.importDraft()?.dirty && !window.confirm(this.text('discardProposals')))return;
    this.importDraft.set(null);this.importDialog()?.nativeElement.close();this.emitPending();
  }
  async downloadImportTemplate(format:'csv'|'xlsx'|'xml'|'json'){
    const object=format!=='xml' && this.selected()?'?object_id='+encodeURIComponent(this.selected()):'';
    try{await this.api.download(this.base()+'/import-template/'+format+object,'metadata-import-template.'+format);}
    catch(error){this.error.set(error instanceof Error?error.message:this.text('importReadFailed'));}
  }
  async downloadImportSource(record:MetadataOperation){
    const source=record.changes.metadata_import_proposal?.source;if(!source)return;
    try{await this.api.download(this.base()+'/import-source/'+record.id,source.filename);}
    catch(error){this.error.set(error instanceof Error?error.message:this.text('importReadFailed'));}
  }
  resetSearchFilters(){this.searchKind.set('');this.searchField.set('');this.searchName.set('');this.searchFrom.set('');this.searchUntil.set('');this.searchFinding.set('');}
  private matchesDates(node:MetadataObject){
    const texts=(rows:MetadataRow[]):string[]=>rows.flatMap(row=>[row.text,...Object.values(row.children).flatMap(texts)]);
    return node.fields.filter(field=>field.date).some(field=>{
      const ranges=texts(field.values||[]).flatMap(value=>{
        const match=/^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?(?:Z|[+-]\d{2}:\d{2})?$/.exec(value.trim());
        return match?[{from:match[1]+'-'+(match[2]||'01')+'-'+(match[3]||'01'),until:match[1]+'-'+(match[2]||'12')+'-'+(match[3]||'31')}]:[];
      });
      if(!ranges.length)return false;
      const from=ranges.map(range=>range.from).sort()[0],until=ranges.map(range=>range.until).sort().at(-1)!;
      return (!this.searchFrom() || until>=this.searchFrom()) && (!this.searchUntil() || from<=this.searchUntil());
    });
  }
  proposed=computed(()=>this.document()?.nodes.flatMap(node=>node.fields.filter(field=>this.drafts().has(field.id!)).map(field=>({node,field})))||[]);
  drafts=signal(new Map<string,MetadataRow[]>());reason='';checkpointName='';comparison=signal<{object:string;field:string;before:string;after:string}[]|null>(null);
  private generation=0;private activeKey='';private retry:{signature:string;request:object}|null=null;
  private freezeRetry:{signature:string;request:object}|null=null;
  frozenCurrent=computed(()=>this.document()?.prepared_inputs.find(r=>r.operation_id===this.document()?.state.head));
  latestFrozen=computed(()=>this.document()?.prepared_inputs.at(-1));
  previousFrozen=computed(()=>this.document()?.prepared_inputs.slice(0,-1).reverse()||[]);
  snapshotLabels=computed(()=>Object.fromEntries((this.document()?.prepared_inputs||[]).map((record,index)=>[record.id,this.text('snapshotLabel')+' '+(index+1)+' · '+record.id.slice(0,8)])));
  snapshotSelected(record:PreparedInput){return this.tool()==='kost' && this.kostPanel()?.chosen()===record.id;}
  freezeBlocked(){
    if(this.busy())return this.text('freezeBusy');
    if(this.pendingCount())return this.text('freezePending');
    if(this.frozenCurrent())return this.text('freezeAlreadyCurrent');
    if(!this.reason.trim())return this.text('freezeNeedsReason');
    return '';
  }
  externalStatus(record:PreparedInput){
    const panel=this.kostPanel(),inventory=panel?.inventory();
    if(panel?.error())return this.text('validatorUnavailable');
    if(!panel || !inventory)return this.text('validatorLoading');
    if(!inventory.enabled)return this.text('validatorDisabled');
    const target=inventory.targets.find(t=>t.target_id===record.id);
    return target?panel.status(target.latest):this.text('validatorLoading');
  }
  usePrepared(record:PreparedInput){
    if(this.busy() || this.pendingCount() || !this.canEdit() || record.id!==this.frozenCurrent()?.id)return;
    this.arrangePrepared.emit({sip_id:this.sipId(),prepared_id:record.id});
  }
  assessmentSha256=computed(()=>this.frozenCurrent()?.sha256 || (this.document()?.state.changed?'':this.document()?.source_sha256||''));
  selectedObject=computed(()=>this.document()?.nodes.find(n=>n.id===this.selected()));
  private navigationSelection=signal<{view:MetadataNavigator;key:string}|null>(null);
  breadcrumbs=computed(()=>{
    const doc=this.document(),node=this.selectedObject(),context=this.navigationSelection();
    if(!doc || !node)return [];
    const rows=context?this.navigationRows()[context.view]:[];
    let row=rows.find(item=>item.navigation_key===context?.key && item.id===node.id);
    const trail:MetadataTreeObject[]=[];
    while(row){trail.unshift(row);const parent=row.parent_key;row=rows.find(item=>item.navigation_key===parent);}
    const items:{node:MetadataObject;source?:MetadataNavigator;key?:string}[]=[];
    for(const id of this.parents().get((trail[0]||node).id)||[]){
      const ancestor=doc.nodes.find(item=>item.id===id);
      if(ancestor && ancestor.kind!=='ablieferung' && ancestor.kind!=='provenienz')items.push({node:ancestor});
    }
    if(trail.length && context)items.push(...trail.map(item=>({node:item,source:context.view,key:item.navigation_key})));
    else items.push({node});
    return items;
  });
  breadcrumbTitle(node:MetadataObject){return node.kind==='paket'?(this.summary('ablieferung','ablieferungsnummer')||node.title):this.navigatorTitle(node);}
  objects=computed(()=>{
    const rows=this.navigationRows()[this.navigator()],query=this.query().trim().toLocaleLowerCase();
    if(!query)return rows;
    const included=new Set<string>();
    for(const node of rows){
      if(![node.title,node.kind,node.label,node.source_id,this.text(node.kind)].some(value=>value?.toLocaleLowerCase().includes(query)))continue;
      let key:string|null=node.navigation_key;
      while(key){included.add(key);key=this.navigationParents().get(key)||null;}
    }
    return rows.filter(node=>included.has(node.navigation_key));
  });
  visibleRows(rows:MetadataTreeObject[]){
    return rows.filter(node=>{
      let parent=node.parent_key;
      while(parent){if(this.collapsed().has(parent))return false;parent=this.navigationParents().get(parent)||null;}
      return true;
    });
  }
  text(key:string,fallback?:string){return editorText(key,this.language(),fallback);}
  canEdit(){return this.identity.hasRealmRole('archive-operator');}
  base(){return '/api/transfers/'+this.transferId()+'/sips/'+this.sipId()+'/preparation';}
  ngOnChanges(changes:SimpleChanges){
    const key=this.transferId()+':'+this.sipId();
    if(key!==this.activeKey){this.activeKey=key;this.contentDraft.set(null);this.contentDialog()?.nativeElement.close();this.importDraft.set(null);this.importDialog()?.nativeElement.close();this.notesOwner.set(null);this.selected.set('');this.collapsed.set(new Set());this.reason='';this.comparison.set(null);this.resetSearchFilters();this.sampleSelection.set('');void this.load();}
    else if(changes['revision'] && this.document() && this.revision()!==this.document()!.revision && !this.busy() && !this.pendingCount())void this.load();
  }
  ngOnDestroy(){this.generation++;}
  async load(){
    const generation=++this.generation;this.error.set('');
    this.sampleResult.set(null);this.sampleLoading.set(false);
    try{
      const doc=await this.api.request<MetadataWorkspace>(this.base());
      if(generation!==this.generation)return;
      this.document.set(doc);this.retry=null;this.freezeRetry=null;
      if(!doc.nodes.some(n=>n.id===this.selected()))this.selected.set((doc.nodes.find(n=>n.kind==='ablieferung')||doc.nodes[0])?.id||'');
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Metadata workspace could not be loaded');}
  }
  discardPending(ask=true){
    if(this.busy())return false;
    if(ask && this.pendingCount() && !window.confirm(this.text('discardProposals')))return false;
    this.drafts.set(new Map());this.noteDraft.set(null);this.creationDraft.set(null);this.contentDraft.set(null);this.contentDialog()?.nativeElement.close();this.closeSampleReview();this.closeBulk();this.importDraft.set(null);this.importDialog()?.nativeElement.close();this.emitPending();return true;
  }
  reload(){if(this.discardPending()){this.comparison.set(null);void this.load();}}
  select(id:string,source?:MetadataNavigator,rowKey?:string){
    const node=this.document()?.nodes.find(n=>n.id===id);if(!node)return;
    const row=source?this.navigationRows()[source].find(item=>rowKey?item.navigation_key===rowKey:item.id===id):undefined;
    this.navigationSelection.set(source && row?{view:source,key:row.navigation_key}:null);
    this.selected.set(id);
    this.editorView.set(node.kind==='ablieferung'||node.kind==='provenienz'?'overview':'metadata');
    let revealContent=false;
    if(source==='classification'){
      const rows=this.navigationRows(),current=this.navigator(),other=current==='hierarchy'?'inventory':'hierarchy';
      if(!rows[current].some(row=>row.id===id) && rows[other].some(row=>row.id===id))this.navigator.set(other);
      revealContent=rows[this.navigator()].some(row=>row.id===id);
      if(revealContent && !this.objectIds().has(id))this.query.set('');
    }
    const ancestors=new Set<string>();
    const occurrences=Object.values(this.navigationRows()).flat().filter(row=>rowKey?row.navigation_key===rowKey || (revealContent && row.id===id):row.id===id);
    for(const row of occurrences){let parent=row.parent_key;while(parent){ancestors.add(parent);parent=this.navigationParents().get(parent)||null;}}
    this.collapsed.update(all=>new Set([...all].filter(parent=>!ancestors.has(parent))));
    if(source && source!=='classification')this.navigator.set(source);
    else if(!source && !this.classificationKinds.has(node.kind))this.navigator.set(node.kind==='datei'||node.kind==='ordner'?'inventory':'hierarchy');
    if(!this.classificationKinds.has(node.kind) && !this.objectIds().has(id))this.query.set('');
    if(revealContent)afterNextRender(()=>{
      if(this.selected()!==id)return;
      const list=this.editorLayout()?.nativeElement.querySelector<HTMLElement>('.content-pane .object-list');
      const row=list?.querySelector<HTMLElement>('.tree-row.selected');
      if(!list || !row)return;
      const bounds=list.getBoundingClientRect(),selected=row.getBoundingClientRect(),top=bounds.top+list.clientTop,bottom=top+list.clientHeight;
      if(selected.top<top)list.scrollTop+=selected.top-top;
      else if(selected.bottom>bottom)list.scrollTop+=selected.bottom-bottom;
    },{injector:this.injector});
  }
  hasChildren(node:MetadataTreeObject){return this.branches().has(node.navigation_key);}
  toggleNode(id:string){this.collapsed.update(all=>{const next=new Set(all);if(next.has(id))next.delete(id);else next.add(id);return next;});}
  showHistory(){this.tool.set('history');}
  setTool(tool:'history'|'changes'|'search'|'notes'|'hierarchy'|'sample'|'kost'){this.tool.set(tool);}
  sampleSizeValid(){const size=this.sampleSize();return Number.isSafeInteger(size)&&size>=1&&size<=(this.sampleMode()==='absolute'?500:100);}
  async drawSample(){
    const doc=this.document(),size=this.sampleSize(),mode=this.sampleMode();
    if(!doc||this.busy()||this.sampleLoading()||!this.sampleSizeValid())return;
    const generation=this.generation,params=new URLSearchParams({mode,size:String(size),revision:String(doc.revision),name:this.sampleNameFilter(),reference:this.sampleReferenceFilter()});
    if(this.sampleSeed())params.set('seed',this.sampleSeed());
    this.sampleLoading.set(true);this.error.set('');
    try{
      const result=await this.api.request<MetadataSample>(this.base()+'/sample?'+params.toString());
      if(generation!==this.generation)return;
      if(result.revision!==this.document()?.revision){this.error.set(this.text('sampleStale'));return;}
      this.sampleSelection.set('');this.sampleResult.set(result);
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Sample could not be drawn');}
    finally{if(generation===this.generation)this.sampleLoading.set(false);}
  }
  exportSample(){
    const items=this.displayedSample()?.items||[];
    if(!items.length)return;
    const quote=(value:string)=>'"'+(/^\s*[=+@-]/.test(value)?"'"+value:value).replaceAll('"','""')+'"';
    const rows=[[this.text('sampleReference'),this.text('sampleDossier')],...items.map(item=>[item.reference,item.name])];
    const csv='\uFEFF'+rows.map(row=>row.map(quote).join(',')).join('\r\n')+'\r\n';
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),anchor=document.createElement('a');
    anchor.href=url;anchor.download='ingest-packager-sample.csv';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  openSearchResult(id:string){this.select(id);}
  private classificationFieldOrder=['name','generation','anwendungszeitraum','mitbenutzung','bemerkung'];
  primary(field:MetadataField,node:MetadataObject){return !!field.minimum || (node.kind==='ordnungssystem' && this.classificationFieldOrder.includes(field.tag)) || ['titel','name','ablieferungstyp','ablieferndeStelle','ablieferungsnummer','aktenbildnerName','aktenzeichen','entstehungszeitraum','schutzfristenkategorie','schutzfrist','systemName','registratur','systemBeschreibung'].includes(field.tag) || this.drafts().has(field.id!);}
  navigatorDepth(node:MetadataTreeObject){return node.navigation_depth;}
  timespan(node:MetadataObject){const rows=node.fields.find(f=>f.tag==='entstehungszeitraum')?.values||[];const strings=(row:MetadataRow):string[]=>[row.text,...Object.values(row.children).flatMap(children=>children.flatMap(strings))].filter(Boolean);return rows.flatMap(strings).join(' - ');}
  recordTitle(node:MetadataObject){return node.kind==='ablieferung'?(this.fieldText(node,'ablieferungsnummer')||node.title):node.title;}
  navigatorTitle(node:MetadataObject){const number=node.kind==='ordnungssystemposition'?this.fieldText(node,'nummer'):'';return number?number+' '+node.title:node.title;}
  displayFields(node:MetadataObject){const order=node.kind==='ablieferung'?['ablieferungstyp','ablieferndeStelle','ablieferungsnummer','entstehungszeitraum','schutzfristenkategorie','schutzfrist']:node.kind==='provenienz'?['aktenbildnerName','systemName','registratur']:node.kind==='ordnungssystem'?this.classificationFieldOrder:node.kind==='datei'?['name','eigenschaft','originalName','pruefalgorithmus','pruefsumme']:[];return [...node.fields].sort((a,b)=>(order.includes(a.tag)?order.indexOf(a.tag):100)-(order.includes(b.tag)?order.indexOf(b.tag):100));}
  displayName(){const name=this.packageName();if(name && name!=='header')return name;return [this.summary('ablieferung','ablieferndeStelle'),this.summary('ablieferung','ablieferungsnummer')].filter(Boolean).join(' · ')||this.sipId();}
  originalUploadName(){const filename=this.originalFilename(),extension=filename.lastIndexOf('.');return extension>0?filename.slice(0,extension):filename;}
  navigateList(event:KeyboardEvent){
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    const target=event.target as HTMLElement;const list=event.currentTarget as HTMLElement;
    const buttons=Array.from(list.querySelectorAll<HTMLButtonElement>('button.metadata-object'));
    const index=buttons.indexOf(target as HTMLButtonElement);if(index<0)return;
    event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowDown'?1:-1)));
    buttons[next]?.focus();
  }
  setNavigator(view:'hierarchy'|'inventory'){this.navigator.set(view);this.query.set('');const first=this.objects()[0];if(first)this.select(first.id,view,first.navigation_key);}
  fieldRows(field:MetadataField){return this.drafts().get(field.id!)||field.values||[];}
  stage(field:MetadataField,rows:MetadataRow[]){
    if(this.structurePending() || this.noteDraft() || this.sampleReviewDraft() || this.bulkDraft())return;
    const next=new Map(this.drafts());
    if(JSON.stringify(rows)===JSON.stringify(field.values))next.delete(field.id!);else next.set(field.id!,rows);
    this.drafts.set(next);this.emitPending();this.message.set('');
  }
  async apply(field:MetadataField,nodeId=this.selected()){await this.mutate('edit',{node_id:nodeId,field_id:field.id,before:field.values,values:this.fieldRows(field)},field.id);}
  async checkpoint(){if(!this.pendingCount() && this.checkpointName.trim())await this.mutate('checkpoint',{name:this.checkpointName});}
  preparedBase(){return '/api/transfers/'+this.transferId()+'/sips/'+this.sipId()+'/prepared-inputs';}
  async freeze(){
    if(!this.document() || this.freezeBlocked() || !this.canEdit())return;
    if(!this.document()!.state.head && !await this.mutate('checkpoint',{name:this.text('freezeCheckpoint')}))return;
    const doc=this.document();
    if(!doc?.state.head || this.freezeBlocked())return;
    const signature=JSON.stringify({head:doc.state.head,reason:this.reason});
    if(this.freezeRetry?.signature!==signature)this.freezeRetry={signature,request:{request_id:crypto.randomUUID(),revision:doc.revision,source_sha256:doc.source_sha256,operation_id:doc.state.head,reason:this.reason}};
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{
      const result=await this.api.request<{prepared_input:PreparedInput}>(this.preparedBase(),{method:'POST',body:JSON.stringify(this.freezeRetry.request)});
      if(generation!==this.generation)return;
      this.message.set(this.text('frozenSaved'));this.freezeRetry=null;await this.load();this.changed.emit();
      this.openKostValidation(result.prepared_input.id);
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Freeze failed');}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  async downloadPrepared(record:PreparedInput,kind:'zip'|'evidence'){
    try{await this.api.download(this.preparedBase()+'/'+record.id+'/'+kind,'prepared-'+record.id+(kind==='evidence'?'-evidence':'')+'.zip');}
    catch(error){this.error.set(error instanceof Error?error.message:'Download failed');}
  }
  async action(kind:'undo'|'redo'|'restore',target_id?:string){if(this.discardPending())await this.mutate(kind,{target_id:target_id||null});}
  private async mutate(kind:string,extra:object,fieldId?:string){
    if(this.contentDraft() && kind!=='content-edit')return false;
    if(this.importDraft()?.dirty && kind!=='import-record' && kind!=='import-apply')return false;
    if(this.bulkDraft() && kind!=='edit-batch')return false;
    if(this.sampleReviewDraft() && kind!=='sample-review')return false;
    if(this.creationDraft() && kind!=='structure-create')return false;
    const reason=kind==='structure-reorder'?'Reordered sibling objects':this.reason;
    const doc=this.document();if(!doc || this.busy() || !reason.trim() || !this.canEdit())return false;
    const signature=JSON.stringify({kind,extra,reason});
    if(this.retry?.signature!==signature)this.retry={signature,request:{request_id:crypto.randomUUID(),revision:doc.revision,source_sha256:doc.source_sha256,base_operation_id:doc.state.head,kind,reason,...extra}};
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{
      await this.api.request(this.base()+'/commands',{method:'POST',body:JSON.stringify(this.retry.request)});
      if(generation!==this.generation)return false;
      if(fieldId){const next=new Map(this.drafts());next.delete(fieldId);this.drafts.set(next);this.emitPending();}
      if(kind==='edit-batch'){this.drafts.set(new Map());this.emitPending();}
      if(kind==='edit-batch' && this.bulkDraft()){this.bulkDraft.set(null);this.bulkDialog()?.nativeElement.close();this.emitPending();}
      if(kind==='sample-review'){this.sampleReviewDraft.set(null);this.sampleReviewDialog()?.nativeElement.close();this.emitPending();}
      if(kind==='import-apply'){this.importDraft.set(null);this.importDialog()?.nativeElement.close();this.emitPending();}
      if(kind==='content-edit'){this.contentDraft.set(null);this.contentDialog()?.nativeElement.close();this.emitPending();}
      if(kind.startsWith('note-')){this.noteDraft.set(null);this.emitPending();}
      const created=kind==='structure-create'?this.creationDraft()?.preview?.objects[0]?.id:null;
      if(kind==='structure-create'){this.creationDraft.set(null);this.emitPending();}
      this.checkpointName='';this.comparison.set(null);this.message.set(this.text('saved'));this.retry=null;
      // Reload the exact server-assigned row identities before another applied edit.
      const remaining=this.drafts();await this.load();
      if(kind==='import-record')this.selectImport(this.importRecords().at(-1)?.id||'');
      if(kind==='sample-record')this.sampleSelection.set(this.sampleRecords().at(-1)?.id||'');
      if(created)this.select(created);
      // Other unapplied fields stay visible, but their old row/transfer baselines must be reviewed.
      if(remaining.size)this.error.set('Other field proposals remain. Reload or review their current baseline before applying.');
      this.changed.emit();
      return !this.error();
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Metadata operation failed');return false;}
    finally{this.busy.set(false);this.working.emit(false);}
  }
  describe(rows:MetadataRow[]|undefined):string{return (rows||[]).map(r=>[r.text,...Object.entries(r.attributes).map(([k,v])=>v?this.text(k)+': '+v:''),...Object.entries(r.children).map(([k,v])=>v.length?this.text(k)+': '+this.describe(v):'')].filter(Boolean).join(' · ')).join('\n');}
  private comparable(rows:MetadataRow[]|undefined){return JSON.stringify(rows||[],(key,value)=>key==='id'?undefined:value);}
  async compare(operation:MetadataOperation){
    this.error.set('');
    try{
      const historical=await this.api.request<MetadataWorkspace>(this.base()+'?operation_id='+operation.id);
      const current=this.document();if(!current)return;
      const older=new Map(historical.nodes.flatMap(n=>n.fields.map(f=>[f.id!,{node:n,field:f}] as const)));
      const historicalNodes=new Map(historical.nodes.map(n=>[n.id,n])),currentNodes=new Map(current.nodes.map(n=>[n.id,n]));
      this.comparison.set(current.nodes.flatMap(n=>n.fields.flatMap(f=>{const before=older.get(f.id!);const a=this.describe(before?.field.values),b=this.describe(f.values);return this.comparable(before?.field.values)===this.comparable(f.values)?[]:[{object:n.title,field:this.text(f.tag,f.label),before:a||this.text('empty'),after:b||this.text('empty')}];})));
      this.comparison.update(rows=>[...(rows||[]),...[...new Set([...historicalNodes.keys(),...currentNodes.keys()])].filter(id=>!historicalNodes.has(id)||!currentNodes.has(id)).map(id=>({object:(currentNodes.get(id)||historicalNodes.get(id))!.title,field:this.text('newObject'),before:historicalNodes.get(id)?.title||this.text('empty'),after:currentNodes.get(id)?.title||this.text('empty')}))]);
      const beforeNotes=new Map(historical.notes.map(n=>[n.id,n]));const afterNotes=new Map(current.notes.map(n=>[n.id,n]));
      this.comparison.update(rows=>[...(rows||[]),...[...new Set([...beforeNotes.keys(),...afterNotes.keys()])].flatMap(id=>{const a=beforeNotes.get(id),b=afterNotes.get(id);return JSON.stringify(a)===JSON.stringify(b)?[]:[{object:this.objectTitle((b||a)!.node_id),field:this.text('notes'),before:a?this.text(a.purpose)+': '+a.text:this.text('empty'),after:b?this.text(b.purpose)+': '+b.text:this.text('empty')}];})]);
      this.comparison.update(rows=>[...(rows||[]),...current.reorder_groups.flatMap(group=>{const before=historical.reorder_groups.find(g=>g.parent_id===group.parent_id && g.child_kind===group.child_kind);const a=before?.children.map(c=>c.id)||[],b=group.children.map(c=>c.id);return JSON.stringify(a)===JSON.stringify(b)?[]:[{object:this.objectTitle(group.parent_id),field:this.text('objectOrder')+' · '+this.text(group.child_kind),before:this.orderText(before?.children,a),after:this.orderText(group.children,b)}];})]);
      this.showHistory();
    }catch(error){this.error.set(error instanceof Error?error.message:'Comparison failed');}
  }
  async download(operation:MetadataOperation,kind:'metadata'|'evidence'|'archive'){
    try{await this.api.download(this.base()+'/'+operation.id+'/'+kind,'preparation-'+operation.id+(kind==='metadata'?'.xml':'.zip'));}
    catch(error){this.error.set(error instanceof Error?error.message:'Download failed');}
  }
}
