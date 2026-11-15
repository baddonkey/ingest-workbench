export interface HandlerSip { id: string; profile: string; sha256: string }
export interface HandlerEvent { id: string; time: string; type: string; actor: string; outcome: string; detail: unknown }
export interface PackageNode {
  id: string; parent_id: string | null; view: 'logical' | 'physical'; kind: string;
  title: string; source_id?: string | null; path: string; reference?: string; unit_id?: string;
  content_node_id?: string; file_id?: string; size?: number; sha256?: string; payload?: boolean;
}
export interface PackageWorkspace {
  sip_id: string; input_sha256: string; original_filename?: string | null; profile: string; kind: string; read_only: boolean;
  metadata_path: string; transfer_revision: number; nodes: PackageNode[];
  statistics: { payload_files: number; payload_bytes: number; zip_members: number; top_level_dossiers: number };
  validation: { internal: string; internal_scope: string; acceptance: string };
  findings: { code: string; severity: string; message: string; action: string }[];
  draft: {active: MetadataDraft | null; patches: DraftPatch[]; history: MetadataDraft[]};
  exports: SipExport[];
}
export interface SipExport {id:string;draft_id:string;source_sha256:string;sha256:string;metadata_sha256:string;evidence_sha256:string;actor:string;reason:string;created:string;internal_status:string}
export interface MetadataDraft {id:string;sequence:number;actor:string;reason:string;created:string;metadata_sha256:string;changes_sha256:string;change_count:number;patch_count:number}
export interface DraftPatch {node_id:string;field_id:string;path:string;before:string;after:string}
export interface EditableField {node_id:string;field_id:string;path:string;label:string;source_value:string;saved_value:string}
export interface PackageNodeDetail {
  node: PackageNode; fields: { path: string; value: string }[]; fields_truncated: boolean;
  xml: string | null; xml_truncated?: boolean; file: PackageNode | null; input_sha256: string;
  preview: { available: boolean; text?: string; reason?: string; truncated?: boolean; byte_limit?: number } | null;
  edit_fields: EditableField[];
}
