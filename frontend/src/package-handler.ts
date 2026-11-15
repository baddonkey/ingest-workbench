// Workspace interaction design informed by Package Turbo (see THIRD_PARTY_NOTICES.md).
import { Component, HostListener, OnChanges, OnDestroy, SimpleChanges, computed, inject, input, output, signal, viewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ArchiveApi } from './archive-api';
import { EditableField, HandlerEvent, HandlerSip, MetadataDraft, PackageNode, PackageNodeDetail, PackageWorkspace, SipExport } from './package-handler.types';
import { KEYCLOAK } from './identity';
import { MetadataEditor } from './metadata-editor';
import {ContextMenu,ContextMenuState} from './context-menu';
import {KostFileFindings} from './kost-file-findings';
import {KostReport} from './kost-report.types';

interface PendingField {node_id:string;field_id:string;path:string;label:string;before:string;value:string}

@Component({
  selector: 'archive-package-handler', standalone: true, imports: [CommonModule, FormsModule, MetadataEditor,ContextMenu,KostFileFindings],
  templateUrl: './package-handler.html', styleUrl: './package-handler.css',
})
export class PackageHandler implements OnChanges, OnDestroy {
  readonly editing = signal(true);
  readonly contextMenu=signal<ContextMenuState|null>(null);
  sourceContext(event:MouseEvent|KeyboardEvent,node:PackageNode){event.preventDefault();event.stopPropagation();const invoker=event.currentTarget as HTMLElement,bounds=invoker.getBoundingClientRect();this.contextMenu.set({x:event instanceof MouseEvent?event.clientX:bounds.left,y:event instanceof MouseEvent?event.clientY:bounds.bottom,label:node.title,invoker,commands:[
    {label:'Show in editor',run:()=>void this.selectNode(node)},
    {label:'Copy name',icon:'copy',run:()=>{void navigator.clipboard.writeText(node.title).catch(()=>this.message.set('Unable to copy.'));}},
    {label:'Download original SIP',icon:'download',run:()=>void this.download('original')},
    {label:'Download source metadata',icon:'file-text',run:()=>void this.download('metadata')},
    {label:'Edit working metadata',icon:'file-text',disabled:this.busy()||this.saving(),run:()=>this.setEditing(true)}]});}
  sourceContextKey(event:KeyboardEvent,node:PackageNode){if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10')this.sourceContext(event,node);}
  private readonly metadataEditor = viewChild(MetadataEditor);
  readonly kostStatuses = signal<Record<string,string>>({});
  readonly kostEnabled = signal(false);
  readonly kostReport = signal<KostReport|null>(null);
  readonly kostPaths = computed(()=>Object.fromEntries((this.document()?.nodes||[])
    .filter(node=>node.kind==='file' && node.source_id).map(node=>[node.source_id!,node.path])));
  openKostFile(selection:{path:string;input_sha256:string}){
    const doc=this.document();
    if(!doc || doc.input_sha256!==selection.input_sha256)return;
    const node=doc.nodes.find(item=>item.kind==='file' && item.path===selection.path);
    if(!node)return;
    if(this.editorPending() || this.pending().size || this.saving()){
      this.message.set('File findings are open in the KOST panel. Unsaved metadata remains in the editor.');return;
    }
    if(!this.discardPending())return;
    this.message.set('');this.editing.set(false);void this.selectNode(node);
  }
  readonly editorPending = signal(0);
  editorWorking(value:boolean){this.saving.set(value);this.working.emit(value);}
  editorChangedCount(value:number){this.editorPending.set(value);this.pendingChanges.emit(value+this.pending().size);}
  setEditing(value:boolean){if(this.discardPending()){this.editing.set(value);}}
  readonly transferId = input.required<string>();
  readonly revision = input.required<number>();
  readonly sips = input.required<HandlerSip[]>();
  readonly events = input.required<HandlerEvent[]>();
  readonly openSipId = input<string>('');
  readonly arrange = output<void>();
  readonly closed = output<void>();
  readonly changed = output<void>();
  readonly about = output<void>();
  readonly pendingChanges = output<number>();
  readonly working = output<boolean>();
  readonly pending = signal(new Map<string,PendingField>());
  readonly saving = signal(false);
  readonly message = signal('');
  draftReason = '';
  exportReason = '';
  private exportAttempt: {key:string;body:object} | null = null;
  private readonly keycloak = inject(KEYCLOAK);
  private readonly api = inject(ArchiveApi);
  readonly document = signal<PackageWorkspace | null>(null);
  readonly chosenSip = signal('');
  readonly detail = signal<PackageNodeDetail | null>(null);
  readonly selected = signal('');
  readonly view = signal<'logical' | 'physical'>('logical');
  readonly expanded = signal(new Set<string>());
  readonly query = signal('');
  readonly busy = signal(false);
  readonly detailBusy = signal(false);
  readonly downloading = signal(false);
  readonly error = signal('');
  readonly tool = signal<'findings' | 'search' | 'history' | 'changes'>('findings');
  readonly pageSize = signal(200);
  private generation = 0;
  private selectionGeneration = 0;
  private activeKey = '';
  private activeSipId = '';
  private preferredNode = '';
  canEdit() { return this.keycloak.hasRealmRole('archive-operator'); }
  hasCurrentExport() { const doc=this.document(); return doc?.exports.some(item=>item.draft_id===doc.draft.active?.id) || false; }

  readonly children = computed(() => {
    const result = new Map<string | null, PackageNode[]>();
    for (const node of this.document()?.nodes || []) {
      const siblings = result.get(node.parent_id) || [];
      siblings.push(node); result.set(node.parent_id, siblings);
    }
    return result;
  });
  readonly rows = computed(() => {
    const result: { node: PackageNode; depth: number; expandable: boolean }[] = [];
    const expanded = this.expanded(), children = this.children();
    const walk = (parent: string | null, depth: number) => {
      for (const node of children.get(parent) || []) {
        if (node.view !== this.view()) continue;
        const expandable = children.has(node.id);
        result.push({ node, depth, expandable });
        if (expanded.has(node.id)) walk(node.id, depth + 1);
      }
    };
    walk(null, 0); return result;
  });
  readonly matches = computed(() => {
    const query = this.query().trim().toLocaleLowerCase();
    if (!query) return [];
    return (this.document()?.nodes || []).filter(node =>
      [node.title, node.source_id, node.path, node.reference].some(value => value?.toLocaleLowerCase().includes(query)));
  });

  ngOnChanges(changes: SimpleChanges) {
    const available = this.sips();
    const current = this.activeSipId;
    const requested = changes['openSipId'] || changes['transferId'] ? this.openSipId() : '';
    const sip = available.find(s => s.id === requested) || available.find(s => s.id === current) || available[0];
    const key = this.transferId() + ':' + (sip?.id || '') + ':' + this.revision();
    if (key !== this.activeKey) {
      if (this.pending().size) { this.error.set('Transfer changed while edits are pending. Save will check the original revision; export or discard proposals before reloading.'); return; }
      this.activeKey = key;
      if (sip) void this.load(sip.id);
      else { this.generation++; this.selectionGeneration++; this.document.set(null); this.detail.set(null); this.busy.set(false); }
    }
  }

  ngOnDestroy() { this.generation++; this.selectionGeneration++; }

  @HostListener('window:beforeunload', ['$event'])
  beforeUnload(event: BeforeUnloadEvent) {
    if (this.pending().size || this.editorPending() || this.saving()) { event.preventDefault(); event.returnValue = ''; }
  }

  discardPending(ask = true) {
    if (this.saving()) return false;
    const editor=this.metadataEditor();
    if (editor && !editor.discardPending(ask)) return false;
    if (ask && this.pending().size && !window.confirm('Discard unsaved metadata proposals? Saved draft history remains retained.')) return false;
    this.pending.set(new Map()); this.pendingChanges.emit(0);
    return true;
  }
  reload() { if (this.discardPending()) void this.load(this.chosenSip()); }
  switchSip(event: Event) {
    const select = event.target as HTMLSelectElement;
    if (!this.discardPending()) { select.value = this.chosenSip(); return; }
    void this.load(select.value);
  }
  fieldValue(field: EditableField) { return this.pending().get(field.field_id)?.value ?? field.saved_value; }
  stage(field: EditableField, value: string) {
    if (!this.canEdit() || this.saving()) return;
    const next = new Map(this.pending());
    if (value === field.saved_value) next.delete(field.field_id);
    else {
      if (!next.has(field.field_id) && next.size >= 200) { this.error.set('Save or discard the 200 pending field changes first.'); return; }
      next.set(field.field_id, {node_id:field.node_id,field_id:field.field_id,path:field.path,label:field.label,before:field.saved_value,value});
    }
    this.pending.set(next); this.pendingChanges.emit(next.size); this.tool.set('changes'); this.message.set('');
  }
  async saveDraft() {
    const document = this.document();
    if (!document || !this.pending().size || !this.draftReason.trim() || this.saving()) return;
    await this.mutateDraft('/draft', {changes:[...this.pending().values()].map(({node_id,field_id,before,value})=>({node_id,field_id,before,value}))});
  }
  async discardDraft() {
    if (this.pending().size || !this.document()?.draft.active || !this.draftReason.trim() || this.saving()) return;
    await this.mutateDraft('/draft/discard', {});
  }
  private async mutateDraft(path: string, extra: object) {
    const document = this.document(); if (!document) return;
    const generation = this.generation;
    this.saving.set(true); this.working.emit(true); this.error.set('');
    try {
      await this.api.request(this.base(document.sip_id)+path, {method:'POST',body:JSON.stringify({revision:document.transfer_revision,source_sha256:document.input_sha256,base_draft_id:document.draft.active?.id || null,reason:this.draftReason,...extra})});
      if (generation !== this.generation) return;
      this.pending.set(new Map()); this.pendingChanges.emit(0); this.draftReason='';
      this.message.set(path.endsWith('/discard') ? 'Saved draft discarded. History retained; independent approval is required again.' : 'Metadata draft saved separately. Original SIP unchanged.');
      this.changed.emit(); await this.load(document.sip_id); this.tool.set('changes');
    } catch (error) {
      if (generation === this.generation) this.error.set(error instanceof Error ? error.message : 'Draft operation failed');
    } finally { this.saving.set(false); this.working.emit(false); }
  }
  async downloadDraft(draft: MetadataDraft, kind: 'metadata' | 'changes') {
    this.error.set('');
    try { await this.api.download(this.base()+'/drafts/'+draft.id+'/'+kind, 'draft-'+draft.id+(kind==='metadata'?'.xml':'-changes.json')); }
    catch(error) { this.error.set(error instanceof Error ? error.message : 'Draft download failed'); }
  }
  async exportSip() {
    const doc = this.document();
    if (!doc?.draft.active || this.pending().size || this.saving() || !this.exportReason.trim()) return;
    const generation = this.generation;
    const key = this.transferId()+':'+doc.sip_id+':'+doc.draft.active.id+':'+this.exportReason;
    // Keep an identical request for retry after a lost acknowledgement.
    if (this.exportAttempt?.key !== key) this.exportAttempt = {key,body:{revision:doc.transfer_revision,source_sha256:doc.input_sha256,draft_id:doc.draft.active.id,reason:this.exportReason}};
    this.saving.set(true); this.working.emit(true); this.error.set('');
    try {
      await this.api.request(this.base(doc.sip_id)+'/exports',{method:'POST',body:JSON.stringify(this.exportAttempt.body)});
      if (generation !== this.generation) return;
      this.exportAttempt=null; this.exportReason='';
      this.message.set('Derived SIP retained with lineage and receipt checks. Saved draft and archival acceptance hold remain.');
      this.changed.emit(); await this.load(doc.sip_id); this.tool.set('changes');
    } catch(error) { if (generation===this.generation) this.error.set(error instanceof Error ? error.message : 'SIP export failed'); }
    finally { this.saving.set(false); this.working.emit(false); }
  }
  async downloadExport(item: SipExport, kind: 'zip' | 'evidence') {
    this.error.set('');
    try { await this.api.download(this.base()+'/exports/'+item.id+'/'+kind,'derived-'+item.id+(kind==='zip'?'.zip':'-evidence.json')); }
    catch(error) { this.error.set(error instanceof Error ? error.message : 'Export download failed'); }
  }
  exportPending() {
    const blob = new Blob([JSON.stringify({source_sha256:this.document()?.input_sha256,base_draft_id:this.document()?.draft.active?.id || null,status:'UNSAVED_BROWSER_PROPOSALS',changes:[...this.pending().values()]},null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob), anchor=document.createElement('a');anchor.href=url;anchor.download='unsaved-metadata-proposals.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }

  base(sipId = this.document()?.sip_id) {
    return '/api/transfers/' + this.transferId() + '/sips/' + sipId;
  }

  async load(sipId: string) {
    if (!this.sips().some(sip => sip.id === sipId)) return;
    if (this.activeSipId !== sipId) this.preferredNode='';
    this.activeSipId = sipId;
    this.activeKey = this.transferId() + ':' + sipId + ':' + this.revision();
    this.chosenSip.set(sipId);
    const generation = ++this.generation;
    this.selectionGeneration++;
    this.document.set(null); this.detail.set(null); this.detailBusy.set(false); this.selected.set(''); this.error.set('');
    this.query.set(''); this.pageSize.set(200); this.busy.set(true);
    const path = this.base(sipId);
    try {
      const document = await this.api.request<PackageWorkspace>(path + '/workspace');
      if (generation !== this.generation) return;
      this.document.set(document);
      // An explicit successful reload establishes a new export baseline.
      this.exportAttempt = null;
      const roots = document.nodes.filter(n => n.parent_id === null);
      this.expanded.set(new Set([...roots.map(n => n.id), ...document.nodes.filter(n => roots.some(r => r.id === n.parent_id)).map(n => n.id)]));
      const root = document.nodes.find(n=>n.id===this.preferredNode) || roots.find(n => n.view === this.view()) || roots[0];
      if (root) void this.selectNode(root);
    } catch (error) {
      if (generation === this.generation) this.error.set(error instanceof Error ? error.message : 'Package could not be opened');
    } finally { if (generation === this.generation) this.busy.set(false); }
  }

  async selectNode(node: PackageNode) {
    this.preferredNode=node.id;
    const selection = ++this.selectionGeneration, generation = this.generation;
    this.selected.set(node.id); this.detail.set(null); this.detailBusy.set(true); this.error.set('');
    this.view.set(node.view);
    const byId = new Map((this.document()?.nodes || []).map(n => [n.id, n]));
    const expanded = new Set(this.expanded());
    let parent = node.parent_id;
    while (parent) { expanded.add(parent); parent = byId.get(parent)?.parent_id || null; }
    this.expanded.set(expanded);
    const targetIndex = this.rows().findIndex(row => row.node.id === node.id);
    this.pageSize.update(limit => Math.max(limit, targetIndex + 1));
    try {
      const detail = await this.api.request<PackageNodeDetail>(this.base() + '/nodes/' + node.id);
      if (selection === this.selectionGeneration && generation === this.generation) this.detail.set(detail);
    } catch (error) {
      if (selection === this.selectionGeneration && generation === this.generation) this.error.set(error instanceof Error ? error.message : 'Object could not be opened');
    } finally { if (selection === this.selectionGeneration && generation === this.generation) this.detailBusy.set(false); }
  }

  setView(view: 'logical' | 'physical') { this.view.set(view); this.pageSize.set(200); }
  toggle(id: string) { this.expanded.update(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  badge(kind: string) {
    const labels: Record<string, string> = { paket: 'SIP', ablieferung: 'SUB', ordnungssystem: 'OS', ordnungssystemposition: 'POS', dossier: 'D', subdossier: 'SD', dokument: 'DOC', mappe: 'M', vorgang: 'V', aktivitaet: 'A', dateiRef: 'REF', package: 'ZIP', folder: 'DIR', file: 'F' };
    return labels[kind] || kind;
  }
  async download(kind: 'original' | 'metadata' | 'file') {
    const document = this.document();
    if (!document) return;
    const file = this.detail()?.file;
    if (kind === 'file' && !file) return;
    const generation = this.generation;
    this.error.set(''); this.downloading.set(true);
    try {
      const path = kind === 'file' ? '/nodes/' + file!.id + '/content' : '/' + kind;
      await this.api.download(this.base(document.sip_id) + path, kind === 'original' ? 'original-' + document.sip_id + '.zip' : kind === 'metadata' ? 'metadata.xml' : file!.title);
    } catch (error) { if (generation === this.generation) this.error.set(error instanceof Error ? error.message : 'Download failed'); }
    finally { this.downloading.set(false); }
  }
}
