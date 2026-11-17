import {Component,ElementRef,Injector,OnChanges,OnDestroy,SimpleChanges,afterNextRender,computed,inject,input,output,signal,viewChild} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {ArchiveApi} from './archive-api';
import {KEYCLOAK} from './identity';
import {MetadataFieldEditor} from './metadata-field';
import {MetadataField,MetadataObject,MetadataRow,MetadataWorkspace,MetadataOperation,PreparedInput,PreparedSelection,PreparationNote,ReorderGroup,CreationOption,MetadataNavigator,MetadataTreeObject,MetadataSample} from './metadata-editor.types';
import {EditorLanguage,editorText} from './metadata-editor.locale';
import {ContextMenu,ContextMenuState,ContextCommand} from './context-menu';
import {KostValidation} from './kost-validation';
import {KostFileFindings} from './kost-file-findings';
import {KostReport} from './kost-report.types';

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
  openKostValidation(target='received'){this.setTool('kost');this.kostPanel()?.show(target);}
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
  historyValue(operation:MetadataOperation,side:'before'|'after',depth=0):string{
    const changes=operation.changes,rows=changes[side];
    if(rows!==undefined)return rows.length?this.describe(rows)||this.text('historyEmptyValue'):this.text('empty');
    if(changes.note_id){const note=side==='before'?changes.note_before:changes.note_after;return note?note.text+' · '+this.text(note.purpose):this.text('empty');}
    const order=side==='before'?changes.before_order:changes.after_order;
    if(order!==undefined)return this.orderText(changes.objects,order)||this.text('empty');
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
  canSave(){return !this.busy() && !this.structurePending() && !this.noteDraft() && this.canEdit() && this.proposed().length>0;}
  async saveChanges(){
    if(!this.canSave())return;
    const dialog=this.saveReasonDialog()?.nativeElement;
    if(!this.requireReason(()=>this.saveChanges()))return;
    dialog?.close();
    for(const id of this.proposed().map(p=>p.field.id!)){
      const item=this.document()?.nodes.flatMap(n=>n.fields.map(field=>({node:n,field}))).find(p=>p.field.id===id);
      if(!item || !this.drafts().has(id))continue;
      await this.apply(item.field,item.node.id);
      if(this.drafts().has(id))break;
    }
  }
  async downloadWorking(){const history=this.document()?.history||[];const latest=history[history.length-1];if(latest)await this.download(latest,'metadata');else this.sourceDownload.emit('metadata');}
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
    const group=this.document()?.reorder_groups.find(g=>g.children.some(c=>c.id===node.id)),index=group?.children.findIndex(c=>c.id===node.id)??-1;
    commands.push({label:this.text('rename'),disabled:blocked||!title?.editable,run:()=>{this.select(node.id);this.chooseEditor('metadata');setTimeout(()=>document.querySelector<HTMLElement>('[data-field-tag="'+title?.tag+'"] textarea')?.focus());}},
      {label:this.text('previewUp'),disabled:blocked||index<=0,run:()=>{this.select(node.id);this.stageReorder(-1);}},
      {label:this.text('previewDown'),disabled:blocked||!group||index<0||index>=group.children.length-1,run:()=>{this.select(node.id);this.stageReorder(1);}},
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
  hierarchyDraft=signal<{group:ReorderGroup;after_order:string[]}|null>(null);
  creationDraft=signal<{option:CreationOption;count:number;values:Record<string,MetadataRow[]>;request:Record<string,unknown>|null;preview:{objects:{id:string;source_id:string;title:string}[]}|null}|null>(null);
  creationOptions=computed(()=>this.document()?.creation_options.filter(o=>o.parent_id===this.selected())||[]);
  structurePending=computed(()=>!!this.hierarchyDraft() || !!this.creationDraft());
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
  selectedGroup=computed(()=>this.document()?.reorder_groups.find(g=>g.children.some(c=>c.id===this.selected())));
  canReorder(delta:number){const group=this.selectedGroup();if(!group || this.busy() || !this.canEdit() || this.pendingCount())return false;const index=group.children.findIndex(c=>c.id===this.selected());return index+delta>=0 && index+delta<group.children.length;}
  stageReorder(delta:number){if(!this.canReorder(delta))return;const group=this.selectedGroup()!;const after=group.children.map(c=>c.id);const index=after.indexOf(this.selected());[after[index],after[index+delta]]=[after[index+delta],after[index]];this.hierarchyDraft.set({group,after_order:after});this.tool.set('hierarchy');this.emitPending();}
  cancelReorder(){if(this.busy() || !window.confirm(this.text('discardHierarchy')))return;this.hierarchyDraft.set(null);this.emitPending();}
  async applyReorder(){const draft=this.hierarchyDraft();if(!draft)return;await this.mutate('structure-reorder',{parent_id:draft.group.parent_id,child_kind:draft.group.child_kind,before_order:draft.group.children.map(c=>c.id),after_order:draft.after_order});}
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
  pendingCount=computed(()=>this.drafts().size+(this.noteDraft()?1:0)+(this.hierarchyDraft()?1:0)+(this.creationDraft()?1:0));
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
  searchResults=computed(()=>{const q=this.search().trim().toLocaleLowerCase();return q?(this.document()?.nodes||[]).flatMap(node=>node.fields.flatMap(field=>{const value=this.describe(field.values);return value.toLocaleLowerCase().includes(q)?[{node,field,value}]:[];})):[];});
  proposed=computed(()=>this.document()?.nodes.flatMap(node=>node.fields.filter(field=>this.drafts().has(field.id!)).map(field=>({node,field})))||[]);
  drafts=signal(new Map<string,MetadataRow[]>());reason='';checkpointName='';comparison=signal<{object:string;field:string;before:string;after:string}[]|null>(null);
  private generation=0;private activeKey='';private retry:{signature:string;request:object}|null=null;
  private freezeRetry:{signature:string;request:object}|null=null;
  frozenCurrent=computed(()=>this.document()?.prepared_inputs.find(r=>r.operation_id===this.document()?.state.head));
  latestFrozen=computed(()=>this.document()?.prepared_inputs.at(-1));
  previousFrozen=computed(()=>this.document()?.prepared_inputs.slice(0,-1).reverse()||[]);
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
    if(key!==this.activeKey){this.activeKey=key;this.notesOwner.set(null);this.selected.set('');this.collapsed.set(new Set());this.reason='';this.comparison.set(null);void this.load();}
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
    this.drafts.set(new Map());this.noteDraft.set(null);this.hierarchyDraft.set(null);this.creationDraft.set(null);this.emitPending();return true;
  }
  reload(){if(this.discardPending()){this.comparison.set(null);void this.load();}}
  select(id:string,source?:MetadataNavigator,rowKey?:string){
    const node=this.document()?.nodes.find(n=>n.id===id);if(!node)return;
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
    if(!doc||this.sampleLoading()||!this.sampleSizeValid())return;
    const generation=this.generation,params=new URLSearchParams({mode,size:String(size),revision:String(doc.revision)});
    this.sampleLoading.set(true);this.error.set('');
    try{
      const result=await this.api.request<MetadataSample>(this.base()+'/sample?'+params.toString());
      if(generation!==this.generation)return;
      if(result.revision!==this.document()?.revision){this.error.set(this.text('sampleStale'));return;}
      this.sampleResult.set(result);
    }catch(error){if(generation===this.generation)this.error.set(error instanceof Error?error.message:'Sample could not be drawn');}
    finally{if(generation===this.generation)this.sampleLoading.set(false);}
  }
  exportSample(){
    const items=this.sampleResult()?.items||[];
    if(!items.length)return;
    const quote=(value:string)=>'"'+(/^\s*[=+@-]/.test(value)?"'"+value:value).replaceAll('"','""')+'"';
    const rows=[[this.text('sampleReference'),this.text('sampleDossier')],...items.map(item=>[item.reference,item.name])];
    const csv='\uFEFF'+rows.map(row=>row.map(quote).join(',')).join('\r\n')+'\r\n';
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),anchor=document.createElement('a');
    anchor.href=url;anchor.download='ingest-packager-sample.csv';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  openSearchResult(id:string){this.select(id);}
  primary(field:MetadataField){return !!field.minimum || ['titel','name','ablieferungstyp','ablieferndeStelle','ablieferungsnummer','aktenbildnerName','aktenzeichen','entstehungszeitraum','schutzfristenkategorie','schutzfrist','systemName','registratur','systemBeschreibung'].includes(field.tag) || this.drafts().has(field.id!);}
  navigatorDepth(node:MetadataTreeObject){return node.navigation_depth;}
  timespan(node:MetadataObject){const rows=node.fields.find(f=>f.tag==='entstehungszeitraum')?.values||[];const strings=(row:MetadataRow):string[]=>[row.text,...Object.values(row.children).flatMap(children=>children.flatMap(strings))].filter(Boolean);return rows.flatMap(strings).join(' - ');}
  recordTitle(node:MetadataObject){return node.kind==='ablieferung'?(this.fieldText(node,'ablieferungsnummer')||node.title):node.title;}
  navigatorTitle(node:MetadataObject){const number=node.kind==='ordnungssystemposition'?this.fieldText(node,'nummer'):'';return number?number+' '+node.title:node.title;}
  displayFields(node:MetadataObject){const order=node.kind==='ablieferung'?['ablieferungstyp','ablieferndeStelle','ablieferungsnummer','entstehungszeitraum','schutzfristenkategorie','schutzfrist']:node.kind==='provenienz'?['aktenbildnerName','systemName','registratur']:[];return [...node.fields].sort((a,b)=>(order.includes(a.tag)?order.indexOf(a.tag):100)-(order.includes(b.tag)?order.indexOf(b.tag):100));}
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
    if(this.structurePending() || this.noteDraft())return;
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
    if(this.hierarchyDraft() && kind!=='structure-reorder')return false;
    if(this.creationDraft() && kind!=='structure-create')return false;
    const doc=this.document();if(!doc || this.busy() || !this.reason.trim() || !this.canEdit())return false;
    const signature=JSON.stringify({kind,extra,reason:this.reason});
    if(this.retry?.signature!==signature)this.retry={signature,request:{request_id:crypto.randomUUID(),revision:doc.revision,source_sha256:doc.source_sha256,base_operation_id:doc.state.head,kind,reason:this.reason,...extra}};
    const generation=this.generation;this.busy.set(true);this.working.emit(true);this.error.set('');
    try{
      await this.api.request(this.base()+'/commands',{method:'POST',body:JSON.stringify(this.retry.request)});
      if(generation!==this.generation)return false;
      if(fieldId){const next=new Map(this.drafts());next.delete(fieldId);this.drafts.set(next);this.emitPending();}
      if(kind.startsWith('note-')){this.noteDraft.set(null);this.emitPending();}
      if(kind==='structure-reorder'){this.hierarchyDraft.set(null);this.emitPending();}
      const created=kind==='structure-create'?this.creationDraft()?.preview?.objects[0]?.id:null;
      if(kind==='structure-create'){this.creationDraft.set(null);this.emitPending();}
      this.checkpointName='';this.comparison.set(null);this.message.set(this.text('saved'));this.retry=null;
      // Reload the exact server-assigned row identities before another applied edit.
      const remaining=this.drafts();await this.load();
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
  async download(operation:MetadataOperation,kind:'metadata'|'evidence'){
    try{await this.api.download(this.base()+'/'+operation.id+'/'+kind,'preparation-'+operation.id+(kind==='evidence'?'.zip':'.xml'));}
    catch(error){this.error.set(error instanceof Error?error.message:'Download failed');}
  }
}
